"""导出文件存储（export_report_tool 的下载链接后端）。

工具生成 CSV / Markdown / JSON 等导出物后写入本地导出目录，返回
``/api/export/<id>`` 下载链接；web.server 提供对应的鉴权下载端点。

- 文件以服务端生成的 uuid 命名（不信任用户文件名，防路径穿越）；
- 用户可见文件名（Content-Disposition）做严格净化；
- 目录固定为 settings.AUDIT_DIR/exports（logs/exports），可配置审计目录。
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from audit.logging import get_logger
from config import settings

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_\u4e00-\u9fff.\- ]")

logger = get_logger("tools._export_store")


class ExportNotFoundError(KeyError):
    """导出文件不存在或 id 非法。"""


@dataclass
class ExportItem:
    """导出的一个文件（供下载端点使用）。"""

    export_id: str
    path: Path
    meta: dict[str, Any]

    @property
    def suffix(self) -> str:
        # 落盘文件不携带扩展名（uuid 裸名），扩展名取自用户可见文件名
        return Path(self.meta.get("filename", "export")).suffix

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


def sanitize_filename(name: str, fallback: str = "export") -> str:
    """净化用户提供的文件名：只保留安全字符，去除路径分隔符。"""
    cleaned = _SAFE_NAME_RE.sub("", (name or "").strip())
    cleaned = cleaned.replace("..", "").strip(" .")
    return cleaned or fallback


class ExportStore:
    """线程安全的导出文件存储。"""

    def __init__(self, root: Path | None = None, ttl_hours: int = 24) -> None:
        self.root = root or (settings.AUDIT_DIR / "exports")
        self.ttl_hours = ttl_hours
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _cleanup_expired(self) -> None:
        """清理过期的导出文件（基于文件修改时间）。"""
        now = time.time()
        cutoff = now - (self.ttl_hours * 3600)

        deleted_count = 0
        try:
            for item in self.root.glob("[0-9a-f]" * 32):
                if not item.is_file():
                    continue
                # 检查文件修改时间
                if item.stat().st_mtime < cutoff:
                    # 删除数据文件
                    item.unlink()
                    # 删除元数据文件
                    meta_file = self.root / f"{item.stem}.meta.json"
                    if meta_file.exists():
                        meta_file.unlink()
                    deleted_count += 1
        except Exception:
            # 清理失败不影响主流程
            pass

        if deleted_count > 0:
            logger.info(f"清理了 {deleted_count} 个过期导出文件", extra={"event": "export_cleanup"})

    def save(self, filename: str, content: bytes, meta: dict[str, Any] | None = None) -> str:
        """写入导出文件，返回下载 id（uuid hex）。

        文件实际落盘为 ``<root>/<id>``（不含扩展名），扩展信息写入
        ``<root>/<id>.meta.json``，供下载端点还原 Content-Disposition。
        """
        export_id = uuid.uuid4().hex
        with self._lock:
            path = self.root / export_id
            path.write_bytes(content)
            meta_path = self.root / f"{export_id}.meta.json"
            meta_path.write_text(
                json.dumps(
                    {"filename": sanitize_filename(filename), **(meta or {})},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        # 偶发清理过期导出文件（大约每 100 次保存触发一次，摊薄扫描成本）
        if random.random() < 0.01:
            self._cleanup_expired()
        return export_id

    def get(self, export_id: str) -> ExportItem:
        """按 id 取回导出文件；不存在或 id 非法时抛 ExportNotFoundError。

        只接受 uuid 十六进制形态的 id，拒绝路径穿越。
        """
        if not re.fullmatch(r"[0-9a-f]{32}", export_id or ""):
            raise ExportNotFoundError(f"非法导出 id: {export_id!r}")
        path = self.root / export_id
        if not path.is_file():
            raise ExportNotFoundError(f"导出文件不存在: {export_id}")
        meta_path = self.root / f"{export_id}.meta.json"
        meta: dict[str, Any] = {"filename": "export"}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return ExportItem(export_id=export_id, path=path, meta=meta)

    def url_for(self, export_id: str) -> str:
        return f"/api/export/{export_id}"

    def __len__(self) -> int:
        return len(list(self.root.glob("[0-9a-f]" * 32))) if self.root.exists() else 0


_default_store: ExportStore | None = None
_store_lock = threading.Lock()


def default_export_store(root: Path | None = None) -> ExportStore:
    """进程内复用的默认导出存储。"""
    global _default_store
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                _default_store = ExportStore(root=root)
    return _default_store
