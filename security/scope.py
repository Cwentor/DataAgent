"""按主体过滤的元数据作用域 —— 守卫前移，最小权限注入（P0）。

目标：在 LLM 生成**之前**，只把主体可用的 Schema / 字段 / 口径注入 Agent，
让"越权字段"根本不进入模型视野，而不是生成后再靠守卫拒绝。
security.guard.apply_policy 仍保留为事后纵深防御（双保险）。

- scoped_tables(principal)   -> 主体可查询的物理表集合；
- scoped_fields(principal)   -> 主体可引用的逻辑字段集合（表级 + 列级取交集）；
- scoped_catalog(principal)  -> 过滤后的字段元数据；
- scoped_field_listing(principal) -> 注入 Prompt 的字段白名单字符串；
- is_field_allowed / is_table_allowed -> 单点判定。

principal 为 None 表示"未绑定主体"（库级调用向后兼容，等价全量可见）；
HTTP 网关层保证每个请求都拿到服务端映射的 principal。
"""

from __future__ import annotations

from security.errors import SecurityError
from security.policy import POLICIES, Policy
from semantic.catalog import COLUMNS, FieldMeta


def resolve_policy(principal: str | None) -> Policy | None:
    """解析主体策略；None -> 全量（无限制）。未登记主体 -> SecurityError。"""
    if principal is None:
        return None
    policy = POLICIES.get(principal)
    if policy is None:
        raise SecurityError(f"未登记的主体: {principal!r}")
    return policy


def scoped_tables(principal: str | None) -> frozenset[str]:
    """主体可查询的物理表集合。"""
    policy = resolve_policy(principal)
    if policy is None:
        return frozenset({meta.table for meta in COLUMNS.values()})
    return policy.allowed_tables


def scoped_fields(principal: str | None) -> frozenset[str]:
    """主体可引用的逻辑字段集合（表级允许 ∩ 列级未禁用）。"""
    policy = resolve_policy(principal)
    if policy is None:
        return frozenset(COLUMNS.keys())
    return frozenset(
        field
        for field, meta in COLUMNS.items()
        if meta.table in policy.allowed_tables and field not in policy.forbidden_columns
    )


def scoped_catalog(principal: str | None) -> dict[str, FieldMeta]:
    """按主体过滤后的字段元数据（语义目录子集）。"""
    allowed = scoped_fields(principal)
    return {field: meta for field, meta in COLUMNS.items() if field in allowed}


def scoped_field_listing(principal: str | None) -> str:
    """注入 Prompt 的字段白名单字符串（逗号分隔、按名排序，确定性可复现）。"""
    return ", ".join(sorted(scoped_fields(principal)))


def is_field_allowed(principal: str | None, field: str) -> bool:
    """字段是否在主体白名单内（Field-level access check）。"""
    return field in scoped_fields(principal)


def is_table_allowed(principal: str | None, table: str) -> bool:
    """表是否在主体白名单内（Table-level access check）。"""
    return table in scoped_tables(principal)
