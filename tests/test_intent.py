"""意图分类器单测：词表硬匹配（长词优先）、三类意图判定、基数 DSL、能力清单。"""

from core.orchestrator.intent import (
    IntentType,
    capability_catalog_lines,
    classify_intent,
    count_dimension_dsl,
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
