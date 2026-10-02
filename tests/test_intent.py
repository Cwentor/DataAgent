"""意图分类器单测：词表硬匹配（长词优先）、三类意图判定、基数 DSL、能力清单。"""

from core.orchestrator.intent import (
    IntentType,
    capability_catalog_lines,
    classify_intent,
    count_dimension_dsl,
    enumeration_dsl,
)


def test_metric_anchor_longest_match_first():
    """长词优先：'退款金额'不被子串误吞（Review Focus 1 前置）。"""
    profile = classify_intent("5月退款金额是多少")
    assert profile.intent == IntentType.METRIC_SCALAR
    assert profile.anchor_fields == ("refund_amount",)
    assert profile.confidence == "hard"


def test_diagnostic_intent():
    profile = classify_intent("为什么GMV下滑")
    assert profile.intent == IntentType.DIAGNOSTIC
    assert "order_amount" in profile.anchor_fields


def test_cardinality_intent_and_filters_with_metric_word():
    """量词+单一维度锚 => 基数；过滤语境的'退款'不构成指标锚（Review Focus 2）。"""
    profile = classify_intent("有多少个省份")
    assert profile.intent == IntentType.CARDINALITY
    assert profile.anchor_fields == ("province",)

    with_refund_filter = classify_intent("有退款的省份有多少个")
    assert with_refund_filter.intent == IntentType.CARDINALITY
    # 含过滤线索：兜底拒绝构造 DSL（全量计数会丢失"有退款"过滤语义），留 LLM
    assert count_dimension_dsl("有退款的省份有多少个") is None

    assert count_dimension_dsl("有多少个省份") is not None


def test_count_dsl_shape():
    dsl = count_dimension_dsl("有多少个省份")
    assert dsl == {
        "metrics": [
            {
                "kind": "aggregate",
                "field": "province",
                "agg": "count_distinct",
                "alias": "province_count",
            }
        ],
        "dimensions": [],
        "filters": [],
    }
    assert count_dimension_dsl("各省GMV多少") is None  # 无量词模式


def test_unknown_intent_for_rate_and_vague_queries():
    """比率词/空泛问题 => UNKNOWN（数仓无字段，兜底不猜）。"""
    assert classify_intent("退款率是多少").intent == IntentType.UNKNOWN
    assert classify_intent("帮我看看最近情况").intent == IntentType.UNKNOWN
    assert classify_intent("流量表现怎么样").intent == IntentType.UNKNOWN


def test_capability_catalog_lines():
    lines = capability_catalog_lines()
    assert any("province" in line and "省份" in line for line in lines)
    assert any("order_amount" in line for line in lines)
    assert any("shop_name" in line for line in lines)  # 店铺维度不遗漏


def test_dimension_scoped_metric_query_is_unknown():
    """复合问句（指标锚+维度限定词）=> UNKNOWN 诚实拒答（终审 Critical #1）。

    回归锚点："海南省的GMV是多少"曾被兜底降维成全域 sum(gmv)——数仓根本
    没有海南，带着准入制合法性外衣输出错范围数据比拒答严重得多。兜底无法
    确定性提取维度取值（"海南省"->'海南'），一律拒答留给 LLM 规划。
    L3 守卫随之不启用（confidence=none），复合问句由 LLM 正确规划。
    """
    profile = classify_intent("海南省的GMV是多少")
    assert profile.intent == IntentType.UNKNOWN

    profile2 = classify_intent("北京的退款金额是多少")
    assert profile2.intent == IntentType.UNKNOWN

    # 纯指标锚（无维度限定）不受影响
    assert classify_intent("5月GMV是多少").intent == IntentType.METRIC_SCALAR


def test_capability_catalog_groups_unit_price_separately():
    """unit_price 单价不属于金额指标（终审 M4）。"""
    lines = capability_catalog_lines()
    money_line = next(line for line in lines if line.startswith("- 金额指标"))
    assert "unit_price" not in money_line
    assert any("unit_price" in line for line in lines)  # 仍在清单中（其他分组）


def test_terms_built_from_catalog_aliases():
    """意图词表从语义目录 FieldMeta.aliases 动态构建（单一事实源）。"""
    from semantic.catalog import COLUMNS

    assert COLUMNS["order_amount"].aliases  # 内置默认已登记
    assert "gmv" in COLUMNS["order_amount"].aliases
    assert COLUMNS["province"].aliases  # 维度字段已登记
    # 长词优先仍成立
    profile = classify_intent("5月退款金额是多少")
    assert profile.anchor_fields == ("refund_amount",)


def test_enumeration_intent_classification():
    """十九期 M1：枚举意图判定（问法词表 + 单维度锚 + 无指标锚）。"""
    profile = classify_intent("把全部品牌名列举给我")
    assert profile.intent == IntentType.ENUMERATION
    assert profile.anchor_fields == ("brand",)
    assert profile.confidence == "hard"

    assert classify_intent("有哪些品类").intent == IntentType.ENUMERATION


def test_enumeration_priority_rules():
    """十九期 M1 Review Focus #1/#2：指标锚排除、基数优先。"""
    # 含指标锚 + 维度限定："全部"不构成枚举——复合问句判 UNKNOWN（留 LLM 规划）
    assert classify_intent("全部品牌的GMV是多少").intent == IntentType.UNKNOWN
    # "全部" + 纯指标锚：走指标直答
    assert classify_intent("全部退款金额是多少").intent == IntentType.METRIC_SCALAR
    # 基数量词优先于枚举词：问计数不问清单
    assert classify_intent("有多少个品牌").intent == IntentType.CARDINALITY


def test_enumeration_multi_dim_is_unknown():
    """十九期 M1 Review Focus #4：多维枚举 M1 判 UNKNOWN（兜底拒答并说明）。"""
    assert classify_intent("列出所有省份和品牌").intent == IntentType.UNKNOWN


def test_enumeration_dsl_shape_and_filter_clue():
    """枚举 DSL 构造：单维投影无指标；过滤线索拒绝构造（宁拒答不冒充）。"""
    dsl = enumeration_dsl("把全部品牌名列举给我")
    assert dsl is not None
    assert dsl["metrics"] == []
    assert dsl["dimensions"] == [{"field": "brand"}]
    assert dsl["filters"] == []
    assert dsl["order_by"] == [{"field": "brand", "direction": "asc"}]

    # Review Focus #3：过滤线索（"退款"）=> 兜底拒绝构造，留 LLM 规划
    assert enumeration_dsl("列出有退款的品牌") is None


def test_capability_catalog_mentions_enumeration():
    """能力清单如实告知枚举能力（拒答报告与工作台同源）。"""
    text = "\n".join(capability_catalog_lines())
    assert "列出全部品牌" in text
