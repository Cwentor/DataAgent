"""Model Provider 配置持久化：JSON 文件 CRUD + API Key 脱敏 + 预置供应商种子。

设计说明：
- 存储于服务端 JSON 文件（``config/providers.json``），线程锁保护并发读写；
- API Key 明文仅存在于服务端文件（与 .env 同级信任边界）；网络传输与前端
  展示一律走脱敏视图（``public_view``）；
- 更新时若提交的 Key 为脱敏串或空串，保留服务端原 Key（禁止脱敏串覆盖明文）；
- 预置供应商（智谱 / OpenAI / Anthropic / Gemini）首次加载自动写入，
  ``is_preset=True`` 不可删除（可禁用 / 编辑）。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from providers.models import (
    ApiProtocol,
    ModelItem,
    ProviderConfig,
    is_masked_key,
    mask_api_key,
)

PRESET_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "zhipu",
        "name": "智谱",
        "is_preset": True,
        "enabled": True,
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "protocol": ApiProtocol.OPENAI_CHAT,
        "models": [
            {
                "id": "glm-5.3-flash",
                "context_window": 128000,
                "capabilities": ["json_schema", "function_calling"],
            },
            {
                "id": "glm-4.6-flash",
                "context_window": 128000,
                "capabilities": ["json_schema", "function_calling"],
            },
            {
                "id": "glm-4.5-flash",
                "context_window": 128000,
                "capabilities": ["json_schema", "function_calling"],
            },
        ],
    },
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
    {
        "id": "gemini",
        "name": "Google Gemini",
        "is_preset": True,
        "enabled": False,
        "base_url": "https://generativelanguage.googleapis.com",
        "protocol": ApiProtocol.GEMINI,
        "models": [
            {
                "id": "gemini-1.5-pro",
                "context_window": 1000000,
                "capabilities": ["vision", "json_schema"],
            },
            {
                "id": "gemini-1.5-flash",
                "context_window": 1000000,
                "capabilities": ["vision", "json_schema"],
            },
        ],
    },
]


def _new_id() -> str:
    """生成供应商唯一 ID（短随机 hex，避免与预置 id 冲突）。"""
    return "p_" + uuid.uuid4().hex[:10]


class ProviderStore:
    """供应商配置持久化存储（JSON 文件；线程安全）。"""

    def __init__(self, path: str | Path | None = None) -> None:
        """打开（或初始化）供应商配置文件；路径缺省取 config/providers.json。"""
        if path is None:
            from config import settings

            path = settings.PROVIDERS_FILE
        self.path = Path(path)
        self._lock = threading.RLock()
        self._providers: dict[str, ProviderConfig] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # 内部读写（明文）
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        """从文件加载；文件不存在时写入预置种子。"""
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._providers = {
                    item["id"]: ProviderConfig.model_validate(item)
                    for item in raw
                    if isinstance(item, dict) and item.get("id")
                }
                return
            except (json.JSONDecodeError, OSError, ValueError):
                # 损坏文件不吞错：备份后重建（保证配置表永远可用）
                backup = self.path.with_suffix(self.path.suffix + ".bak")
                try:
                    backup.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    pass
        self._providers = {p["id"]: ProviderConfig.model_validate(p) for p in PRESET_PROVIDERS}
        self._save()

    def _save(self) -> None:
        """全量写回 JSON 文件（原子替换，避免半写残留）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        items = [p.model_dump(mode="json") for p in self._providers.values()]
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
        """按 ID 取供应商（含明文 Key，仅服务端内部使用）。"""
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
        """按 ID 更新供应商；预置 id / 不存在时返回 None。api_key 为脱敏串或空则保留原值。"""
        with self._lock:
            current = self._providers.get(provider_id)
            if current is None:
                return None
            payload = dict(data)
            payload.pop("id", None)  # ID 不可变更
            payload.pop("is_preset", None)  # 预置标记不可变更
            # Key 脱敏保护：提交值为脱敏串 / 空串时保留服务端原 Key
            submitted_key = payload.get("api_key")
            if submitted_key is None or submitted_key == "" or is_masked_key(str(submitted_key)):
                payload.pop("api_key", None)
            merged = current.model_copy(update=payload, deep=True)
            if "models" in payload:
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
    # 对外脱敏视图
    # ------------------------------------------------------------------ #
    def public_view(self, provider: ProviderConfig) -> dict[str, Any]:
        """序列化为前端可安全展示的字典（API Key 一律脱敏）。"""
        view = provider.model_dump(mode="json")
        view["api_key"] = mask_api_key(str(provider.api_key or ""))
        return view

    def public_list(self) -> list[dict[str, Any]]:
        """返回脱敏后的供应商列表（前端设置面板 / 模型切换器数据源）。"""
        return [self.public_view(p) for p in self.list_providers()]


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
    "ProviderStore",
    "default_provider_store",
    "reset_default_provider_store",
]
