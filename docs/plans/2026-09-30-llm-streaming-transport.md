# LLM 网关层流式传输改造 实施计划

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。

**Goal:** providers 适配器传输层支持 SSE 流式（openai_chat / openai_responses / anthropic 三协议），客户端聚合为完整字符串后走既有 `_llm_json` 契约；移除 Gemini 协议；前端供应商表单提供流式开关。

**Architecture:** 共享 SSE 行读取器（`_http_post_sse`，纯标准库 `http.client` 逐行读）+ 共享帧聚合器（`_consume_stream`，统一终止帧校验/错误帧映射/断连裁决）+ 三协议各自的 delta/usage 提取器；各适配器 `chat()` 内按 `provider.stream` 字段分支，聚合完复用现有 `_build_response`。流式被网关拒绝时自动回退非流式重试一次。

**Tech Stack:** 纯标准库（http.client / json / time），pydantic v2 契约，pytest 单测（monkeypatch 打桩）。

**Spec:** `docs/superpowers/specs/2026-09-30-llm-streaming-transport-design.md`

## Global Constraints

- Python 3.12 + conda 环境（`dataagent`，本机 `futurebi` 等效）；提交前 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿；
- 上层代码（`core/orchestrator/`、`agent/`）零 diff——本改造全部收在 `providers/`、`config/`、`web/`、`tests/`；
- `stream` 字段默认 `false`：不开流式时行为与现状逐字节一致（存量 755+ 测试不许动语义）；
- 流式路径任何失败一律抛标准错误族（`providers/errors.py`），严禁把部分聚合内容当完整内容返回；
- 新增代码零第三方依赖（只用标准库 + 既有 pydantic）；注释/docstring 简体中文，标识符英文；
- 测试命令统一用 `python -m pytest tests/test_providers.py -v`（本机先 `conda activate futurebi` 或直接用 `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest`）。

## Review Focus

规格隐含但容易被漏测的失效模式（各条已挂到对应任务的测试）：

1. **DeepSeek 系网关把回答放进 `reasoning_content`、`content` 为空** → 终止时抛 `ProtocolError` 走兜底，不许静默返回空串（Task 2）；
2. **流中途断连（EOF 未收到协议终止帧）** → `ProviderError`，绝不返回半截 JSON（Task 2）；
3. **网关不认 `stream_options` 参数** → 去掉该参数保流式重试一次（Task 3）；
4. **网关整体拒绝 `stream`（HTTP 400）** → 自动回退非流式重试一次，三协议各自覆盖（Task 3/4/5）；
5. **存量 providers.json 含 gemini 等未知协议条目** → store 跳过该条并告警，不拒载整个文件（Task 6）；
6. **块间空闲超时 / 流总时长超限** → 均映射 `ProviderTimeoutError`（Task 2）。

---

### Task 1: 契约与配置面（ProviderConfig.stream + 总时长上限）

**Files:**
- Modify: `providers/models.py:52`（`enabled` 字段后）
- Modify: `config/settings.py:153`（`PROVIDER_TIMEOUT` 后）
- Test: `tests/test_providers.py`（文件末尾追加）

**Interfaces:**
- Produces: `ProviderConfig.stream: bool = False`（Task 3/4/5 分支依据；Task 7 前端透传）；`settings.PROVIDER_STREAM_MAX_SECONDS: int`（Task 2/3/4/5 的流式总上限入参）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_providers.py` 末尾追加：

```python
# --------------------------------------------------------------------------- #
# 流式契约（ProviderConfig.stream / 总时长上限）
# --------------------------------------------------------------------------- #
def test_provider_config_stream_defaults_false_and_roundtrips(tmp_path):
    cfg = _provider()
    assert cfg.stream is False  # 存量配置缺省关闭，行为不变
    cfg2 = _provider(stream=True)
    assert cfg2.stream is True
    # 序列化往返（public_view 透传给前端）
    dumped = cfg2.model_dump(mode="json")
    assert dumped["stream"] is True
    assert ProviderConfig.model_validate(dumped).stream is True


def test_settings_stream_max_seconds_exists():
    from config import settings

    assert isinstance(settings.PROVIDER_STREAM_MAX_SECONDS, int)
    assert settings.PROVIDER_STREAM_MAX_SECONDS > 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_providers.py::test_provider_config_stream_defaults_false_and_roundtrips tests/test_providers.py::test_settings_stream_max_seconds_exists -v`
Expected: FAIL（`stream` 字段不存在 / `PROVIDER_STREAM_MAX_SECONDS` 属性不存在）

- [ ] **Step 3: 最小实现**

`providers/models.py` 的 `ProviderConfig`（第 52 行 `enabled` 之后）插入：

```python
    stream: bool = False  # SSE 流式传输（网关对长请求整包读超时时开启）
