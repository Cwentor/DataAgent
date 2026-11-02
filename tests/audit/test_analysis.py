"""审计离线分析测试（#23）：查询耗时分组聚合与慢案例标注。

慢案例定义（issue #23 边界）：组内 sql_latency_ms > 组内历史 P95 的成功
执行记录，只标注、不建议、不诊断；分组样本不足 min_samples 时不标注
（样本不足时分位数无统计意义，严禁虚构显著性）。
"""

from __future__ import annotations

import duckdb
import pytest

from audit.analysis import query_latency_report


def _setup_db(tmp_path, rows: list[tuple]) -> str:
    """合成审计库：最小列集（分析只读 query_domain/db_backend/sql_latency_ms 等）。"""
    db = str(tmp_path / "audit.duckdb")
    conn = duckdb.connect(db)
    conn.execute(
        "CREATE TABLE audit_log ("
        "request_id VARCHAR, prompt VARCHAR, query_domain VARCHAR, db_backend VARCHAR, "
        "sql_latency_ms DOUBLE, row_count BIGINT, created_at VARCHAR)"
    )
    for row in rows:
        conn.execute("INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?, ?)", row)
    conn.close()
    return db


def _sample_rows(n: int, domain: str = "order_detail", backend: str = "duckdb") -> list[tuple]:
    """n 条等差耗时样本（1..n ms），request_id 可溯源。"""
    return [
        (f"req-{domain}-{i}", f"问题{i}", domain, backend, float(i), 10, "2026-11-02T00:00:00Z")
        for i in range(1, n + 1)
    ]


def test_query_latency_report_marks_slow_case(tmp_path):
    """样本充足组内超 P95 的执行被标注为慢案例，按耗时降序。"""
    rows = _sample_rows(20)
    # 追加一条同组超 P95 的执行（P95=20，999 远超）与一条未超的
    rows.append(
        ("req-slow", "慢查询问题", "order_detail", "duckdb", 999.0, 1, "2026-11-02T01:00:00Z")
    )
    rows.append(
        ("req-fast", "快查询问题", "order_detail", "duckdb", 20.0, 1, "2026-11-02T01:01:00Z")
    )
    db = _setup_db(tmp_path, rows)

    report = query_latency_report(db_path=db)
    assert report["total_executions"] == 22
    group = next(g for g in report["groups"] if g["query_domain"] == "order_detail")
    assert group["count"] == 22
    assert group["insufficient_sample"] is False
    # 快查询（=P95，非严格大于）不标注；只有严格超阈的执行进慢案例
    assert [c["request_id"] for c in report["slow_cases"]] == ["req-slow"]
    assert report["slow_cases"][0]["sql_latency_ms"] == 999.0
    assert report["slow_cases"][0]["prompt"] == "慢查询问题"


def test_query_latency_report_insufficient_sample_skips_marking(tmp_path):
    """分组样本不足 min_samples 时标记 insufficient_sample 且不产出慢案例。"""
    rows = _sample_rows(5)
    rows.append(("req-slow", "慢查询问题", "order_detail", "duckdb", 999.0, 1, "t"))
    db = _setup_db(tmp_path, rows)

    report = query_latency_report(db_path=db, min_samples=20)
    assert report["groups"][0]["insufficient_sample"] is True
    assert report["slow_cases"] == []


def test_query_latency_report_groups_by_domain_and_backend(tmp_path):
    """不同（domain, backend）组合独立分组、独立分位数与标注阈值。"""
    rows = _sample_rows(20, domain="order_detail", backend="duckdb")
    rows += _sample_rows(20, domain="fact_page_view", backend="mysql")
    # 两条组内慢案例：各超本组 P95
    rows.append(("req-slow-trade", "交易慢", "order_detail", "duckdb", 900.0, 1, "t1"))
    rows.append(("req-slow-traffic", "流量慢", "fact_page_view", "mysql", 500.0, 1, "t2"))
    db = _setup_db(tmp_path, rows)

    report = query_latency_report(db_path=db)
    assert {g["query_domain"] for g in report["groups"]} == {"order_detail", "fact_page_view"}
    assert all(g["db_backend"] in ("duckdb", "mysql") for g in report["groups"])
    assert {c["request_id"] for c in report["slow_cases"]} == {"req-slow-trade", "req-slow-traffic"}


def test_query_latency_report_missing_domain_lands_unspecified(tmp_path):
    """query_domain/db_backend 缺失（探索层 SQL 等）落 (unspecified)/(unknown) 不丢样本。"""
    rows = _sample_rows(21)
    rows.append(("req-explore", "探索查询", None, None, 15.0, 1, "t"))
    db = _setup_db(tmp_path, rows)

    report = query_latency_report(db_path=db)
    keys = {(g["query_domain"], g["db_backend"]) for g in report["groups"]}
    assert ("(unspecified)", "(unknown)") in keys


def test_query_latency_report_empty_sources(tmp_path):
    """库缺失 / 表缺失 / 零成功执行一律返回空报表（分析脚本可安全随意执行）。"""
    empty = query_latency_report(db_path=tmp_path / "missing.duckdb")
    assert empty == {"total_executions": 0, "min_samples": 20, "groups": [], "slow_cases": []}

    db = _setup_db(tmp_path, [])
    report = query_latency_report(db_path=db)
    assert report["total_executions"] == 0
    assert report["groups"] == []


def test_query_latency_report_conn_injection():
    """conn 注入（测试挂接）：无 audit_log 表时返回空报表而非报错。"""
    conn = duckdb.connect(":memory:")
    try:
        report = query_latency_report(conn=conn)
        assert report["total_executions"] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("min_samples,expect_marked", [(20, True), (25, False)])
def test_query_latency_report_min_samples_threshold(tmp_path, min_samples, expect_marked):
    """min_samples 参数生效：20 条正常样本垫底时离群点可标注，阈值抬高后被样本量门槛拦下。"""
    rows = _sample_rows(20)
    rows.append(("req-slow", "慢查询问题", "order_detail", "duckdb", 900.0, 1, "t"))
    db = _setup_db(tmp_path, rows)

    report = query_latency_report(db_path=db, min_samples=min_samples)
    group = report["groups"][0]
    assert group["insufficient_sample"] is (not expect_marked)
    assert bool(report["slow_cases"]) is expect_marked


def test_query_latency_report_single_outlier_absorbed_by_p95(tmp_path):
    """单条离群点被 P95 自包含吸收时不标注（统计诚实）。

    1 条离群需要至少 20 条正常样本垫底（>95% 分位）才能在 P95 上显形——
    这正是默认 min_samples=20 的依据；样本更少时严禁虚构离群显著性。
    """
    rows = _sample_rows(10)
    rows.append(("req-slow", "慢查询问题", "order_detail", "duckdb", 900.0, 1, "t"))
    db = _setup_db(tmp_path, rows)

    report = query_latency_report(db_path=db, min_samples=5)
    assert report["groups"][0]["insufficient_sample"] is False
    # P95 = 离群值自身（排序第 11 位），900 > 900 不成立 -> 无慢案例
    assert report["groups"][0]["p95"] == 900.0
    assert report["slow_cases"] == []
