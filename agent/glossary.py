"""业务指标口径文档（口径词典，Single Source of Truth）。

这是「口径文档 RAG 检索」的知识源：每条口径记录指标的业务定义、别名、
计算口径（公式）与所依赖的语义字段。检索层（agent.rag）与澄清层
（agent.clarify）都消费这份词典，保证"已定义指标"判定与文档检索同源。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GlossaryDoc:
    """口径文档：指标定义 / 计算公式 / 同义词 / 关联字段（Glossary entry）。"""

    key: str
    title: str
    aliases: tuple[str, ...]
    definition: str
    formula: str
    fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """序列化为检索结果字典（Serialize to a retrieval payload）。"""
        return {
            "key": self.key,
            "title": self.title,
            "aliases": list(self.aliases),
            "definition": self.definition,
            "formula": self.formula,
            "fields": list(self.fields),
        }


GLOSSARY: tuple[GlossaryDoc, ...] = (
    GlossaryDoc(
        key="gmv",
        title="GMV（商品交易总额）",
        aliases=(
            "gmv",
            "销售额",
            "销售总额",
            "成交额",
            "成交金额",
            "总销售",
            "总销售额",
        ),
        definition="成功支付订单的实付金额之和；通常需限定支付状态为成功，并给定时间窗口。",
        formula="SUM(fact_orders.order_amount)",
        fields=("order_amount", "pay_status", "order_time"),
    ),
    GlossaryDoc(
        key="order_count",
        title="订单数",
        aliases=("订单数", "订单量", "订单总数", "订单笔数"),
        definition="订单主键的计数；若要求成功订单，需附加支付状态为成功的过滤条件。",
        formula="COUNT(fact_orders.order_id)",
        fields=("order_id", "pay_status", "order_time"),
    ),
    GlossaryDoc(
        key="active_users",
        title="活跃用户数（去重用户数）",
        aliases=("去重用户", "活跃用户", "去重用户数", "活跃用户数"),
        definition="在指定时间窗口内发生下单行为的去重用户数。注意：该口径不区分活跃度高低。",
        formula="COUNT(DISTINCT fact_orders.user_id)",
        fields=("user_id", "order_time"),
    ),
    GlossaryDoc(
        key="arpu",
        title="ARPU（人均消费）",
        aliases=("arpu", "人均消费"),
        definition="总成交金额除以去重用户数，即平均每用户的消费金额。",
        formula="SUM(order_amount) / COUNT(DISTINCT user_id)",
        fields=("order_amount", "user_id"),
    ),
    GlossaryDoc(
        key="refund_rate",
        title="退款率",
        aliases=("退款率",),
        definition="退款金额占订单金额的比例，用于衡量订单退款强度。",
        formula="SUM(fact_refunds.refund_amount) / SUM(fact_orders.order_amount)",
        fields=("refund_amount", "order_amount"),
    ),
    GlossaryDoc(
        key="refund_amount",
        title="退款金额",
        aliases=("退款金额", "退款总额", "退款额"),
        definition="退款事实表中退款金额的求和。",
        formula="SUM(fact_refunds.refund_amount)",
        fields=("refund_amount",),
    ),
    GlossaryDoc(
        key="avg_order_amount",
        title="客单价",
        aliases=("客单价",),
        definition="平均每笔订单的实付金额。",
        formula="AVG(fact_orders.order_amount)",
        fields=("order_amount",),
    ),
)

# 全部已定义指标别名（统一小写；中文小写等于原文），供澄清层判断"是否已定义业务指标"。
METRIC_TERMS: frozenset[str] = frozenset(alias.lower() for doc in GLOSSARY for alias in doc.aliases)

# 分析/聚合操作词（不是指标本身，但澄清层应视其为已识别语义，避免误判为"未定义指标"）。
OPERATOR_TERMS: frozenset[str] = frozenset(
    {"累计", "移动平均", "滑动平均", "环比", "同比", "yoy", "mom"}
)


def scoped_glossary(principal: str | None = None) -> tuple[GlossaryDoc, ...]:
    """按主体过滤口径文档（守卫前移）：只保留引用字段全部可见的文档。

    - principal 为 None -> 全量（库级调用向后兼容）；
    - 如 refund_rate / refund_amount 依赖退款字段，restricted 主体不可见。
    """
    from security.scope import scoped_fields

    allowed = scoped_fields(principal)
    return tuple(doc for doc in GLOSSARY if set(doc.fields) <= allowed)
