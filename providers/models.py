"""Model Provider 网关层数据契约（Single Source of Truth）。

定义供应商配置、统一请求/响应与协议枚举。所有模型沿用 pydantic v2 严格契约
（``extra="forbid"``），与 semantic/dsl_schema 的契约风格保持一致：
- ``ProviderConfig``：持久化的供应商配置（含 API Key，服务端存储，展示脱敏）；
- ``UnifiedChatRequest``：适配器统一入参（协议无关）；
- ``UnifiedChatResponse``：适配器统一出参（含 JSON 解析兜底结果）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# 协议枚举
# --------------------------------------------------------------------------- #
class ApiProtocol(StrEnum):
    """供应商对外暴露的 API 协议类型。"""

    OPENAI_CHAT = "openai_chat"  # POST {baseUrl}/chat/completions
    OPENAI_RESPONSES = "openai_responses"  # POST {baseUrl}/responses
    ANTHROPIC = "anthropic"  # POST {baseUrl}/v1/messages
    GEMINI = "gemini"  # POST {baseUrl}/v1beta/models/{model}:generateContent


# --------------------------------------------------------------------------- #
# 模型与供应商配置
# --------------------------------------------------------------------------- #
class ModelItem(BaseModel):
    """单个模型条目（供应商下的模型清单项）。"""

    model_config = {"extra": "forbid"}

    id: str  # 模型标识，如 "glm-5.3-flash"
    name: str = ""  # 展示名称；缺省取 id
    context_window: int | None = None  # 上下文窗口大小（Token）
    capabilities: list[str] = Field(default_factory=list)  # vision / function_calling / json_schema


class ProviderConfig(BaseModel):
    """供应商配置（持久化到服务端 JSON 文件；API Key 展示/传输一律脱敏）。"""

    model_config = {"extra": "forbid"}

    id: str
    name: str
    is_preset: bool = False  # 预置供应商不可删除（可禁用）
    enabled: bool = True
    base_url: str
    api_key: str = ""
    protocol: ApiProtocol = ApiProtocol.OPENAI_CHAT
    models: list[ModelItem] = Field(default_factory=list)
    custom_headers: dict[str, str] = Field(default_factory=dict)
    created_at: int = 0  # epoch 秒
    updated_at: int = 0  # epoch 秒


# --------------------------------------------------------------------------- #
# 统一请求 / 响应（协议抹平契约）
# --------------------------------------------------------------------------- #
class UnifiedMessage(BaseModel):
    """统一消息条目（适配器负责映射到各协议的消息结构）。"""

    model_config = {"extra": "forbid"}

    role: str  # system / user / assistant
    content: str


class ResponseFormat(BaseModel):
    """结构化输出请求：type 为 json_object 时启用 JSON Mode（协议层抹平）。"""

    model_config = {"extra": "forbid"}

    type: str = "text"  # text / json_object
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")


class UnifiedChatRequest(BaseModel):
    """适配器统一入参（协议无关的对话请求）。"""

    model_config = {"extra": "forbid"}

    messages: list[UnifiedMessage]
    model: str
    temperature: float | None = None
    response_format: ResponseFormat | None = None


class Usage(BaseModel):
    """Token 用量（协议字段差异在适配器内抹平）。"""

    model_config = {"extra": "forbid"}

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class UnifiedChatResponse(BaseModel):
    """适配器统一出参：文本 + 可选 JSON 解析结果 + 用量。"""

    model_config = {"extra": "forbid"}

    content: str
    parsed_json: dict[str, Any] | None = None
    usage: Usage | None = None


class TestConnectionResult(BaseModel):
    """连通性探测结果（成功 + 延时 + 可选错误信息）。"""

    model_config = {"extra": "forbid"}

    success: bool
    latency_ms: float = 0.0
    error: str | None = None


# --------------------------------------------------------------------------- #
# API Key 脱敏（展示与传输）
# --------------------------------------------------------------------------- #
def mask_api_key(key: str) -> str:
    """对 API Key 做可逆展示脱敏：保留首 4 / 末 4 字符，中间星号。

    脱敏后的串以 ``*`` 结尾标记，store 更新时若提交值等于脱敏串则视为
    "未修改"，保留服务端原 Key（禁止用脱敏串覆盖明文）。
    """
    if not key:
        return ""
    if key.endswith("**"):
        return key  # 已是脱敏串
    if len(key) <= 8:
        return "*" * len(key) + "**"
    return key[:4] + "*" * (len(key) - 8) + key[-4:] + "**"


def is_masked_key(key: str) -> bool:
    """判断传入的 Key 是否为脱敏展示串（服务端更新时据此保留原值）。"""
    return bool(key) and key.endswith("**")


__all__ = [
    "ApiProtocol",
    "ModelItem",
    "ProviderConfig",
    "ResponseFormat",
    "TestConnectionResult",
    "UnifiedChatRequest",
    "UnifiedChatResponse",
    "UnifiedMessage",
    "Usage",
    "is_masked_key",
    "mask_api_key",
]