```

`config/settings.py` 第 153 行 `PROVIDER_TIMEOUT` 之后插入：

```python
# 流式传输总时长上限（秒）：块间空闲超时复用 PROVIDER_TIMEOUT，本项防无限流
PROVIDER_STREAM_MAX_SECONDS: int = int(os.getenv("PROVIDER_STREAM_MAX_SECONDS", "300"))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_providers.py -v`
Expected: PASS（全文件，含存量测试）

- [ ] **Step 5: Commit**

```bash
git add providers/models.py config/settings.py tests/test_providers.py
git commit -m "feat(providers): 契约新增 ProviderConfig.stream 字段与流式总时长上限配置"
```

---

### Task 2: SSE 共享读取器与帧聚合器（providers/adapters.py）

**Files:**
- Modify: `providers/adapters.py:24-32`（import 区）、`adapters.py:167-174`（`_http_post` 状态映射提取为共享函数）、`adapters.py:178`（`_brief` 之后新增三个函数）
- Test: `tests/test_providers.py`（文件末尾追加）

**Interfaces:**
- Consumes: `settings.PROVIDER_STREAM_MAX_SECONDS`（Task 1）。
- Produces（Task 3/4/5 消费，签名逐字固定）:
  - `_iter_sse_payloads(lines: Iterable[str]) -> Iterator[str]`；
  - `_http_post_sse(url: str, *, payload: dict, headers: dict, timeout: int, max_seconds: int, api_key: str | None = None) -> Iterator[str]`（产出 `data:` 负载原文，含 `"[DONE]"` 字面量）；
  - `_consume_stream(payloads, *, extract_delta: Callable[[dict], str], terminal: Callable[[dict], bool], extract_usage: Callable[[dict], dict | None]) -> tuple[str, dict | None]`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_providers.py` 末尾追加（import 区同步补 `from providers.adapters import` 列表：`_consume_stream, _http_post_sse, _iter_sse_payloads`，以及 `from providers.errors import ProviderTimeoutError, ProtocolError`——后者文件已部分导入，按需补）：

```python
# --------------------------------------------------------------------------- #
# SSE 流式（纯函数 / 读取器 / 聚合器）
# --------------------------------------------------------------------------- #
def test_iter_sse_payloads_ignores_noise_and_yields_data():
    lines = [
        ": keep-alive",
        "event: response.output_text.delta",
        'data: {"a": 1}',
        "",
        "data: [DONE]",
    ]
    assert list(_iter_sse_payloads(lines)) == ['{"a": 1}', "[DONE]"]


def test_consume_stream_aggregates_and_collects_usage():
    frames = [
        json.dumps({"choices": [{"delta": {"content": "he"}}]}),
        json.dumps({"choices": [{"delta": {"content": "llo"}}]}),
        json.dumps(
            {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
        ),
        "[DONE]",
    ]
    content, usage = _consume_stream(
        frames,
        extract_delta=lambda f: ((f.get("choices") or [{}])[0].get("delta") or {}).get("content")
        or "",
        terminal=lambda f: False,
        extract_usage=lambda f: f.get("usage"),
    )
    assert content == "hello"
    assert usage["total_tokens"] == 3


def test_consume_stream_eof_without_terminal_raises():
    # 流中途断连：EOF 先于终止帧 => ProviderError，绝不返回半截内容
    with pytest.raises(ProviderError):
        _consume_stream(
            [json.dumps({"choices": [{"delta": {"content": "half"}}]})],
            extract_delta=lambda f: ((f.get("choices") or [{}])[0].get("delta") or {}).get("content")
            or "",
            terminal=lambda f: False,
            extract_usage=lambda f: None,
        )


def test_consume_stream_empty_content_raises_protocol_error():
    # DeepSeek 系网关内容进 reasoning_content 的形态：content 为空必须报错走兜底
    frames = [json.dumps({"choices": [{"delta": {}}]}), "[DONE]"]
    with pytest.raises(ProtocolError):
        _consume_stream(
            frames,
            extract_delta=lambda f: ((f.get("choices") or [{}])[0].get("delta") or {}).get("content")
            or "",
            terminal=lambda f: False,
            extract_usage=lambda f: None,
        )


def test_consume_stream_error_frame_maps_429():
    frames = [json.dumps({"error": {"code": "429", "message": "rate limited"}})]
    with pytest.raises(RateLimitError):
        _consume_stream(
            frames,
            extract_delta=lambda f: "",
            terminal=lambda f: False,
            extract_usage=lambda f: None,
        )


def test_consume_stream_protocol_terminal_frame_collects_usage():
    frames = [
        json.dumps({"type": "response.output_text.delta", "delta": "ok"}),
        json.dumps(
            {"type": "response.completed", "response": {"usage": {"total_tokens": 7}}}
        ),
    ]
    content, usage = _consume_stream(
        frames,
        extract_delta=lambda f: (f.get("delta") or "")
        if f.get("type") == "response.output_text.delta"
        else "",
        terminal=lambda f: f.get("type") == "response.completed",
        extract_usage=lambda f: f.get("response", {}).get("usage"),
    )
    assert content == "ok"
    assert usage["total_tokens"] == 7


class _SSEResponse:
    """SSE 读取器测试桩响应：按预置行序列逐行 readline。"""

    def __init__(self, lines, status=200):
        self.status = status
        self._lines = lines
        self._i = 0

    def readline(self):
        if self._i >= len(self._lines):
            return b""
        item = self._lines[self._i]
        self._i += 1
        return item.encode("utf-8")

    def read(self):
        return b'{"error": "bad request"}'

    def getheaders(self):
        return {}


class _SSEConnStub:
    """SSE 读取器测试桩连接：记录请求体/头，返回类属性 lines 组成的响应。"""

    lines: list[str] = []
    last: dict | None = None

    def __init__(self, host, port, timeout=None):
        self.host = host

    def request(self, method, path, body=None, headers=None):
        _SSEConnStub.last = {"body": json.loads(body), "headers": headers}

    def getresponse(self):
        return _SSEResponse(_SSEConnStub.lines)

    def close(self):
        pass


def test_http_post_sse_yields_data_lines_and_sends_stream_payload(monkeypatch):
    _SSEConnStub.lines = [": ping", 'data: {"x": 1}', "data: [DONE]", ""]
    monkeypatch.setattr("http.client.HTTPSConnection", _SSEConnStub)
    out = list(
        _http_post_sse(
            "https://gw.example.com/v1/chat/completions",
            payload={"stream": True, "messages": []},
            headers={"X-Custom": "1"},
            timeout=5,
            max_seconds=30,
            api_key="k-test",
        )
    )
    assert out == ['{"x": 1}', "[DONE]"]
    assert _SSEConnStub.last["body"]["stream"] is True
    assert _SSEConnStub.last["headers"]["Authorization"] == "Bearer k-test"
    assert _SSEConnStub.last["headers"]["Accept"] == "text/event-stream"


def test_http_post_sse_idle_timeout_maps_to_provider_timeout(monkeypatch):
    class IdleResp:
        status = 200

        def readline(self):
            raise TimeoutError("read timed out")

    class IdleConn(_SSEConnStub):
        def getresponse(self):
            return IdleResp()

    monkeypatch.setattr("http.client.HTTPSConnection", IdleConn)
    with pytest.raises(ProviderTimeoutError):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=1, max_seconds=30
            )
        )


def test_http_post_sse_total_limit_raises(monkeypatch):
    _SSEConnStub.lines = ['data: {"a": 1}', 'data: {"b": 2}']
    monkeypatch.setattr("http.client.HTTPSConnection", _SSEConnStub)
    with pytest.raises(ProviderTimeoutError):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=0
            )
        )


def test_http_post_sse_first_packet_401_maps(monkeypatch):
    class UnauthorizedResp:
        status = 401

        def read(self):
            return b"unauthorized"

        def readline(self):
            return b""

    class UnauthorizedConn(_SSEConnStub):
        def getresponse(self):
            return UnauthorizedResp()

    monkeypatch.setattr("http.client.HTTPSConnection", UnauthorizedConn)
    with pytest.raises(AuthenticationError):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=30
            )
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_providers.py -k "sse or consume_stream" -v`
Expected: FAIL（ImportError：`_iter_sse_payloads` 等不存在）

