"""Golden Dataset 端到端评测测试。"""

from __future__ import annotations

from eval.eval_runner import evaluate_all, evaluate_case, load_golden


def test_golden_dataset_has_expected_cases():
    cases = load_golden()
    # Gmall 电商模型重建：Q01-Q30 单轮 30 例（基础语义 / 进阶语义 / 时间代数 /
    # 写语句红线 / 交叉表多指标×多维度）+ 多轮对话序列 3 例（M1 省略指代继承 /
    # M2 下钻加维 / M3 三轮追问链）
    assert len(cases) == 31
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "用例 id 必须唯一"


def test_all_golden_cases_pass(conn):
    summary = evaluate_all(conn)
    assert summary.total == 31
    assert summary.failed == 0, [
        (r.id, r.error, r.dsl_ok, r.sql_ok, r.result_ok) for r in summary.reports
    ]
    # 断言分层（M-P0）：全量模式契约与快照分节都应全绿
    assert summary.contract_failed == 0
    assert summary.snapshot_failed == 0
    assert all(r.contract_ok and r.snapshot_ok for r in summary.reports)


def test_skip_snapshot_contract_regression(conn):
    """--skip-snapshot 快速契约回归：零数据依赖，不执行任何 SQL。

    契约断言全绿；快照断言标记跳过、不计失败；退出语义（failed=0）不受影响。
    """
    summary = evaluate_all(conn, skip_snapshot=True)
    assert summary.total == 31
    assert summary.failed == 0
    assert summary.contract_failed == 0
    assert summary.snapshot_failed == 0  # 跳过不计失败
    assert summary.passed == 31
    skipped = [r for r in summary.reports if r.snapshot_skipped]
    assert len(skipped) == 31
    assert all(not r.snapshot_ok and r.contract_ok for r in summary.reports)
    assert all(r.hash == "" for r in summary.reports)  # 未执行 SQL，无结果哈希


def test_contract_ok_fields_populated(conn):
    """单用例契约/快照分节字段随断言结果正确置位。"""
    item = load_golden()[0]
    report = evaluate_case(conn, item)
    assert report.contract_ok is True  # dsl_ok + sql_ok
    assert report.snapshot_ok is True  # result_ok


def test_multi_turn_cases_present():
    """多轮序列用例（整改指令2-4）已纳入 golden 且走真实会话链路。"""
    multi = [c for c in load_golden() if c.get("type") == "multi_turn"]
    # M3 为题库第 4 层三轮追问链（评审 [S-2]）：至少一条 >= 3 轮
    assert len(multi) == 3
    assert max(len(item["turns"]) for item in multi) >= 3, "必须存在 3 轮以上的追问链"
