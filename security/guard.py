"""安全守卫：对 QueryDSL 施加主体策略。

流程：
1. 解析主体策略（未登记主体 -> SecurityError）；
2. 表级校验：DSL 引用的物理表必须都在 allowed_tables；
3. 列级校验：DSL 引用的逻辑字段不得在 forbidden_columns；
4. 行级 RLS：把策略的 row_filters 追加进 DSL.filters，返回新 DSL。

守卫是纯函数：不修改入参 DSL，失败抛 SecurityError（拒绝而非放行）。
"""

from __future__ import annotations

from typing import Any

from security.errors import SecurityError
from security.policy import POLICIES, PRINCIPAL_ATTRS, Policy
from semantic import catalog
from semantic.dsl_schema import Filter, QueryDSL

__all__ = ["SecurityError", "apply_policy"]


def _resolve_row_filter(rf: dict, principal: str) -> dict:
    """解析 RLS 参数化模板（P0-3）：把 param 按主体属性解析为实际值。

    支持的 param 形态：principal.<attr>（如 principal.provinces），
    值取自 security.policy.PRINCIPAL_ATTRS[principal][attr]。
    无 param 的普通谓词原样返回（向后兼容）。
    """
    param = rf.get("param")
    if param is None:
        return rf
    if not isinstance(param, str) or not param.startswith("principal."):
        raise SecurityError(f"不支持的 RLS 参数模板: {param!r}")
    attr = param.split(".", 1)[1]
    attrs = PRINCIPAL_ATTRS.get(principal, {})
    if attr not in attrs:
        raise SecurityError(f"主体 {principal!r} 缺少 RLS 属性 {attr!r}")
    resolved = dict(rf)
    resolved.pop("param", None)
    resolved["value"] = attrs[attr]
    return resolved


def _referenced_fields(dsl: QueryDSL) -> set[str]:
    """收集 DSL 引用的全部逻辑字段（指标 + 维度 + 过滤）。

    十九期 M2：表达式指标的字段经 ref 回溯到被引用的聚合指标——禁列不得
    借表达式绕过列级权限（ref 已由契约层限定为同 DSL 聚合指标别名）。
    """
    fields: set[str] = set()
    agg_by_alias: dict[str, Any] = {}
    for m in dsl.metrics:
        kind = getattr(m, "kind", "")
        if kind == "expression":
            continue
        if kind == "ratio":
            fields.add(m.numerator.field)
            fields.add(m.denominator.field)
        elif kind == "window":
            fields.add(m.base.field)
        else:
            fields.add(m.field)
        agg_by_alias[m.alias] = m
    for m in dsl.metrics:
        if getattr(m, "kind", "") != "expression":
            continue
        stack = [m.expr]
        while stack:
            node = stack.pop()
            if node.ref is not None:
                ref_metric = agg_by_alias.get(node.ref)
                if ref_metric is not None and getattr(ref_metric, "field", None):
                    fields.add(ref_metric.field)
            stack.extend(getattr(node, "args", []))
    for d in dsl.dimensions:
        fields.add(d.field)
    for f in dsl.filters:
        fields.add(f.field)
    return fields


def _referenced_tables(fields: set[str]) -> set[str]:
    """字段 -> 所属物理表（未登记字段跳过，交由编译器兜底报错）。"""
    tables: set[str] = set()
    for f in fields:
        meta = catalog.COLUMNS.get(f)
        if meta is not None:
            tables.add(meta.table)
    return tables


def apply_policy(dsl: QueryDSL, principal: str | None) -> QueryDSL:
    """施加主体策略，返回（可能追加了 RLS 过滤的）新 DSL。

    principal 为 None 时不施加任何限制（等价于 admin），保持向后兼容。
    """
    if principal is None:
        return dsl

    policy: Policy | None = POLICIES.get(principal)
    if policy is None:
        raise SecurityError(f"未登记的主体: {principal!r}")

    fields = _referenced_fields(dsl)
    tables = _referenced_tables(fields)

    # 表级校验
    forbidden_tables = tables - policy.allowed_tables
    if forbidden_tables:
        raise SecurityError(f"主体 {principal!r} 无权访问表: {sorted(forbidden_tables)}")

    # 列级校验
    forbidden_fields = fields & policy.forbidden_columns
    if forbidden_fields:
        raise SecurityError(f"主体 {principal!r} 无权访问字段: {sorted(forbidden_fields)}")

    # 行级 RLS：解析参数化模板后追加过滤条件
    if policy.row_filters:
        extra = [
            Filter.model_validate(_resolve_row_filter(rf, principal)) for rf in policy.row_filters
        ]
        dsl = dsl.model_copy(update={"filters": list(dsl.filters) + extra})

    return dsl