- [ ] **Step 3: 最小实现**

`providers/adapters.py`：

（a）import 区（第 24-32 行）改为：

```python
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
```

模块级（`_JSON_FENCE` 附近）加：

```python
logger = logging.getLogger(__name__)
```

（b）把 `_http_post` 内联状态映射（第 167-174 行）提取为共享函数，`_http_post` 末尾改为 `return resp.status, dict(resp.getheaders()), raw` 之前调用 `_raise_for_status(resp.status, raw)`：

```python
def _raise_for_status(status: int, raw: str) -> None:
    """HTTP 状态 -> 标准错误族映射（_http_post 与 SSE 读取器共用）。"""
    if status == 401:
        raise AuthenticationError(f"鉴权失败（HTTP 401）: {_brief(raw)}")
    if status == 429:
        raise RateLimitError(f"配额超限或请求过于频繁（HTTP 429）: {_brief(raw)}")
    if status >= 400:
        raise ProviderError(
            f"模型服务返回 HTTP {status}: {_brief(raw)}", code="provider_error"
        )
```

（c）在 `_brief` 函数之后新增：

```python
# --------------------------------------------------------------------------- #
# SSE 流式传输（stream=True：块间空闲超时 + 总时长上限，客户端聚合）
# --------------------------------------------------------------------------- #
def _iter_sse_payloads(lines: Iterable[str]) -> Iterator[str]:
    """从 SSE 行序列提取 data 负载（协议中立纯函数，便于单测）。

    忽略空行 / event: / comment:（: 开头）行；`data: <payload>` 产出 payload
    原文（含 "[DONE]" 字面量——终止语义由消费方判读，本函数不解析 JSON）。
    """
    for line in lines:
        text = line.strip()
        if not text or text.startswith(":") or text.startswith("event:"):
            continue
        if text.startswith("data:"):
            yield text[len("data:"):].strip()


def _raise_for_error_frame(err: Any) -> None:
    """流式错误帧 -> 标准错误族映射（429/鉴权特征 -> 专用异常，其余 ProviderError）。"""
    text = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
    lowered = str(text).lower()
    if "429" in lowered or "rate_limit" in lowered or "ratelimit" in lowered:
        raise RateLimitError(f"流式限流（429 特征）: {_brief(text)}")
    if "401" in lowered or "authentication" in lowered or "invalid_api_key" in lowered:
        raise AuthenticationError(f"流式鉴权失败: {_brief(text)}")
    raise ProviderError(f"流式响应错误帧: {_brief(text)}", code="provider_error")


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

    超时语义：timeout 为块间空闲上限（socket 读超时对 readline 天然逐块计时）；
    max_seconds 为整条流的 wall-clock 总上限，超限抛 ProviderTimeoutError。
    首包 HTTP 状态非 2xx 与 _http_post 同映射；EOF（b""）正常结束迭代——
    终止帧校验归消费方（_consume_stream）。
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
            _raise_for_status(resp.status, raw)
        while True:
            if time.perf_counter() - started > max_seconds:
                raise ProviderTimeoutError(f"流式总时长超限（>{max_seconds}s）")
            line = resp.readline()
            if not line:
                return  # EOF：终止帧校验归消费方
            for item in _iter_sse_payloads([line.decode("utf-8", errors="replace")]):
                yield item
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
) -> tuple[str, dict[str, Any] | None]:
    """聚合 SSE 帧：返回 (content, usage_raw)；终止校验与错误裁决统一在此。

    - "[DONE]" 字面量视为优雅终止（仅 OpenAI 系协议发送，anthropic 不发）；
    - 协议终止帧由 terminal(frame) 判定；EOF 先于终止帧 => ProviderError（断连）；
    - 帧内 error 字段 => _raise_for_error_frame 映射标准错误族；
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
```

