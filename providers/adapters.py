"""Model Provider 网关层：统一 LLM 抽象基类与多协议适配器。

实现协议抹平（Protocol Adapters）：
- ``BaseAdapter``：统一抽象（``chat`` / ``test_connection`` / ``chat_text``）；
- ``OpenAIChatAdapter``：POST {baseUrl}/chat/completions（标准 OpenAI 兼容）；
- ``OpenAIResponsesAdapter``：POST {baseUrl}/responses（新版 Responses 规范）；
- ``AnthropicAdapter``：POST {baseUrl}/v1/messages（System Prompt 提取至顶级字段）。

结构化输出保障（JSON Mode）：
- 请求侧：支持原生 JSON Schema 的协议透传原生参数（openai_chat 的
  ``response_format`` / responses 的 ``text.format``），anthropic 与所有
  兜底路径在 System Prompt 注入 Strict JSON 约束；
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
import logging
import re
import time
import urllib.parse
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from providers.errors import (
    AuthenticationError,
    ProtocolError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
    StreamHandshakeRejected,
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

logger = logging.getLogger(__name__)

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
    _raise_for_status(resp.status, raw)
    return resp.status, dict(resp.getheaders()), raw


def _raise_for_status(status: int, raw: str) -> None:
    """HTTP 状态 -> 标准错误族映射（_http_post 与 SSE 读取器共用）。"""
    if status == 401:
        raise AuthenticationError(f"鉴权失败（HTTP 401）: {_brief(raw)}")
    if status == 429:
        raise RateLimitError(f"配额超限或请求过于频繁（HTTP 429）: {_brief(raw)}")
    if status >= 400:
        raise ProviderError(f"模型服务返回 HTTP {status}: {_brief(raw)}", code="provider_error")


def _brief(raw: str, limit: int = 300) -> str:
    """压缩错误响应体为单行摘要（防审计日志膨胀 / 前端泄露敏感信息）。"""
    text = raw.replace("\n", " ").strip()
    return text[:limit]


# --------------------------------------------------------------------------- #
# SSE 流式传输（stream=True：块间空闲超时 + 总时长上限，客户端聚合）
# --------------------------------------------------------------------------- #
# 单行字节上限：网关永不换行的慢滴流会在该处被截断并熔断（防内存无界增长）
# 单行字节上限（256KB）：防无换行慢滴流内存无界增长；取值需容纳合法的
# 超长单行大 payload（如伪流式整包网关把完整 JSON 放一行），故远大于常规帧
_SSE_MAX_LINE_BYTES = 256 * 1024


def _iter_sse_payloads(lines: Iterable[str]) -> Iterator[str]:
    """从 SSE 行序列提取 data 负载（协议中立纯函数，便于单测）。

    忽略空行 / event: / comment:（: 开头）行；`data: <payload>` 产出 payload
    原文（含 "[DONE]" 字面量——终止语义由消费方判读，本函数不解析 JSON）。
    空 data: 字段（仅冒号或纯空白）按 SSE 规范忽略，不产出空负载。
    """
    for line in lines:
        text = line.strip()
        if not text or text.startswith(":") or text.startswith("event:"):
            continue
        if text.startswith("data:"):
            payload = text[len("data:") :].strip()
            if payload:
                yield payload


def _raise_for_error_frame(err: Any) -> None:
    """流式错误帧 -> 标准错误族映射（429/鉴权特征 -> 专用异常，其余 ProviderError）。"""
    text = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
    lowered = str(text).lower()
    if "429" in lowered or "rate_limit" in lowered or "ratelimit" in lowered:
        raise RateLimitError(f"流式限流（429 特征）: {_brief(text)}")
    if "401" in lowered or "authentication" in lowered or "invalid_api_key" in lowered:
        raise AuthenticationError(f"流式鉴权失败: {_brief(text)}")
    raise ProviderError(f"流式响应错误帧: {_brief(text)}", code="provider_error")


def _stream_fallback_eligible(exc: BaseException, *extra_hints: str) -> bool:
    """流式回退非流式的资格判定：仅限握手阶段 400 且报文命中特征。

    StreamHandshakeRejected 只在 _http_post_sse 首包（未产出任何 chunk 前）
    抛出——mid-stream 错误帧 / 断连 / 超时永远是其他错误类型，即使其文本
    巧合包含 "HTTP 400"/"stream" 字样也绝不触发非流式重发（防重复计费）。
    特征词：stream 前缀族 + JSON Mode 参数族（_UNSUPPORTED_JSON_HINTS）+
    调用方附加 hints（如 openai_chat 的 stream_options）。
    """
    return isinstance(exc, StreamHandshakeRejected) and any(
        h in str(exc).lower() for h in (*extra_hints, "stream", *_UNSUPPORTED_JSON_HINTS)
    )


def _http_post_sse(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    max_seconds: int,
    api_key: str | None = None,
) -> Iterator[str]:
    """发送流式 JSON POST，逐条产出 SSE data 负载（惰性生成器）。

    两道防无限流防线：
    - 块间空闲上限 timeout 与流总剩余预算取最小值，经 sock.settimeout 收紧
      单次 readline 的等待上限（慢滴流持续供字节也无法拖过总预算）；
    - 单行字节上限 _SSE_MAX_LINE_BYTES：网关永不换行时 readline 在该处被
      截断，达到上限仍无换行 => ProviderError 熔断（防内存无界增长）。
    总预算耗尽抛 ProviderTimeoutError。首包 HTTP 状态非 2xx 与 _http_post
    同映射；EOF（b""）正常结束迭代——终止帧校验归消费方（_consume_stream）。
    """
    body = json.dumps(payload).encode("utf-8")
    merged_headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        **headers,
    }
    if api_key:
        merged_headers["Authorization"] = f"Bearer {api_key}"
    parsed = _validate_outbound_url(url)
    conn_cls = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    conn = conn_cls(parsed.hostname, parsed.port, timeout=timeout)
    started = time.perf_counter()
    try:
        conn.request("POST", parsed.path or "/", body=body, headers=merged_headers)
        resp = conn.getresponse()
        if resp.status >= 400:
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.status == 400:
                # 握手阶段 400 专用类型：回退资格判定只认它——此时尚未产出
                # 任何 chunk，重发无重复计费风险；mid-stream 错误帧/断连永远
                # 是其他错误类型，结构上不可能误触发回退
                raise StreamHandshakeRejected(
                    f"模型服务返回 HTTP 400: {_brief(raw)}", code="provider_error"
                )
            _raise_for_status(resp.status, raw)
        while True:
            # 防线一：剩余预算语义——块间空闲上限与总剩余取最小值收紧单次读
            remaining = max_seconds - (time.perf_counter() - started)
            if remaining <= 0:
                raise ProviderTimeoutError(f"流式总时长超限（>{max_seconds}s）")
            sock = getattr(conn, "sock", None)
            if sock is not None:
                sock.settimeout(min(timeout, remaining))
            # 防线二：行字节上限——无换行慢滴流单行熔断
            line = resp.readline(_SSE_MAX_LINE_BYTES)
            if not line:
                return  # EOF：终止帧校验归消费方
            if len(line) == _SSE_MAX_LINE_BYTES and not line.endswith(b"\n"):
                raise ProviderError("SSE 行超长（无换行慢滴流），已熔断", code="provider_error")
            yield from _iter_sse_payloads([line.decode("utf-8", errors="replace")])
    except TimeoutError as exc:  # socket.timeout（块间空闲超时）
        raise ProviderTimeoutError(f"请求超时: {exc}") from exc
    except (http.client.HTTPException, OSError) as exc:
        raise ProviderError(f"网络请求失败: {exc}", code="provider_error") from exc
    finally:
        conn.close()


def _consume_stream(
    payloads: Iterable[str],
    *,
    extract_delta: Callable[[dict[str, Any]], str],
    terminal: Callable[[dict[str, Any]], bool],
    extract_usage: Callable[[dict[str, Any]], dict[str, Any] | None],
    extract_error: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """聚合 SSE 帧：返回 (content, usage_raw)；终止校验与错误裁决统一在此。

    - "[DONE]" 字面量视为优雅终止（仅 OpenAI 系协议发送，anthropic 不发）；
    - 协议终止帧由 terminal(frame) 判定；EOF 先于终止帧 => ProviderError（断连）；
    - 帧内 error 字段 => _raise_for_error_frame 映射标准错误族；
    - extract_error（可选）：协议专属错误帧识别（如 responses 的
      {"type": "error"} / response.failed——无 "error" 顶层键，普通检测
      漏判后会退化成"断连"），返回真值即按错误帧映射；
    - 终止时 content 为空 => ProtocolError（如内容全部落在 reasoning_content）。
    """
    parts: list[str] = []
    usage: dict[str, Any] | None = None
    for raw in payloads:
        text = raw.strip()
        if text == "[DONE]":
            break
        try:
            frame = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"SSE 帧解析失败: {exc}") from exc
        if not isinstance(frame, dict):
            raise ProtocolError(f"SSE 帧非 JSON 对象: {type(frame).__name__}")
        if frame.get("error"):
            _raise_for_error_frame(frame["error"])
        if extract_error is not None:
            protocol_error = extract_error(frame)
            if protocol_error:
                _raise_for_error_frame(protocol_error)
        if terminal(frame):
            usage = extract_usage(frame) or usage
            break
        piece = extract_delta(frame)
        if piece:
            parts.append(piece)
        usage = extract_usage(frame) or usage
    else:
        raise ProviderError("流式响应中途断连（未收到终止帧）", code="provider_error")
    content = "".join(parts)
    if not content:
        raise ProtocolError("流式响应未产出内容（content 为空）")
    return content, usage


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
        timeout: int | None = None,
    ) -> str:
        """旧形态便捷入口：messages 列表 -> 文本；默认启用 JSON Mode。

        供 agent 层既有调用点（LLMNL2DSL / LLMPlanner 等）透明切换，
        无需改动其内部消息构造逻辑。

        温度显式接线 settings.LLM_TEMPERATURE（缺省 0.0）：agent 层全部 LLM
        调用（规划/总结/反思/自愈）的温度由此统一控制，评测确定性锁定
        （eval_runner._lock_determinism）才能真正落到请求层。

        timeout：本次调用读超时覆盖（秒），None 时由适配器回退网关默认。
        """
        from config import settings

        resp = self.chat(
            UnifiedChatRequest(
                messages=[{"role": m["role"], "content": m["content"]} for m in messages],
                model=model or self.model_id,
                temperature=settings.LLM_TEMPERATURE,
                response_format={"type": "json_object"} if json_mode else None,
                timeout=timeout,
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
        """发送 Chat Completions 请求并解析 choices[0].message.content。

        provider.stream=True 时走 SSE 流式聚合（超时语义=块间空闲+总上限）；
        网关在握手阶段拒绝流式（StreamHandshakeRejected 且报文含
        stream/JSON 参数特征）时自动回退非流式重试一次；mid-stream 错误帧/
        断连/超时永远原样上抛不回退（防重复计费）。
        """
        messages, system_prompt = self._split_messages(request)
        want_json = (
            request.response_format is not None and request.response_format.type == "json_object"
        )
        if want_json and system_prompt is not None:
            system_prompt = _inject_strict_json(system_prompt)
        if system_prompt is not None:
            messages = [{"role": "system", "content": system_prompt}, *messages]

        from config import settings

        # 读超时：请求级覆盖优先，回退网关默认（PROVIDER_TIMEOUT 配置面）
        timeout = request.timeout or settings.PROVIDER_TIMEOUT
        if self.provider.stream:
            try:
                return self._chat_via_stream(request, messages, want_json, timeout)
            except ProviderError as exc:
                if not _stream_fallback_eligible(exc, "stream_options"):
                    raise
                logger.info("网关拒绝流式请求，回退非流式重试: %s", _brief(str(exc)))
        status, _, raw = self._post(
            {
                "model": request.model,
                "messages": messages,
                "temperature": request.temperature if request.temperature is not None else 0.0,
            },
            want_json=want_json,
            timeout=timeout,
        )
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            return self._build_response(content, usage, json_mode=want_json)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"Chat Completions 响应解析失败（HTTP {status}）: {exc}") from exc

    def _chat_via_stream(
        self,
        request: UnifiedChatRequest,
        messages: list[dict[str, Any]],
        want_json: bool,
        timeout: int,
    ) -> UnifiedChatResponse:
        """SSE 流式调用并聚合为完整 content（provider.stream=True 专用路径）。

        中转站不认 stream_options（HTTP 400 提及该参数）时：去掉该参数保
        流式重试一次（usage 丢失可接受——上层仅可观测消费）。
        """
        from config import settings

        url = f"{self.provider.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature if request.temperature is not None else 0.0,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if want_json:
            # JSON Mode 原生参数流式下照常透传；被网关拒绝时交由外层回退非流式
            # （非流式 _post 自带原生参数降级链，行为与现状一致）
            payload["response_format"] = {"type": "json_object"}
        headers = self._build_headers()
        max_seconds = settings.PROVIDER_STREAM_MAX_SECONDS

        def _delta(frame: dict[str, Any]) -> str:
            return ((frame.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""

        try:
            content, usage = _consume_stream(
                _http_post_sse(
                    url,
                    payload=payload,
                    headers=headers,
                    timeout=timeout,
                    max_seconds=max_seconds,
                    api_key=self.provider.api_key,
                ),
                extract_delta=_delta,
                terminal=lambda f: False,  # 终止帧 = [DONE]，_consume_stream 内建处理
                extract_usage=lambda f: f.get("usage"),
            )
        except ProviderError as exc:
            # 仅握手阶段 400 且报文提及 stream_options 才去参重试；mid-stream
            # 错误（StreamHandshakeRejected 之外）原样上抛
            if not (
                isinstance(exc, StreamHandshakeRejected) and "stream_options" in str(exc).lower()
            ):
                raise
            payload.pop("stream_options", None)
            logger.info("网关不认 stream_options，去参数保流式重试")
            content, usage = _consume_stream(
                _http_post_sse(
                    url,
                    payload=payload,
                    headers=headers,
                    timeout=timeout,
                    max_seconds=max_seconds,
                    api_key=self.provider.api_key,
                ),
                extract_delta=_delta,
                terminal=lambda f: False,
                extract_usage=lambda f: f.get("usage"),
            )
        return self._build_response(content, usage, json_mode=want_json)

    def _post(
        self,
        payload: dict[str, Any],
        *,
        want_json: bool,
        timeout: int,
        attempt_native: bool = True,
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
            return _http_post(url, payload=body, headers=headers, timeout=timeout, api_key=None)
        except ProviderError as exc:
            if (
                want_json
                and attempt_native
                and isinstance(exc, ProviderError)
                and exc.code == "provider_error"
                and any(h in str(exc).lower() for h in _UNSUPPORTED_JSON_HINTS)
            ):
                # 模型/中转不支持 response_format -> 降级：仅保留 Prompt 约束重试
                return self._post(
                    payload, want_json=want_json, timeout=timeout, attempt_native=False
                )
            raise

    def test_connection(self) -> TestConnectionResult:
        """发送极小 ping 文本，验证 HTTP 200 与延时。"""
        from config import settings

        started = time.perf_counter()
        try:
            payload = {
                "model": self.model_id,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
            status, _, _ = self._post(payload, want_json=False, timeout=settings.PROVIDER_TIMEOUT)
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
        """按 /responses 规范组装请求并解析 output 文本。

        provider.stream=True 时走 SSE 流式聚合；网关在握手阶段拒绝流式
        （StreamHandshakeRejected 且报文含 stream/JSON 参数特征）时回退
        非流式重试一次。
        """
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

        from config import settings

        # 读超时：请求级覆盖优先，回退网关默认（PROVIDER_TIMEOUT 配置面）
        timeout = request.timeout or settings.PROVIDER_TIMEOUT
        if self.provider.stream:
            try:
                return self._chat_via_stream(request, input_blocks, want_json, timeout)
            except ProviderError as exc:
                if not _stream_fallback_eligible(exc):
                    raise
                logger.info("网关拒绝流式请求，回退非流式重试: %s", _brief(str(exc)))
        payload: dict[str, Any] = {
            "model": request.model,
            "input": input_blocks,
            "temperature": request.temperature if request.temperature is not None else 0.0,
        }
        status, _, raw = self._post(payload, want_json=want_json, timeout=timeout)
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

    def _chat_via_stream(
        self,
        request: UnifiedChatRequest,
        input_blocks: list[dict[str, Any]],
        want_json: bool,
        timeout: int,
    ) -> UnifiedChatResponse:
        """SSE 流式调用：delta 取 response.output_text.delta，usage 取 response.completed。"""
        from config import settings

        url = f"{self.provider.base_url.rstrip('/')}/responses"
        payload: dict[str, Any] = {
            "model": request.model,
            "input": input_blocks,
            "temperature": request.temperature if request.temperature is not None else 0.0,
            "stream": True,
        }
        if want_json:
            # JSON Mode 原生参数流式下照常透传（被拒时交由外层回退非流式降级链）
            payload["text"] = {"format": {"type": "json_object"}}

        def _extract_error(frame: dict[str, Any]) -> Any:
            """responses 协议错误帧：{"type":"error"} 与 response.failed 无顶层
            "error" 键，普通检测漏判后会退化成"断连"——此处显式识别。"""
            ftype = frame.get("type")
            if ftype == "error":
                return frame
            if ftype == "response.failed":
                return (frame.get("response") or {}).get("error") or frame
            return None

        content, usage = _consume_stream(
            _http_post_sse(
                url,
                payload=payload,
                headers=self._build_headers(),
                timeout=timeout,
                max_seconds=settings.PROVIDER_STREAM_MAX_SECONDS,
                api_key=self.provider.api_key,
            ),
            extract_delta=lambda f: (
                (f.get("delta") or "") if f.get("type") == "response.output_text.delta" else ""
            ),
            terminal=lambda f: f.get("type") == "response.completed",
            extract_usage=lambda f: (f.get("response") or {}).get("usage"),
            extract_error=_extract_error,
        )
        return self._build_response(content, usage, json_mode=want_json)

    def _post(
        self,
        payload: dict[str, Any],
        *,
        want_json: bool,
        timeout: int,
        attempt_native: bool = True,
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
            return _http_post(url, payload=body, headers=headers, timeout=timeout, api_key=None)
        except ProviderError as exc:
            if (
                want_json
                and attempt_native
                and isinstance(exc, ProviderError)
                and exc.code == "provider_error"
                and any(h in str(exc).lower() for h in _UNSUPPORTED_JSON_HINTS)
            ):
                return self._post(
                    payload, want_json=want_json, timeout=timeout, attempt_native=False
                )
            raise

    def test_connection(self) -> TestConnectionResult:
        """发送极小 ping 文本，验证 HTTP 200 与延时。"""
        from config import settings

        started = time.perf_counter()
        try:
            payload = {
                "model": self.model_id,
                "input": [{"role": "user", "content": "ping"}],
                "max_output_tokens": 1,
            }
            status, _, _ = self._post(payload, want_json=False, timeout=settings.PROVIDER_TIMEOUT)
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
        from config import settings

        # 读超时：请求级覆盖优先，回退网关默认（PROVIDER_TIMEOUT 配置面）
        timeout = request.timeout or settings.PROVIDER_TIMEOUT
        if self.provider.stream:
            try:
                return self._chat_via_stream(payload, want_json, timeout)
            except ProviderError as exc:
                if not _stream_fallback_eligible(exc):
                    raise
                logger.info("网关拒绝流式请求，回退非流式重试: %s", _brief(str(exc)))
        status, _, raw = _http_post(url, payload=payload, headers=headers, timeout=timeout)
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

    def _chat_via_stream(
        self, payload_base: dict[str, Any], want_json: bool, timeout: int
    ) -> UnifiedChatResponse:
        """SSE 流式调用：delta 取 content_block_delta，usage 由 message_start/message_delta 合并。"""
        from config import settings

        url = f"{self.provider.base_url.rstrip('/')}/v1/messages"
        payload = {**payload_base, "stream": True}
        # 鉴权与非流式路径同源：内联 x-api-key + anthropic-version（api_key 参数保持 None）
        headers = {**self._build_headers()}
        if self.provider.api_key:
            headers["x-api-key"] = self.provider.api_key
            headers["anthropic-version"] = "2023-06-01"
        usage_acc: dict[str, int] = {}

        def _extract_usage(frame: dict[str, Any]) -> dict[str, Any] | None:
            ftype = frame.get("type")
            if ftype == "message_start":
                start = (frame.get("message") or {}).get("usage") or {}
                usage_acc["input_tokens"] = int(start.get("input_tokens") or 0)
            elif ftype == "message_delta":
                delta_usage = frame.get("usage") or {}
                usage_acc["output_tokens"] = int(delta_usage.get("output_tokens") or 0)
            return dict(usage_acc) if usage_acc else None

        content, usage = _consume_stream(
            _http_post_sse(
                url,
                payload=payload,
                headers=headers,
                timeout=timeout,
                max_seconds=settings.PROVIDER_STREAM_MAX_SECONDS,
            ),
            extract_delta=lambda f: (
                (f.get("delta") or {}).get("text") or ""
                if f.get("type") == "content_block_delta"
                else ""
            ),
            terminal=lambda f: f.get("type") == "message_stop",
            extract_usage=_extract_usage,
        )
        parsed = None
        if want_json:
            try:
                parsed = extract_json_object(content)
            except ProtocolError:
                parsed = None
        usage = usage or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        return UnifiedChatResponse(
            content=content,
            parsed_json=parsed,
            usage=Usage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )

    def test_connection(self) -> TestConnectionResult:
        """发送极小 ping 文本，验证 HTTP 200 与延时。"""
        from config import settings

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
            status, _, _ = _http_post(
                url, payload=payload, headers=headers, timeout=settings.PROVIDER_TIMEOUT
            )
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
    "OpenAIChatAdapter",
    "OpenAIResponsesAdapter",
    "build_adapter",
    "extract_json_object",
]
