"""中文标签映射：逻辑字段 / 聚合 / 操作符 / 枚举值。

展示层（解释 & 可视化）依赖本模块把结构化 DSL 转成人类可读中文，
保持纯数据、确定性、无外部依赖。
"""

from __future__ import annotations

FIELD_LABELS: dict[str, str] = {
    # 订单事实
    "order_id": "订单数",
    "user_id": "用户",
    "product_id": "商品",
    "order_amount": "订单金额",
    "discount_amount": "优惠金额",
    "pay_status": "支付状态",
    "order_time": "下单时间",
    # 退款事实
    "refund_id": "退款单数",
    "refund_amount": "退款金额",
    "refund_time": "退款时间",
    "refund_status": "退款状态",
    # 用户维度
    "province": "省份",
    "gender": "性别",
    "register_time": "注册时间",
    # 商品维度
    "category": "类目",
    "brand": "品牌",
    "unit_price": "单价",
}

AGG_LABELS: dict[str, str] = {
    "sum": "求和",
    "count": "计数",
    "count_distinct": "去重计数",
    "avg": "平均",
    "min": "最小",
    "max": "最大",
}

OP_LABELS: dict[str, str] = {
    "eq": "等于",
    "ne": "不等于",
    "in": "属于",
    "gt": "大于",
    "gte": "大于等于",
    "lt": "小于",
    "lte": "小于等于",
    "between": "介于",
}

VALUE_LABELS: dict[str, dict[str, str]] = {
    "pay_status": {"SUCCESS": "成功", "CANCELLED": "已取消"},
    "refund_status": {"SUCCESS": "退款成功", "PENDING": "处理中"},
    "gender": {"M": "男", "F": "女"},
}


def field_label(field: str) -> str:
    """逻辑字段的中文展示名（Display label for a logical field）。"""
    return FIELD_LABELS.get(field, field)


def agg_label(agg: str) -> str:
    """聚合函数的中文展示名（Display label for an aggregation）。"""
    return AGG_LABELS.get(agg, agg)


def op_label(op: str) -> str:
    """过滤操作符的中文展示名（Display label for an operator）。"""
    return OP_LABELS.get(op, op)


def value_label(field: str, value) -> str:
    """枚举字段的取值中文映射（Value mapping；列表值拼接渲染）。"""
    mapping = VALUE_LABELS.get(field, {})
    if isinstance(value, list):
        return "[" + ", ".join(mapping.get(v, str(v)) for v in value) + "]"
    return mapping.get(value, str(value))
