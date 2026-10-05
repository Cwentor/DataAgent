"""LLM 流式握手期超时安全重试测试。

spec: docs/superpowers/specs/2026-10-05-llm-handshake-retry-design.md
覆盖：异常体系兼容性、配置默认值、指标计数器、握手期判定与独立预算、
mid-stream 禁重试、重试包装（成功 / 双失败 / 开关关闭 / 鉴权头一致）、
三协议适配器接入。
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from audit.metrics import MetricsRegistry
from config import settings
from providers.adapters import (
    AnthropicAdapter,
    OpenAIChatAdapter,
    OpenAIResponsesAdapter,
    _http_post_sse,
    _stream_fallback_eligible,
)
from providers.errors import (
    ERROR_MESSAGES,
    ProviderTimeoutError,
    StreamHandshakeRejected,
    StreamHandshakeTimeout,
    error_message,
)
from providers.models import ProviderConfig, UnifiedChatRequest
from tests.fixture_keys import fake_key


# --------------------------------------------------------------------------- #
# 异常体系兼容性
# --------------------------------------------------------------------------- #
def test_stream_handshake_timeout_hierarchy():
    """专用异常：ProviderTimeoutError 子类 + code 仍为 timeout（上层捕获面零变化）。"""
    assert issubclass(StreamHandshakeTimeout, ProviderTimeoutError)
    exc = StreamHandshakeTimeout("握手期超时（未收到任何响应字节）: read timed out")
    assert exc.code == "timeout"
    assert error_message(exc) == ERROR_MESSAGES["timeout"]


def test_stream_handshake_timeout_not_stream_handshake_rejected():
    """不是 StreamHandshakeRejected 子类：回退资格判定恒 False，绝不误触发非流式重发。"""
    assert not issubclass(StreamHandshakeTimeout, StreamHandshakeRejected)


def test_stream_handshake_timeout_not_fallback_eligible():
    """_stream_fallback_eligible 对新异常恒为 False（含附加 hints 场景）。"""

    exc = StreamHandshakeTimeout("握手期超时（未收到任何响应字节）: read timed out")
    assert _stream_fallback_eligible(exc) is False
    assert _stream_fallback_eligible(exc, "stream_options") is False


# --------------------------------------------------------------------------- #
# 配置默认值
# --------------------------------------------------------------------------- #
def test_handshake_settings_defaults():
    """两项新配置默认值：握手预算 30s、安全重试 1 次。"""
    assert settings.PROVIDER_HANDSHAKE_TIMEOUT == 30
    assert settings.PROVIDER_HANDSHAKE_RETRY_MAX == 1


# --------------------------------------------------------------------------- #
# 指标计数器
# --------------------------------------------------------------------------- #
def test_record_llm_handshake_and_snapshot():
    """record_llm_handshake 锁内计数 + snapshot 以 llm_handshake 键导出。"""
    reg = MetricsRegistry()
    reg.record_llm_handshake("handshake_timeout")
    reg.record_llm_handshake("retry_success")
    reg.record_llm_handshake("retry_success")
    reg.record_llm_handshake("retry_fail")
    assert reg.snapshot()["llm_handshake"] == {
        "handshake_timeout": 1,
        "retry_success": 2,
        "retry_fail": 1,
    }


# --------------------------------------------------------------------------- #
# _http_post_sse 握手期判定与独立预算
# --------------------------------------------------------------------------- #
def _make_conn(*, lines=None, handshake_error=False, read_error=False):
    """工厂生成连接测试桩：每测试独立状态（类属性桩会跨测试污染）。

    - handshake_error=True：getresponse 抛 TimeoutError（等待响应头挂起 => 握手期形态）；
    - read_error=True：getresponse 正常返回 200，但 readline 抛 TimeoutError
      （响应头已到达 => mid-stream 形态）；
    - created_timeouts 记录构造时收到的 timeout 实参（预算 min 语义断言用）。
    """

    class Conn:
        created_timeouts: ClassVar[list] = []

        def __init__(self, host, port, timeout=None):
            Conn.created_timeouts.append(timeout)

        def request(self, method, path, body=None, headers=None):
            pass

        def getresponse(self):
            if handshake_error:
                raise TimeoutError("read timed out")
            resp_lines = list(lines or [])

            class Resp:
                status = 200

                def readline(self, limit=-1):
                    if read_error:
                        raise TimeoutError("read timed out")
                    if not resp_lines:
                        return b""
                    return resp_lines.pop(0).encode("utf-8")

                def read(self):
                    return b'{"error": "bad request"}'

            return Resp()

        def close(self):
            pass

    return Conn


def test_handshake_getresponse_timeout_raises_dedicated_type(monkeypatch):
    """getresponse 阶段挂起（未收到任何响应头字节）=> StreamHandshakeTimeout。"""
    conn_cls = _make_conn(handshake_error=True)
    monkeypatch.setattr("http.client.HTTPSConnection", conn_cls)
    with pytest.raises(StreamHandshakeTimeout) as ei:
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=30
            )
        )
    assert "握手期超时" in str(ei.value)


def test_readline_timeout_after_response_header_stays_mid_stream(monkeypatch):
    """响应头已到达后 readline 超时 => mid-stream：ProviderTimeoutError 本类（非子类实例）。"""
    conn_cls = _make_conn(read_error=True)
    monkeypatch.setattr("http.client.HTTPSConnection", conn_cls)
    with pytest.raises(ProviderTimeoutError) as ei:
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=30
            )
        )
    assert type(ei.value) is ProviderTimeoutError


def test_handshake_budget_min_of_caller_timeout(monkeypatch):
    """握手预算 = min(配置 100, 调用方 timeout 5, 总预算 300) => 构造连接收到 5。"""
    monkeypatch.setattr("config.settings.PROVIDER_HANDSHAKE_TIMEOUT", 100)
    conn_cls = _make_conn(handshake_error=True)
    monkeypatch.setattr("http.client.HTTPSConnection", conn_cls)
    with pytest.raises(StreamHandshakeTimeout):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=300
            )
        )
    assert conn_cls.created_timeouts == [5]


def test_handshake_budget_clamps_nonpositive_config(monkeypatch):
    """PROVIDER_HANDSHAKE_TIMEOUT=0 非法 => 运行时钳制为 1s，不崩溃。"""
    monkeypatch.setattr("config.settings.PROVIDER_HANDSHAKE_TIMEOUT", 0)
    conn_cls = _make_conn(handshake_error=True)
    monkeypatch.setattr("http.client.HTTPSConnection", conn_cls)
    with pytest.raises(StreamHandshakeTimeout):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=60, max_seconds=300
            )
        )
    assert conn_cls.created_timeouts == [1]


# --------------------------------------------------------------------------- #
# 重试包装与三协议适配器接入
# --------------------------------------------------------------------------- #
def _provider(**overrides) -> ProviderConfig:
    """供应商配置测试基座（抄自 tests/test_providers.py 同名 helper）。"""
    base = {
        "id": "p1",
        "name": "测试站",
        "is_preset": False,
        "enabled": True,
        "base_url": "https://gw.example.com/v1",
        "api_key": fake_key("gw"),
        "protocol": "openai_chat",
        "models": [{"id": "m-1", "name": "M1"}],
    }
    base.update(overrides)
    return ProviderConfig.model_validate(base)


def _flaky_handshake_sse(calls: list, *, ok_frames: list):
    """构造"首次握手期超时、之后成功"的 _http_post_sse 替身工厂。

    每次调用把 (payload, headers, timeout, api_key) 快照进 calls，供
    "重试请求与首次逐字段一致"断言（鉴权头完整性专项，Review Focus #4）。
    """

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        calls.append(
            {"payload": payload, "headers": headers, "timeout": timeout, "api_key": api_key}
        )
        if len(calls) == 1:

            def gen_fail():
                raise StreamHandshakeTimeout("握手期超时（未收到任何响应字节）: read timed out")

                yield  # pragma: no cover —— 使其成为生成器函数，异常延迟到迭代时抛出

            return gen_fail()

        def gen_ok():
            yield from ok_frames

        return gen_ok()

    return fake_sse


def _midstream_timeout_sse(calls: list):
    """构造"首次即 mid-stream 超时"的替身：验证穿透不重试。"""

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        calls.append(
            {"payload": payload, "headers": headers, "timeout": timeout, "api_key": api_key}
        )

        def gen_fail():
            raise ProviderTimeoutError("请求超时: read timed out")

            yield  # pragma: no cover

        return gen_fail()

    return fake_sse


def _bind_metrics(monkeypatch) -> MetricsRegistry:
    """隔离进程级单例：重定向 providers.adapters 的 default_registry 到独立实例。"""
    reg = MetricsRegistry()
    monkeypatch.setattr("providers.adapters.default_registry", lambda: reg)
    return reg


_OPENAI_OK_FRAMES = [
    json.dumps({"choices": [{"delta": {"content": "ok"}}]}),
    "[DONE]",
]
_ANTHROPIC_OK_FRAMES = [
    json.dumps({"type": "content_block_delta", "delta": {"text": "ok"}}),
    json.dumps({"type": "message_stop"}),
]
_RESPONSES_OK_FRAMES = [
    json.dumps({"type": "response.output_text.delta", "delta": "ok"}),
    json.dumps({"type": "response.completed", "response": {}}),
]


def test_openai_chat_handshake_retry_recovers(monkeypatch):
    """openai_chat：首次握手超时 → 重试成功返回；两次请求逐字段一致；指标齐全。"""
    adapter = OpenAIChatAdapter(_provider(stream=True), "m-1")
    calls: list = []
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _flaky_handshake_sse(calls, ok_frames=_OPENAI_OK_FRAMES),
    )
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
    )
    assert resp.content == "ok"
    assert len(calls) == 2
    assert calls[0] == calls[1]  # 鉴权头/参数逐字段一致（Review Focus #4）
    # dict(Counter) 只含已打点键（未发生事件不以 0 导出，同 circuit_breakers 语义）
    assert reg.snapshot()["llm_handshake"] == {"handshake_timeout": 1, "retry_success": 1}


def test_openai_chat_double_handshake_failure_raises(monkeypatch):
    """两次握手超时 => 上抛 StreamHandshakeTimeout；指标 handshake_timeout=2 + retry_fail=1。"""
    adapter = OpenAIChatAdapter(_provider(stream=True), "m-1")
    calls: list = []

    def always_fail(url, *, payload, headers, timeout, max_seconds, api_key=None):
        calls.append(
            {"payload": payload, "headers": headers, "timeout": timeout, "api_key": api_key}
        )

        def gen_fail():
            raise StreamHandshakeTimeout("握手期超时（未收到任何响应字节）: read timed out")

            yield  # pragma: no cover

        return gen_fail()

    monkeypatch.setattr("providers.adapters._http_post_sse", always_fail)
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    with pytest.raises(StreamHandshakeTimeout):
        adapter.chat(UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1"))
    assert len(calls) == 2
    assert reg.snapshot()["llm_handshake"] == {"handshake_timeout": 2, "retry_fail": 1}


def test_openai_chat_mid_stream_timeout_no_retry(monkeypatch):
    """mid-stream 超时（响应头已到后挂起）=> 原样上抛、零重试、零 retry_* 计数。"""
    adapter = OpenAIChatAdapter(_provider(stream=True), "m-1")
    calls: list = []
    monkeypatch.setattr("providers.adapters._http_post_sse", _midstream_timeout_sse(calls))
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    with pytest.raises(ProviderTimeoutError):
        adapter.chat(UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1"))
    assert len(calls) == 1  # 铁律：mid-stream 禁止重试
    assert reg.snapshot()["llm_handshake"] == {}  # 零打点（未发生任何握手事件）


def test_openai_chat_retry_disabled(monkeypatch):
    """RETRY_MAX=0 关闭重试：首次握手超时即上抛，只打 handshake_timeout、无 retry_fail。"""
    monkeypatch.setattr("config.settings.PROVIDER_HANDSHAKE_RETRY_MAX", 0)
    adapter = OpenAIChatAdapter(_provider(stream=True), "m-1")
    calls: list = []
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _flaky_handshake_sse(calls, ok_frames=_OPENAI_OK_FRAMES),
    )
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    with pytest.raises(StreamHandshakeTimeout):
        adapter.chat(UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1"))
    assert len(calls) == 1
    assert reg.snapshot()["llm_handshake"] == {"handshake_timeout": 1}  # 从未重试 => 无 retry_fail


def test_anthropic_handshake_retry_recovers(monkeypatch):
    """anthropic 协议：握手重试同样生效（x-api-key 鉴权头一致性随 calls[0]==calls[1] 断言）。"""
    adapter = AnthropicAdapter(_provider(protocol="anthropic", stream=True), "m-1")
    calls: list = []
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _flaky_handshake_sse(calls, ok_frames=_ANTHROPIC_OK_FRAMES),
    )
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
    )
    assert resp.content == "ok"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert reg.snapshot()["llm_handshake"]["retry_success"] == 1


def test_responses_handshake_retry_recovers(monkeypatch):
    """responses 协议：握手重试同样生效。"""
    adapter = OpenAIResponsesAdapter(_provider(protocol="openai_responses", stream=True), "m-1")
    calls: list = []
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _flaky_handshake_sse(calls, ok_frames=_RESPONSES_OK_FRAMES),
    )
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
    )
    assert resp.content == "ok"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert reg.snapshot()["llm_handshake"]["retry_success"] == 1
