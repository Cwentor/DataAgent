"""会话级安全视图层（十九期 M5 + 二十一期方言化）：探索层的"构造即安全"执行面。

按 principal 生成投影定义：**禁列物理投影掉**（不存在，而非查询时拦截）+
**RLS 行过滤固化**（无论 SQL 写得多绕都无条件生效）。RLS 过滤字段的物理列
不在本表时（如 province 在 base_province），经受控连接路径 join 到字段
所属表过滤——与编译器对 RLS 过滤的 join 语义同口径；join 仅服务过滤，
字段不进投影（越权列不泄露）。实体维度视图（dim_*）不做行过滤（品牌
等实体目录本身无行级归属；行级敏感数据全部在事实表视图上强制）。

双保险原则（spec §3.4）：DSL 核（L1/L2）继续走编译器注入 RLS，视图定义与
guard 语义一致性由 tests/test_security_views.py 交叉验证锁定——两层独立实现、
同一策略事实源（security.policy），任一层被绕过另一层仍在。

二十一期方言化：投影构建下沉纯函数 ``build_secure_projection``（标识符经
DialectRenderer 渲染）。DuckDB 后端拼 CREATE TEMP VIEW DDL（安装入口在
exec.engine.duckdb_adapter）；MySQL 无临时视图，由探索层把同一投影内联为
派生表（core/retrieval/exploration.py）——投影/RLS/禁列缺席单一实现。
本模块不 import 任何引擎（security 包引擎无关）。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from compiler.dialect.base import DialectRenderer
from compiler.dialect.duckdb import DuckDbRenderer
from compiler.sql_compiler import render_literal
from security.errors import SecurityError
from security.guard import _resolve_row_filter
from security.policy import POLICIES
from semantic import catalog

# 视图名前缀：探索层 SQL 只授权该命名空间（M6 连接管理强制）
VIEW_PREFIX = "sec_"

# 视图 DDL 前缀（评审 LOW #7）：build_secure_views 产出的定义与 install 的
# TEMP 改写共用同一常量源，改一处不致另一处静默失效
_VIEW_DDL_PREFIX = "CREATE OR REPLACE VIEW"
_VIEW_TEMP_DDL_PREFIX = "CREATE OR REPLACE TEMP VIEW"


@dataclass(frozen=True)
class SecureProjection:
    """单表安全投影（方言无关结构，渲染由 renderer 决定）。

    - select_list：投影片段列表（`col AS logical_name` 形态，禁列缺席）；
    - from_sql：FROM 子句体（主表 + 受控 JOIN，含表别名）；
    - where_sql：RLS 行过滤条件（**不含** WHERE 关键字；空串 = 无行过滤）。
    """

    select_list: list[str] = field(default_factory=list)
    from_sql: str = ""
    where_sql: str = ""


def _policy_for(principal: str | None):
    """解析主体策略（None 等价 admin，与 guard.apply_policy 口径一致）。"""
    name = principal or "admin"
    policy = POLICIES.get(name)
    if policy is None:
        raise SecurityError(f"未登记的主体: {name!r}")
    return policy


def _render_predicate(
    field: str, operator: str, value: Any, alias: str, renderer: DialectRenderer
) -> str:
    """把解析后的 RLS 谓词渲染为 SQL 片段（限定列名 + 字面量强转义）。

    仅支持 guard 策略实际使用的形态（eq / in），其他操作符显式拒绝——
    投影是安全边界，严禁引入未审计的渲染路径。
    """
    meta = catalog.COLUMNS.get(field)
    if meta is None or operator not in ("eq", "in"):
        raise SecurityError(f"不支持的 RLS 谓词形态: field={field!r}, operator={operator!r}")
    quoted = renderer.quote_column(alias, meta.column)
    if operator == "eq":
        return f"{quoted} = {render_literal(value, meta.dtype)}"
    rendered = ", ".join(render_literal(v, meta.dtype) for v in value)
    return f"{quoted} IN ({rendered})"


def _join_edges() -> dict[str, dict[str, tuple[str, str]]]:
    """连接边：table -> {对端表: (本端列, 对端列)}（与 catalog 受控连接同一事实源）。"""
    edges: dict[str, dict[str, tuple[str, str]]] = {}
    for dim, rule in catalog.JOIN_RULES.items():
        for dim_col, fact_col in rule.on:
            edges.setdefault(catalog.FACT_TABLE, {})[dim] = (fact_col, dim_col)
            edges.setdefault(dim, {})[catalog.FACT_TABLE] = (dim_col, fact_col)
    for fact2, rule in catalog.FACT_JOIN_RULES.items():
        for f2_col, main_col in rule.on:
            edges.setdefault(catalog.FACT_TABLE, {})[fact2] = (main_col, f2_col)
            edges.setdefault(fact2, {})[catalog.FACT_TABLE] = (f2_col, main_col)
    return edges


_JOIN_EDGES = _join_edges()


def _join_path(table: str, target: str) -> list[str] | None:
    """BFS 求受控连接路径（table -> target），返回途经表序列（含两端）。"""
    if table == target:
        return [table]
    queue: deque[tuple[str, list[str]]] = deque([(table, [table])])
    visited = {table}
    while queue:
        current, path = queue.popleft()
        for neighbor in _JOIN_EDGES.get(current, {}):
            if neighbor in visited:
                continue
            if neighbor == target:
                return [*path, neighbor]
            visited.add(neighbor)
            queue.append((neighbor, [*path, neighbor]))
    return None


def build_secure_projection(
    table: str, policy, renderer: DialectRenderer
) -> SecureProjection | None:
    """策略 + 表 -> 安全投影（纯函数，确定性；方言经 renderer 渲染）。

    语义口径（与十九期 build_secure_views 单一实现同源）：

    - 投影面 = policy.allowed_tables 白名单；列以**逻辑字段名**输出；
    - 主事实表投影自身字段 + 受控连接的允许维度表字段（与编译器 JOIN
      语义同口径——province/brand 等维度字段物理在维表，经 join 可查询），
      维度列名与事实列冲突时事实列优先；
    - 禁列（forbidden_columns）物理不存在于投影；
    - 行过滤（RLS）仅施加于事实表（行级敏感数据全在事实数据上；dim
      实体目录投影不做行过滤），过滤字段经 BFS 受控连接路径 join 过滤；
    - 全列被禁的表返回 None（引用即不存在，等价拒绝）。
    """
    resolved_filters = [_resolve_row_filter(rf, policy.name) for rf in policy.row_filters]
    own_fields = [
        fname
        for fname, meta in sorted(catalog.COLUMNS.items())
        if meta.table == table and fname not in policy.forbidden_columns
    ]
    if not own_fields:
        return None  # 全列被禁的表不生成投影（引用即不存在，等价拒绝）
    # 事实表判定（十九期 M5）：主事实表可扩展维度 join 投影；第二事实表
    # 经 FACT_JOIN_RULES 挂主表（无法直连维表），仅做 RLS 行过滤；
    # dim 实体投影只投影自身字段、不做行过滤
    expand_dims = table == catalog.FACT_TABLE
    is_fact = expand_dims or table in catalog.FACT_JOIN_RULES
    projections: list[str] = []
    seen: set[str] = set()
    joins: list[str] = []
    joined: set[str] = set()
    for fname in own_fields:
        meta = catalog.COLUMNS[fname]
        projections.append(
            f"{renderer.quote_column(catalog.ALIASES[table], meta.column)}"
            f" AS {renderer.quote_ident(fname)}"
        )
        seen.add(fname)
    if expand_dims:
        for dim in sorted(catalog.JOIN_RULES):
            if dim not in policy.allowed_tables:
                continue
            dim_rule = catalog.JOIN_RULES[dim]
            fact_col, dim_col = dim_rule.on[0][1], dim_rule.on[0][0]
            dim_alias, fact_alias = catalog.ALIASES[dim], catalog.ALIASES[table]
            joins.append(
                f" JOIN {dim} {dim_alias}"
                f" ON {renderer.quote_column(dim_alias, dim_col)}"
                f" = {renderer.quote_column(fact_alias, fact_col)}"
            )
            joined.add(dim)
            for fname, meta in sorted(catalog.COLUMNS.items()):
                if (
                    meta.table == dim
                    and fname not in policy.forbidden_columns
                    and fname not in seen
                ):
                    projections.append(
                        f"{renderer.quote_column(catalog.ALIASES[dim], meta.column)}"
                        f" AS {renderer.quote_ident(fname)}"
                    )
                    seen.add(fname)
    where_parts: list[str] = []
    if is_fact:
        for rf in resolved_filters:
            meta = catalog.COLUMNS[rf["field"]]
            if meta.table == table:
                alias = catalog.ALIASES[table]
            else:
                path = _join_path(table, meta.table)
                if path is None:
                    raise SecurityError(f"表 {table!r} 的行过滤字段 {rf['field']!r} 无受控连接路径")
                for a, b in pairwise(path):
                    if b in joined:
                        continue
                    local_col, remote_col = _JOIN_EDGES[a][b]
                    joins.append(
                        f" JOIN {b} {catalog.ALIASES[b]}"
                        f" ON {renderer.quote_column(catalog.ALIASES[b], remote_col)}"
                        f" = {renderer.quote_column(catalog.ALIASES[a], local_col)}"
                    )
                    joined.add(b)
                alias = catalog.ALIASES[meta.table]
            where_parts.append(
                _render_predicate(rf["field"], rf["operator"], rf["value"], alias, renderer)
            )
    return SecureProjection(
        select_list=projections,
        from_sql=f"{table} {catalog.ALIASES[table]}{''.join(joins)}",
        where_sql=" AND ".join(where_parts),
    )


def build_secure_views(principal: str | None) -> dict[str, str]:
    """策略 -> DuckDB 视图定义（纯函数，确定性；TEMP 安装见 duckdb_adapter）。

    返回 {视图名: CREATE VIEW SQL}。视图 DDL 是 DuckDB 专属路径（MySQL 无
    临时视图，走 build_secure_projection 内联派生表），固定用 DuckDbRenderer。
    """
    policy = _policy_for(principal)
    renderer = DuckDbRenderer()
    views: dict[str, str] = {}
    for table in sorted(policy.allowed_tables):
        proj = build_secure_projection(table, policy, renderer)
        if proj is None:
            continue
        where_sql = f" WHERE {proj.where_sql}" if proj.where_sql else ""
        view_name = f"{VIEW_PREFIX}{table}"
        views[view_name] = (
            f"{_VIEW_DDL_PREFIX} {view_name} AS "
            f"SELECT {', '.join(proj.select_list)} FROM {proj.from_sql}{where_sql}"
        )
    return views


__all__ = [
    "VIEW_PREFIX",
    "SecureProjection",
    "build_secure_projection",
    "build_secure_views",
]
