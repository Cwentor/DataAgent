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


def test_query_latency_aggregation():
    """#23：SQL 执行耗时按维度聚合，缺失 domain 落 (unspecified) 不丢样本。"""
    reg = MetricsRegistry()
    samples = [
        (10.0, "duckdb", "order_detail"),
        (20.0, "duckdb", "order_detail"),
        (100.0, "mysql", None),
        (5.0, "duckdb", "fact_page_view"),
    ]
    for duration, backend, domain in samples:
        reg.record_sql_exec(duration_ms=duration, backend=backend, domain=domain)
    ql = reg.snapshot()["query_latency"]
    assert ql["count"] == 4
    assert ql["p95"] == 100.0
    assert ql["by_domain"]["order_detail"]["count"] == 2
    assert ql["by_domain"]["(unspecified)"]["count"] == 1
    assert ql["by_backend"]["mysql"]["max"] == 100.0
    assert ql["by_backend"]["duckdb"]["p50"] == 10.0


def test_query_latency_empty_snapshot():
    """零打点时输出结构化零值（便于前端/告警消费）。"""
    ql = MetricsRegistry().snapshot()["query_latency"]
    assert ql["count"] == 0
    assert ql["p50"] is None
    assert ql["by_domain"] == {}
    assert ql["by_backend"] == {}
