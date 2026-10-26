"""PostgreSQL 适配器（二十二期 spec D2-D6）：psycopg3 只读执行面。

超时取消（spec D3）：服务端 statement_timeout 为主（连接 options + 会话 SET
双保险），SQLSTATE 57014 归一为 QueryTimeoutError；pg_cancel_backend 方案
已否决（需独立 pid 通道，注入复杂度不值得——与 MySQL KILL QUERY 否决同口径）。

连接加固（spec D6，fail-closed）：default_transaction_read_only /
standard_conforming_strings（转义口径确定性，spec D7）/ lock_timeout /
statement_timeout。扫描预检（spec D5）：EXPLAIN ANALYZE actual 段 rows 口径，
解析不可计量时 warning 留痕跳过（不静默放行也不硬熔断）。

探索层（spec D10）：PostgreSQL 原生支持 CREATE OR REPLACE TEMP VIEW，与
DuckDB 同走 TEMP 视图路线（MySQL 无临时视图故走内联派生表）。
"""

from __future__ import annotations

import re
from typing import Any

from config import settings
from exec.engine.base import physical_from_rows
from exec.engine.normalize import normalize_cell
from exec.errors import QueryTimeoutError, SqlExecutionError

# PostgreSQL statement_timeout 触发的 SQLSTATE（query_canceled）
PG_QUERY_CANCELED_SQLSTATE = "57014"

# 只认 actual 段的 rows（cost 段的 rows=N 是优化器估算，绝不能混入实际口径）。
# 输出样例（PG 16 树形文本）：
#   Seq Scan on order_detail  (cost=0.00..32.50 rows=1200 width=6)
#     (actual time=0.012..9.876 rows=1180 loops=1)
# 版本敏感：格式随版本演进可能变化（spec §8.1），不可计量时返回 (0, False)
# 由上层 warning 留痕，不得静默当成 0 行放行。
_PG_ACTUAL_ROWS_RE = re.compile(r"\(\s*actual\s+time=[^)]*?\brows=(\d+)", re.IGNORECASE)


def parse_pg_scan_rows(plan_text: str) -> tuple[int, bool]:
    """解析 EXPLAIN ANALYZE 树形文本，返回 (最大 actual rows, 是否可计量)。

    口径与 DuckDB/MySQL 版对齐：取所有算子实际行数的最大值作为"扫描行数"
    预算（多表查询即各基表扫描的最大值）。
    """
    counts = [int(m.group(1)) for m in _PG_ACTUAL_ROWS_RE.finditer(plan_text)]
    if not counts:
        return 0, False
    return max(counts), True


def HARDEN_STATEMENTS(timeout_ms: int | None = None) -> tuple[str, ...]:
    """PostgreSQL 会话加固语句清单（spec D6；顺序执行、fail-closed）。

    作为模块级函数供纯逻辑单测锁定基线（四项缺一不可）。SET 天然幂等，
    无需 DuckDB 式先读回再设。
    """
    t = timeout_ms if timeout_ms is not None else settings.QUERY_TIMEOUT_MS
    return (
        "SET default_transaction_read_only = on",
        "SET standard_conforming_strings = on",
        "SET lock_timeout = 10000",
        f"SET statement_timeout = {int(t)}",
    )