（d）`__all__` 不动（下划线私有，不导出）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_providers.py -v`
Expected: PASS（新 10 个测试 + 存量全绿）

- [ ] **Step 5: Commit**

```bash
git add providers/adapters.py tests/test_providers.py
git commit -m "feat(providers): SSE 流式共享读取器与帧聚合器——块间空闲+总上限双超时、断连/错误帧统一裁决"
```

---

### Task 3: OpenAIChatAdapter 流式分支 + 双层降级

**Files:**
- Modify: `providers/adapters.py:272-299`（`OpenAIChatAdapter.chat`）、`adapters.py:301-331` 之后新增 `_chat_via_stream`
- Test: `tests/test_providers.py`（文件末尾追加）

**Interfaces:**
- Consumes: Task 1 `ProviderConfig.stream`、`settings.PROVIDER_STREAM_MAX_SECONDS`；Task 2 `_http_post_sse` / `_consume_stream`。
- Produces: `OpenAIChatAdapter` 在 `provider.stream=True` 时自动流式聚合，`chat()` 返回值契约不变（`UnifiedChatResponse`）。

- [ ] **Step 1: 写失败测试**

`tests/test_providers.py` 末尾追加：

```python
# --------------------------------------------------------------------------- #
# OpenAIChatAdapter 流式分支
# --------------------------------------------------------------------------- #
def test_openai_chat_stream_aggregates(monkeypatch):
    provider = _provider(stream=True)
    frames = [
        json.dumps({"choices": [{"delta": {"content": '{"ok"'}}]}),
        json.dumps({"choices": [{"delta": {"content": ": 1}"}}]}),
        json.dumps(
            {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
        ),
        "[DONE]",
    ]
    captured: dict = {}

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        captured["url"] = url
        captured["payload"] = payload
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = OpenAIChatAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["stream_options"] == {"include_usage": True}
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert resp.content == '{"ok": 1}'
    assert resp.parsed_json == {"ok": 1}
    assert resp.usage.total_tokens == 5


def test_openai_chat_stream_degrades_without_stream_options(monkeypatch):
    # Review Focus #3：网关不认 stream_options -> 去参保流式重试
    provider = _provider(stream=True)
    frames = [json.dumps({"choices": [{"delta": {"content": '{"a": 1}'}}]}), "[DONE]"]
    calls: list[dict] = []

    def flaky_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        calls.append(dict(payload))
        if "stream_options" in payload:
            raise ProviderError(
                "模型服务返回 HTTP 400: stream_options not supported", code="provider_error"
            )
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", flaky_sse)
    adapter = OpenAIChatAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
    )
    assert "stream_options" not in calls[-1]
    assert calls[-1]["stream"] is True  # 保流式，仅去参数
    assert resp.parsed_json == {"a": 1}


def test_openai_chat_stream_falls_back_to_non_stream_on_400(monkeypatch):
    # Review Focus #4：网关整体拒绝 stream -> 回退非流式重试一次
    provider = _provider(stream=True)

    def reject_stream(url, *, payload, headers, timeout, max_seconds, api_key=None):
        raise ProviderError(
            "模型服务返回 HTTP 400: stream is not supported", code="provider_error"
        )

    monkeypatch.setattr("providers.adapters._http_post_sse", reject_stream)
    stub = _HttpStub(body={"choices": [{"message": {"content": '{"ok": 1}'}}]})
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIChatAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
    )
    assert stub.calls  # 确实走了非流式路径
    assert resp.parsed_json == {"ok": 1}


