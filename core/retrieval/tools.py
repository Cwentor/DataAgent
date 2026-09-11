"""typed 查询工具：execute_dsl_query -> ParquetRef（Control Plane 的取数接口）。

防线复用（与 tools.builtins._query_core 相同的防御栈，不绕过任何一层）：
1. ``guardrails.validate_dsl_payload``：网关层裸 SQL / 非法形态拒绝；
2. ``security.guard.apply_policy``：表级 / 列级 / 行级 RLS 注入；
3. ``compiler.compile_sql``：字段白名单 + 受限操作符确定性编译；
4. ``exec.guards.execute_sql``：只读 AST 校验 / 执行前审计（笛卡尔积等）/
   超时 / 扫描行数熔断 / LIMIT 硬上限；
5. ``export.export_to_parquet``：PII 脱敏 + 行数上限截断 + Parquet 物化；
6. ``quality.run_quality_assertions``：执行后 DataQA 结果断言（空结果 /
   NULL 率 / 负值 / 维度唯一性），发现随 ParquetRef.audit 输出。

与 NL 入口（run_guarded_query）的差异：本工具接收**结构化 DSL**（由编排器
Planner 产出），编译/执行报错直接上抛，由编排层的 error_context 自愈循环
统一处理（不在工具层内嵌自愈，避免双重重试语义）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from audit.logging import get_logger
from compiler.sql_compiler import compile_sql
from core.retrieval.export import DEFAULT_MAX_EXPORT_ROWS, export_to_parquet
from core.retrieval.guardrails import GuardrailViolation, validate_dsl_payload
from core.retrieval.parquet_ref import ParquetRef
from exec.guards import execute_sql
from security.guard import apply_policy
from semantic.dsl_schema import QueryDSL

logger = get_logger("core.retrieval.tools")


def _acquire_conn() -> duckdb.DuckDBPyConnection:
    """复用进程内默认只读连接池（与受控链路一致的连接治理）。"""
    from exec.pool import default_pool

    return default_pool().acquire()


def compile_dsl_to_duckdb(dsl_payload: Any, *, principal: str | None = None) -> str:
    """DSL -> DuckDB SQL 的纯编译（Dry-run，不执行）。

    经完整网关守卫 + RLS 策略后返回确定性编译产物；供编排层做计划期
    成本预估 / 调试展示。
    """
    dsl = validate_dsl_payload(dsl_payload, where="compile_dsl_to_duckdb")
    guarded_dsl = apply_policy(dsl, principal)
    return compile_sql(guarded_dsl)


def execute_dsl_query(
    dsl_payload: Any,
    *,
    principal: str | None = None,
    workspace: Path | str,
    name: str,
    query: str = "",
    max_rows: int = DEFAULT_MAX_EXPORT_ROWS,
    conn: duckdb.DuckDBPyConnection | None = None,
    timeout_ms: int | None = None,
) -> ParquetRef:
    """执行一次 DSL 查询并把结果物化为 ParquetRef（编排器标准取数工具）。

    参数：
    - ``dsl_payload``：QueryDSL 对象或符合契约的 dict（经网关重建强契约）；
    - ``principal``：数据权限主体（RLS）；
    - ``workspace``：会话工作区（结果导出到 ``workspace/inputs/<name>.parquet``）；
    - ``name``：数据集逻辑名；
    - ``query``：溯源用的自然语言问题；
    - ``max_rows``：导出行数上限（超出截断）。
    """
    from config import settings

    try:
        dsl: QueryDSL = validate_dsl_payload(dsl_payload, where="execute_dsl_query")
    except GuardrailViolation as exc:
        # 网关拒绝（裸 SQL 夹带 / 契约外字段）：安全审计必须留痕
        logger.warning("DSL 网关拒绝载荷", extra={"error": str(exc)[:500]})
        raise
    guarded_dsl = apply_policy(dsl, principal)
    sql = compile_sql(guarded_dsl)

    own_conn = conn is None
    if own_conn:
        conn = _acquire_conn()
    try:
        exec_result = execute_sql(
            conn,
            sql,
            statement_timeout_ms=timeout_ms or settings.QUERY_TIMEOUT_MS,
            max_scan_rows=settings.MAX_SCAN_ROWS,
            max_result_rows=max_rows,
        )
    finally:
        if own_conn:
            from exec.pool import default_pool

            default_pool().release(conn)

    # DataQAAgent 结果断言（执行后、导出前）：四类确定性质检，发现随
    # ParquetRef.audit 传递（不否决执行；处置权在 critic 与报告层）
    from core.retrieval.quality import run_quality_assertions

    qa_findings = [
        f.to_dict()
        for f in run_quality_assertions(exec_result.columns, exec_result.rows, guarded_dsl)
    ]
    # 质检发现分级留痕：error 级（维度组合重复=编译/聚合缺陷）必须可被采集告警
    for finding in qa_findings:
        record = logger.error if finding["severity"] == "error" else logger.warning
        record(
            f"DataQA 质检发现（{finding['check']}）",
            extra={"error": finding["message"][:500]},
        )
    logger.info(
        "DSL 查询执行完成",
        extra={"row_count": len(exec_result.rows), "scan_rows": exec_result.scan_rows},
    )

    inputs_dir = Path(workspace) / "inputs"
    return export_to_parquet(
        list(exec_result.columns),
        [list(row) for row in exec_result.rows],
        inputs_dir,
        name,
        query=query,
        dsl=guarded_dsl.model_dump(mode="json"),
        max_rows=max_rows,
        audit={"guard": exec_result.findings, "qa": qa_findings},
    )


__all__ = ["compile_dsl_to_duckdb", "execute_dsl_query"]
