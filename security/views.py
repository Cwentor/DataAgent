"""会话级安全视图层（十九期 M5）：探索层的"构造即安全"执行面。

按 principal 生成视图定义：**禁列物理投影掉**（视图里不存在，而非查询时
拦截）+ **RLS 行过滤固化在视图定义**（无论 SQL 写得多绕都无条件生效）。
RLS 过滤字段的物理列不在本表时（如 province 在 dim_shop），经受控连接路径
join 到字段所属表过滤——与编译器对 RLS 过滤的 join 语义同口径；join 仅服务
过滤，字段不进投影（越权列不泄露）。实体维度视图（dim_*）不做行过滤（品牌
等实体目录本身无行级归属；行级敏感数据全部在事实表视图上强制）。

双保险原则（spec §3.4）：DSL 核（L1/L2）继续走编译器注入 RLS，视图定义与
guard 语义一致性由 tests/test_security_views.py 交叉验证锁定——两层独立实现、
同一策略事实源（security.policy），任一层被绕过另一层仍在。

连接级加固（harden_connection）：enable_external_access 封死文件/ATTACH，
lock_configuration 阻止后续解锁——fail-closed，设置失败抛 SecurityError。

边界（如实记录）：CREATE VIEW 需要可写连接，视图安装的生命周期接线（探索
层连接的创建/销毁时机）属 M6 探索层连接管理；本模块只提供纯函数与安装接口。
"""

from __future__ import annotations

from collections import deque
from typing import Any

from duckdb import DuckDBPyConnection

from compiler.sql_compiler import _literal
from security.errors import SecurityError
from security.guard import _resolve_row_filter
from security.policy import POLICIES
from semantic import catalog

# 视图名前缀：探索层 SQL 只授权该命名空间（M6 连接管理强制）
VIEW_PREFIX = "sec_"


def _policy_for(principal: str | None):
    """解析主体策略（None 等价 admin，与 guard.apply_policy 口径一致）。"""
    name = principal or "admin"
    policy = POLICIES.get(name)
    if policy is None:
        raise SecurityError(f"未登记的主体: {name!r}")
    return policy


def _render_predicate(field: str, operator: str, value: Any, alias: str) -> str:
    """把解析后的 RLS 谓词渲染为 SQL 片段（限定列名 + 字面量强转义）。

    仅支持 guard 策略实际使用的形态（eq / in），其他操作符显式拒绝——
    视图定义是安全边界，严禁引入未审计的渲染路径。
    """
    meta = catalog.COLUMNS.get(field)
    if meta is None or operator not in ("eq", "in"):
        raise SecurityError(f"不支持的 RLS 谓词形态: field={field!r}, operator={operator!r}")
    quoted = f'{alias}."{meta.column}"'
    if operator == "eq":
        return f"{quoted} = {_literal(value, meta.dtype)}"
    rendered = ", ".join(_literal(v, meta.dtype) for v in value)
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
                return path + [neighbor]
            visited.add(neighbor)
            queue.append((neighbor, path + [neighbor]))
    return None


def _render_view_body(table: str, predicates: list[dict[str, Any]]) -> str:
    """渲染视图主体：FROM table + 跨表过滤 join 链 + WHERE。

    过滤字段的物理列不在本表时，经受控连接路径（BFS）join 到字段所属表。
    """
    from_sql = f"{table} {catalog.ALIASES[table]}"
    where_parts: list[str] = []
    joined: set[str] = set()
    for pred in predicates:
        field = pred["field"]
        meta = catalog.COLUMNS[field]
        if meta.table != table:
            path = _join_path(table, meta.table)
            if path is None:
                raise SecurityError(
                    f"表 {table!r} 的行过滤字段 {field!r} 无受控连接路径（策略配置错误）"
                )
            for a, b in zip(path, path[1:]):
                if b in joined:
                    continue
                local_col, remote_col = _JOIN_EDGES[a][b]
                from_sql += (
                    f" JOIN {b} {catalog.ALIASES[b]}"
                    f' ON {catalog.ALIASES[b]}."{remote_col}" = {catalog.ALIASES[a]}."{local_col}"'
                )
                joined.add(b)
            target_alias = catalog.ALIASES[meta.table]
        else:
            target_alias = catalog.ALIASES[table]
        where_parts.append(
            _render_predicate(field, pred["operator"], pred["value"], target_alias)
        )
    where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    return from_sql + where_sql


