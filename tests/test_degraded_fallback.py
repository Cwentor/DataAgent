"""分层降级兜底（2026-09）：UNKNOWN 二次判定与弱解析的回归锚点。

- 指标锚存在但模板不可拆 => 弱解析（plan/clarify），不再直接拒答；
- 无指标锚/跨表混合锚 => NOT_EXIST 拒答（宁拒不错）；
- 筛选条件仅来自显式时间/用户确认/枚举值精确命中，严禁猜测。

锚点验证记录（以 classify_intent 真实行为为准，详见 .plan-work 执行报告）：
- 复合问句（指标锚+维度限定共存）判 UNKNOWN 且 ``anchor_fields`` 为空
  （intent.py 契约），锚点由 ``_degraded_parse`` 内按同一词表确定性重提取；
- "西藏"不在维度词表（会被判 METRIC_SCALAR、无维度锚），澄清用例改用
  "西藏省的GMV"（"省"命中 province 锚、"西藏"未命中枚举）达成同一断言意图；
- ``parse_explicit_time_window`` 返回 [start, end) 排他上界
  （"2024年3月1日到3月31日" => end=2024-04-01）；
- "GMV趋势"被 classify_intent 判为 METRIC_SCALAR 而非 UNKNOWN（不影响本
  函数——``_degraded_parse`` 只吃锚点画像不判 intent 字段），断言依然成立。
"""

from __future__ import annotations

from core.orchestrator.intent import classify_intent
from core.orchestrator.nodes import _degraded_parse

# 模拟 profiling 枚举（低基数字段真实取值）
ENUMS = {"province": ["北京", "上海", "海南", "广东"], "category": ["服饰", "数码"]}


def test_compound_query_with_enum_hit_yields_plan():
    """复合问句"海南省的GMV"（指标+维度锚）=> 枚举命中唯一 => 降级计划。"""
    profile = classify_intent("海南省的GMV")
    mode, payload = _degraded_parse("海南省的GMV", profile, ENUMS)
    assert mode == "plan"
    s1, s2 = payload
    assert s1.kind == "query" and s1.dsl is not None
    assert s2.kind == "synthesize" and s2.depends_on == ["s1"]
    dsl = s1.dsl
    assert dsl["metrics"][0]["field"] == "order_amount"
    assert {"field": "province", "operator": "eq", "value": "海南"} in dsl["filters"]
    assert dsl["dimensions"] == []  # 筛选形态，非分组


def test_grouping_form_yields_dimensions():
    """ "各省份的GMV" => 分组用法（各+别名）=> dimensions 分组、无筛选。"""
    query = "各省份的GMV"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "plan"
    assert payload[0].dsl["dimensions"] == [{"field": "province"}]
    assert all(f["field"] != "province" for f in payload[0].dsl["filters"])


def test_enum_miss_asks_for_clarification():
    """取值未命中枚举（数仓没有"西藏"）=> clarify 留白确认，严禁猜条件。

    措辞按 classify_intent 真实锚点修正："西藏的GMV"会因"西藏"不在维度
    词表而判 METRIC_SCALAR（无维度锚）；"西藏省的GMV"中"省"命中 province
    锚、取值"西藏"未命中枚举，与原意（未命中留白澄清）一致。
    """
    query = "西藏省的GMV"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "clarify"
    question, options = payload
    assert "AI 规划暂不可用" in question
    assert any("省份" in o for o in options)


def test_multi_enum_hits_asks_for_clarification():
    """多枚举命中（"北京和上海的GMV"）=> 多候选澄清，不擅自多值筛选。"""
    query = "北京和上海的GMV"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "clarify"
    _, options = payload
    assert any("北京" in o for o in options) and any("上海" in o for o in options)


def test_dimension_only_anchor_is_not_exist():
    """只识别到维度无指标 => NOT_EXIST（区分文案：识别到维度但缺指标）。"""
    profile = classify_intent("按省份看一下")
    mode, payload = _degraded_parse("按省份看一下", profile, ENUMS)
    assert mode == "not_exist"
    assert "维度" in payload and "指标" in payload


def test_no_anchor_is_not_exist():
    """完全无锚点 => NOT_EXIST（原拒答语义保留）。"""
    profile = classify_intent("随便看看")
    mode, payload = _degraded_parse("随便看看", profile, ENUMS)
    assert mode == "not_exist"
    assert "未命中" in payload


def test_cross_table_anchors_are_not_exist():
    """跨表混合锚（GMV+退款金额）=> NOT_EXIST，严禁硬造单查询 DSL。"""
    query = "海南省的GMV和退款金额"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "not_exist"
    assert "跨表" in payload


def test_metric_only_unknown_gets_scalar_dsl():
    """纯指标锚 UNKNOWN（如"GMV趋势"）=> 无维度标量 DSL（不视为猜条件）。"""
    query = "GMV趋势"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "plan"
    dsl = payload[0].dsl
    assert dsl["metrics"][0]["field"] == "order_amount"
    assert dsl["dimensions"] == []


def test_explicit_time_window_wins_over_default():
    """显式时间解析优先于缺省 2024-05 锚。

    parse_explicit_time_window 返回 [start, end) 排他上界："2024年3月1日
    到3月31日"解析为 3 月整月窗口，end=2024-04-01。
    """
    query = "海南省的GMV 2024年3月1日到3月31日"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "plan"
    window = payload[0].dsl["time_filter"]["absolute"]
    assert window["start"] == "2024-03-01" and window["end"].startswith("2024-04-01")


def test_default_window_and_pay_status_scope():
    """无显式时间 => 缺省 2024-05 锚；fact_orders 锚带 pay_status 口径。"""
    mode, payload = _degraded_parse("海南省的GMV", classify_intent("海南省的GMV"), ENUMS)
    assert mode == "plan"
    dsl = payload[0].dsl
    assert dsl["time_filter"]["absolute"]["start"] == "2024-05-01"
    assert {"field": "pay_status", "operator": "eq", "value": "SUCCESS"} in dsl["filters"]
