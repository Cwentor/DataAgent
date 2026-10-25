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
        """连接级加固（十九期 M5 spec §3.4，迁自 security/views.harden_connection）。

        封死外部访问并锁定配置。幂等：先读 current_setting，已达标则跳过
        （lock_configuration 锁定后重复 SET 会被 DuckDB 拒绝）。fail-closed：
        设置失败即抛 SecurityError——严禁带弱配置继续执行（弱配置连接进入
        探索层等于治理管道整体失效）。
        """
        from security.errors import SecurityError

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

    def install_secure_views(self, conn: Any, principal: str | None) -> dict[str, str]:
        """在连接上安装该 principal 的安全视图（迁自 security/views.install_secure_views）。

        使用 **TEMP 视图**：生命周期随连接、不污染库文件，且 read_only 连接
        （生产连接池口径）上可创建——探索层在池化只读连接上直接安装。
        """
        from security.views import (
            _VIEW_DDL_PREFIX,
            _VIEW_TEMP_DDL_PREFIX,
            build_secure_views,
        )

        views = build_secure_views(principal)
        for _name, ddl in views.items():
            # DDL 前缀常量化（评审 LOW #7）：严禁依赖散落的字面量 replace 耦合，
            # build_secure_views 改前缀时此处随常量同步失效
            conn.execute(ddl.replace(_VIEW_DDL_PREFIX, _VIEW_TEMP_DDL_PREFIX, 1))
        return views

    def insert_dataframe(self, conn: Any, table: str, df: Any) -> int:
        conn.execute(f"INSERT INTO {table} SELECT * FROM df")
        return len(df)


# 连接加固设置（DuckDB 专属，迁自 security/views）
_HARDEN_SETTINGS: tuple[tuple[str, str], ...] = (
    ("enable_external_access", "false"),
    ("lock_configuration", "true"),
)


__all__ = ["DuckDbAdapter"]
