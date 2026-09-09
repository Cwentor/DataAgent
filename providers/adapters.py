"""Model Provider 网关层：统一 LLM 抽象基类与多协议适配器。

实现协议抹平（Protocol Adapters）：
- ``BaseAdapter``：统一抽象（``chat`` / ``test_connection`` / ``chat_text``）；
- ``OpenAIChatAdapter``：POST {baseUrl}/chat/completions（标准 OpenAI 兼容）；
- ``OpenAIResponsesAdapter``：POST {baseUrl}/responses（新版 Responses 规范）；
- ``AnthropicAdapter``：POST {baseUrl}/v1/messages（System Prompt 提取至顶级字段）；
- ``GeminiAdapter``：POST {baseUrl}/v1beta/models/{model}:generateContent。

结构化输出保障（JSON Mode）：
- 请求侧：支持原生 JSON Schema 的协议透传原生参数（openai_chat 的
  ``response_format`` / responses 的 ``text.format`` / gemini 的
  ``responseMimeType``），anthropic 与所有兜底路径在 System Prompt 注入
  Strict JSON 约束；
- 响应侧：统一正则安全清洗提取 JSON（``extract_json_object``），对带
  ```json 围栏 / 前后杂文本的响应一律可用；
- 降级：透传原生结构化参数被服务端拒绝（400 且模型不支持）时，自动去掉
  原生参数重试一次（仅保留 Prompt 约束），保证自定义中转站也能产出合法 JSON。

错误码映射（DoD 4）：HTTP 401 -> AuthenticationError，429 -> RateLimitError，
超时 -> ProviderTimeoutError，其余 -> ProviderError / ProtocolError。
"""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.parse
from abc import ABC, abstractmethod
from typing import Any

from providers.errors import (
    AuthenticationError,
    ProtocolError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
)
from providers.models import (
    ApiProtocol,
    ProviderConfig,
    TestConnectionResult,
    UnifiedChatRequest,
    UnifiedChatResponse,
    Usage,
)

BT = chr(96)  # backtick
FENCE = BT * 3
_JSON_FENCE = re.compile(FENCE + r"(?:json)?\s*(.*?)\s*" + FENCE, re.DOTALL)

# System Prompt 注入的 Strict JSON 约束（JSON Mode 兜底，协议无关）
_STRICT_JSON_PROMPT = (
    "You must reply with ONLY a valid JSON object, no markdown fences, "
    "no explanatory text before or after. "
    "If you cannot produce a valid JSON object, reply with "
    '{"error": "failed"}'
)

# 原生 JSON 参数被服务端拒绝时的错误特征（模型不支持 response_format 等）
_UNSUPPORTED_JSON_HINTS = (
    "response_format",
    "response format",
    "json_object",
    "json_schema",
    "output_format",
    "not supported",
    "unsupported",
    "invalid parameter",
)


def extract_json_object(text: str) -> dict[str, Any]:
    """从 LLM 文本中安全提取 JSON 对象（容忍代码块围栏与前后杂文本）。

    优先直接解析；失败则依次尝试剥离 Markdown 围栏、截取首尾花括号。
    提取失败抛 ProtocolError（由上层决定是否降级重试）。
    """
    if not isinstance(text, str):
        raise ProtocolError(f"LLM 响应非文本: {type(text).__name__}")
    stripped = text.strip()
    if stripped:
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                return obj
            raise ValueError("顶层不是 JSON 对象")
        except json.JSONDecodeError:
            pass
    fence = _JSON_FENCE.search(stripped)
    if fence:
        return json.loads(fence.group(1))
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return json.loads(stripped[start : end + 1])
    raise ProtocolError("响应中未找到合法 JSON 对象")


def _inject_strict_json(system_prompt: str | None) -> str:
    """在 System Prompt 追加 Strict JSON 约束（JSON Mode 兜底，幂等）。"""
    if system_prompt and _STRICT_JSON_PROMPT in system_prompt:
        return system_prompt
    if system_prompt:
        return system_prompt + "\n\n" + _STRICT_JSON_PROMPT
    return _STRICT_JSON_PROMPT


