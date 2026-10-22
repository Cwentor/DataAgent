"""SQL 执行层（P0/P1 资源治理与自愈支持）。

- guards.execute_sql：带 statement_timeout / 扫描行数上限 / LIMIT 硬上限的受控执行；
- 执行错误（SqlExecutionError 及其子类）携带精确引擎报错，供上层自愈循环喂回 LLM。
"""

from exec.guards import (
    ExecutionResult,
    MaxRowsScannedExceeded,
    QueryTimeoutError,
    ResultLimitExceeded,
    SqlExecutionError,
    UnsafeSqlError,
    assert_read_only_sql,
    execute_sql,
    parse_scan_rows,
    plan_rows_measurable,
)

__all__ = [
    "ExecutionResult",
    "MaxRowsScannedExceeded",
    "QueryTimeoutError",
    "ResultLimitExceeded",
    "SqlExecutionError",
    "UnsafeSqlError",
    "assert_read_only_sql",
    "execute_sql",
    "parse_scan_rows",
    "plan_rows_measurable",
]
