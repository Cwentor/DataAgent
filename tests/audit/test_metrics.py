"""可观测性指标（P0 / §4 项5）单元测试：打点聚合与快照。"""

from __future__ import annotations

from audit.metrics import MetricsRegistry


def test_snapshot_empty_registry():
    reg = MetricsRegistry()
    snap = reg.snapshot()
    assert snap["total_queries"] == 0
    assert snap["qps"] == 0.0
    assert snap["latency_ms"]["p50"] is None
    assert snap["latency_ms"]["p95"] is None


def test_record_query_aggregates():
    reg = MetricsRegistry()
    for i in range(10):
        reg.record_query(
            intent="text2sql",
            action="text2sql",
            latency_ms=100.0 + i,
            error=None,
            degraded=False,
            rewrites=0,
            clarify_filled=False,
        )
    reg.record_query(
        intent="text2sql",
        action="text2sql",
        latency_ms=500.0,
        error="查询超时，请缩小时间范围后重试",
        degraded=False,
        rewrites=0,
        clarify_filled=False,
        circuit_breaker="query_timeout",
    )
    snap = reg.snapshot()
    assert snap["total_queries"] == 11
    assert snap["errors"] == 1
    assert snap["error_rate"] > 0
    assert snap["intent_distribution"] == {"text2sql": 11}
    assert snap["circuit_breakers"] == {"query_timeout": 1}
    assert snap["latency_ms"]["p50"] == 105.0  # 11 个样本排序后索引 5
    assert snap["latency_ms"]["max"] == 500.0


def test_record_query_degraded_and_self_heal():
    reg = MetricsRegistry()
    reg.record_query(
        intent="text2sql",
        action="text2sql",
        latency_ms=50.0,
        error=None,
        degraded=True,
        rewrites=2,
        clarify_filled=False,
    )
    reg.record_self_heal_failure()
    snap = reg.snapshot()
    assert snap["degraded"] == 1
    assert snap["self_heal"]["success"] == 1
    assert snap["self_heal"]["failures"] == 1
    assert snap["self_heal"]["success_rate"] == 0.5


def test_clarify_trigger_counts():
    reg = MetricsRegistry()
    reg.record_query(
        intent="text2sql",
        action="text2sql",
        latency_ms=30.0,
        error=None,
        degraded=False,
        rewrites=0,
        clarify_filled=True,
    )
    assert reg.snapshot()["clarify_triggers"] == 1


def test_audit_write_failure_counter_visible_in_snapshot():
    """审计落库失败必须可观测（2026-10-27 审计 P2）。

    审计失败被设计为「不影响主链路」，代价是静默丢数据——畸形列 bug 曾因此
    潜伏数周（JSONL 有、库无）。计数进 /api/metrics 才能被运维告警发现。
    """
    reg = MetricsRegistry()
    assert reg.snapshot()["audit_write_failures"] == 0
    assert reg.record_audit_write_failure() == 1
    assert reg.record_audit_write_failure() == 2
    assert reg.snapshot()["audit_write_failures"] == 2


def test_llm_tokens_empty_snapshot_is_structured_zero():
    """零打点时 llm_tokens 输出结构化零值（便于前端/告警消费）。"""
    reg = MetricsRegistry()
    snap = reg.snapshot()["llm_tokens"]
    assert snap["calls"] == 0
    assert snap["missing_usage"] == 0
    assert snap["missing_rate"] is None
    assert snap["prompt_tokens"] == 0
    assert snap["completion_tokens"] == 0
    assert snap["total_tokens"] == 0
    assert snap["by_model"] == {}
    assert snap["by_role"] == {}


def test_record_llm_usage_aggregates_by_model_and_role():
    """token 按模型（流式维度并入键）与调用点归集，全局汇总一致。"""
    reg = MetricsRegistry()
    reg.record_llm_usage(
        role="orchestrator.planner",
        provider_id="aiaa",
        model_id="doubao-pro",
        streamed=True,
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        missing=False,
    )
    reg.record_llm_usage(
        role="agent.nl2dsl",
        provider_id="aiaa",
        model_id="doubao-pro",
        streamed=False,
        prompt_tokens=50,
        completion_tokens=10,
        total_tokens=60,
        missing=False,
    )
    snap = reg.snapshot()["llm_tokens"]
    assert snap["calls"] == 2
    assert snap["prompt_tokens"] == 150
    assert snap["completion_tokens"] == 30
    assert snap["total_tokens"] == 180
    assert snap["missing_usage"] == 0
    # 流式与非流式分键（同一模型两个维度单元）
    assert snap["by_model"]["aiaa/doubao-pro/stream"]["calls"] == 1
    assert snap["by_model"]["aiaa/doubao-pro/stream"]["total_tokens"] == 120
    assert snap["by_model"]["aiaa/doubao-pro/batch"]["total_tokens"] == 60
    assert snap["by_role"]["orchestrator.planner"]["total_tokens"] == 120
    assert snap["by_role"]["agent.nl2dsl"]["total_tokens"] == 60


def test_record_llm_usage_missing_not_fabricated():
    """usage 缺失（missing=True）只计次数不虚构 token（不虚构红线），丢失率可见。"""
    reg = MetricsRegistry()
    reg.record_llm_usage(
        role="orchestrator.planner",
        provider_id="aiaa",
        model_id="doubao-pro",
        streamed=True,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        missing=True,
    )
    reg.record_llm_usage(
        role="orchestrator.planner",
        provider_id="aiaa",
        model_id="doubao-pro",
        streamed=True,
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        missing=False,
    )
    snap = reg.snapshot()["llm_tokens"]
    assert snap["calls"] == 2
    assert snap["missing_usage"] == 1
    assert snap["missing_rate"] == 0.5
    # 缺失调用的 token 不计入（严禁估算补齐）
    assert snap["total_tokens"] == 120
    cell = snap["by_role"]["orchestrator.planner"]
    assert cell["calls"] == 2
    assert cell["missing_usage"] == 1
    assert cell["total_tokens"] == 120