def test_openai_chat_stream_eof_propagates_without_fallback(monkeypatch):
    # 断连不是"网关拒绝流式"：不得触发非流式回退（避免重复计费长请求）
    provider = _provider(stream=True)

    def eof_stream(url, *, payload, headers, timeout, max_seconds, api_key=None):
        return iter([json.dumps({"choices": [{"delta": {"content": "half"}}]})])

    monkeypatch.setattr("providers.adapters._http_post_sse", eof_stream)
    stub = _HttpStub(body={"choices": [{"message": {"content": "{}"}}]})
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIChatAdapter(provider, "m-1")
    with pytest.raises(ProviderError):
        adapter.chat(
            UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
        )
    assert not stub.calls
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_providers.py -k "openai_chat_stream" -v`
Expected: FAIL（`_provider(stream=True)` 后仍走非流式路径，`captured["url"]` KeyError / 断言失败）

- [ ] **Step 3: 最小实现**

`OpenAIChatAdapter.chat`（`providers/adapters.py:272` 起）整体替换为：

```python
    def chat(self, request: UnifiedChatRequest) -> UnifiedChatResponse:
        """发送 Chat Completions 请求并解析 choices[0].message.content。

        provider.stream=True 时走 SSE 流式聚合（超时语义=块间空闲+总上限）；
        网关拒绝流式（HTTP 400 且报文含 stream/JSON 参数特征）时自动回退
        非流式重试一次；流中途断连不属于"拒绝流式"，原样上抛不回退。
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
                fallback = (
                    exc.code == "provider_error"
                    and "HTTP 400" in str(exc)
                    and any(
                        h in str(exc).lower()
                        for h in ("stream_options", "stream", *_UNSUPPORTED_JSON_HINTS)
                    )
                )
                if not fallback:
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
                    url, payload=payload, headers=headers, timeout=timeout, max_seconds=max_seconds
                ),
                extract_delta=_delta,
                terminal=lambda f: False,  # 终止帧 = [DONE]，_consume_stream 内建处理
                extract_usage=lambda f: f.get("usage"),
            )
        except ProviderError as exc:
            if "stream_options" not in str(exc).lower():
                raise
            payload.pop("stream_options", None)
            logger.info("网关不认 stream_options，去参数保流式重试")
            content, usage = _consume_stream(
                _http_post_sse(
                    url, payload=payload, headers=headers, timeout=timeout, max_seconds=max_seconds
                ),
                extract_delta=_delta,
                terminal=lambda f: False,
                extract_usage=lambda f: f.get("usage"),
            )
        return self._build_response(content, usage, json_mode=want_json)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_providers.py -v`
Expected: PASS（全文件）

- [ ] **Step 5: Commit**

```bash
git add providers/adapters.py tests/test_providers.py
git commit -m "feat(providers): OpenAIChatAdapter 流式分支——双层降级（去 stream_options / 回退非流式）"
```

---

### Task 4: OpenAIResponsesAdapter 流式分支 + 回退

**Files:**
- Modify: `providers/adapters.py:394-429`（`OpenAIResponsesAdapter.chat`）+ 其后新增 `_chat_via_stream`
- Test: `tests/test_providers.py`（文件末尾追加）

**Interfaces:**
- Consumes: 同 Task 3。
- Produces: `OpenAIResponsesAdapter` 在 `provider.stream=True` 时自动流式，`chat()` 契约不变。

- [ ] **Step 1: 写失败测试**

`tests/test_providers.py` 末尾追加：

```python
# --------------------------------------------------------------------------- #
# OpenAIResponsesAdapter 流式分支
# --------------------------------------------------------------------------- #
def test_openai_responses_stream_aggregates(monkeypatch):
    provider = _provider(protocol="openai_responses", stream=True)
    frames = [
        json.dumps({"type": "response.output_text.delta", "delta": '{"r": '}),
        json.dumps({"type": "response.output_text.delta", "delta": "2}"}),
        json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
                },
            }
        ),
    ]
    captured: dict = {}

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        captured["url"] = url
        captured["payload"] = payload
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = OpenAIResponsesAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert captured["url"].endswith("/responses")
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["text"]["format"] == {"type": "json_object"}
    assert resp.content == '{"r": 2}'
    assert resp.parsed_json == {"r": 2}
    assert resp.usage.total_tokens == 3


def test_openai_responses_stream_falls_back_to_non_stream_on_400(monkeypatch):
    provider = _provider(protocol="openai_responses", stream=True)

    def reject_stream(url, *, payload, headers, timeout, max_seconds, api_key=None):
        raise ProviderError(
            "模型服务返回 HTTP 400: streaming not supported here", code="provider_error"
        )

    monkeypatch.setattr("providers.adapters._http_post_sse", reject_stream)
    stub = _HttpStub(
        body={
            "output": [{"content": [{"type": "output_text", "text": '{"r": 2}'}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    )
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIResponsesAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
    )
    assert stub.calls
    assert "stream" not in stub.calls[0][1]  # 回退请求不带 stream 参数
    assert resp.parsed_json == {"r": 2}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_providers.py -k "openai_responses_stream" -v`
Expected: FAIL

- [ ] **Step 3: 最小实现**

`OpenAIResponsesAdapter.chat`（`providers/adapters.py:394` 起）整体替换为：

```python
    def chat(self, request: UnifiedChatRequest) -> UnifiedChatResponse:
        """按 /responses 规范组装请求并解析 output 文本。

        provider.stream=True 时走 SSE 流式聚合；网关拒绝流式（HTTP 400 且
        报文含 stream 特征）时回退非流式重试一次。
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
                if not (
                    exc.code == "provider_error"
                    and "HTTP 400" in str(exc)
                    and "stream" in str(exc).lower()
                ):
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
        content, usage = _consume_stream(
            _http_post_sse(
                url,
                payload=payload,
                headers=self._build_headers(),
                timeout=timeout,
                max_seconds=settings.PROVIDER_STREAM_MAX_SECONDS,
            ),
            extract_delta=lambda f: (f.get("delta") or "")
            if f.get("type") == "response.output_text.delta"
            else "",
            terminal=lambda f: f.get("type") == "response.completed",
            extract_usage=lambda f: (f.get("response") or {}).get("usage"),
        )
        return self._build_response(content, usage, json_mode=want_json)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_providers.py -v`
Expected: PASS（全文件）

- [ ] **Step 5: Commit**

```bash
git add providers/adapters.py tests/test_providers.py
git commit -m "feat(providers): OpenAIResponsesAdapter 流式分支——output_text.delta 聚合与 400 回退"
```

---

### Task 5: AnthropicAdapter 流式分支 + 回退

**Files:**
- Modify: `providers/adapters.py:525-586`（`AnthropicAdapter.chat`）+ 其后新增 `_chat_via_stream`
- Test: `tests/test_providers.py`（文件末尾追加）

**Interfaces:**
- Consumes: 同 Task 3。
- Produces: `AnthropicAdapter` 在 `provider.stream=True` 时自动流式，`chat()` 契约不变；usage 由 `message_start`（input）与 `message_delta`（output）合并。

- [ ] **Step 1: 写失败测试**

`tests/test_providers.py` 末尾追加：

```python
# --------------------------------------------------------------------------- #
# AnthropicAdapter 流式分支
# --------------------------------------------------------------------------- #
def test_anthropic_stream_aggregates_and_merges_usage(monkeypatch):
    provider = _provider(protocol="anthropic", stream=True)
    frames = [
        json.dumps({"type": "message_start", "message": {"usage": {"input_tokens": 4}}}),
        json.dumps({"type": "content_block_delta", "delta": {"text": '{"a": '}}),
        json.dumps({"type": "content_block_delta", "delta": {"text": "1}"}}),
        json.dumps({"type": "message_delta", "usage": {"output_tokens": 6}}),
        json.dumps({"type": "message_stop"}),
    ]
    captured: dict = {}

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        captured["url"] = url
        captured["payload"] = payload
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = AnthropicAdapter(provider, "claude-x")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "system", "content": "你是助手"}, {"role": "user", "content": "hi"}],
            model="claude-x",
            response_format={"type": "json_object"},
        )
    )
    assert captured["url"].endswith("/v1/messages")
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["system"].startswith("你是助手")  # system 顶级字段照旧
    assert resp.content == '{"a": 1}'
    assert resp.parsed_json == {"a": 1}
    assert resp.usage.prompt_tokens == 4
    assert resp.usage.completion_tokens == 6
    assert resp.usage.total_tokens == 10


