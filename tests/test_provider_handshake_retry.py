"""LLM 流式握手期超时安全重试测试。

spec: docs/superpowers/specs/2026-10-05-llm-handshake-retry-design.md
覆盖：异常体系兼容性、配置默认值、指标计数器、握手期判定与独立预算、
mid-stream 禁重试、重试包装（成功 / 双失败 / 开关关闭 / 鉴权头一致）、
三协议适配器接入。
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from audit.metrics import MetricsRegistry
from config import settings
from providers.adapters import _http_post_sse
from providers.errors import (
    ERROR_MESSAGES,
    ProviderTimeoutError,
    StreamHandshakeRejected,
    StreamHandshakeTimeout,
    error_message,
)


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
    from providers.adapters import _stream_fallback_eligible

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
