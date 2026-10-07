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
    ENUMERATION = "enumeration"
    METRIC_SCALAR = "metric_scalar"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntentProfile:
    """意图画像：类型 + 硬命中语义字段 + 置信分级。"""

    intent: str
    anchor_fields: tuple[str, ...]
    confidence: str  # "hard"（词表命中）| "llm"（LLM 判定）| "none"（无锚点）


def _build_metric_terms() -> tuple[tuple[str, str], ...]:
    """从语义目录 FieldMeta.aliases 构建指标词表（数值字段 = int/float）。"""
    from semantic.catalog import COLUMNS

    terms: list[tuple[str, str]] = []
    for name, meta in COLUMNS.items():
        if meta.dtype in ("int", "float"):
            terms.extend((alias, name) for alias in meta.aliases)
    return tuple(terms)


def _build_dimension_terms() -> dict[str, str]:
    """从语义目录 FieldMeta.aliases 构建维度词表（非数值字段）。

    同名别名先序保留（setdefault），保证词表确定性。
    """
    from semantic.catalog import COLUMNS

    terms: dict[str, str] = {}
    for name, meta in COLUMNS.items():
        if meta.dtype not in ("int", "float"):
            for alias in meta.aliases:
                terms.setdefault(alias, name)
    return terms


def dimension_terms() -> dict[str, str]:
    """维度词表（动态构建；nodes 的显式维度识别消费）。"""
    return _build_dimension_terms()


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

# 枚举问句触发词（"把全部品牌名列举给我 / 有哪些品类"）：维度取值清单，
# 十九期 M1 新增直答意图。这是问法词表（非字段别名词表），允许字面维护。
_ENUMERATION_TERMS: tuple[str, ...] = (
    "列举",
    "列出",
    "有哪些",
    "都有什么",
    "都有哪些",
    "全部",
)


def _is_enumeration_question(query: str) -> bool:
    return any(t in query for t in _ENUMERATION_TERMS)


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
    枚举（问法词+单维度锚+无指标锚）> 标量指标（指标硬锚）> UNKNOWN。
    基数与枚举均以指标锚为排除条件；量词与枚举词共存时基数优先
    （"有多少个品牌"问计数而非清单）。
    复合问句（指标锚+维度限定词共存，如"海南省的GMV"）判 UNKNOWN：
    兜底无法确定性提取维度取值（"海南省"->'海南'？数仓可能根本没有该
    取值），降维成全域聚合会输出错范围数据（终审 Critical #1 回归锚点）
    ——宁可拒答留给 LLM 规划；L3 守卫对 UNKNOWN 不启用，由 LLM 正确处理。
    """
    metrics = _extract_anchors(query, _build_metric_terms())
    dims = _extract_anchors(query, _build_dimension_terms())
    is_diagnostic = any(t in query for t in _DIAGNOSTIC_TERMS)
    if is_diagnostic and (metrics or dims):
        return IntentProfile(IntentType.DIAGNOSTIC, metrics + dims, "hard")
    if _is_count_question(query) and not metrics and len(dims) == 1:
        return IntentProfile(IntentType.CARDINALITY, dims, "hard")
    if _is_enumeration_question(query) and not metrics and dims:
        # 十九期 M2：多维枚举放开（单维 M1 已落地）——投影形态天然支持
        # 多维 DISTINCT，字段序 = 词表提取序
        return IntentProfile(IntentType.ENUMERATION, dims, "hard")
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


def enumeration_dsl(query: str) -> dict | None:
    """枚举类问题的确定性纯维度投影 DSL（十九期 M1，元数据探查，无时间过滤）。

    非枚举问题返回 None；含过滤线索（"列出有退款的品牌"）时也返回 None——
    兜底无法确定性推导 WHERE 口径，全量清单会冒充过滤语义（宁拒答不给
    口径不完整的结果；LLM 在场时由 Planner 规划带过滤的投影）。
    """
    profile = classify_intent(query)
    if profile.intent != IntentType.ENUMERATION:
        return None
    if any(t in query for t in _FILTER_CLUE_TERMS):
        return None
    field = profile.anchor_fields[0]
    return {
        "metrics": [],
        "dimensions": [{"field": f} for f in profile.anchor_fields],
        "filters": [],
        "order_by": [{"field": field, "direction": "asc"}],
    }


def capability_catalog_lines() -> list[str]:
    """拒答报告的能力清单（诚实告知系统边界，含中文 label）。

    price 标价非金额聚合语义，独立"其他"分组（对齐原 unit_price 设计）。
    """
    from semantic import catalog
    from semantic.catalog import COLUMNS, DRILLDOWN_DIM_FIELDS

    dim_fields = sorted(set(DRILLDOWN_DIM_FIELDS) | set(_build_dimension_terms().values()))
    dims = "、".join(f"{f}（{COLUMNS[f].label or f}）" for f in dim_fields if f in COLUMNS)
    metrics = "、".join(
        f"{name}（{meta.label or name}）"
        for name, meta in COLUMNS.items()
        if meta.dtype == "float" and name != "price"
    )
    others = "、".join(
        f"{name}（{meta.label or name}）" for name, meta in COLUMNS.items() if name == "price"
    )
    # 计数指标从 semantic.json metrics[] 派生（M-P1：能力清单严禁硬编码指标名）
    count_metrics = "、".join(
        f"{m.get('title', m['key'])}（{m['key']}）"
        for m in catalog.METRICS
        if any(
            isinstance(part, dict)
            and part.get("kind") == "aggregate"
            and part.get("agg") in ("count", "count_distinct")
            for part in (m.get("shape") if isinstance(m.get("shape"), list) else [m.get("shape")])
        )
    )
    lines = [
        f"- 分析维度：{dims}；支持基数探查（如「有多少个省份」）"
        "与取值枚举（如「列出全部品牌」）",
        f"- 金额指标：{metrics}",
        f"- 计数指标：{count_metrics}",
    ]
    if others:
        lines.append(f"- 其他：{others}")
    return lines
