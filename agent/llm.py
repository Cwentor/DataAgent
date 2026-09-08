"""OpenAI 兼容 LLM 客户端（纯标准库实现，无额外依赖）。

仅依赖 urllib 完成一次 Chat Completions 调用，支持任意 OpenAI 兼容端点
（OpenAI / DeepSeek / Moonshot / vLLM 等）。未配置 API Key 时不会走到这里，
Agent 会自动回退到确定性启发式实现。

该模块同时承担 Model Provider 网关层的兼容底座：``resolve_default_client``
从 ProviderFactory 解析默认适配器，供 pipeline / tool_agent / intent_router
统一获取"当前启用的 LLM 客户端"（无可用配置返回 None）。
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    """调用 LLM 失败（网络 / 鉴权 / 服务端错误）。"""


class OpenAICompatClient:
    """OpenAI 兼容 Chat Completions 客户端（零 SDK 依赖，纯标准库实现）。

    保留本类作为向后兼容的轻量客户端：网关未启用 / 测试桩场景仍可使用；
    生产链路已切换为由 ``providers.chat_text`` 统一抹平的适配器客户端。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        timeout: int = 60,
    ) -> None:
        """初始化连接参数（base_url / api_key / model / temperature / timeout）。"""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]]) -> str:
        """发送一轮对话，返回 assistant 的文本内容。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise LLMError(f"LLM 网络/服务错误: {exc}") from exc
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LLMError(f"LLM 响应解析失败: {exc}") from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM 响应缺少 choices[0].message.content: {exc}") from exc


def resolve_default_client() -> Any | None:
    """从 Model Provider 网关解析"当前启用的默认 LLM 客户端"。

    返回请求感知的分发代理（``providers.dispatching_adapter``）：持有方
    （pipeline / tool_agent / intent_router）可长期缓存，``chat`` 调用时按
    请求上下文（provider_id + model_id）动态转发到真实协议适配器；未绑定
    请求上下文时回落默认适配器。

    无任何可用供应商 / 环境变量配置时返回 None（上层回退到确定性启发式实现）。
    """
    try:
        from providers.context import dispatching_adapter
        from providers.factory import default_provider_factory

        if not default_provider_factory().has_provider():
            return None
        return dispatching_adapter()
    except Exception:
        return None
