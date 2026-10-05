"""Model Provider 网关层：多供应商接入 + 多协议适配 + 配置管理。

对外暴露的统一入口（供 agent / web 层调用）：
- ``chat_text(client, messages, ...)``：协议无关的文本对话（兼容旧形态 client）；
- ``ProviderFactory`` / ``default_provider_factory``：适配器工厂与默认回退；
- ``ProviderStore`` / ``default_provider_store``：供应商配置持久化（脱敏）；
- ``BaseAdapter`` / 各协议适配器：协议抹平与 JSON Mode 保障。
"""

from __future__ import annotations

import time
from typing import Any

from providers.adapters import (
    AnthropicAdapter,
    BaseAdapter,
    OpenAIChatAdapter,
    OpenAIResponsesAdapter,
    build_adapter,
    extract_json_object,
)
from providers.context import (
    DispatchingAdapter,
    dispatching_adapter,
    get_request_model,
    pop_request_model,
    reset_dispatching_adapter,
    set_request_model,
)
from providers.errors import (
    AuthenticationError,
    ProtocolError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    RateLimitError,
    error_message,
)
from providers.factory import (
    ProviderFactory,
    default_provider_factory,
    reset_provider_factory,
)
from providers.models import (
    ApiProtocol,
    ModelItem,
    ProviderConfig,
    TestConnectionResult,
    UnifiedChatRequest,
    UnifiedChatResponse,
    mask_api_key,
)
from providers.store import (
    PRESET_PROVIDERS,
    ProviderStore,
    default_provider_store,
    reset_default_provider_store,
)


def _chat_dispatch(
    client: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None,
    json_mode: bool,
    timeout: int | None,
) -> str:
    """单次分发（不重试）：三形态透明分发的原有逻辑。"""
    if hasattr(client, "chat_text"):
        # 适配器（BaseAdapter）与分发代理（DispatchingAdapter）均为 chat_text 形态；
        # 鸭子类型分发使测试桩等纯 chat_text 形态同样获得 timeout 透传
        return client.chat_text(messages, model=model, json_mode=json_mode, timeout=timeout)
    return client.chat(messages)


def chat_text(
    client: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    json_mode: bool = True,
    timeout: int | None = None,
) -> str:
    """统一对话入口，三形态透明分发（429 限流自动退避重试）：

    - Model Provider 适配器（``BaseAdapter``）走 UnifiedChatRequest 统一接口；
    - 请求感知分发代理（``DispatchingAdapter``）按请求上下文转发真实适配器；
    - 旧形态 client（测试桩 / 既有 OpenAICompatClient）走 ``chat(messages) -> str``。

    timeout：本次调用读超时覆盖（秒），None 时适配器回退网关默认
    （settings.PROVIDER_TIMEOUT）；报告综合等长文生成调用传更大预算。

    429 限流（RateLimitError）按 LLM_MAX_RETRIES（封顶 2，短退避 1s/3s）
    自动重试——共享中转的间歇性限流不应直接打穿为降级；其他异常不重试。
    （读超时刻意不在此层重试：无法区分"握手挂起"与"mid-stream 超时"，
    后者重发等于整段重复生成/重复计费——防重复计费铁律优先，见
    adapters._stream_fallback_eligible 同源约束。）
    """
    from config import settings

    attempts = max(0, min(int(settings.LLM_MAX_RETRIES), 2))
    delays = (1.0, 3.0)
    for attempt in range(attempts + 1):
        try:
            return _chat_dispatch(
                client, messages, model=model, json_mode=json_mode, timeout=timeout
            )
        except RateLimitError:
            if attempt >= attempts:
                raise
            time.sleep(delays[min(attempt, len(delays) - 1)])
    raise RateLimitError("重试循环异常退出（不可达防御分支）")


__all__ = [
    "PRESET_PROVIDERS",
    "AnthropicAdapter",
    "ApiProtocol",
    "AuthenticationError",
    "BaseAdapter",
    "DispatchingAdapter",
    "ModelItem",
    "OpenAIChatAdapter",
    "OpenAIResponsesAdapter",
    "ProtocolError",
    "ProviderConfig",
    "ProviderError",
    "ProviderFactory",
    "ProviderNotConfiguredError",
    "ProviderStore",
    "ProviderTimeoutError",
    "RateLimitError",
    "TestConnectionResult",
    "UnifiedChatRequest",
    "UnifiedChatResponse",
    "build_adapter",
    "chat_text",
    "default_provider_factory",
    "default_provider_store",
    "dispatching_adapter",
    "error_message",
    "extract_json_object",
    "get_request_model",
    "mask_api_key",
    "pop_request_model",
    "reset_default_provider_store",
    "reset_dispatching_adapter",
    "reset_provider_factory",
    "set_request_model",
]
