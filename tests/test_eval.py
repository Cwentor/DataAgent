"""Golden Dataset 端到端评测测试。"""

from __future__ import annotations

from eval.eval_runner import evaluate_all, load_golden


def test_golden_dataset_has_expected_cases():
    cases = load_golden()
    # 基础语义 14 例 + 进阶语义 5 例 + 时间代数补全 4 例（Q20 季度 / Q21 至今 /
    # Q22 按位配对 / Q23 时间主轴）+ 多轮对话序列 2 例（M1 省略指代继承 / M2 下钻加维）
    assert len(cases) == 25
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "用例 id 必须唯一"


def test_all_golden_cases_pass(conn):
    summary = evaluate_all(conn)
    assert summary.total == 25
    assert summary.failed == 0, [
        (r.id, r.error, r.dsl_ok, r.sql_ok, r.result_ok) for r in summary.reports
    ]


def test_multi_turn_cases_present():
    """多轮序列用例（整改指令2-4）已纳入 golden 且走真实会话链路。"""
    multi = [c for c in load_golden() if c.get("type") == "multi_turn"]
    assert len(multi) == 2
    for item in multi:
        assert item.get("turns") and len(item["turns"]) >= 2
        # 每轮都必须带 question/dsl/sql 三元组
        for turn in item["turns"]:
            for key in ("question", "dsl", "sql"):
                assert key in turn, f"{item['id']} 第 {key} 缺失"
