"""Golden Dataset 端到端评测测试。"""

from __future__ import annotations

from eval.eval_runner import evaluate_all, load_golden


def test_golden_dataset_has_expected_cases():
    cases = load_golden()
    # 基础语义 14 例 + 进阶语义 5 例 + 时间代数补全 4 例（Q20 季度 / Q21 至今 /
    # Q22 按位配对 / Q23 时间主轴）+ 多轮对话序列 2 例（M1 省略指代继承 / M2 下钻加维）
    # + 十九期写语句红线 2 例（Q26/Q27 收口批次）+ 题库第 2/4 层 2 例
    # （Q28 交叉表多指标×多维度 / M3 三轮追问链，M7 评审 [S-2] 补全）
    assert len(cases) == 29
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "用例 id 必须唯一"


def test_all_golden_cases_pass(conn):
    summary = evaluate_all(conn)
    assert summary.total == 29
    assert summary.failed == 0, [
        (r.id, r.error, r.dsl_ok, r.sql_ok, r.result_ok) for r in summary.reports
    ]


def test_multi_turn_cases_present():
    """多轮序列用例（整改指令2-4）已纳入 golden 且走真实会话链路。"""
    multi = [c for c in load_golden() if c.get("type") == "multi_turn"]
    # M3 为题库第 4 层三轮追问链（评审 [S-2]）：至少一条 >= 3 轮
    assert len(multi) == 3
    assert max(len(item["turns"]) for item in multi) >= 3, "必须存在 3 轮以上的追问链"
