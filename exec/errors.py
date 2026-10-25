"""SQL 执行层异常体系（二十一期自 exec.guards 迁出，re-export 保旧路径）。

执行层异常是全仓稳定 API（web/agent/retrieval/audit/tests 均按
``from exec.guards import SqlExecutionError`` 等旧路径消费），迁移后
exec.guards 对本模块做兼容 re-export，消费方零改动。
"""

from __future__ import annotations


class SqlExecutionError(RuntimeError):
    """SQL 执行失败（可自愈：精确报错可喂回 LLM 重写 DSL 后重试）。"""

    code = "sql_execution_error"


class QueryTimeoutError(SqlExecutionError):
    """查询超过 statement_timeout，已被中断取消。"""

    code = "query_timeout"


class MaxRowsScannedExceeded(SqlExecutionError):
    """任一基表扫描行数超过上限，熔断拒绝执行。"""

    code = "max_rows_scanned_exceeded"


class ResultLimitExceeded(SqlExecutionError):
    """返回行数超过 LIMIT 硬上限，熔断拒绝放行结果。"""

    code = "result_limit_exceeded"


class UnsafeSqlError(RuntimeError):
    """SQL 未通过只读语句白名单，拒绝执行且不进入自愈。"""

    code = "unsafe_sql"


__all__ = [
    "MaxRowsScannedExceeded",
    "QueryTimeoutError",
    "ResultLimitExceeded",
    "SqlExecutionError",
    "UnsafeSqlError",
]
