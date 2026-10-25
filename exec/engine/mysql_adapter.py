"""MySQL 适配器（二十一期 spec D1/D2/D10）：PyMySQL 只读执行面。

超时取消（spec D2）：服务端 max_execution_time 为主——仅对 SELECT 生效，
恰与只读白名单口径一致；超时错误码 3024 归一为 QueryTimeoutError。客户端
read_timeout 留 5s 余量保证服务端先超时、连接可复用；KILL QUERY 方案
已否决（需第二管理连接 + PROCESS 权限，竞态复杂）。

连接加固（spec D10，fail-closed）：只读事务 / NO_BACKSLASH_ESCAPES
（反斜杠防线一）/ cte_max_recursion_depth（递归 spine）/ max_execution_time。
扫描预检（spec D3）：EXPLAIN ANALYZE actual rows 口径，Task 4 落地。
"""

from __future__ import annotations

import math
from typing import Any

from config import settings
from exec.engine.normalize import normalize_cell
from exec.errors import QueryTimeoutError, SqlExecutionError

# MySQL ER_QUERY_TIMEOUT（服务端 max_execution_time 触发，8.0.1+）
ER_QUERY_TIMEOUT = 3024


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
        raise NotImplementedError("扫描行预检由 T4 落地（EXPLAIN ANALYZE actual rows 解析）")

    def insert_dataframe(self, conn, table: str, df) -> int:
        cols = list(df.columns)
        placeholders = ", ".join(["%s"] * len(cols))
        sql = f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in cols)}) VALUES ({placeholders})"
        # NaN -> None：pandas NaN 直插 DECIMAL 列会报错（DuckDB 隐式转 NULL，对齐口径）
        data = [
            tuple(None if v is None or (isinstance(v, float) and math.isnan(v)) else v for v in row)
            for row in df.itertuples(index=False)
        ]
        with conn.cursor() as cur:
            for i in range(0, len(data), 5000):
                cur.executemany(sql, data[i : i + 5000])
        return len(data)


__all__ = ["ER_QUERY_TIMEOUT", "HARDEN_STATEMENTS", "MySqlAdapter"]
