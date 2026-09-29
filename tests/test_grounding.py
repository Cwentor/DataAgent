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


def test_markdown_list_numbers_are_not_flagged():
    """排查建议的 Markdown 序号（1. 2. 3. 4.）不得计入不可溯源。

    2026-09-29 线上实证：四段式报告的"业务假设与排查建议"小节通常
    4 条编号列表，序号被数字正则抠成业务数值 => 恰超阈值 >3 =>
    整份忠实报告被弃用。序号是结构标记，不是数据。
    """
    allowed = {1156943.73, 0.078}
    report = (
        "### 业务假设与排查建议\n"
        "1. 排查北京促销活动退出的影响；\n"
        "2. 关注客单价的下滑趋势；\n"
        "3. 核对支付成功率变化；\n"
        "4. 跟进重点省份的复购情况。\n"
    )
    assert grounding_review(report, allowed) == []


def test_chinese_list_numbers_are_not_flagged():
    """中文序号形态（1、2、）与行内编号同样排除。"""
    allowed = {0.078}
    report = "1、排查活动影响\n2、关注客单价\n3、核对支付成功率"
    assert grounding_review(report, allowed) == []


def test_summary_string_numbers_enter_whitelist():
    """summary 字符串值（findings/title 文本）里的数字必须入白名单。

    2026-09-29 线上实证：findings 叙述"增长 6.2%"里的数字不在数值
    字段中，LLM 忠实复述被误杀；title 被 Coder 误塞统计 dict 时其
    repr 里的分析结果数字同理。字符串数字来自确定性上游产物，
    不是 LLM 编造，必须可溯源。
    """
    from core.orchestrator.grounding import collect_allowed_values
    from core.orchestrator.state import AgentState, Artifact

    state = AgentState(
        session_id="g1",
        turn_id="t1",
        trace_id="tr1",
        user_query="x",
        artifacts=[
            Artifact(
                kind="summary",
                name="s1",
                payload={
                    "summary": {
                        "title": "驱动因子分解",
                        "findings": ["GMV 增长 6.2%，主要因子 [买家数] 贡献 62%"],
                        "metrics": {"baseline": 1230127.5},
                        "table": {},
                    }
                },
            )
        ],
    )
    allowed = collect_allowed_values(state, None)
    assert 6.2 in allowed and 0.062 in allowed  # "6.2%" 两种量纲候选
    assert 62.0 in allowed and 0.62 in allowed  # "62%" 两种量纲候选
    # 忠实复述 findings 的报告不再被误杀
    assert grounding_review("GMV 增长 6.2%，买家数贡献 62%。", allowed) == []
