"""用户问题意图分类器（十八期：理解优先、兜底准入、诚实拒答）。

三级路由的确定性 L1 层：
- 词表硬匹配（**长词优先**遍历并消费已匹配片段，防"退款金额"被"金额"式
  子串误吞）=> confidence="hard"；
- 判定不出的交给 LLM Planner（confidence="llm"）或诚实拒答（UNKNOWN）；
- L3 意图-DSL 错位守卫使用本模块对 user_query 的确定性重判结果，
  不信任 LLM 回传意图（LLM 意图可能出错，据其拦截会误杀合法查询）。
"""

from __future__ import annotations

from dataclasses import dataclass


class IntentType:
    """意图类型常量（str 平铺，兼容 AgentState 契约序列化）。"""

    DIAGNOSTIC = "diagnostic"
    CARDINALITY = "cardinality"
    METRIC_SCALAR = "metric_scalar"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntentProfile:
    """意图画像：类型 + 硬命中语义字段 + 置信分级。"""

    intent: str
    anchor_fields: tuple[str, ...]
    confidence: str  # "hard"（词表命中）| "llm"（LLM 判定）| "none"（无锚点）


# 指标别名词表：业务词 -> 语义目录字段（长词优先匹配）。
# 仅收录事实主表（fact_orders/fact_refunds）既有字段；比率/派生指标
# （退款率/客单价）数仓无现成字段，严禁收录——宁可 UNKNOWN 拒答。
_METRIC_TERMS: tuple[tuple[str, str], ...] = (
    ("退款金额", "refund_amount"),
    ("优惠金额", "discount_amount"),
    ("折扣金额", "discount_amount"),
    ("成交金额", "order_amount"),
    ("订单金额", "order_amount"),
    ("销售额", "order_amount"),
    ("gmv", "order_amount"),
    ("订单量", "order_id"),
    ("订单数", "order_id"),
    ("买家数", "user_id"),
    ("用户数", "user_id"),
)

# 维度别名词表（自 nodes.py _DIMENSION_TERMS 收编，值=语义字段）。
# 末段为数仓实际省份取值词根（广东/浙江/…）：用于识别"海南省的GMV"式
# 维度限定问句（判 UNKNOWN 拒答，终审 Critical #1）——兜底无法确定性
# 提取全部维度取值，宁拒答不降维成全域聚合。
DIMENSION_TERMS: dict[str, str] = {
    "province": "province",
    "省份": "province",
    "省": "province",
    "地区": "province",
    "地域": "province",
    "区域": "province",
    "大区": "province",
    "城市": "province",
    "广东": "province",
    "浙江": "province",
    "江苏": "province",
    "北京": "province",
    "上海": "province",
    "四川": "province",
    "湖北": "province",
    "山东": "province",
    "category": "category",
    "品类": "category",
    "类目": "category",
    "品类结构": "category",
    "brand": "brand",
    "品牌": "brand",
    "店铺": "shop_name",
    "门店": "shop_name",
    "shop_name": "shop_name",
}

_DIAGNOSTIC_TERMS: tuple[str, ...] = (
    "为什么",
    "下滑",
    "下降",
    "下跌",
    "上涨",
    "增长",
    "归因",
    "原因",
)

# 基数问句量词模式（"多少个省份 / 几个品类 / 多少家店铺"）。
_COUNT_QUESTION_TERMS: tuple[str, ...] = (
    "多少个",
    "几个",
    "多少种",
    "几种",
    "多少类",
    "几类",
    "多少家",
    "几家",
)

# 基数问句中的过滤线索词根（"有退款的省份有多少个"）：此类问句的 WHERE
# 口径无法从词表确定性推导，兜底构造全量计数会丢失过滤语义（答非所问的
# 温和形态）——count_dimension_dsl 返回 None 拒答，留给 LLM 规划。
# 注意：这只是"兜底是否构造 DSL"的约束；意图分类仍判 CARDINALITY，
# L3 守卫对 LLM 规划的带 WHERE 基数查询放行（filters 不受限）。
_FILTER_CLUE_TERMS: tuple[str, ...] = ("退款", "优惠", "折扣", "支付", "成功", "失败")