# --------------------------------------------------------------------------- #
# 底层 HTTP 发送（纯标准库 urllib，零 SDK 依赖）
# --------------------------------------------------------------------------- #
_ALLOWED_URL_SCHEMES = ("http", "https")


def _validate_outbound_url(url: str) -> urllib.parse.ParseResult:
    """出站 URL 安全校验：协议白名单 http/https + 主机名非空（SSRF 边界校验）。

    目标主机来自管理员配置的供应商 base_url（企业内网网关属合法场景），
    故不做私网段封禁，仅做协议与主机名合法性边界校验。返回解析结果供
    http.client 显式建连（不自动跟随重定向，符合 SSRF 重定向限制建议）。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ProviderError(f"供应商 base_url 协议必须为 http/https: {url}")
    if not parsed.hostname:
        raise ProviderError(f"供应商 base_url 缺少主机名: {url}")
    return parsed


def _http_post(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    api_key: str | None = None,
) -> tuple[int, dict[str, str], str]:
    """发送一次 JSON POST 请求，返回 (status, response_headers, body)。

    使用 http.client 显式建连（替代 urlopen）：目标 host/port 经白名单校验，
    不跟随重定向。统一把网络错误 / HTTP 状态映射为供应商标准错误码：
    - 401 -> AuthenticationError；429 -> RateLimitError；
    - HTTP 超时 -> ProviderTimeoutError；
    - 其余 HTTP 状态 -> ProviderError（携带状态码）。
    """
    body = json.dumps(payload).encode("utf-8")
    merged_headers = {"Content-Type": "application/json", **headers}
    if api_key:
        merged_headers["Authorization"] = f"Bearer {api_key}"
    parsed = _validate_outbound_url(url)
    conn_cls = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    conn = conn_cls(parsed.hostname, parsed.port, timeout=timeout)
    try:
        conn.request("POST", parsed.path or "/", body=body, headers=merged_headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
    except TimeoutError as exc:  # socket.timeout（含连接/读超时）
        raise ProviderTimeoutError(f"请求超时: {exc}") from exc
    except (http.client.HTTPException, OSError) as exc:
        raise ProviderError(f"网络请求失败: {exc}", code="provider_error") from exc
    finally:
        conn.close()
    if resp.status == 401:
        raise AuthenticationError(f"鉴权失败（HTTP 401）: {_brief(raw)}")
    if resp.status == 429:
        raise RateLimitError(f"配额超限或请求过于频繁（HTTP 429）: {_brief(raw)}")
    if resp.status >= 400:
        raise ProviderError(
            f"模型服务返回 HTTP {resp.status}: {_brief(raw)}", code="provider_error"
        )
    return resp.status, dict(resp.getheaders()), raw


def _brief(raw: str, limit: int = 300) -> str:
    """压缩错误响应体为单行摘要（防审计日志膨胀 / 前端泄露敏感信息）。"""
    text = raw.replace("\n", " ").strip()
    return text[:limit]


# --------------------------------------------------------------------------- #
# 统一抽象基类
# --------------------------------------------------------------------------- #
class BaseAdapter(ABC):
    """统一 LLM 适配器抽象：协议无关的 chat / 连通性探测。"""

    def __init__(self, provider: ProviderConfig, model_id: str) -> None:
        """绑定供应商配置与目标模型（模型不存在时仍允许调用，交由服务端判定）。"""
        self.provider = provider
        self.model_id = model_id or (provider.models[0].id if provider.models else "")

    # -- 统一能力 -------------------------------------------------------- #
    @abstractmethod
    def chat(self, request: UnifiedChatRequest) -> UnifiedChatResponse:
        """发送一轮对话，返回统一响应（含 JSON 兜底解析）。"""

    @abstractmethod
    def test_connection(self) -> TestConnectionResult:
        """发送极小的 ping 文本验证连通性与延时。"""

    # -- 便捷方法（兼容旧形态 client.chat(messages) -> str 的调用方）---- #
    def chat_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        json_mode: bool = True,
    ) -> str:
        """旧形态便捷入口：messages 列表 -> 文本；默认启用 JSON Mode。

        供 agent 层既有调用点（LLMNL2DSL / LLMPlanner 等）透明切换，
        无需改动其内部消息构造逻辑。
        """
        resp = self.chat(
            UnifiedChatRequest(
                messages=[{"role": m["role"], "content": m["content"]} for m in messages],
                model=model or self.model_id,
                response_format={"type": "json_object"} if json_mode else None,
            )
        )
        return resp.content

    # -- 内部工具 -------------------------------------------------------- #
    def _split_messages(
        self, request: UnifiedChatRequest
    ) -> tuple[list[dict[str, str]], str | None]:
        """把统一消息拆为 (可放入 messages 的消息列表, 独立 system_prompt)。

        Anthropic 协议要求 System 独立于 messages，其余协议可把 system 保留
        在 messages 内；本方法统一返回，由各适配器决定组装方式。
        """
        system_parts: list[str] = []
        rest: list[dict[str, str]] = []
        for m in request.messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                rest.append({"role": m.role, "content": m.content})
        system_prompt = "\n".join(system_parts) if system_parts else None
        return rest, system_prompt

    def _build_headers(self) -> dict[str, str]:
        """构造基础请求头（供应商自定义头 + 鉴权头由子类补充）。"""
        return dict(self.provider.custom_headers or {})


# --------------------------------------------------------------------------- #
# OpenAI Chat Completions 适配器
# --------------------------------------------------------------------------- #
class OpenAIChatAdapter(BaseAdapter):
    """OpenAI Chat Completions 协议（``/chat/completions``，兼容主流中转站）。

    JSON Mode：request.response_format.type == "json_object" 时透传
    ``response_format`` 原生参数，并同时在 System Prompt 注入 Strict JSON
    约束（兜底）；原生参数被服务端拒绝（400）时自动降级重试一次。
    """

    def chat(self, request: UnifiedChatRequest) -> UnifiedChatResponse:
        """发送 Chat Completions 请求并解析 choices[0].message.content。"""
        messages, system_prompt = self._split_messages(request)
        want_json = (
            request.response_format is not None and request.response_format.type == "json_object"
        )
        if want_json and system_prompt is not None:
            system_prompt = _inject_strict_json(system_prompt)
        if system_prompt is not None:
            messages = [{"role": "system", "content": system_prompt}, *messages]

        base_payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature if request.temperature is not None else 0.0,
        }
        status, _, raw = self._post(base_payload, want_json=want_json)
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            return self._build_response(content, usage, json_mode=want_json)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"Chat Completions 响应解析失败（HTTP {status}）: {exc}") from exc

    def _post(
        self, payload: dict[str, Any], *, want_json: bool, attempt_native: bool = True
    ) -> tuple[int, dict[str, str], str]:
        """发送请求；JSON 原生参数被拒（400）时去掉后重试一次（仅留 Prompt 约束）。"""
        body: dict[str, Any] = payload
        if want_json and attempt_native:
            body = {**payload, "response_format": {"type": "json_object"}}
        url = f"{self.provider.base_url.rstrip('/')}/chat/completions"
        headers = {**self._build_headers()}
        if self.provider.api_key:
            headers["Authorization"] = f"Bearer {self.provider.api_key}"
        try:
            return _http_post(url, payload=body, headers=headers, timeout=60, api_key=None)
        except ProviderError as exc:
            if (
                want_json
                and attempt_native
                and isinstance(exc, ProviderError)
                and exc.code == "provider_error"
                and any(h in str(exc).lower() for h in _UNSUPPORTED_JSON_HINTS)
            ):
                # 模型/中转不支持 response_format -> 降级：仅保留 Prompt 约束重试
                return self._post(payload, want_json=want_json, attempt_native=False)
            raise

    def test_connection(self) -> TestConnectionResult:
        """发送极小 ping 文本，验证 HTTP 200 与延时。"""
        started = time.perf_counter()
        try:
            payload = {
                "model": self.model_id,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
            status, _, _ = self._post(payload, want_json=False)
            return TestConnectionResult(
                success=status == 200, latency_ms=round((time.perf_counter() - started) * 1000.0, 1)
            )
        except ProviderError as exc:
            return TestConnectionResult(
                success=False,
                latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
                error=str(exc),
            )

    @staticmethod
    def _build_response(
        content: Any, usage: dict[str, Any] | None, *, json_mode: bool
    ) -> UnifiedChatResponse:
        """构造统一响应；content 非字符串时按协议容错（转 JSON 字符串）。"""
        if content is None:
            raise ProtocolError("Chat Completions 响应 content 为空")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        parsed = None
        if json_mode:
            try:
                parsed = extract_json_object(text)
            except ProtocolError:
                parsed = None  # 透传原生参数失败时不阻断主链路（上层自会校验重试）
        return UnifiedChatResponse(
            content=text,
            parsed_json=parsed,
            usage=(
                Usage(
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                )
                if usage
                else None
            ),
        )


# --------------------------------------------------------------------------- #
# OpenAI Responses API 适配器
# --------------------------------------------------------------------------- #
class OpenAIResponsesAdapter(BaseAdapter):
    """OpenAI Responses API（``/responses`` 新版规范：input / response_format）。

    JSON Mode：在 ``text.format`` 注入 ``{"type": "json_object"}``；被服务端
    拒绝时降级为仅 Prompt 约束重试。
    """

    def chat(self, request: UnifiedChatRequest) -> UnifiedChatResponse:
        """按 /responses 规范组装请求并解析 output 文本。"""
        messages, system_prompt = self._split_messages(request)
        want_json = (
            request.response_format is not None and request.response_format.type == "json_object"
        )
        if want_json and system_prompt is not None:
            system_prompt = _inject_strict_json(system_prompt)
        input_blocks: list[dict[str, Any]] = []
        if system_prompt is not None:
            input_blocks.append({"role": "system", "content": system_prompt})
        input_blocks.extend({"role": m["role"], "content": m["content"]} for m in messages)

        payload: dict[str, Any] = {
            "model": request.model,
            "input": input_blocks,
            "temperature": request.temperature if request.temperature is not None else 0.0,
        }
        status, _, raw = self._post(payload, want_json=want_json)
        try:
            data = json.loads(raw)
            parts: list[str] = []
            for item in data.get("output", []):
                content = item.get("content") or []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        parts.append(block.get("text", ""))
            content = "".join(parts)
            usage = data.get("usage") or {}
            return self._build_response(content, usage, json_mode=want_json)
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"Responses API 响应解析失败（HTTP {status}）: {exc}") from exc

    def _post(
        self, payload: dict[str, Any], *, want_json: bool, attempt_native: bool = True
    ) -> tuple[int, dict[str, str], str]:
        """发送请求；原生 JSON 参数被拒（400）时降级重试（同 OpenAIChatAdapter）。"""
        body: dict[str, Any] = payload
        if want_json and attempt_native:
            body = {**payload, "text": {"format": {"type": "json_object"}}}
        url = f"{self.provider.base_url.rstrip('/')}/responses"
        headers = {**self._build_headers()}
        if self.provider.api_key:
            headers["Authorization"] = f"Bearer {self.provider.api_key}"
        try:
            return _http_post(url, payload=body, headers=headers, timeout=60, api_key=None)
        except ProviderError as exc:
            if (
                want_json
                and attempt_native
                and isinstance(exc, ProviderError)
                and exc.code == "provider_error"
                and any(h in str(exc).lower() for h in _UNSUPPORTED_JSON_HINTS)
            ):
                return self._post(payload, want_json=want_json, attempt_native=False)
            raise

    def test_connection(self) -> TestConnectionResult:
        """发送极小 ping 文本，验证 HTTP 200 与延时。"""
        started = time.perf_counter()
        try:
            payload = {
                "model": self.model_id,
                "input": [{"role": "user", "content": "ping"}],
                "max_output_tokens": 1,
            }
            status, _, _ = self._post(payload, want_json=False)
            return TestConnectionResult(
                success=status == 200, latency_ms=round((time.perf_counter() - started) * 1000.0, 1)
            )
        except ProviderError as exc:
            return TestConnectionResult(
                success=False,
                latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
                error=str(exc),
            )

    @staticmethod
    def _build_response(
        content: str, usage: dict[str, Any] | None, *, json_mode: bool
    ) -> UnifiedChatResponse:
        """构造统一响应（复用 Chat 的构建逻辑，文本 + JSON 兜底解析）。"""
        parsed = None
        if json_mode:
            try:
                parsed = extract_json_object(content)
            except ProtocolError:
                parsed = None
        return UnifiedChatResponse(
            content=content,
            parsed_json=parsed,
            usage=(
                Usage(
                    prompt_tokens=int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
                    completion_tokens=int(
                        usage.get("output_tokens") or usage.get("completion_tokens") or 0
                    ),
                    total_tokens=int(usage.get("total_tokens") or 0),
                )
                if usage
                else None
            ),
        )


# --------------------------------------------------------------------------- #
# Anthropic Messages 适配器
# --------------------------------------------------------------------------- #
class AnthropicAdapter(BaseAdapter):
    """Anthropic Messages 协议（``/v1/messages``）。

    抹平要点：
    - System Prompt 提取至顶级 ``system`` 字段（Anthropic 不允许 system 角色
      出现在 messages 内）；
    - 请求体无 temperature 之外的额外参数即可（temperature 需在 0~1）；
    - 无原生 JSON Mode：统一走 System Prompt Strict JSON 约束 + 响应清洗。
    """

    def chat(self, request: UnifiedChatRequest) -> UnifiedChatResponse:
        """组装 Anthropic 消息并解析 content[].text。"""
        messages, system_prompt = self._split_messages(request)
        want_json = (
            request.response_format is not None and request.response_format.type == "json_object"
        )
        if want_json and system_prompt is not None:
            system_prompt = _inject_strict_json(system_prompt)
        elif want_json:
            system_prompt = _STRICT_JSON_PROMPT

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages or [{"role": "user", "content": "ping"}],
            "max_tokens": 4096,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if request.temperature is not None:
            payload["temperature"] = min(max(request.temperature, 0.0), 1.0)

        url = f"{self.provider.base_url.rstrip('/')}/v1/messages"
        headers = {**self._build_headers()}
        if self.provider.api_key:
            headers["x-api-key"] = self.provider.api_key
            headers["anthropic-version"] = "2023-06-01"
        status, _, raw = _http_post(url, payload=payload, headers=headers, timeout=60)
        try:
            data = json.loads(raw)
            parts = [
                block.get("text", "")
                for block in data.get("content", [])
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            content = "".join(parts)
            usage = data.get("usage") or {}
            parsed = None
            if want_json:
                try:
                    parsed = extract_json_object(content)
                except ProtocolError:
                    parsed = None
            return UnifiedChatResponse(
                content=content,
                parsed_json=parsed,
                usage=(
                    Usage(
                        prompt_tokens=int(usage.get("input_tokens") or 0),
                        completion_tokens=int(usage.get("output_tokens") or 0),
                        total_tokens=int(usage.get("input_tokens") or 0)
                        + int(usage.get("output_tokens") or 0),
                    )
                    if usage
                    else None
                ),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"Anthropic 响应解析失败（HTTP {status}）: {exc}") from exc

    def test_connection(self) -> TestConnectionResult:
        """发送极小 ping 文本，验证 HTTP 200 与延时。"""
        started = time.perf_counter()
        try:
            payload = {
                "model": self.model_id,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
            url = f"{self.provider.base_url.rstrip('/')}/v1/messages"
            headers = {**self._build_headers()}
            if self.provider.api_key:
                headers["x-api-key"] = self.provider.api_key
                headers["anthropic-version"] = "2023-06-01"
            status, _, _ = _http_post(url, payload=payload, headers=headers, timeout=60)
            return TestConnectionResult(
                success=status == 200, latency_ms=round((time.perf_counter() - started) * 1000.0, 1)
            )
        except ProviderError as exc:
            return TestConnectionResult(
                success=False,
                latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
                error=str(exc),
            )


# --------------------------------------------------------------------------- #
# Gemini 适配器
# --------------------------------------------------------------------------- #
class GeminiAdapter(BaseAdapter):
    """Google Gemini 协议（``/v1beta/models/{model}:generateContent``）。

    JSON Mode：透传 ``generationConfig.responseMimeType="application/json"``
    并注入 System Prompt 约束（双保险）。
    """

    def chat(self, request: UnifiedChatRequest) -> UnifiedChatResponse:
        """组装 Gemini 请求（contents + systemInstruction）并解析候选文本。"""
        messages, system_prompt = self._split_messages(request)
        want_json = (
            request.response_format is not None and request.response_format.type == "json_object"
        )
        if want_json and system_prompt is not None:
            system_prompt = _inject_strict_json(system_prompt)

        contents: list[dict[str, Any]] = []
        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
        if not contents:
            contents = [{"role": "user", "parts": [{"text": "ping"}]}]
        payload: dict[str, Any] = {"contents": contents}
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        generation: dict[str, Any] = {}
        if request.temperature is not None:
            generation["temperature"] = request.temperature
        if want_json:
            generation["responseMimeType"] = "application/json"
        if generation:
            payload["generationConfig"] = generation

        url = f"{self.provider.base_url.rstrip('/')}/v1beta/models/{request.model}:generateContent"
        headers = {**self._build_headers()}
        if self.provider.api_key:
            headers["x-goog-api-key"] = self.provider.api_key
        status, _, raw = _http_post(url, payload=payload, headers=headers, timeout=60)
        try:
            data = json.loads(raw)
            candidates = data.get("candidates") or []
            content = ""
            for cand in candidates:
                for part in (cand.get("content") or {}).get("parts", []) or []:
                    if isinstance(part, dict) and part.get("text"):
                        content += part["text"]
            usage = data.get("usageMetadata") or {}
            parsed = None
            if want_json:
                try:
                    parsed = extract_json_object(content)
                except ProtocolError:
                    parsed = None
            return UnifiedChatResponse(
                content=content,
                parsed_json=parsed,
                usage=(
                    Usage(
                        prompt_tokens=int(usage.get("promptTokenCount") or 0),
                        completion_tokens=int(usage.get("candidatesTokenCount") or 0),
                        total_tokens=int(usage.get("totalTokenCount") or 0),
                    )
                    if usage
                    else None
                ),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"Gemini 响应解析失败（HTTP {status}）: {exc}") from exc

    def test_connection(self) -> TestConnectionResult:
        """发送极小 ping 文本，验证 HTTP 200 与延时。"""
        started = time.perf_counter()
        try:
            payload = {
                "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                "generationConfig": {"maxOutputTokens": 1},
            }
            url = f"{self.provider.base_url.rstrip('/')}/v1beta/models/{self.model_id}:generateContent"
            headers = {**self._build_headers()}
            if self.provider.api_key:
                headers["x-goog-api-key"] = self.provider.api_key
            status, _, _ = _http_post(url, payload=payload, headers=headers, timeout=60)
            return TestConnectionResult(
                success=status == 200, latency_ms=round((time.perf_counter() - started) * 1000.0, 1)
            )
        except ProviderError as exc:
            return TestConnectionResult(
                success=False,
                latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
                error=str(exc),
            )


# --------------------------------------------------------------------------- #
# 适配器工厂表
# --------------------------------------------------------------------------- #
ADAPTER_BY_PROTOCOL: dict[ApiProtocol, type[BaseAdapter]] = {
    ApiProtocol.OPENAI_CHAT: OpenAIChatAdapter,
    ApiProtocol.OPENAI_RESPONSES: OpenAIResponsesAdapter,
    ApiProtocol.ANTHROPIC: AnthropicAdapter,
    ApiProtocol.GEMINI: GeminiAdapter,
}


def build_adapter(provider: ProviderConfig, model_id: str) -> BaseAdapter:
    """按供应商协议构造适配器（未知协议抛 ProviderError）。"""
    adapter_cls = ADAPTER_BY_PROTOCOL.get(provider.protocol)
    if adapter_cls is None:
        raise ProviderError(f"未知 API 协议: {provider.protocol}")
    return adapter_cls(provider=provider, model_id=model_id)


__all__ = [
    "ADAPTER_BY_PROTOCOL",
    "AnthropicAdapter",
    "BaseAdapter",
    "GeminiAdapter",
    "OpenAIChatAdapter",
    "OpenAIResponsesAdapter",
    "build_adapter",
    "extract_json_object",
]
