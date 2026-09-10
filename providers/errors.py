"""Model Provider 网关层标准错误码体系。

适配器把不同厂商的 HTTP 状态与协议异常规范化为统一错误类型：
- ``ProviderError``：基类，携带 ``code`` 标准错误码（前端据此渲染可理解提示）；
- ``AuthenticationError``：401 鉴权失败（API Key 无效/过期）；
- ``RateLimitError``：429 配额超限 / 请求过于频繁；
- ``ProviderTimeoutError``：请求超时（网络或服务端无响应）；
- ``ProtocolError``：响应结构异常 / JSON 解析失败。

``exc_to_code`` 提供"标准错误码 -> 人类可读中文文案"映射，供服务端
错误捕获与前端 Toast 使用（DoD 4：优雅呈现 401 / 429）。
"""

from __future__ import annotations


class ProviderError(RuntimeError):
    """Model Provider 网关层异常基类（携带统一错误码）。"""

    code = "provider_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """初始化错误（code 缺省取类属性，消息保留供审计）。"""
        super().__init__(message)
        self.code = code or self.code


class AuthenticationError(ProviderError):
    """鉴权失败（HTTP 401：API Key 无效 / 过期 / 权限不足）。"""

    code = "auth_failed"


class RateLimitError(ProviderError):
    """配额超限或请求过于频繁（HTTP 429）。"""

    code = "rate_limited"


class ProviderTimeoutError(ProviderError):
    """请求超时（连接建立超时 / 服务端长时间无响应）。"""

    code = "timeout"


class ProtocolError(ProviderError):
    """响应结构与协议不符（缺字段 / JSON 解析失败）。"""

    code = "protocol_error"


class ProviderNotConfiguredError(ProviderError):
    """供应商未配置或未启用（无法构造适配器）。"""

    code = "provider_not_configured"


# 标准错误码 -> 面向业务用户的可读中文文案（前端 Toast / 服务端错误透传）
ERROR_MESSAGES: dict[str, str] = {
    "auth_failed": "模型服务鉴权失败，请检查 API Key 是否正确",
    "rate_limited": "模型服务请求过于频繁或配额超限（429），请稍后重试",
    "timeout": "模型服务请求超时，请重试或检查网络",
    "protocol_error": "模型服务返回异常，无法解析响应",
    "provider_not_configured": "未找到可用的模型供应商，请在设置中配置",
    "provider_error": "模型服务调用失败，请稍后重试",
}


def error_message(exc: ProviderError) -> str:
    """把供应商异常映射为可读中文文案（未知码回退通用提示）。"""
    return ERROR_MESSAGES.get(exc.code, ERROR_MESSAGES["provider_error"])


__all__ = [
    "ERROR_MESSAGES",
    "AuthenticationError",
    "ProtocolError",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderTimeoutError",
    "RateLimitError",
    "error_message",
]
