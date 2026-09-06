"""受控查询执行核心：复用现有 NL -> DSL -> Compiler -> Exec 防御链路的工具底座。

本模块是"保持现有防御链路"的落点——任何数据查询类工具（query_metric /
trend_analysis / export_report 的查询阶段）都必须经由这里执行，而不是
自己裸跑 SQL：

1. NL -> DSL：``agent.pipeline.run_pipeline_with_status``（LLM 严格校验 +
   确定性启发式兜底，失败拒绝而非猜测）；
2. 安全守卫：``security.guard.apply_policy`` 施加表级/列级/行级 RLS；
   外部传入的 DSL 同样强制过守卫（防权限逃逸）；
3. 编译：``compiler.sql_compiler.compile_sql``（字段白名单 + 受限操作符）；
4. 受控执行：``exec.guards.execute_sql``（只读 AST 校验 / 超时中断 /
   扫描行数熔断 / LIMIT 硬上限）；
5. SQL 自愈：编译或执行报错时把精确报错喂回 LLM 重写 DSL 并重试
   （至少 1 次，受 settings.SQL_SELF_HEAL_MAX_RETRIES 约束），
   重写后的 DSL 重新过安全守卫。

执行器与自愈重写器均可注入（executor / rewriter），便于测试替换与
与 web.service 的既有桩函数对接；留空时使用生产默认实现。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import duckdb

from agent.pipeline import rewrite_dsl, run_pipeline_with_status
from audit.metrics import default_registry
from compiler.sql_compiler import CompileError, compile_sql
from config import settings
from exec.guards import ExecutionResult, SqlExecutionError, execute_sql
from security.guard import apply_policy
from semantic.dsl_schema import QueryDSL

__all__ = ["GuardedQueryResult", "run_guarded_query"]


@dataclass
class GuardedQueryResult:
    """一次受控查询的完整产物（供工具层组装展示与审计）。"""

    query: str
    dsl: QueryDSL
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    scan_rows: int
    rewrites: int
    degraded: bool
    duration_ms: float
    # 结果缓存命中标记（QUERY_CACHE_ENABLED 开启时可能为 True）
    cached: bool = False


def _acquire_conn() -> duckdb.DuckDBPyConnection:
    """从进程内默认只读连接池取连接（None 注入时使用）。"""
    from exec.pool import default_pool

    return default_pool().acquire()


def run_guarded_query(
    query: str,
    principal: str | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    *,
    executor: Callable[..., Any] | None = None,
    rewriter: Callable[..., Any] | None = None,
    dsl: QueryDSL | None = None,
) -> GuardedQueryResult:
    """执行一次带全部护栏的查询，返回结构化结果；失败抛原始异常（可自愈/可审计）。

    参数：
    - ``query``：自然语言问题（用于 NL->DSL 与自愈重写上下文）；
    - ``principal``：数据权限主体（RLS 注入）；
    - ``conn``：只读连接；None 时从 exec.pool.default_pool 获取（用完归还）；
    - ``executor``：受控执行器，默认 exec.guards.execute_sql（可注入桩函数）；
    - ``rewriter``：自愈重写器，默认 agent.pipeline.rewrite_dsl（可注入桩函数）；
    - ``dsl``：外部传入的 QueryDSL（如趋势工具规范化后的 DSL），
      传入时跳过 NL->DSL 生成，但仍强制安全守卫。
    """
    executor = executor or execute_sql
    rewriter = rewriter or rewrite_dsl
    started = time.perf_counter()

    own_conn = conn is None
    if own_conn:
        conn = _acquire_conn()
    try:
        return _run_guarded(
            query,
            principal,
            conn,
            executor=executor,
            rewriter=rewriter,
            dsl=dsl,
            started=started,
        )
    finally:
        if own_conn:
            from exec.pool import default_pool

            default_pool().release(conn)


def _run_guarded(
    query: str,
    principal: str | None,
    conn: duckdb.DuckDBPyConnection,
    *,
    executor: Callable[..., Any],
    rewriter: Callable[..., Any],
    dsl: QueryDSL | None,
    started: float,
) -> GuardedQueryResult:
    # 1) NL -> DSL（或使用外部 DSL），并施加安全守卫（表/列/行级 RLS）
    degraded = False
    if dsl is None:
        dsl, degraded = run_pipeline_with_status(query, principal)
    else:
        dsl = apply_policy(dsl, principal)

    # 2) 编译 + 受控执行 + SQL 自愈重写循环
    max_rewrites = settings.SQL_SELF_HEAL_MAX_RETRIES
    current_dsl = dsl
    rewrites = 0
    _cached_flag = False
    while True:
        try:
            sql = compile_sql(current_dsl)
            cache_key = _result_cache_key(principal, sql)
            cached = _cache_get(cache_key) if cache_key is not None else None
            if cached is not None:
                # 缓存命中：还原为 ExecutionResult 形态，后续消费路径保持一致
                columns, rows, scan_rows = cached
                exec_result = ExecutionResult(
                    columns=columns, rows=rows, scan_rows=scan_rows, duration_ms=0.0
                )
                _cached_flag = True
                break
            exec_result = executor(
                conn,
                sql,
                statement_timeout_ms=settings.QUERY_TIMEOUT_MS,
                max_scan_rows=settings.MAX_SCAN_ROWS,
                max_result_rows=settings.MAX_RESULT_ROWS,
            )
            if cache_key is not None:
                _cache_put(cache_key, exec_result)
            break
        except (CompileError, SqlExecutionError) as exc:
            if rewrites >= max_rewrites:
                raise
            try:
                rewritten = rewriter(
                    query,
                    current_dsl,
                    f"{type(exc).__name__}: {exc}",
                    attempts=1,
                    principal=principal,
                )
                current_dsl = apply_policy(rewritten, principal)
                rewrites += 1
            except Exception:
                # 自愈失败（无 LLM / LLM 拒绝 / 安全守卫拒绝）-> 透传原始报错
                default_registry().record_self_heal_failure()
                raise exc from None

    duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return GuardedQueryResult(
        query=query,
        dsl=current_dsl,
        sql=sql,
        columns=list(exec_result.columns),
        rows=[[_json_safe(v) for v in row] for row in exec_result.rows],
        scan_rows=exec_result.scan_rows,
        rewrites=rewrites,
        degraded=degraded,
        duration_ms=duration_ms,
        cached=_cached_flag,
    )


def _result_cache_key(principal: str | None, sql: str) -> str | None:
    """结果缓存键；缓存关闭时返回 None（跳过缓存路径）。"""
    from exec.query_cache import query_cache_enabled, query_cache_key

    if not query_cache_enabled():
        return None
    return query_cache_key(
        principal,
        sql,
        statement_timeout_ms=settings.QUERY_TIMEOUT_MS,
        max_scan_rows=settings.MAX_SCAN_ROWS,
        max_result_rows=settings.MAX_RESULT_ROWS,
    )


def _cache_get(key: str) -> Any | None:
    from exec.query_cache import default_query_cache

    return default_query_cache().get(key)


def _cache_put(key: str, exec_result: Any) -> None:
    from exec.query_cache import default_query_cache

    default_query_cache().put(key, exec_result.columns, exec_result.rows, exec_result.scan_rows)


def _json_safe(value: Any) -> Any:
    """把 DuckDB 返回值转成 JSON 可序列化类型（datetime/date/Decimal）。"""
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