def test_anthropic_stream_falls_back_to_non_stream_on_400(monkeypatch):
    provider = _provider(protocol="anthropic", stream=True)

    def reject_stream(url, *, payload, headers, timeout, max_seconds, api_key=None):
        raise ProviderError(
            "模型服务返回 HTTP 400: streaming is not supported", code="provider_error"
        )

    monkeypatch.setattr("providers.adapters._http_post_sse", reject_stream)
    stub = _HttpStub(
        body={
            "content": [{"type": "text", "text": '{"a": 1}'}],
            "usage": {"input_tokens": 4, "output_tokens": 6},
        }
    )
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = AnthropicAdapter(provider, "claude-x")
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="claude-x")
    )
    assert stub.calls
    assert "stream" not in stub.calls[0][1]
    assert resp.parsed_json == {"a": 1}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_providers.py -k "anthropic_stream" -v`
Expected: FAIL

- [ ] **Step 3: 最小实现**

`AnthropicAdapter.chat`（`providers/adapters.py:525` 起）：在现有实现中，`timeout = request.timeout or settings.PROVIDER_TIMEOUT`（第 554 行）之后、`status, _, raw = _http_post(...)`（第 555 行）之前插入流式分支：

```python
        if self.provider.stream:
            try:
                return self._chat_via_stream(payload, want_json, timeout)
            except ProviderError as exc:
                if not (
                    exc.code == "provider_error"
                    and "HTTP 400" in str(exc)
                    and "stream" in str(exc).lower()
                ):
                    raise
                logger.info("网关拒绝流式请求，回退非流式重试: %s", _brief(str(exc)))
