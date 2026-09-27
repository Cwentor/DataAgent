"""Grounding 数值溯源校验单测：量纲归一化容差、不可溯源检测（Review Focus 5）。"""

from core.orchestrator.grounding import collect_allowed_values, grounding_review
from core.orchestrator.state import AgentState


def test_report_values_within_tolerance_are_grounded():
    """千分位/万元/百分比/舍入均应溯源命中，不误报。"""
    allowed = {1156943.73, 0.078, 8.0}
    report = (
        "GMV 为 115.69 万元（1,156,943.73 元），环比 -7.8%；"
        "共覆盖 8 个省份，另一指标为 1,156,900。"
    )
    assert grounding_review(report, allowed) == []


def test_fabricated_value_is_flagged():
    """数据中完全不存在且无法折算的数值 => 不可溯源。"""
    allowed = {1156943.73}
    report = "GMV 为 115.69 万元，另据测算转化率高达 42.5%。"
    flagged = grounding_review(report, allowed)
    assert any("42.5" in t for t in flagged)
    assert not any("115.69" in t for t in flagged)


def test_collect_allowed_values_from_state():
    state = AgentState(user_query="x")
    state.datasets["s1"] = {"rows": 1, "columns": ["gmv"], "path": "s1.parquet"}
    # collect_allowed_values 对无 workspace 文件时容忍降级（返回空集不抛错）
    values = collect_allowed_values(state, workspace=None)
    assert isinstance(values, set)


def test_date_tokens_are_not_flagged():
    """报告中的日期（2024年5月 / 2024-05-01）不得计入不可溯源（终审 Important #4）。

    LLM 报告必然引用日期，日期 token 几乎从不在 allowed 集——不排除会
    造成系统性误报，触发"数据溯源提示"狼来了效应。
    """
    allowed = {1156943.73}
    report = (
        "2024年5月 GMV 为 115.69 万元（统计窗口 2024-05-01 至 2024-05-15），"
        "环比基准期为 2024 年 4 月。"
    )
    assert grounding_review(report, allowed) == []
