"""Model Provider 适配器工厂：按 (provider_id, model_id) 分派并缓存适配器。

- ``get_adapter(provider_id, model_id)``：显式指定供应商与模型（请求级切换）；
- ``default_adapter()``：未指定时优先取第一个启用供应商的默认模型，
  否则回退环境变量（``LLM_*``，保持既有部署语义）；
- ``resolve(provider_id, model_id)``：对外统一入口，provider_id 为空时自动
  落到默认适配器，找不到可用供应商抛 ``ProviderNotConfiguredError``；
- 供应商配置变更后调用 ``invalidate`` 清缓存（web 写操作触发）。
"""

from __future__ import annotations

import threading
from typing import Any

from providers.adapters import BaseAdapter, build_adapter
from providers.errors import ProviderNotConfiguredError
from providers.models import ModelItem, ProviderConfig
from providers.store import ProviderStore, default_provider_store


def _env_provider_config() -> ProviderConfig | None:
    """回退：从环境变量（LLM_*）构造一个只读供应商配置（无 LLM_API_KEY 时返回 None）。"""
    from config import settings

    if not settings.LLM_API_KEY:
        return None
    return ProviderConfig(
        id="env",
        name="Environment",
        is_preset=True,
        enabled=True,
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        protocol="openai_chat",
        models=[ModelItem(id=settings.LLM_MODEL, name=settings.LLM_MODEL)],
    )


class ProviderFactory:
    """适配器工厂：构造 + 缓存 + 默认回退（线程安全）。"""

    def __init__(self, store: ProviderStore | None = None) -> None:
        """绑定供应商存储（缺省进程级默认）。"""
        self._store = store or default_provider_store()
        self._cache: dict[tuple[str, str], BaseAdapter] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 构造
    # ------------------------------------------------------------------ #
    def get_adapter(self, provider_id: str, model_id: str) -> BaseAdapter:
        """按 (provider_id, model_id) 获取适配器（进程内缓存复用）。"""
        key = (provider_id, model_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        provider = self._store.get_provider(provider_id)
        if provider is None or not provider.enabled:
            raise ProviderNotConfiguredError(f"供应商不存在或未启用: {provider_id}")
        adapter = build_adapter(provider, model_id)
        with self._lock:
            # 双检锁：并发首调只构造一次
            if key not in self._cache:
                self._cache[key] = adapter
        return adapter

    def default_adapter(self) -> BaseAdapter | None:
        """未指定供应商时的默认适配器：首个启用供应商 -> 环境变量回退 -> None。"""
        for provider in self._store.list_providers():
            if provider.enabled:
                model_id = provider.models[0].id if provider.models else ""
                if model_id:
                    return self.get_adapter(provider.id, model_id)
        env = _env_provider_config()
        if env is not None:
            return self.get_adapter(env.id, env.models[0].id)
        return None

    def resolve(self, provider_id: str | None, model_id: str | None) -> BaseAdapter:
        """统一入口：显式指定优先，缺省落默认；均不可用时抛配置错误。"""
        if provider_id or model_id:
            pid = provider_id or ""
            mid = model_id or ""
            provider = self._store.get_provider(pid)
            if provider is None:
                raise ProviderNotConfiguredError(f"供应商不存在: {provider_id}")
            if not mid and provider.models:
                mid = provider.models[0].id
            return self.get_adapter(pid, mid)
        adapter = self.default_adapter()
        if adapter is None:
            raise ProviderNotConfiguredError("未配置任何可用的模型供应商")
        return adapter

    def has_provider(self) -> bool:
        """是否存在可用供应商（启用且有模型），供上层决定 LLM/确定性模式。"""
        for provider in self._store.list_providers():
            if provider.enabled and provider.models:
                return True
        return _env_provider_config() is not None

    # ------------------------------------------------------------------ #
    # 换肤与切换
    # ------------------------------------------------------------------ #
    def invalidate(self, provider_id: str | None = None) -> None:
        """清除缓存：provider_id 为 None 时全量清空（供应商配置变更后调用）。"""
        with self._lock:
            if provider_id is None:
                self._cache.clear()
                return
            for key in [k for k in self._cache if k[0] == provider_id]:
                del self._cache[key]

    def list_choices(self) -> list[dict[str, Any]]:
        """返回启用供应商的模型候选（前端模型切换器数据源）。"""
        choices: list[dict[str, Any]] = []
        for provider in self._store.list_providers():
            if not provider.enabled or not provider.models:
                continue
            choices.append(
                {
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "protocol": provider.protocol.value,
                    "models": [m.model_dump(mode="json") for m in provider.models],
                }
            )
        if not choices:
            env = _env_provider_config()
            if env is not None:
                choices.append(
                    {
                        "provider_id": env.id,
                        "provider_name": env.name,
                        "protocol": "openai_chat",
                        "models": [m.model_dump(mode="json") for m in env.models],
                    }
                )
        return choices


# --------------------------------------------------------------------------- #
# 进程级单例
# --------------------------------------------------------------------------- #
_default_factory: ProviderFactory | None = None
_factory_lock = threading.Lock()


def default_provider_factory() -> ProviderFactory:
    """进程内复用的默认适配器工厂（双检锁）。"""
    global _default_factory
    if _default_factory is None:
        with _factory_lock:
            if _default_factory is None:
                _default_factory = ProviderFactory()
    return _default_factory


def reset_provider_factory(factory: ProviderFactory | None = None) -> None:
    """重置进程级默认工厂（供测试注入桩实例）。"""
    global _default_factory
    _default_factory = factory


__all__ = [
    "ProviderFactory",
    "default_provider_factory",
    "reset_provider_factory",
]