```

并在类内（`chat` 之后）新增：

```python
    def _chat_via_stream(
        self, payload_base: dict[str, Any], want_json: bool, timeout: int
    ) -> UnifiedChatResponse:
        """SSE 流式调用：delta 取 content_block_delta，usage 由 message_start/message_delta 合并。"""
        from config import settings

        url = f"{self.provider.base_url.rstrip('/')}/v1/messages"
        payload = {**payload_base, "stream": True}
        usage_acc: dict[str, int] = {}

        def _extract_usage(frame: dict[str, Any]) -> dict[str, Any] | None:
            ftype = frame.get("type")
            if ftype == "message_start":
                start = ((frame.get("message") or {}).get("usage") or {})
                usage_acc["input_tokens"] = int(start.get("input_tokens") or 0)
            elif ftype == "message_delta":
                delta_usage = frame.get("usage") or {}
                usage_acc["output_tokens"] = int(delta_usage.get("output_tokens") or 0)
            return dict(usage_acc) if usage_acc else None

        content, usage = _consume_stream(
            _http_post_sse(
                url,
                payload=payload,
                headers=self._build_headers(),
                timeout=timeout,
                max_seconds=settings.PROVIDER_STREAM_MAX_SECONDS,
            ),
            extract_delta=lambda f: (f.get("delta") or {}).get("text") or ""
            if f.get("type") == "content_block_delta"
            else "",
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_providers.py -v`
Expected: PASS（全文件）

- [ ] **Step 5: Commit**

```bash
git add providers/adapters.py tests/test_providers.py
git commit -m "feat(providers): AnthropicAdapter 流式分支——content_block_delta 聚合与 usage 两帧合并"
```

---

### Task 6: store 加载跳过非法条目 + Gemini 协议整体移除

**Files:**
- Modify: `providers/models.py:27`（删 GEMINI 枚举成员）
- Modify: `providers/adapters.py:619-719`（删 GeminiAdapter 类）、`adapters.py:729`（工厂表删 GEMINI 行）、`adapters.py:745`（`__all__` 删 GeminiAdapter）、`adapters.py:7-12`（模块 docstring 删 gemini 行）
- Modify: `providers/__init__.py:18`（删 import）、`providers/__init__.py:122`（删 `__all__` 条目）
- Modify: `providers/store.py:113-115`（加载逐条校验跳过非法条目）、`providers/store.py:21-28`（补 `import logging` + logger）
- Modify: `web/providers_api.py:13,67,135`（docstring 措辞去 gemini）
- Test: `tests/test_providers.py`（删 GeminiAdapter import 与 2 个 gemini 测试，新增跳过条目测试）

**Interfaces:**
- Produces: `ApiProtocol` 仅含 openai_chat / openai_responses / anthropic；`ProviderStore._load` 对非法条目（未知协议、契约校验失败）跳过并 warning，不拒载。

- [ ] **Step 1: 写失败测试**

（a）删除 `tests/test_providers.py` 中：第 25 行 `GeminiAdapter,` import、`test_gemini_payload_and_url`（402 行起）、`test_gemini_chat_timeout_override`（813 行起）。

（b）`tests/test_providers.py` 末尾追加：

```python
# --------------------------------------------------------------------------- #
# store 加载防毒 + gemini 移除回归
# --------------------------------------------------------------------------- #
def test_store_skips_invalid_protocol_entry_with_warning(tmp_path, caplog):
    # Review Focus #5：存量文件含 gemini 等未知协议条目 -> 跳过并告警，不拒载
    import logging

    raw = [
        {
            "id": "bad",
            "name": "坏条目",
            "protocol": "gemini",
            "base_url": "https://x.example.com",
        },
        {
            "id": "ok",
            "name": "好条目",
            "protocol": "openai_chat",
            "base_url": "https://y.example.com",
        },
    ]
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="providers.store"):
        store = ProviderStore(path)
    assert [p.id for p in store.list_providers()] == ["ok"]


def test_gemini_protocol_removed_from_contract():
    from providers.models import ApiProtocol

    assert "GEMINI" not in ApiProtocol.__members__
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        _provider(protocol="gemini")


def test_gemini_adapter_removed_from_package():
    import providers

    assert not hasattr(providers, "GeminiAdapter")
    assert "GeminiAdapter" not in providers.__all__
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_providers.py -k "skips_invalid or gemini_protocol_removed or gemini_adapter_removed" -v`
Expected: FAIL（`GEMINI` 仍在枚举 / store 拒载抛 ValidationError）

- [ ] **Step 3: 最小实现**

（a）`providers/models.py` 删除第 27 行 `GEMINI = "gemini" ...`；

（b）`providers/adapters.py`：删除整个 `GeminiAdapter` 类（619-719 行，含注释横幅）、工厂表 `ApiProtocol.GEMINI: GeminiAdapter,` 行、`__all__` 中 `"GeminiAdapter",`；模块 docstring 删除 `- ``GeminiAdapter``：POST {baseUrl}/v1beta/models/{model}:generateContent。` 行与 JSON Mode 说明中 "` / gemini 的 ``responseMimeType``" 片段；

（c）`providers/__init__.py`：删 import 块中 `GeminiAdapter,` 与 `__all__` 中 `"GeminiAdapter",`；

（d）`providers/store.py`：import 区加 `import logging`；`_load` 中第 113-115 行的字典推导替换为逐条校验：

