"""LLM 流式握手期超时安全重试测试。

spec: docs/superpowers/specs/2026-10-05-llm-handshake-retry-design.md
覆盖：异常体系兼容性、配置默认值、指标计数器、握手期判定与独立预算、
mid-stream 禁重试、重试包装（成功 / 双失败 / 开关关闭 / 鉴权头一致）、
三协议适配器接入。
"""

from __future__ import annotations

from audit.metrics import MetricsRegistry
from config import settings
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
