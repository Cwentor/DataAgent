"""Golden Dataset 端到端评测测试。"""

from __future__ import annotations

from eval.eval_runner import evaluate_all, load_golden


def test_golden_dataset_has_expected_cases():
    cases = load_golden()
    # 基础语义 14 例 + 进阶语义 5 例 + 时间代数补全 4 例（Q20 季度 / Q21 至今 / Q22 按位配对 / Q23 时间主轴）
    assert len(cases) == 23
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "用例 id 必须唯一"


def test_all_golden_cases_pass(conn):
    summary = evaluate_all(conn)
    assert summary.total == 23
    assert summary.failed == 0, [
        (r.id, r.error, r.dsl_ok, r.sql_ok, r.result_ok) for r in summary.reports
    ]
