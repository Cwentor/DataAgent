"""慢案例回流评测候选生成测试（#23）。

覆盖：候选条目结构与 golden schema 对齐（SC 前缀 + 溯源 meta）、dsl/sql
缺失的案例跳过并留痕、limit 生效。全流程（提取 -> 生成 -> 人工复核 ->
并入 golden）的人工裁决环节不可自动化，由脚本契约（只生成候选）保证。
"""

from __future__ import annotations

from audit.record import AuditRecord
from scripts.slow_case_backfill import build_candidates


def _slow_case(request_id: str, latency: float) -> dict:
    return {
        "request_id": request_id,
        "prompt": f"问题{request_id}",
        "query_domain": "order_detail",
        "db_backend": "duckdb",
        "sql_latency_ms": latency,
        "row_count": 3,
        "created_at": "2026-11-02T00:00:00Z",
    }


def test_build_candidates_matches_golden_schema():
    """候选条目与 golden schema 同构（id/question/scenario/dsl/sql）+ 溯源 meta。"""
    report = {"slow_cases": [_slow_case("r1", 900.0)]}
    records = {
        "r1": AuditRecord(
            request_id="r1",
            prompt="问题r1",
            dsl={"metrics": [{"kind": "aggregate", "field": "split_total_amount", "agg": "sum"}]},
            sql="SELECT SUM(split_total_amount) FROM order_detail",
        )
    }
    candidates = build_candidates(report, records, limit=10)
    assert len(candidates) == 1
    entry = candidates[0]
    assert entry["id"] == "SC01"
    assert entry["question"] == "问题r1"
    assert entry["scenario"] == "slow-case-backfill"
    assert entry["dsl"]["metrics"][0]["field"] == "split_total_amount"
    assert "SUM(split_total_amount)" in entry["sql"]
    meta = entry["_slow_case_meta"]
    assert meta["audit_request_id"] == "r1"
    assert meta["sql_latency_ms"] == 900.0
    assert meta["db_backend"] == "duckdb"


def test_build_candidates_skips_missing_dsl_or_sql():
    """dsl/sql 缺失（探索层执行 / 历史脏数据）的慢案例跳过——无契约形态可锚定。"""
    report = {"slow_cases": [_slow_case("r1", 900.0), _slow_case("r2", 800.0)]}
    records = {
        # 无 dsl：无法锚定 DSL 形态
        "r1": AuditRecord(request_id="r1", prompt="问题r1", sql="SELECT 1"),
        # 无 sql：无法锚定 SQL 形态
        "r2": AuditRecord(request_id="r2", prompt="问题r2", dsl={"metrics": []}),
    }
    candidates = build_candidates(report, records, limit=10)
    assert candidates == []


def test_build_candidates_respects_limit():
    """limit 生效：最多生成 limit 条候选（贪多无益，人工复核有边际成本）。"""
    report = {"slow_cases": [_slow_case(f"r{i}", 900.0 - i) for i in range(5)]}
    records = {
        f"r{i}": AuditRecord(
            request_id=f"r{i}", prompt=f"问题r{i}", dsl={"metrics": []}, sql="SELECT 1"
        )
        for i in range(5)
    }
    candidates = build_candidates(report, records, limit=2)
    assert [c["id"] for c in candidates] == ["SC01", "SC02"]
    assert [c["_slow_case_meta"]["audit_request_id"] for c in candidates] == ["r0", "r1"]


def test_build_candidates_ids_are_sequential_after_skips():
    """跳过缺失 dsl 的案例后编号连续（SC 前缀与 golden Q 前缀区分）。"""
    report = {"slow_cases": [_slow_case("r1", 900.0), _slow_case("r2", 800.0)]}
    records = {
        "r2": AuditRecord(request_id="r2", prompt="问题r2", dsl={"metrics": []}, sql="SELECT 1")
    }
    candidates = build_candidates(report, records, limit=10)
    assert [c["id"] for c in candidates] == ["SC01"]
    assert candidates[0]["_slow_case_meta"]["audit_request_id"] == "r2"
