"""探索层执行器（十九期 M6）：不可提升 SQL 在安全视图上受治理执行。

LLM 产出的 SQL 经审批后**仍不执行原文**——先由 sqlglot 把基表引用重写到
该 principal 的 sec_* 安全视图（越权表精确拒绝），再在 TEMP 视图上经
``exec.execute_sql`` 三护栏（超时 / 扫描熔断 / 行数硬上限）执行，结果走
``export_to_parquet`` 既有管道（PII 脱敏 + audit.exploration=True）。

视图安装用 **TEMP 视图**：read_only 连接（生产连接池口径）上可创建，生命周期
随连接，不污染库文件。连接加固（harden_connection）由连接管理方（M6 接线处）
在安装前调用。

敏感判定（exploration_risk）：SQL 文本级引用 principal 的 forbidden_columns
即判敏感——L4 自动放行的硬边界（含敏感列必须挂起人工审批）。
"""

from __future__ import annotations

import re
from pathlib import Path

import sqlglot
from sqlglot import exp

from security.errors import SecurityError
from security.policy import POLICIES
from security.views import (
    VIEW_PREFIX,
    build_secure_views,
    harden_connection,
    install_secure_views,
)
from semantic import catalog


def _principal_for(principal: str | None):
    name = principal or "admin"
    policy = POLICIES.get(name)
    if policy is None:
        raise SecurityError(f"未登记的主体: {name!r}")
    return policy


def _view_name(table: str) -> str:
    return f"{VIEW_PREFIX}{table}"


def rewrite_sql_to_views(sql: str, principal: str | None) -> tuple[str | None, list[str]]:
    """把基表引用重写为该 principal 的 sec_* 安全视图。

    重写规则：凡语义目录登记的物理表名，一律替换为 sec_<表>——principal 无
    该表视图（越权）或表未登记，精确拒绝并指名，严禁静默放行。
    """
    policy = _principal_for(principal)
    views = build_secure_views(principal)
    try:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
    except Exception as exc:
        return None, [f"SQL 解析失败: {exc}"]
    rejects: list[str] = []
    # CTE 别名（含嵌套）：CTE 体内部的表引用照常重写，别名引用本身跳过——
    # 计算型 CTE 在探索层是合法形态（这正是探索层存在的意义），与提升闸门
    # 的保守内联不同
    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name
        if name.startswith(VIEW_PREFIX) or name in cte_names:
            continue  # 已是视图名（幂等）/ CTE 内部引用（其体单独重写）
        if name not in catalog.ALIASES:
            rejects.append(f"未登记的表 {name!r}（不在语义目录，拒绝执行）")
            continue
        view = _view_name(name)
        if view not in views:
            rejects.append(f"越权表 {name!r}（主体 {policy.name!r} 无安全视图，拒绝执行）")
            continue
        table.set("this", sqlglot.to_identifier(view))
    if rejects:
        return None, rejects
    return tree.sql(dialect="duckdb"), []


def exploration_risk(sql: str, principal: str | None) -> tuple[bool, list[str]]:
    """探索执行风险判定：(是否含敏感列, 触达表清单)。

    敏感判定为 SQL 文本级：引用 principal 的 forbidden_columns 即敏感——
    L4 自动放行的硬边界（Review Focus #3：含敏感列必须挂起人工审批）。
    """
    policy = _principal_for(principal)
    # 大小写规范化（评审 HIGH #4）：DuckDB 标识符大小写不敏感，敏感判定若按
    # 原文名精确匹配，SELECT DISCOUNT_AMOUNT 可绕过"含敏感列必须挂人工审批"
    # 的 L4 硬边界——两侧统一 lower 后比对
    forbidden = {c.lower() for c in policy.forbidden_columns}
    tables: set[str] = set()
    sensitive = False
    try:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
    except Exception:
        return True, []  # 解析失败按敏感处理（保守，交人工审批）
    for column in tree.find_all(exp.Column):
        col_name = column.name.lower()
        if col_name in forbidden:
            sensitive = True
        # 触达表按列归属归集（目录键统一小写）；表级引用单独收集
        meta_col = catalog.COLUMNS.get(col_name)
        if meta_col is not None and meta_col.table in catalog.ALIASES:
            tables.add(meta_col.table)
    for table in tree.find_all(exp.Table):
        name = table.name
        if name in catalog.ALIASES:
            tables.add(name)
    return sensitive, sorted(tables)


def execute_exploration_query(
    sql: str,
    *,
    principal: str | None,
    workspace: Path | str,
    name: str,
    query: str = "",
    conn: object | None = None,
    max_rows: int = 1000,
) -> object:
    """审批通过后的探索执行：重写 → TEMP 视图安装 → 三护栏执行 → PII 导出。

    返回 ParquetRef（audit 带 exploration=True）。SQL 永不原文执行。
    """
    from config import settings
    from core.retrieval.export import export_to_parquet
    from exec.guards import execute_sql

    rewritten, rejects = rewrite_sql_to_views(sql, principal)
    if rejects:
        raise SecurityError("探索 SQL 重写拒绝：" + "；".join(rejects))

    own_conn = conn is None
    if own_conn:
        from exec.pool import default_pool

        conn = default_pool().acquire()
    try:
        harden_connection(conn)
        install_secure_views(conn, principal)
        exec_result = execute_sql(
            conn,
            rewritten,
            statement_timeout_ms=settings.QUERY_TIMEOUT_MS,
            max_scan_rows=settings.MAX_SCAN_ROWS,
            max_result_rows=max_rows,
        )
    finally:
        if own_conn:
            from exec.pool import default_pool

            default_pool().release(conn)

    inputs_dir = Path(workspace) / "inputs"
    return export_to_parquet(
        list(exec_result.columns),
        [list(row) for row in exec_result.rows],
        inputs_dir,
        name,
        query=query,
        dsl=None,
        max_rows=max_rows,
        audit={
            "exploration": True,
            "original_sql_digest": re.sub(r"\s+", " ", sql)[:500],
            "guard": exec_result.findings,
        },
    )