def build_secure_views(principal: str | None) -> dict[str, str]:
    """策略 -> 视图定义（纯函数，确定性）。

    返回 {视图名: CREATE VIEW SQL}。语义口径：

    - 视图面 = policy.allowed_tables 白名单；列以**逻辑字段名**输出；
    - 主事实表视图投影自身字段 + 受控连接的允许维度表字段（与编译器 JOIN
      语义同口径——province/brand 等维度字段物理在维表，经 join 可查询），
      维度列名与事实列冲突时事实列优先；
    - 禁列（forbidden_columns）物理不存在于任何视图；
    - 行过滤（RLS）仅施加于事实表视图（行级敏感数据全在事实数据上；dim
      实体目录视图不做行过滤），过滤字段经 BFS 受控连接路径 join 过滤。
    """
    policy = _policy_for(principal)
    resolved_filters = [_resolve_row_filter(rf, principal) for rf in policy.row_filters]
    views: dict[str, str] = {}
    for table in sorted(policy.allowed_tables):
        own_fields = [
            name
            for name, meta in sorted(catalog.COLUMNS.items())
            if meta.table == table and name not in policy.forbidden_columns
        ]
        if not own_fields:
            continue  # 全列被禁的表不生成视图（引用即不存在，等价拒绝）
        # 事实表判定（十九期 M5）：主事实表可扩展维度 join 投影；第二事实表
        # 经 FACT_JOIN_RULES 挂主表（无法直连维表），仅做 RLS 行过滤；
        # dim 实体视图只投影自身字段、不做行过滤
        expand_dims = table == catalog.FACT_TABLE
        is_fact = expand_dims or table in catalog.FACT_JOIN_RULES
        projections: list[str] = []
        seen: set[str] = set()
        joins: list[str] = []
        joined: set[str] = set()
        for name in own_fields:
            meta = catalog.COLUMNS[name]
            projections.append(f'{catalog.ALIASES[table]}."{meta.column}" AS "{name}"')
            seen.add(name)
        if expand_dims:
            for dim in sorted(catalog.JOIN_RULES):
                if dim not in policy.allowed_tables:
                    continue
                dim_rule = catalog.JOIN_RULES[dim]
                fact_col, dim_col = dim_rule.on[0][1], dim_rule.on[0][0]
                joins.append(
                    f" JOIN {dim} {catalog.ALIASES[dim]}"
                    f' ON {catalog.ALIASES[dim]}."{dim_col}" = {catalog.ALIASES[table]}."{fact_col}"'
                )
                joined.add(dim)
                for name, meta in sorted(catalog.COLUMNS.items()):
                    if (
                        meta.table == dim
                        and name not in policy.forbidden_columns
                        and name not in seen
                    ):
                        projections.append(
                            f'{catalog.ALIASES[dim]}."{meta.column}" AS "{name}"'
                        )
                        seen.add(name)
        where_parts: list[str] = []
        if is_fact or table in catalog.FACT_JOIN_RULES:
            for rf in resolved_filters:
                meta = catalog.COLUMNS[rf["field"]]
                if meta.table == table:
                    alias = catalog.ALIASES[table]
                else:
                    path = _join_path(table, meta.table)
                    if path is None:
                        raise SecurityError(
                            f"表 {table!r} 的行过滤字段 {rf['field']!r} 无受控连接路径"
                        )
                    for a, b in zip(path, path[1:]):
                        if b in joined:
                            continue
                        local_col, remote_col = _JOIN_EDGES[a][b]
                        joins.append(
                            f" JOIN {b} {catalog.ALIASES[b]}"
                            f' ON {catalog.ALIASES[b]}."{remote_col}"'
                            f' = {catalog.ALIASES[a]}."{local_col}"'
                        )
                        joined.add(b)
                    alias = catalog.ALIASES[meta.table]
                where_parts.append(
                    _render_predicate(rf["field"], rf["operator"], rf["value"], alias)
                )
        where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        view_name = f"{VIEW_PREFIX}{table}"
        views[view_name] = (
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT {', '.join(projections)} FROM {table} "
            f"{catalog.ALIASES[table]}{''.join(joins)}{where_sql}"
        )
    return views


def install_secure_views(conn: DuckDBPyConnection, principal: str | None) -> dict[str, str]:
    """在连接上安装该 principal 的安全视图（返回安装的视图定义）。"""
    views = build_secure_views(principal)
    for name, ddl in views.items():
        conn.execute(f'DROP VIEW IF EXISTS "{name}"')
        conn.execute(ddl)
    return views


_HARDEN_SETTINGS: tuple[tuple[str, str], ...] = (
    ("enable_external_access", "false"),
    ("lock_configuration", "true"),
)


def harden_connection(conn: DuckDBPyConnection) -> None:
    """连接级加固（十九期 M5，spec §3.4）：封死外部访问并锁定配置。

    幂等：先读 current_setting，已达标则跳过（lock_configuration 锁定后
    重复 SET 会被 DuckDB 拒绝）。fail-closed：设置失败即抛 SecurityError——
    严禁带弱配置继续执行（弱配置连接进入探索层等于治理管道整体失效）。
    """
    for name, expected in _HARDEN_SETTINGS:
        try:
            current = str(conn.execute(f"SELECT current_setting('{name}')").fetchone()[0])
            if current.lower() == expected:
                continue
            conn.execute(f"SET {name} = {expected}")
        except SecurityError:
            raise
        except Exception as exc:
            raise SecurityError(f"连接加固失败（{name} -> {expected}）: {exc}") from exc


__all__ = ["VIEW_PREFIX", "build_secure_views", "harden_connection", "install_secure_views"]