class PostgresAdapter:
    """PostgreSQL 查询执行适配器（EngineAdapter 协议实现）。"""

    name = "postgres"
    dialect = "postgres"

    def __init__(self, dsn: dict[str, Any] | None = None) -> None:
        # dsn 可注入（双后端一致性测试指向独立测试库）；缺省读 settings.POSTGRES_*
        self._dsn = dsn or {
            "host": settings.POSTGRES_HOST,
            "port": settings.POSTGRES_PORT,
            "user": settings.POSTGRES_USER,
            "password": settings.POSTGRES_PASSWORD,
            "database": settings.POSTGRES_DATABASE,
        }

    def _new_conn(self):
        import psycopg

        # 连接级预加固（options）与 harden() 会话 SET 构成双保险（spec D3/D6）：
        # 即使 harden 未被调用（直连场景），连接也已带只读/超时/转义口径
        return psycopg.connect(
            host=self._dsn["host"],
            port=self._dsn["port"],
            user=self._dsn["user"],
            password=self._dsn["password"],
            dbname=self._dsn["database"],
            autocommit=True,
            connect_timeout=5,
            options=(
                f"-c statement_timeout={int(settings.QUERY_TIMEOUT_MS)} "
                "-c default_transaction_read_only=on "
                "-c standard_conforming_strings=on"
            ),
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
            columns = [d.name for d in (cur.description or [])]
            rows = [[normalize_cell(v) for v in row] for row in cur.fetchall()]
        return columns, rows

    def cancel(self, conn) -> None:
        # 服务端 statement_timeout 已自限（spec D3）；看门狗回调为空操作，
        # 仅等待 worker 携超时异常返回
        return None

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        import psycopg

        if not isinstance(exc, psycopg.Error):
            return None
        if getattr(exc, "sqlstate", None) == PG_QUERY_CANCELED_SQLSTATE:
            return QueryTimeoutError(f"查询超时（PostgreSQL statement_timeout），已强制取消: {exc}")
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
            raise SecurityError(f"PostgreSQL 连接加固失败: {exc}") from exc

    def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
        """EXPLAIN ANALYZE 预检（spec D5）：actual 段 rows 口径，解析失败不可计量。"""
        from exec.engine.watchdog import run_with_timeout

        _, rows = run_with_timeout(self, conn, "EXPLAIN ANALYZE " + sql, timeout_ms)
        # PostgreSQL EXPLAIN 输出为单列多行（每行计划文本一行）
        plan_text = "\n".join(str(r[0]) for r in rows) if rows else ""
        return parse_pg_scan_rows(plan_text)

    def install_secure_views(self, conn, principal: str | None) -> dict[str, str]:
        """在连接上安装该 principal 的安全视图（探索层 TEMP 视图路线，spec D10）。

        与 DuckDB 同路：TEMP 视图生命周期随连接、不污染库文件，加固后只读
        会话（生产连接池口径）上可创建——default_transaction_read_only 只锁
        DML，DDL 不受限。方法存在性分发（exploration 侧 getattr），MySQL
        adapter 无此方法（内联派生表路线）。
        """
        from security.views import (
            _VIEW_DDL_PREFIX,
            _VIEW_TEMP_DDL_PREFIX,
            build_secure_views,
        )

        views = build_secure_views(principal)
        for _name, ddl in views.items():
            with conn.cursor() as cur:
                # DDL 前缀常量化（评审 LOW #7）：严禁依赖散落的字面量 replace 耦合，
                # build_secure_views 改前缀时此处随常量同步失效
                cur.execute(ddl.replace(_VIEW_DDL_PREFIX, _VIEW_TEMP_DDL_PREFIX, 1))
        return views

    def insert_dataframe(self, conn, table: str, df) -> int:
        cols = [str(c) for c in df.columns]
        placeholders = ", ".join(["%s"] * len(cols))
        # 无列名 DataFrame（元组行构造，columns 为 0..n 整数）：按位置对应
        # 灌入（与 DuckDB `INSERT INTO t SELECT * FROM df` 同语义；具名
        # DataFrame 仍显式拼列——标识符双引号，杜绝保留字列名裸拼）。
        if all(c.isdigit() for c in cols):
            sql = f'INSERT INTO "{table}" VALUES ({placeholders})'
        else:
            quoted_cols = ", ".join(f'"{c}"' for c in cols)
            sql = f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})'
        # 空值统一清洗：pd.isna 覆盖 None/NaN/pd.NA/NaT（与 MySQL 口径一致：
        # pandas 空值直插 NUMERIC/TIMESTAMP 列报错，显式转 NULL 对齐 DuckDB 行为）
        import pandas as pd

        data = [tuple(None if pd.isna(v) else v for v in row) for row in df.itertuples(index=False)]
        with conn.cursor() as cur:
            for i in range(0, len(data), 5000):
                cur.executemany(sql, data[i : i + 5000])
        return len(data)

    def physical_columns(self, conn=None) -> dict[str, dict[str, str]]:
        """PostgreSQL 物理列（二十四期 spec D1/D3/D4）：按 `POSTGRES_SCHEMA` 过滤。

        不过滤会把 `pg_catalog` / `information_schema` 自身等系统 schema 一并
        读进目录（与二十一期 MySQL 实例级污染同型）；多 schema 同名表也无法
        用单层 `{表: {列}}` 表达，故按配置的单一 schema 读取。

        占位符 `%s`（psycopg 参数风格），与执行层 SQL（无参数）分属不同通道。
        """
        sql = (
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s ORDER BY table_name, ordinal_position"
        )
        own = None
        if conn is None:
            own = self.connect_readonly()
            conn = own
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (settings.POSTGRES_SCHEMA,))
                rows = cur.fetchall()
        finally:
            if own is not None:
                own.close()
        return physical_from_rows(rows)


__all__ = [
    "HARDEN_STATEMENTS",
    "PG_QUERY_CANCELED_SQLSTATE",
    "PostgresAdapter",
    "parse_pg_scan_rows",
]