```python
                loaded: dict[str, ProviderConfig] = {}
                for item in items:
                    try:
                        cfg = ProviderConfig.model_validate(item)
                    except ValidationError as exc:
                        # 加载防毒：单枚脏条目（未知协议/契约非法）跳过并告警，
                        # 不拒载整个文件（拒载会导致全部供应商不可用）
                        logging.getLogger(__name__).warning(
                            "跳过非法供应商条目 id=%s: %s", item.get("id"), exc
                        )
                        continue
                    loaded[cfg.id] = cfg
                self._providers = loaded
```

（e）`web/providers_api.py`：三处 docstring 措辞把 "（gemini 拒绝）" 等改为 "（gemini 协议已移除）" 或直接删除 gemini 字样，保持与枚举一致；

（f）确认无残留：`grep -rn "GEMINI\|GeminiAdapter" --include=*.py . | grep -v docs/` 输出为空（`providers/store.py:16,42,105` 的历史注释提及 Gemini 属于历史预置清理语境，改为 "含 OpenAI / Anthropic / 智谱等" 措辞消除歧义）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_providers.py -v`
Expected: PASS（全文件；gemini 旧测试已删，新 3 个测试绿）

- [ ] **Step 5: Commit**

```bash
git add providers/models.py providers/adapters.py providers/__init__.py providers/store.py web/providers_api.py tests/test_providers.py
git commit -m "refactor(providers): 移除 Gemini 协议——枚举/适配器/工厂/导出清理，store 加载跳过非法条目防毒"
```

---

### Task 7: 前端流式开关

**Files:**
- Modify: `web/static/index.html:244`（协议 form-row 之后插入）
- Modify: `web/static/app.js:313-338`（`openProvider`）、`app.js:340-357`（`openNewProvider`）、`app.js:294-311`（`renderProviderList` 徽标）、`app.js:413-423`（`collectForm`）

**Interfaces:**
- Consumes: Task 1 `ProviderConfig.stream`（`/api/settings/providers` 读写经 pydantic 自动透传，`public_view` 含 stream 字段）。
- Produces: 供应商表单可勾选"流式传输"，列表展示流式徽标。

- [ ] **Step 1: index.html 插入开关行**

`web/static/index.html` 第 244 行（协议 form-row 的 `</div>`）之后插入（复用 pf-enabled 的 switch 样式）：

```html
            <div class="form-row">
              <label>流式传输</label>
              <label class="switch" title="SSE 流式：网关对长请求整包读超时时开启">
                <input id="pf-stream" type="checkbox">
                <span class="slider"></span>
              </label>
            </div>
```

- [ ] **Step 2: app.js 四处接线**

（a）`openProvider` 内 `$("pf-protocol").value = p ? p.protocol : "openai_chat";`（323 行）之后加：

```js
    $("pf-stream").checked = p ? !!p.stream : false;
```

（b）`openNewProvider` 内 `$("pf-protocol").value = "openai_chat";`（349 行）之后加：

```js
    $("pf-stream").checked = false;
```

（c）`collectForm` 返回对象（415-422 行）中 `protocol` 行后加：

```js
      stream: $("pf-stream").checked,
```

（d）`renderProviderList` 徽标区（301 行 `if (!p.enabled)` 之后）加：

```js
      if (p.stream) { badge += "<span class='p-badge custom'>流式</span>"; }
```

- [ ] **Step 3: 验证**

Run: `python -m pytest -q`
Expected: 全绿（前端无自动化测试，启动 `python -m web.server 8000` 在设置面板人工确认：开关可勾选、保存后刷新回显、列表出现"流式"徽标）

- [ ] **Step 4: Commit**

```bash
git add web/static/index.html web/static/app.js
git commit -m "feat(web): 供应商表单流式开关——stream 字段读写回显与列表徽标"
```

---

### Task 8: 全量回归 + 真连通冒烟（手动）

**Files:**
- 无新文件；验证任务

- [ ] **Step 1: 全量门禁**

Run: `black --check . && ruff check . && python -m pytest -q`
Expected: 三项全绿（存量 755+ 基线 + 本计划新增测试）

- [ ] **Step 2: 上层零 diff 确认**

Run: `git diff --stat master...HEAD -- core/ agent/ semantic/ compiler/ exec/`
Expected: 输出为空（改造未越出 providers/config/web/tests 边界；设计文档提交除外）

- [ ] **Step 3: 真连通冒烟（手动，非 CI；需用户在场）**

在 `config/providers.json` 给 `aiaa` 供应商加 `"stream": true`（或前端开关），然后：

```bash
python -c "
from providers.factory import default_provider_factory
from providers import chat_text
a = default_provider_factory().default_adapter()
msgs = [{'role': 'user', 'content': '请只回复一个 JSON 对象：{\"ok\": 1}'}]
print(repr(chat_text(a, msgs)))
"
```

Expected: 连续 3 次调用均返回含 `{"ok": 1}` 的字符串、无 `ProviderTimeoutError`（对照改造前 2/3 超时基线）。随后用真实 Planner 提示词（`core.orchestrator.nodes.planner_prompt`）再跑 3 次确认。

- [ ] **Step 4: Commit（如冒烟发现配置文件需调整则一并提交）**

```bash
git status  # 确认工作区干净或仅剩预期变更
```
