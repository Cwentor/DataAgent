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
