"""Token 用量登记收口（#22）单元测试：挂点登记、维度归集、请求级收集器。"""

from __future__ import annotations

import pytest

from audit.metrics import default_registry
from providers.adapters import BaseAdapter
from providers.models import (
    ApiProtocol,
    ModelItem,
    ProviderConfig,
    UnifiedChatResponse,
    Usage,
)
from providers.usage import (
    UsageLedger,
    current_llm_call_role,
    llm_call_role,
    pop_usage_ledger,
    set_usage_ledger,
)


def _provider(stream: bool = False) -> ProviderConfig:
    """构造最小供应商配置（不发起真实 HTTP，chat 被假适配器覆写）。"""
    return ProviderConfig(
        id="aiaa",
        name="测试供应商",
        base_url="http://localhost:1/v1",
        protocol=ApiProtocol.OPENAI_CHAT,
        stream=stream,
        models=[ModelItem(id="doubao-pro")],
    )


class _FakeAdapter(BaseAdapter):
    """假适配器：chat 直接返回固定响应，用于触发 chat_text 挂点登记。"""

    def __init__(self, usage: Usage | None, stream: bool = False) -> None:
        super().__init__(_provider(stream), "doubao-pro")
        self._usage = usage

    def chat(self, request) -> UnifiedChatResponse:
        return UnifiedChatResponse(content="ok", usage=self._usage)

    def test_connection(self):
        raise AssertionError("本测试不触达探测路径")


def _snapshot_diff(before: dict, after: dict) -> dict:
    """取 llm_tokens 快照差值（进程级单例计数器的标准测法）。"""
    diff = {}
    for key in ("calls", "missing_usage", "prompt_tokens", "completion_tokens", "total_tokens"):
        diff[key] = after[key] - before[key]
    return diff


def _tokens() -> dict:
    return default_registry().snapshot()["llm_tokens"]


def test_chat_text_registers_usage_in_metrics():
    """chat_text 挂点登记：有 usage 时进内存聚合（每次真实调用各计一次）。"""
    adapter = _FakeAdapter(Usage(prompt_tokens=10, completion_tokens=2, total_tokens=12))
    before = _tokens()
    assert adapter.chat_text([{"role": "user", "content": "hi"}]) == "ok"
    assert adapter.chat_text([{"role": "user", "content": "hi"}]) == "ok"
    diff = _snapshot_diff(before, _tokens())
    assert diff["calls"] == 2
    assert diff["prompt_tokens"] == 20
    assert diff["completion_tokens"] == 4
    assert diff["total_tokens"] == 24
    assert diff["missing_usage"] == 0


def test_chat_text_missing_usage_counted_not_fabricated():
    """usage=None（流式 stream_options 被中转站剥除的典型场景）登记为缺失：
    计次数、token 不虚构。"""
    adapter = _FakeAdapter(None)
    before = _tokens()
    adapter.chat_text([{"role": "user", "content": "hi"}])
    diff = _snapshot_diff(before, _tokens())
    assert diff["calls"] == 1
    assert diff["missing_usage"] == 1
    assert diff["total_tokens"] == 0


def test_role_and_stream_dimensions():
    """role 上下文归集到 by_role；流式维度并入 by_model 键。"""
    adapter = _FakeAdapter(
        Usage(prompt_tokens=10, completion_tokens=2, total_tokens=12), stream=True
    )
    before = _tokens()
    with llm_call_role("orchestrator.planner"):
        adapter.chat_text([{"role": "user", "content": "hi"}])
    after = _tokens()
    assert _snapshot_diff(before, after)["calls"] == 1
    assert after["by_role"]["orchestrator.planner"]["calls"] == 1
    assert after["by_model"]["aiaa/doubao-pro/stream"]["calls"] == 1
    assert current_llm_call_role() == "unknown"


def test_unwrapped_call_falls_back_to_unknown_role():
    """未包裹 role 的调用落 unknown（诚实可见，不猜标签）。"""
    adapter = _FakeAdapter(Usage(prompt_tokens=10, completion_tokens=2, total_tokens=12))
    before = _tokens()
    adapter.chat_text([{"role": "user", "content": "hi"}])
    before_calls = before["by_role"].get("unknown", {}).get("calls", 0)
    assert _tokens()["by_role"]["unknown"]["calls"] == before_calls + 1


def test_llm_call_role_restored_on_exception():
    """role 包裹的异常路径恢复原值（防标签跨调用泄漏）。"""
    with pytest.raises(RuntimeError):
        with llm_call_role("agent.self_heal"):
            raise RuntimeError("boom")
    assert current_llm_call_role() == "unknown"


def test_ledger_collects_per_call_details():
    """请求级收集器：per-call 明细与汇总同源；pop 后不再收集。"""
    adapter = _FakeAdapter(Usage(prompt_tokens=10, completion_tokens=2, total_tokens=12))
    ledger = UsageLedger()
    set_usage_ledger(ledger)
    try:
        with llm_call_role("agent.nl2dsl"):
            adapter.chat_text([{"role": "user", "content": "hi"}])
        snap = ledger.snapshot()
        assert snap is not None
        assert snap["calls"] == 1
        assert snap["total_tokens"] == 12
        detail = snap["calls_detail"][0]
        assert detail["role"] == "agent.nl2dsl"
        assert detail["provider_id"] == "aiaa"
        assert detail["model_id"] == "doubao-pro"
        assert detail["streamed"] is False
        assert detail["missing"] is False
    finally:
        assert pop_usage_ledger() is ledger
    # pop 后新调用只进内存指标，不再进已取出的收集器
    adapter.chat_text([{"role": "user", "content": "hi"}])
    assert ledger.snapshot()["calls"] == 1


def test_ledger_empty_snapshot_is_none():
    """零调用返回 None：没有 LLM 调用与用量缺失是两回事，AuditRecord 不写空结构。"""
    ledger = UsageLedger()
    assert ledger.snapshot() is None


def test_ledger_missing_usage_aggregated():
    """收集器同样遵循不虚构：missing 明细只有次数与零 token。"""
    adapter = _FakeAdapter(None)
    ledger = UsageLedger()
    set_usage_ledger(ledger)
    try:
        adapter.chat_text([{"role": "user", "content": "hi"}])
        snap = ledger.snapshot()
        assert snap["calls"] == 1
        assert snap["missing_usage"] == 1
        assert snap["total_tokens"] == 0
        assert snap["calls_detail"][0]["missing"] is True
    finally:
        pop_usage_ledger()
