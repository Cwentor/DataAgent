"""请求级模型切换上下文：ContextVar + 分发代理适配器。

设计目标：在不动既有调用点（LLMNL2DSL / LLMPlanner / IntentRouter 等）的
前提下，让每次 HTTP 查询请求可以携带 ``provider_id`` + ``model_id`` 动态
选择模型供应商：

- ``set_request_model`` / ``pop_request_model``：请求入口（web.service）
  绑定/清理本次请求的目标 (provider_id, model_id)；
- ``get_request_model``：分发代理据此从 ProviderFactory 解析真实适配器；
- ``dispatching_adapter()``：进程级单例的"分发代理"。default_tool_agent /
  _default_agent 等缓存位持有该代理，``chat`` 调用时按请求上下文转发；
  未绑定请求上下文时回落默认适配器（首位启用且有 Key 的供应商）。

代理实现 ``chat`` / ``chat_text``，与 BaseAdapter 的便捷入口形态兼容，
因此 ``providers.chat_text`` 的双形态分发自动生效。
"""

from __future__ import annotations

import contextvars
import threading
from typing import Any

from providers.adapters import BaseAdapter
from providers.errors import ProviderNotConfiguredError
from providers.factory import default_provider_factory

# 请求级 (provider_id, model_id) 上下文（asyncio / 线程均安全）
_request_model: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "request_model", default=None
)

__all__ = [
    "dispatching_adapter",
    "get_request_model",
    "pop_request_model",
    "reset_dispatching_adapter",
    "set_request_model",
]


def set_request_model(provider_id: str, model_id: str) -> None:
    """绑定当前请求上下文的目标模型（provider_id / model_id 允许其一为空）。"""
    _request_model.set((str(provider_id or ""), str(model_id or "")))


def pop_request_model() -> None:
    """清除当前请求上下文的目标模型（请求结束时必须调用，防上下文泄漏）。"""
    _request_model.set(None)


def get_request_model() -> tuple[str, str] | None:
    """读取当前请求上下文的目标模型；未绑定时返回 None。"""
    return _request_model.get()


class DispatchingAdapter:
    """请求感知的分发代理：按 ContextVar 转发到真实协议适配器。

    进程内单例（default_tool_agent 等缓存位持有），``chat`` 每次调用按
    请求上下文重新解析目标适配器，因此供应商配置变更 / 请求级切换即时生效。
    """

    def chat_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        json_mode: bool = True,
    ) -> str:
        """兼容旧形态便捷入口：转发到当前请求的真实适配器。"""
        return self._resolve(model).chat_text(messages, model=model, json_mode=json_mode)

    def chat(self, request: Any) -> Any:
        """兼容 UnifiedChatRequest 直调形态：转发到当前请求的真实适配器。"""
        model = getattr(request, "model", None)
        return self._resolve(str(model) if model else None).chat(request)

    def _resolve(self, model_hint: str | None = None) -> BaseAdapter:
        """解析当前应使用的真实适配器。

        优先级：请求上下文 (provider_id, model_id) -> 默认适配器。
        model_hint 仅在请求上下文未指定 model 时作为兜底（构造请求方已知模型名）。
        """
        factory = default_provider_factory()
        ctx = get_request_model()
        if ctx is not None:
            provider_id, model_id = ctx
            if not model_id:
                model_id = model_hint or ""
            try:
                return factory.resolve(provider_id or None, model_id or None)
            except ProviderNotConfiguredError:
                raise
        adapter = factory.default_adapter()
        if adapter is None:
            raise ProviderNotConfiguredError("未配置任何可用的模型供应商")
        return adapter


_dispatching: DispatchingAdapter | None = None
_dispatching_lock = threading.Lock()


def dispatching_adapter() -> DispatchingAdapter:
    """进程级分发代理单例（双检锁；持有方应长期缓存本对象）。"""
    global _dispatching
    if _dispatching is None:
        with _dispatching_lock:
            if _dispatching is None:
                _dispatching = DispatchingAdapter()
    return _dispatching


def reset_dispatching_adapter() -> None:
    """重置分发代理单例（供测试注入隔离状态）。"""
    global _dispatching
    with _dispatching_lock:
        _dispatching = None
