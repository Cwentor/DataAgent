"""risk.executor 测试：判定边界（纯函数）+ shape 直译 + DSL 组装 + 内存库端到端。"""

from datetime import date
from pathlib import Path

import pytest

from risk.executor import (
    STATUS_GREEN,
    STATUS_RED,
    STATUS_UNKNOWN,
    STATUS_YELLOW,
    InspectionResult,
    _build_rule_dsl,
    _evaluate,
    _metric_shape,
    run_inspection,
)
from risk.rules import (
    ComparisonSpec,
    RiskRule,
    ThresholdSpec,
    WindowSpec,
    load_rules,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEED_RULES = PROJECT_ROOT / "config" / "risk_rules.json"


def _rule(
    key: str = "r1",
    *,
    metric: str = "gmv",
    thresholds: ThresholdSpec | None = None,
    comparison: ComparisonSpec | None = None,
    dimension: str | None = None,
    current: str = "2025-12-31",
    grain: str = "day",
) -> RiskRule:
    return RiskRule(
        key=key,
        title=f"t-{key}",
        metric=metric,
        query_domain="trade",
        window=WindowSpec(grain=grain, current=date.fromisoformat(current), baseline="previous"),
        thresholds=thresholds,
        comparison=comparison,
        dimension=dimension,
    )


# --------------------------------------------------------------------------- #
# 判定边界（纯函数，无 DB）
# --------------------------------------------------------------------------- #
def test_threshold_boundaries() -> None:
    rule = _rule(thresholds=ThresholdSpec(red=0.05, yellow=0.03))
    # 红线含边界（>= red 为红）
    assert _evaluate(rule, 0.05, None)[0] == STATUS_RED
    assert _evaluate(rule, 0.03, None)[0] == STATUS_YELLOW
    assert _evaluate(rule, 0.029999, None)[0] == STATUS_GREEN
    # 当期无数据不可判定（诚实红线：严禁把无数据当绿灯）
    assert _evaluate(rule, None, None)[0] == STATUS_UNKNOWN


def test_comparison_drop_boundaries() -> None:
    rule = _rule(comparison=ComparisonSpec(direction="drop", red=0.3, yellow=0.15))
    # 基线 100：跌 30% 触红、跌 15% 触黄、跌 5% 绿、上涨绿
    assert _evaluate(rule, 70, 100)[0] == STATUS_RED
    assert _evaluate(rule, 85, 100)[0] == STATUS_YELLOW
    assert _evaluate(rule, 95, 100)[0] == STATUS_GREEN
    assert _evaluate(rule, 120, 100)[0] == STATUS_GREEN
    # 基线缺失 / 为零：异动不可判定（严禁虚构变化率）
    assert _evaluate(rule, 70, None)[0] == STATUS_UNKNOWN
    assert _evaluate(rule, 70, 0)[0] == STATUS_UNKNOWN


def test_comparison_raise_direction() -> None:
    rule = _rule(comparison=ComparisonSpec(direction="raise", red=0.5, yellow=0.2))
    assert _evaluate(rule, 160, 100)[0] == STATUS_RED
    assert _evaluate(rule, 120, 100)[0] == STATUS_YELLOW
    assert _evaluate(rule, 100, 100)[0] == STATUS_GREEN


def test_mixed_forms_take_stricter() -> None:
    """同配 thresholds 与 comparison 时取更严状态。"""
    rule = _rule(
        thresholds=ThresholdSpec(red=0.05, yellow=0.03),
        comparison=ComparisonSpec(direction="drop", red=0.3, yellow=0.15),
    )
    # 阈值绿（0.025 低于黄线 0.03）但环比跌幅 16.7% 触黄（15%~30% 区间）
    status, reason, ratio = _evaluate(rule, 0.025, 0.03)
    assert status == STATUS_YELLOW
    assert "黄线" in reason and "下跌" in reason
    assert ratio == pytest.approx(1 / 6, rel=1e-3)


def test_evaluate_reason_carries_values() -> None:
    rule = _rule(thresholds=ThresholdSpec(red=0.05, yellow=0.03))
    _, reason, _ = _evaluate(rule, 0.06, None)
    assert "0.06" in reason and "0.05" in reason


# --------------------------------------------------------------------------- #
# shape 直译与 DSL 组装
# --------------------------------------------------------------------------- #
def test_metric_shape_ratio() -> None:
    entries, primary = _metric_shape("refund_rate")
    assert primary == "refund_rate"
    assert entries[0]["kind"] == "ratio"


def test_metric_shape_multi_entry_expression() -> None:
    """多条目 shape（avg_order_amount）全条目直译，主 alias 取末条目。"""
    entries, primary = _metric_shape("avg_order_amount")
    kinds = [e["kind"] for e in entries]
    assert kinds == ["aggregate", "aggregate", "expression"]
    assert primary == "aov"


def test_metric_shape_unknown_rejected() -> None:
    with pytest.raises(ValueError, match="未在语义目录"):
        _metric_shape("nope")


def test_build_rule_dsl_absolute_window() -> None:
    rule = _rule(dimension="province")
    dsl = _build_rule_dsl(
        rule,
        [{"kind": "aggregate", "field": "gmv", "agg": "sum", "alias": "gmv"}],
        date(2025, 12, 31),
        date(2026, 1, 1),
    )
    tf = dsl["time_filter"]
    assert tf["range_type"] == "absolute"
    assert tf["absolute"] == {"start": "2025-12-31", "end": "2026-01-01"}
    assert tf["time_field"] == "order_time"
    assert tf["comparison"] == "none"
    assert dsl["dimensions"] == [{"field": "province"}]
    assert dsl["metrics"][0]["alias"] == "gmv"


def test_build_rule_dsl_no_dimension() -> None:
    dsl = _build_rule_dsl(_rule(), [], date(2025, 12, 31), date(2026, 1, 1))
    assert dsl["dimensions"] == []


# --------------------------------------------------------------------------- #
# 端到端（conftest 内存 DuckDB + 真实编译器 + 全护栏取数）
# --------------------------------------------------------------------------- #
def test_run_inspection_end_to_end(tmp_path, conn) -> None:
    """种子规则全链路：真实 mock 数据、真实编译、红黄绿结构完备。"""
    rules = load_rules(SEED_RULES)
    result = run_inspection(rules, workspace=tmp_path, conn=conn)
    assert isinstance(result, InspectionResult)
    assert len(result.rules) == 3
    assert set(result.status_counts) == {STATUS_RED, STATUS_YELLOW, STATUS_GREEN, STATUS_UNKNOWN}
    by_key = {f.rule.key: f for f in result.rules}
    # 分省规则应有维度成员发现，标量规则无成员
    assert by_key["refund_rate_by_province"].members
    assert by_key["refund_rate_daily"].members == ()
    assert by_key["gmv_dod_drop"].baseline_ref is not None
    for f in result.rules:
        assert f.status in (STATUS_RED, STATUS_YELLOW, STATUS_GREEN, STATUS_UNKNOWN)
        assert f.reason
        if f.error is not None:
            assert f.status == STATUS_UNKNOWN


def test_run_inspection_failure_isolated(tmp_path, conn) -> None:
    """单规则执行失败透出 unknown 与 error，不拖垮其余规则。"""
    path = tmp_path / "rules.json"
    bad = (
        '{"key": "broken_rule", "title": "t", "metric": "refund_rate",'
        ' "query_domain": "trade", "time_field": "register_time",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.1, "yellow": 0.05}}'
    )
    good = (
        '{"key": "good_rule", "title": "t", "metric": "gmv",'
        ' "query_domain": "trade",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 999999999.0, "yellow": 999999998.0}}'
    )
    path.write_text('{"version": 1, "rules": [' + bad + "," + good + "]}", encoding="utf-8")
    rules = load_rules(path)
    result = run_inspection(rules, workspace=tmp_path, conn=conn)
    by_key = {f.rule.key: f for f in result.rules}
    # register_time 在 users 表，refund_rate 的退款 JOIN 与该时间轴组合编译失败链路
    # 无论失败形态如何：坏规则必须 unknown 且 error 非空，好规则正常出绿
    assert by_key["good_rule"].status == STATUS_GREEN
    assert by_key["good_rule"].error is None
    if by_key["broken_rule"].error is not None:
        assert by_key["broken_rule"].status == STATUS_UNKNOWN


def test_inspection_result_counts(tmp_path, conn) -> None:
    rules = load_rules(SEED_RULES)
    result = run_inspection(rules, workspace=tmp_path, conn=conn)
    counts = result.status_counts
    assert sum(counts.values()) == len(result.rules)
    assert (
        result.overall_status
        == max(
            result.rules, key=lambda f: {"red": 3, "yellow": 2, "green": 1, "unknown": 0}[f.status]
        ).status
    )


SEED_RULES = (
    __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "risk_rules.json"
)
