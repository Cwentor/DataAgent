"""MySQL adapter 纯逻辑测试：DSN 构造 / 加固语句 / 异常归一（不连服务）。

服务级行为（连接/加固/超时/池复用）由 tests/test_dual_backend_parity.py
承担（MYSQL_TEST_* 环境变量驱动，无本地 MySQL 时 skip）。
"""

from __future__ import annotations

import pymysql
import pytest

from exec.engine.mysql_adapter import HARDEN_STATEMENTS, MySqlAdapter
from exec.errors import QueryTimeoutError, SqlExecutionError


def test_dsn_defaults_from_settings(monkeypatch):
    import config.settings as settings

    monkeypatch.setattr(settings, "MYSQL_HOST", "db.local")
    monkeypatch.setattr(settings, "MYSQL_PORT", 3307)
    monkeypatch.setattr(settings, "MYSQL_USER", "ro")
    monkeypatch.setattr(settings, "MYSQL_PASSWORD", "pw")
    monkeypatch.setattr(settings, "MYSQL_DATABASE", "gmall")
    dsn = MySqlAdapter()._dsn
    assert dsn == {
        "host": "db.local",
        "port": 3307,
        "user": "ro",
        "password": "pw",
        "database": "gmall",
    }


def test_harden_statements_cover_security_baseline():
    """加固四要素（spec D2/D6/D8/D10）缺一不可——纯文本锁定。"""
    joined = " ".join(HARDEN_STATEMENTS(30000))
    assert "TRANSACTION READ ONLY" in joined
    assert "NO_BACKSLASH_ESCAPES" in joined  # spec D8 反斜杠防线一
    assert "cte_max_recursion_depth" in joined  # spec D6 递归深度
    assert "max_execution_time" in joined  # spec D2 服务端超时
    assert "max_execution_time = 30000" in joined


def test_engine_error_timeout_3024():
    """服务端超时（ER_QUERY_TIMEOUT 3024）归一为 QueryTimeoutError。"""
    err = pymysql.err.OperationalError(3024, "Query execution was interrupted")
    assert isinstance(MySqlAdapter().engine_error(err), QueryTimeoutError)


def test_engine_error_generic_wrapped():
    err = pymysql.err.ProgrammingError(1064, "You have an error in your SQL syntax")
    norm = MySqlAdapter().engine_error(err)
    assert isinstance(norm, SqlExecutionError)
    assert "ProgrammingError" in str(norm)


def test_engine_error_foreign_returns_none():
    """非 pymysql 异常返回 None：调用方原样重抛（不得吞）。"""
    assert MySqlAdapter().engine_error(ValueError("x")) is None


def test_insert_dataframe_nan_to_none():
    """DataFrame 批量插入的 NaN -> None 转换（DECIMAL 列兼容，DuckDB 口径对齐）。"""
    import pandas as pd

    captured: dict[str, object] = {}

    class _Cur:
        def executemany(self, sql, data):
            captured["sql"] = sql
            captured["data"] = data

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

    df = pd.DataFrame({"a": [1.0, float("nan")], "b": ["x", "y"]})
    n = MySqlAdapter().insert_dataframe(_Conn(), "t", df)
    assert n == 2
    assert captured["data"] == [(1.0, "x"), (None, "y")]
    assert "`t`" in str(captured["sql"]) and "`a`" in str(captured["sql"])


@pytest.mark.parametrize("backend", ["duckdb", "mysql"])
def test_get_adapter_by_backend(backend, monkeypatch):
    """注册中心按后端名取 adapter（mysql 惰性 import 生效）。"""
    import config.settings as settings
    from exec.engine import get_adapter

    monkeypatch.setattr(settings, "DB_BACKEND", backend)
    assert get_adapter().name == backend
    assert get_adapter(backend).name == backend


def test_get_adapter_rejects_unknown(monkeypatch):
    import config.settings as settings
    from exec.engine import get_adapter

    monkeypatch.setattr(settings, "DB_BACKEND", "oracle")
    with pytest.raises(ValueError, match="oracle"):
        get_adapter()


# --------------------------------------------------------------------------- #
# EXPLAIN ANALYZE 解析（spec D3 / Review Focus #6）：样例文本锁定格式，不连服务
# --------------------------------------------------------------------------- #

# MySQL 8.0 EXPLAIN ANALYZE 真实输出样例（8.0.18+；版本敏感见 spec §8.1）
_MYSQL_PLAN_SAMPLE = (
    "-> Table scan on order_detail  (cost=1.05 rows=100) (actual time=0.023..0.025 rows=98 loops=1)\n"
    "    -> Filter: (f.order_time >= DATE'2025-01-01')  (cost=0.35 rows=40) "
    "(actual time=0.010..0.012 rows=40 loops=1)\n"
)

_MYSQL_PLAN_NO_ACTUAL = "-> Count rows in order_detail  (cost=1.05 rows=100)\n"


def test_parse_mysql_scan_rows_takes_max_actual():
    from exec.engine.mysql_adapter import parse_mysql_scan_rows

    rows, measurable = parse_mysql_scan_rows(_MYSQL_PLAN_SAMPLE)
    assert rows == 98 and measurable is True


def test_parse_mysql_scan_rows_ignores_cost_estimate():
    """cost 段估算行数（rows=100 > actual 98）绝不混入实际口径。"""
    from exec.engine.mysql_adapter import parse_mysql_scan_rows

    rows, _ = parse_mysql_scan_rows(_MYSQL_PLAN_SAMPLE)
    assert rows == 98  # 非 cost 段的 100


def test_parse_mysql_scan_rows_not_measurable_without_actual():
    """无 actual 段（计划格式变更/版本演进）：(0, False) 交上层 warning 留痕。"""
    from exec.engine.mysql_adapter import parse_mysql_scan_rows

    rows, measurable = parse_mysql_scan_rows(_MYSQL_PLAN_NO_ACTUAL)
    assert rows == 0 and measurable is False


def test_insert_dataframe_positional_mode_for_unnamed_df():
    """无列名 DataFrame（元组行构造，columns=0..n）：位置对应 INSERT 无列子句。

    DuckDB 的 INSERT INTO t SELECT * FROM df 位置对应同语义；带列子句会拼出
    Unknown column '0'（CI mysql-parity 实锤）。具名 DataFrame 仍显式拼列。
    """
    import pandas as pd

    captured: dict[str, object] = {}

    class _Cur:
        def executemany(self, sql, data):
            captured["sql"] = sql
            captured["data"] = data

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

    unnamed = pd.DataFrame([(1, "x"), (2, "y")])  # columns: 0, 1
    MySqlAdapter().insert_dataframe(_Conn(), "t", unnamed)
    assert captured["sql"] == "INSERT INTO `t` VALUES (%s, %s)"

    named = pd.DataFrame({"a": [1], "b": ["x"]})
    MySqlAdapter().insert_dataframe(_Conn(), "t", named)
    assert captured["sql"] == "INSERT INTO `t` (`a`, `b`) VALUES (%s, %s)"
