"""Model Provider 网关层：多供应商接入 + 多协议适配 + 配置管理。

对外暴露的统一入口（供 agent / web 层调用）：
- ``chat_text(client, messages, ...)``：协议无关的文本对话（兼容旧形态 client）；
- ``ProviderFactory`` / ``default_provider_factory``：适配器工厂与默认回退；
- ``ProviderStore`` / ``default_provider_store``：供应商配置持久化（脱敏）；
- ``BaseAdapter`` / 各协议适配器：协议抹平与 JSON Mode 保障。
"""

from __future__ import annotations

from typing import Any

from providers.adapters import (
    AnthropicAdapter,
    BaseAdapter,
    GeminiAdapter,
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


def chat_text(
    client: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    json_mode: bool = True,
    timeout: int | None = None,
) -> str:
    """统一对话入口，三形态透明分发：

    - Model Provider 适配器（``BaseAdapter``）走 UnifiedChatRequest 统一接口；
    - 请求感知分发代理（``DispatchingAdapter``）按请求上下文转发真实适配器；
    - 旧形态 client（测试桩 / 既有 OpenAICompatClient）走 ``chat(messages) -> str``。

    timeout：本次调用读超时覆盖（秒），None 时适配器回退网关默认
    （settings.PROVIDER_TIMEOUT）；报告综合等长文生成调用传更大预算。

    该辅助函数使 agent 层既有调用点（LLMNL2DSL / LLMPlanner 等）无需关心
    客户端形态即可透明接入 Model Provider 网关；JSON Mode 默认开启，保证
    NL -> DSL 链路的结构化输出约束在协议层得到抹平保障。
    """
    if hasattr(client, "chat_text"):
        # 适配器（BaseAdapter）与分发代理（DispatchingAdapter）均为 chat_text 形态；
        # 鸭子类型分发使测试桩等纯 chat_text 形态同样获得 timeout 透传
        return client.chat_text(messages, model=model, json_mode=json_mode, timeout=timeout)
    return client.chat(messages)


__all__ = [
    "PRESET_PROVIDERS",
    "AnthropicAdapter",
    "ApiProtocol",
    "AuthenticationError",
    "BaseAdapter",
    "DispatchingAdapter",
    "GeminiAdapter",
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
