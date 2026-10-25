"""MySQL 适配器（二十一期 spec D1/D2/D10）：PyMySQL 只读执行面。

超时取消（spec D2）：服务端 max_execution_time 为主——仅对 SELECT 生效，
恰与只读白名单口径一致；超时错误码 3024 归一为 QueryTimeoutError。客户端
read_timeout 留 5s 余量保证服务端先超时、连接可复用；KILL QUERY 方案
已否决（需第二管理连接 + PROCESS 权限，竞态复杂）。

连接加固（spec D10，fail-closed）：只读事务 / NO_BACKSLASH_ESCAPES
（反斜杠防线一）/ cte_max_recursion_depth（递归 spine）/ max_execution_time。
扫描预检（spec D3）：EXPLAIN ANALYZE actual rows 口径（8.0.18+），
解析不可计量时 warning 留痕跳过（不静默放行也不硬熔断）。
"""

from __future__ import annotations

import math
import re
from typing import Any

from config import settings
from exec.engine.normalize import normalize_cell
from exec.errors import QueryTimeoutError, SqlExecutionError

# MySQL ER_QUERY_TIMEOUT（服务端 max_execution_time 触发，8.0.1+）
ER_QUERY_TIMEOUT = 3024

# 只认 actual 段的 rows（cost 段的 rows=N 是优化器估算，绝不能混入实际口径）。
# 输出样例（8.0.18+ 树形文本）：
#   -> Table scan on t  (cost=1.05 rows=100) (actual time=0.023..0.025 rows=98 loops=1)
# 版本敏感：格式随版本演进可能变化（spec §8.1），不可计量时返回 (0, False)
# 由上层 warning 留痕，不得静默当成 0 行放行。
_MYSQL_ACTUAL_ROWS_RE = re.compile(r"\(\s*actual\s+time=[^)]*?\brows=(\d+)", re.IGNORECASE)


def parse_mysql_scan_rows(plan_text: str) -> tuple[int, bool]:
    """解析 EXPLAIN ANALYZE 树形文本，返回 (最大 actual rows, 是否可计量)。

    口径与 DuckDB 版 parse_scan_rows 对齐：取所有算子实际行数的最大值
    作为"扫描行数"预算（多表查询即各基表扫描的最大值）。
    """
    counts = [int(m.group(1)) for m in _MYSQL_ACTUAL_ROWS_RE.finditer(plan_text)]
    if not counts:
        return 0, False
    return max(counts), True


def HARDEN_STATEMENTS(timeout_ms: int | None = None) -> tuple[str, ...]:
    """MySQL 会话加固语句清单（spec D10；顺序执行、fail-closed）。

    作为模块级函数供纯逻辑单测锁定基线（四项缺一不可）。
    """
    t = timeout_ms if timeout_ms is not None else settings.QUERY_TIMEOUT_MS
    return (
        "SET SESSION TRANSACTION READ ONLY",
        "SET SESSION sql_mode = CONCAT(@@sql_mode, ',NO_BACKSLASH_ESCAPES')",
        "SET SESSION cte_max_recursion_depth = 100000",
        f"SET SESSION max_execution_time = {int(t)}",
    )


class MySqlAdapter:
    """MySQL 查询执行适配器（EngineAdapter 协议实现）。"""

    name = "mysql"
    dialect = "mysql"

    def __init__(self, dsn: dict[str, Any] | None = None) -> None:
        # dsn 可注入（双后端一致性测试指向独立测试库）；缺省读 settings.MYSQL_*
        self._dsn = dsn or {
            "host": settings.MYSQL_HOST,
            "port": settings.MYSQL_PORT,
            "user": settings.MYSQL_USER,
            "password": settings.MYSQL_PASSWORD,
            "database": settings.MYSQL_DATABASE,
        }

    def _new_conn(self):
        import pymysql

        # read_timeout > 服务端超时 +5s：保证服务端先超时（3024），连接不因
        # 客户端读超时被 pymysql 报废，归还池后可继续复用
        return pymysql.connect(
            **self._dsn,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
            read_timeout=settings.QUERY_TIMEOUT_MS / 1000.0 + 5.0,
        )

    def connect_readonly(self):
        conn = self._new_conn()
        try:
            self.harden(conn)
        except Exception:
            conn.close()
            raise
        return conn

    def run_query(self, conn, sql: str) -> tuple[list[str], list[list[Any]]]:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = [[normalize_cell(v) for v in row] for row in cur.fetchall()]
        return columns, rows

    def cancel(self, conn) -> None:
        # 服务端 max_execution_time 已自限（spec D2）；看门狗回调为空操作，
        # 仅等待 worker 携超时异常返回
        return None

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        import pymysql

        if not isinstance(exc, pymysql.err.Error):
            return None
        if (
            isinstance(exc, pymysql.err.OperationalError)
            and exc.args
            and exc.args[0] == ER_QUERY_TIMEOUT
        ):
            return QueryTimeoutError(f"查询超时（MySQL max_execution_time），已强制取消: {exc}")
        return SqlExecutionError(f"{type(exc).__name__}: {exc}")

    def harden(self, conn) -> None:
        from security.errors import SecurityError

        try:
            with conn.cursor() as cur:
                for stmt in HARDEN_STATEMENTS():
                    cur.execute(stmt)
        except SecurityError:
            raise
        except Exception as exc:
            raise SecurityError(f"MySQL 连接加固失败: {exc}") from exc

    def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
        """EXPLAIN ANALYZE 预检（spec D3）：actual rows 口径，解析失败不可计量。"""
        from exec.engine.watchdog import run_with_timeout

        _, rows = run_with_timeout(self, conn, "EXPLAIN ANALYZE " + sql, timeout_ms)
        # MySQL EXPLAIN ANALYZE 输出单列长文本（一列一行）
        plan_text = str(rows[0][0]) if rows else ""
        return parse_mysql_scan_rows(plan_text)

    def insert_dataframe(self, conn, table: str, df) -> int:
        cols = [str(c) for c in df.columns]
        placeholders = ", ".join(["%s"] * len(cols))
        # 无列名 DataFrame（元组行构造，columns 为 0..n 整数）：按位置对应
        # 灌入（与 DuckDB `INSERT INTO t SELECT * FROM df` 同语义；物理表
        # 列名全为字母/下划线，纯数字列名即无列名形态——CI 实锤拼进列子句
        # 报 Unknown column '0'）；具名 DataFrame 仍显式拼列。
        if all(c.isdigit() for c in cols):
            sql = f"INSERT INTO `{table}` VALUES ({placeholders})"
        else:
            sql = f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in cols)}) VALUES ({placeholders})"
        # NaN/pd.NA -> None：pandas 空值直插 DECIMAL 列会报错（DuckDB 隐式
        # 转 NULL，对齐口径）；nullable Int64 列的 itertuples 产出 pd.NA
        import pandas as pd

        def _cell(v: Any) -> Any:
            if v is None or v is pd.NA:
                return None
            if isinstance(v, float) and math.isnan(v):
                return None
            return v

        data = [tuple(_cell(v) for v in row) for row in df.itertuples(index=False)]
        with conn.cursor() as cur:
            for i in range(0, len(data), 5000):
                cur.executemany(sql, data[i : i + 5000])
        return len(data)


__all__ = ["ER_QUERY_TIMEOUT", "HARDEN_STATEMENTS", "MySqlAdapter"]
