"""Model Provider 配置持久化：JSON 文件 CRUD + API Key 落盘加密 + 预置种子。

设计说明：
- 存储于服务端 JSON 文件（``config/providers.json``），线程锁保护并发读写；
- API Key **落盘加密**（``providers/crypto.py``，Encrypt-then-MAC）：文件内容
  不含明文，备份 / 泄露 / 误提交仓库时不直接暴露；内存中为明文（适配器调用
  上游 API 必需）；
- 对外视图（``public_view`` / ``public_list``）**完全不含 api_key 字段**：
  前端不再回填任何脱敏串，杜绝「把脱敏串当真 Key 用」；需要查看密钥走
  ``reveal_key``（受认证保护的显式端点，并记审计日志）；
- 更新时若提交的 Key 为历史脱敏串或空串，保留服务端原 Key（向后兼容旧客户端）；
- 预置供应商（OpenAI / Anthropic）首次加载自动写入，``is_preset=True``
  不可删除（可禁用 / 编辑）；供应商控制只开放这两家；
- 加载时自动清理不在预置清单内的历史条目（智谱 / Gemini / 自定义中转站），
  清理前把原文件备份为 ``providers.json.bak``；
- 历史明文文件在加载时自动迁移为加密格式（一次性写回）。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from providers.crypto import decrypt_secret, encrypt_secret, is_encrypted, normalize_master
from providers.models import (
    ApiProtocol,
    ModelItem,
    ProviderConfig,
    is_masked_key,
)

PRESET_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "openai",
        "name": "OpenAI",
        "is_preset": True,
        "enabled": True,
        "base_url": "https://api.openai.com/v1",
        "protocol": ApiProtocol.OPENAI_CHAT,
        "models": [
            {
                "id": "gpt-4o",
                "context_window": 128000,
                "capabilities": ["vision", "json_schema", "function_calling"],
            },
            {
                "id": "gpt-4o-mini",
                "context_window": 128000,
                "capabilities": ["vision", "json_schema", "function_calling"],
            },
        ],
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "is_preset": True,
        "enabled": False,
        "base_url": "https://api.anthropic.com",
        "protocol": ApiProtocol.ANTHROPIC,
        "models": [
            {
                "id": "claude-sonnet-4-20250514",
                "context_window": 200000,
                "capabilities": ["json_schema", "function_calling"],
            },
            {
                "id": "claude-haiku-4-20250514",
                "context_window": 200000,
                "capabilities": ["json_schema"],
            },
        ],
    },
]

# 供应商控制白名单：仅保留 OpenAI 与 Anthropic 两家预置供应商。
SUPPORTED_PRESET_IDS: frozenset[str] = frozenset(p["id"] for p in PRESET_PROVIDERS)


def _new_id() -> str:
    """生成供应商唯一 ID（短随机 hex，避免与预置 id 冲突）。"""
    return "p_" + uuid.uuid4().hex[:10]


class ProviderStore:
    """供应商配置持久化存储（JSON 文件；线程安全；Key 落盘加密）。"""

    def __init__(self, path: str | Path | None = None) -> None:
        """打开（或初始化）供应商配置文件；路径缺省取 config/providers.json。"""
        if path is None:
            from config import settings

            path = settings.PROVIDERS_FILE
        self.path = Path(path)
        self._lock = threading.RLock()
        self._providers: dict[str, ProviderConfig] = {}
        self._master: bytes | None = None  # 惰性初始化（依赖 settings）
        self._load()

    # ------------------------------------------------------------------ #
    # 加密主密钥
    # ------------------------------------------------------------------ #
    def _key(self) -> bytes:
        """落盘加密主密钥（PROVIDERS_ENC_SECRET 优先，回退 AUTH_JWT_SECRET）。"""
        if self._master is None:
            from config import settings

            secret = settings.PROVIDERS_ENC_SECRET or settings.AUTH_JWT_SECRET
            self._master = normalize_master(secret)
        return self._master

    # ------------------------------------------------------------------ #
    # 内部读写（磁盘加密 <-> 内存明文）
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        """从文件加载并解密 Key；文件不存在时写入预置种子。"""
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                items = [item for item in raw if isinstance(item, dict) and item.get("id")]
            except (json.JSONDecodeError, OSError):
                # 损坏文件不吞错：备份后重建（保证配置表永远可用）
                backup = self.path.with_suffix(self.path.suffix + ".bak")
                try:
                    backup.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    pass
                items = []
            if items:
                supported_items = [item for item in items if item["id"] in SUPPORTED_PRESET_IDS]
                pruned = len(supported_items) != len(items)
                if pruned:
                    # 供应商控制收窄迁移：清掉非白名单条目（智谱 / Gemini /
                    # 自定义中转站），原文件先备份，避免密文配置不可恢复丢失。
                    try:
                        backup = self.path.with_suffix(self.path.suffix + ".bak")
                        backup.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
                    except OSError:
                        pass
                    items = supported_items
                if not items:
                    return self._seed_presets()  # 清理后为空：回落预置种子
                self._providers = {
                    item["id"]: ProviderConfig.model_validate(item) for item in items
                }
                migrated = self._decrypt_all()
                if pruned or migrated:
                    # 清理 / 明文迁移结果立即写回，避免下次加载重复迁移
                    self._save()
                return
        self._seed_presets()

    def _seed_presets(self) -> None:
        """写入预置种子（OpenAI / Anthropic）并落盘。"""
        self._providers = {p["id"]: ProviderConfig.model_validate(p) for p in PRESET_PROVIDERS}
        self._save()

    def _decrypt_all(self) -> bool:
        """把内存中各供应商的落盘密文解密为明文；返回是否发生明文迁移。"""
        migrated = False
        for provider in self._providers.values():
            if not provider.api_key:
                continue
            if is_encrypted(provider.api_key):
                provider.api_key = decrypt_secret(provider.api_key, self._key())
            else:
                migrated = True  # 历史明文：加载即触发一次性加密写回
        return migrated

    def _save(self) -> None:
        """全量写回 JSON 文件（原子替换；api_key 加密后落盘）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        items = []
        for p in self._providers.values():
            item = p.model_dump(mode="json")
            item["api_key"] = encrypt_secret(str(p.api_key or ""), self._key())
            items.append(item)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def list_providers(self) -> list[ProviderConfig]:
        """返回全部供应商（按创建时间排序，预置靠前）。"""
        with self._lock:
            items = sorted(
                self._providers.values(),
                key=lambda p: (not p.is_preset, p.created_at, p.name),
            )
            return list(items)

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        """按 ID 取供应商（内存明文 Key，仅服务端内部使用）。"""
        with self._lock:
            return self._providers.get(provider_id)

    # ------------------------------------------------------------------ #
    # 写操作
    # ------------------------------------------------------------------ #
    def create_provider(self, data: dict[str, Any]) -> ProviderConfig:
        """创建供应商；返回落库后的配置（模型缺省能力与名称自动补齐）。"""
        with self._lock:
            now = int(time.time())
            payload = dict(data)
            payload["id"] = str(payload.get("id") or _new_id())
            if payload["id"] in self._providers:
                raise ValueError(f"供应商 ID 已存在: {payload['id']}")
            payload.setdefault("is_preset", False)
            payload.setdefault("enabled", True)
            # 兼容旧客户端：创建时提交脱敏串无原始 Key 可还原，按未配置处理
            if payload.get("api_key") and is_masked_key(str(payload["api_key"])):
                payload["api_key"] = ""
            payload.setdefault("api_key", "")
            payload.setdefault("models", [])
            payload.setdefault("custom_headers", {})
            payload.setdefault("created_at", now)
            payload.setdefault("updated_at", now)
            provider = ProviderConfig.model_validate(payload)
            provider.models = self._normalize_models(provider.models)
            provider.created_at = now
            provider.updated_at = now
            self._providers[provider.id] = provider
            self._save()
            return provider

    def update_provider(self, provider_id: str, data: dict[str, Any]) -> ProviderConfig | None:
        """按 ID 更新供应商；不存在时返回 None。

        Key 语义：空串 / 缺省 / 历史脱敏串 => 保留服务端原 Key；其余值 =>
        视为用户重新输入的新明文（服务端加密落盘）。前端不再回填脱敏串，
        该保护仅为兼容旧客户端保留。

        更新语义（重要修复）：**先 dump 再合并后整体重新验证**。此前用
        ``model_copy(update=payload)`` 直接合并——pydantic v2 的 model_copy
        不重新校验字段，前端提交的 ``models``（list[dict]）会原样塞进实例，
        后续 ``_normalize_models`` 访问 ``m.id`` 触发 AttributeError（HTTP 500），
        模型清单从未持久化 -> 模型切换器永远不收录该供应商（刷新后看似
        "配置丢失"）。重建式更新保证 list[dict] / ModelItem 混合输入都被
        严格验证为契约内的 ModelItem。
        """
        with self._lock:
            current = self._providers.get(provider_id)
            if current is None:
                return None
            payload = dict(data)
            payload.pop("id", None)  # ID 不可变更
            payload.pop("is_preset", None)  # 预置标记不可变更
            submitted_key = payload.get("api_key")
            if submitted_key is None or submitted_key == "" or is_masked_key(str(submitted_key)):
                payload.pop("api_key", None)  # 保留原 Key
            merged_data = current.model_dump(mode="python")
            merged_data.update(payload)
            # api_key 语义由上面的 payload 过滤保证：未提交/空串/脱敏串时
            # merged_data 保留 dump 出的服务端原 Key；提交新值时被 payload 覆盖。
            try:
                merged = ProviderConfig.model_validate(merged_data)
            except ValidationError as exc:
                raise ValueError(f"供应商配置更新非法: {exc}") from exc
            merged.models = self._normalize_models(merged.models)
            merged.updated_at = int(time.time())
            self._providers[provider_id] = merged
            self._save()
            return merged

    def delete_provider(self, provider_id: str) -> bool:
        """删除供应商；预置供应商拒绝删除（返回 False）。"""
        with self._lock:
            current = self._providers.get(provider_id)
            if current is None or current.is_preset:
                return False
            del self._providers[provider_id]
            self._save()
            return True

    def set_enabled(self, provider_id: str, enabled: bool) -> ProviderConfig | None:
        """切换供应商启用状态（快捷开关）。"""
        return self.update_provider(provider_id, {"enabled": bool(enabled)})

    # ------------------------------------------------------------------ #
    # 模型辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_models(models: list[ModelItem]) -> list[ModelItem]:
        """补齐模型展示名称（缺省用 id）并去重。"""
        seen: set[str] = set()
        normalized: list[ModelItem] = []
        for m in models:
            if m.id in seen:
                continue
            seen.add(m.id)
            if not m.name:
                m = m.model_copy(update={"name": m.id})
            normalized.append(m)
        return normalized

    # ------------------------------------------------------------------ #
    # 对外视图（不含 Key；查看走 reveal_key）
    # ------------------------------------------------------------------ #
    def public_view(self, provider: ProviderConfig) -> dict[str, Any]:
        """序列化为前端可安全展示的字典（不含 api_key；仅暴露 has_api_key 布尔位）。"""
        view = provider.model_dump(mode="json")
        view.pop("api_key", None)
        view["has_api_key"] = bool(provider.api_key)
        return view

    def public_list(self) -> list[dict[str, Any]]:
        """返回脱敏后的供应商列表（前端设置面板 / 模型切换器数据源）。"""
        return [self.public_view(p) for p in self.list_providers()]

    def reveal_key(self, provider_id: str) -> str | None:
        """返回供应商的真实 API Key（明文）。

        仅供受认证保护的「查看密钥」端点调用（调用方负责审计日志）；
        供应商不存在返回 None。
        """
        with self._lock:
            provider = self._providers.get(provider_id)
            return provider.api_key if provider else None


# --------------------------------------------------------------------------- #
# 进程级单例
# --------------------------------------------------------------------------- #
_default_store: ProviderStore | None = None
_store_lock = threading.Lock()


def default_provider_store() -> ProviderStore:
    """进程内复用的默认供应商存储（双检锁保护并发首调）。"""
    global _default_store
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                _default_store = ProviderStore()
    return _default_store


def reset_default_provider_store(store: ProviderStore | None = None) -> None:
    """重置进程级默认存储（供测试注入临时文件实例）。"""
    global _default_store
    _default_store = store


__all__ = [
    "PRESET_PROVIDERS",
    "SUPPORTED_PRESET_IDS",
    "ProviderStore",
    "default_provider_store",
    "reset_default_provider_store",
]
