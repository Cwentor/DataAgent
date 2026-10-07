"""中文标签映射：逻辑字段 / 聚合 / 操作符 / 枚举值。

展示层（解释 & 可视化）依赖本模块把结构化 DSL 转成人类可读中文。

M-P1（2026-10）：字段/枚举标签的单一事实源 = 语义目录
（semantic.json fields[].label / value_labels），本模块只做动态读取与
展示兜底，严禁再手写字段标签映射（业务事实与代码双写是 Gmall 迁移
66 文件波及的主要根源之一）。聚合/操作符中文属 DSL 语法层标签，保留于此。
"""

from __future__ import annotations


def _field_labels() -> dict[str, str]:
    """字段 -> 中文标签（动态读语义目录，refresh_catalog 后即时生效）。"""
    from semantic import catalog

    return {name: (meta.label or name) for name, meta in catalog.COLUMNS.items()}


def field_label(field: str) -> str:
    """逻辑字段的中文展示名（Display label for a logical field）。"""
    return _field_labels().get(field, field)


def agg_label(agg: str) -> str:
    """聚合函数的中文展示名（Display label for an aggregation）。"""
    return AGG_LABELS.get(agg, agg)


def op_label(op: str) -> str:
    """过滤操作符的中文展示名（Display label for an operator）。"""
    return OP_LABELS.get(op, op)


def value_label(field: str, value) -> str:
    """枚举字段的取值中文映射（Value mapping；列表值拼接渲染）。

    枚举值标签单一事实源 = semantic.json value_labels（catalog.VALUE_LABELS）。
    """
    from semantic import catalog

    mapping = catalog.VALUE_LABELS.get(field, {})
    if isinstance(value, list):
        return "[" + ", ".join(mapping.get(v, str(v)) for v in value) + "]"
    return mapping.get(value, str(value))


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
