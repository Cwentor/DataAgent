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
) -> str:
    """统一对话入口：适配器走新接口（UnifiedChatRequest），旧形态 client
    （测试桩 / 既有 OpenAICompatClient）走 ``chat(messages) -> str``。

    该辅助函数使 agent 层既有调用点（LLMNL2DSL / LLMPlanner 等）无需关心
    客户端形态即可透明接入 Model Provider 网关；JSON Mode 默认开启，保证
    NL -> DSL 链路的结构化输出约束在协议层得到抹平保障。
    """
    if isinstance(client, BaseAdapter):
        return client.chat_text(messages, model=model, json_mode=json_mode)
    return client.chat(messages)


__all__ = [
    "PRESET_PROVIDERS",
    "AnthropicAdapter",
    "ApiProtocol",
    "AuthenticationError",
    "BaseAdapter",
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
    "error_message",
    "extract_json_object",
    "mask_api_key",
    "reset_default_provider_store",
    "reset_provider_factory",
]