def _extract_anchors(
    query: str, terms: tuple[tuple[str, str], ...] | dict[str, str]
) -> tuple[str, ...]:
    """词表锚点提取：长词优先遍历，命中后消费片段（防子串重复/误吞）。"""
    pairs = sorted(
        terms.items() if isinstance(terms, dict) else terms,
        key=lambda p: len(p[0]),
        reverse=True,
    )
    lowered = query.lower()
    found: list[str] = []
    remaining = lowered
    for term, field in pairs:
        if term in remaining and field not in found:
            found.append(field)
            remaining = remaining.replace(term, " ")
    return tuple(found)


def _is_count_question(query: str) -> bool:
    return any(t in query for t in _COUNT_QUESTION_TERMS)


def classify_intent(query: str) -> IntentProfile:
    """确定性意图分类（L1 硬匹配）。

    判定优先级：诊断（触发词+锚点）> 基数（量词+单维度锚+无指标锚）>
    标量指标（指标硬锚）> UNKNOWN。基数判定以指标锚为排除条件——
    "有多少个品类的GMV"（量词+维度+指标锚）是按维度的指标问题而非基数问题。

    复合问句（指标锚+维度限定词共存，如"海南省的GMV"）判 UNKNOWN：
    兜底无法确定性提取维度取值（"海南省"->'海南'？数仓可能根本没有该
    取值），降维成全域聚合会输出错范围数据（终审 Critical #1 回归锚点）
    ——宁可拒答留给 LLM 规划；L3 守卫对 UNKNOWN 不启用，由 LLM 正确处理。
    """
    metrics = _extract_anchors(query, _METRIC_TERMS)
    dims = _extract_anchors(query, DIMENSION_TERMS)
    is_diagnostic = any(t in query for t in _DIAGNOSTIC_TERMS)
    if is_diagnostic and (metrics or dims):
        return IntentProfile(IntentType.DIAGNOSTIC, metrics + dims, "hard")
    if _is_count_question(query) and not metrics and len(dims) == 1:
        return IntentProfile(IntentType.CARDINALITY, dims, "hard")
    if metrics and not dims and not is_diagnostic:
        return IntentProfile(IntentType.METRIC_SCALAR, metrics, "hard")
    return IntentProfile(IntentType.UNKNOWN, (), "none")


def count_dimension_dsl(query: str) -> dict | None:
    """基数类问题的确定性 count_distinct DSL（元数据探查，无时间过滤）。

    非基数问题返回 None；含过滤线索（"有退款的省份"）时也返回 None——
    兜底无法确定性推导 WHERE 口径，构造全量计数会丢失过滤语义，宁拒答
    不给口径不完整的结果（LLM 在场时由 Planner 规划带过滤的查询）。
    多维度计数（"多少个省份和品类"）不兜底。
    """
    profile = classify_intent(query)
    if profile.intent != IntentType.CARDINALITY:
        return None
    if any(t in query for t in _FILTER_CLUE_TERMS):
        return None
    field = profile.anchor_fields[0]
    return {
        "metrics": [
            {
                "kind": "aggregate",
                "field": field,
                "agg": "count_distinct",
                "alias": f"{field}_count",
            }
        ],
        "dimensions": [],
        "filters": [],
    }


def capability_catalog_lines() -> list[str]:
    """拒答报告的能力清单（诚实告知系统边界，含中文 label）。

    unit_price 单价非金额聚合语义，独立"其他"分组（二期 M4）。
    """
    from semantic.catalog import COLUMNS, DRILLDOWN_DIM_FIELDS

    dim_fields = sorted(set(DRILLDOWN_DIM_FIELDS) | set(DIMENSION_TERMS.values()))
    dims = "、".join(f"{f}（{COLUMNS[f].label or f}）" for f in dim_fields if f in COLUMNS)
    metrics = "、".join(
        f"{name}（{meta.label or name}）"
        for name, meta in COLUMNS.items()
        if meta.dtype == "float" and name != "unit_price"
    )
    others = "、".join(
        f"{name}（{meta.label or name}）" for name, meta in COLUMNS.items() if name == "unit_price"
    )
    lines = [
        f"- 分析维度：{dims}；支持基数探查（如「有多少个省份」）",
        f"- 金额指标：{metrics}",
        "- 计数指标：订单量（order_id）、买家数（user_id）",
    ]
    if others:
        lines.append(f"- 其他：{others}")
    return lines
