"""DuckDB 适配器（二十一期）：现有执行/取消/计划解析逻辑自 exec.guards 下沉。

行为与迁移前逐点一致（连接只读、execute 游标、interrupt 取消、
EXPLAIN ANALYZE 计划文本解析），差异仅在归属模块。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from config import settings
from exec.engine.plan_text import parse_scan_rows, plan_rows_measurable
from exec.engine.watchdog import run_with_timeout
from exec.errors import SqlExecutionError


class DuckDbAdapter:
    """DuckDB 查询执行适配器（EngineAdapter 协议实现）。"""

    name = "duckdb"
    dialect = "duckdb"

    def __init__(self, db_path: str | Path | None = None) -> None:
        # db_path 可注入：测试 / 建仓链路使用独立文件；缺省读 settings.DB_PATH
        self._db_path = str(db_path) if db_path is not None else str(settings.DB_PATH)

    def connect_readonly(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(self._db_path, read_only=True)

    def run_query(self, conn: Any, sql: str) -> tuple[list[str], list[list[Any]]]:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description]
        rows = [list(row) for row in cur.fetchall()]
        return columns, rows

    def cancel(self, conn: Any) -> None:
        conn.interrupt()

    def explain_scan_rows(self, conn: Any, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
        plan_sql = "EXPLAIN ANALYZE " + sql
        _, plan_rows = run_with_timeout(self, conn, plan_sql, timeout_ms)
        plan_text = (
            str(plan_rows[0][1])
            if plan_rows and len(plan_rows[0]) > 1
            else (str(plan_rows[0][0]) if plan_rows else "")
        )
        return parse_scan_rows(plan_text), plan_rows_measurable(plan_text)

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        if isinstance(exc, duckdb.Error):
            return SqlExecutionError(f"{type(exc).__name__}: {exc}")
        return None

    def harden(self, conn: Any) -> None:
        # 文件级只读已由 connect(read_only=True) 保证；探索层连接加固
        # （enable_external_access 等）在 security/views 层实现，此处无引擎级设置
        return None

    def insert_dataframe(self, conn: Any, table: str, df: Any) -> int:
        conn.execute(f"INSERT INTO {table} SELECT * FROM df")
        return len(df)


__all__ = ["DuckDbAdapter"]
