"""PostgreSQL adapter 纯逻辑测试：DSN 构造 / 加固语句 / 异常归一 / 计划解析（不连服务）。

服务级行为（连接/加固/超时/池复用）由 tests/test_pg_backend_parity.py
承担（POSTGRES_TEST_* 环境变量驱动，本地未配置时 skip、CI postgres-parity 门禁执行）。
"""

from __future__ import annotations

import pytest

from exec.engine.postgres_adapter import (
    HARDEN_STATEMENTS,
    PG_QUERY_CANCELED_SQLSTATE,
    PostgresAdapter,
    parse_pg_scan_rows,
)
from exec.errors import QueryTimeoutError, SqlExecutionError


def test_dsn_defaults_from_settings(monkeypatch):
    import config.settings as settings

    monkeypatch.setattr(settings, "POSTGRES_HOST", "db.local")
    monkeypatch.setattr(settings, "POSTGRES_PORT", 5433)
    monkeypatch.setattr(settings, "POSTGRES_USER", "ro")
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "pw")
    monkeypatch.setattr(settings, "POSTGRES_DATABASE", "gmall")
    dsn = PostgresAdapter()._dsn
    assert dsn == {
        "host": "db.local",
        "port": 5433,
        "user": "ro",
        "password": "pw",
        "database": "gmall",
    }


def test_names_align_with_sqlglot_dialect():
    """name/dialect 与 sqlglot 方言名一致（护栏 AST 解析复用）。"""
    assert PostgresAdapter.name == "postgres"
    assert PostgresAdapter.dialect == "postgres"


def test_harden_statements_cover_security_baseline():
    """加固四要素（spec D3/D6）缺一不可——纯文本锁定。"""
    joined = " ".join(HARDEN_STATEMENTS(30000))
    assert "default_transaction_read_only = on" in joined
    assert "standard_conforming_strings = on" in joined  # Review Focus #3：转义口径 pin
    assert "lock_timeout = 10000" in joined
    assert "statement_timeout = 30000" in joined


def test_engine_error_timeout_57014():
    """Review Focus #1：statement_timeout 触发（SQLSTATE 57014）归一为 QueryTimeoutError。"""
    import psycopg

    assert PG_QUERY_CANCELED_SQLSTATE == "57014"
    err = psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    assert err.sqlstate == PG_QUERY_CANCELED_SQLSTATE
    assert isinstance(PostgresAdapter().engine_error(err), QueryTimeoutError)


def test_engine_error_generic_wrapped():
    """普通 psycopg 异常包装为 SqlExecutionError（喂自愈链路）。"""
    import psycopg

    err = psycopg.OperationalError("connection refused")
    norm = PostgresAdapter().engine_error(err)
    assert isinstance(norm, SqlExecutionError)
    assert "OperationalError" in str(norm)


def test_engine_error_foreign_returns_none():
    """非 psycopg 异常返回 None：调用方原样重抛（不得吞）。"""
    assert PostgresAdapter().engine_error(ValueError("x")) is None


# --------------------------------------------------------------------------- #
# EXPLAIN ANALYZE 解析（spec D5 / Review Focus #2）：样例文本锁定格式，不连服务
# --------------------------------------------------------------------------- #

# PostgreSQL 16 EXPLAIN ANALYZE 真实输出样例（版本敏感见 spec §8.1）
_PG_PLAN_SAMPLE = (
    "Aggregate  (cost=36.60..36.61 rows=1 width=8) (actual time=12.345..12.346 rows=1 loops=1)\n"
    "  ->  Seq Scan on order_detail  (cost=0.00..32.50 rows=1200 width=6) "
    "(actual time=0.012..9.876 rows=1180 loops=1)\n"
    "Planning Time: 0.123 ms\n"
    "Execution Time: 12.456 ms\n"
)

_PG_PLAN_NO_ACTUAL = "Seq Scan on order_detail  (cost=0.00..32.50 rows=1200 width=6)\n"


def test_parse_pg_scan_rows_takes_max_actual():
    rows, measurable = parse_pg_scan_rows(_PG_PLAN_SAMPLE)
    assert rows == 1180 and measurable is True


def test_parse_pg_scan_rows_ignores_cost_estimate():
    """cost 段估算行数（rows=1200 > actual 1180）绝不混入实际口径。"""
    rows, _ = parse_pg_scan_rows(_PG_PLAN_SAMPLE)
    assert rows == 1180


def test_parse_pg_scan_rows_not_measurable_without_actual():
    """Review Focus #2：无 actual 段（格式变更/版本演进）：(0, False) 交上层 warning 留痕。"""
    rows, measurable = parse_pg_scan_rows(_PG_PLAN_NO_ACTUAL)
    assert rows == 0 and measurable is False


# --------------------------------------------------------------------------- #
# 灌数通道（spec D13 / Review Focus #5）：占位符/标识符引用/空值清洗
# --------------------------------------------------------------------------- #


class _CaptureCursor:
    def __init__(self, captured: dict):
        self._captured = captured

    def executemany(self, sql, data):
        self._captured["sql"] = sql
        self._captured["data"] = data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _CaptureConn:
    def __init__(self):
        self.captured: dict = {}

    def cursor(self):
        return _CaptureCursor(self.captured)


def test_insert_dataframe_quoted_identifiers_and_nan_to_none():
    import pandas as pd

    conn = _CaptureConn()
    df = pd.DataFrame({"a": [1.0, float("nan")], "b": ["x", "y"]})
    n = PostgresAdapter().insert_dataframe(conn, "t", df)
    assert n == 2
    assert conn.captured["data"] == [(1.0, "x"), (None, "y")]
    assert conn.captured["sql"] == 'INSERT INTO "t" ("a", "b") VALUES (%s, %s)'


def test_insert_dataframe_positional_mode_for_unnamed_df():
    """无列名 DataFrame（元组行构造，columns=0..n）：位置对应 INSERT 无列子句。"""
    import pandas as pd

    conn = _CaptureConn()
    unnamed = pd.DataFrame([(1, "x"), (2, "y")])
    PostgresAdapter().insert_dataframe(conn, "t", unnamed)
    assert conn.captured["sql"] == 'INSERT INTO "t" VALUES (%s, %s)'


def test_insert_dataframe_nat_to_none():
    """datetime 列空值 NaT -> None（与 MySQL 口径一致：空值直插 TIMESTAMP 列报错）。"""
    import pandas as pd

    conn = _CaptureConn()
    df = pd.DataFrame({"t": [pd.Timestamp("2025-01-01"), pd.NaT]})
    PostgresAdapter().insert_dataframe(conn, "t", df)
    assert conn.captured["data"][1][0] is None


# --------------------------------------------------------------------------- #
# 注册中心（二十二期注册表化）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", ["duckdb", "mysql", "postgres"])
def test_get_adapter_by_backend(backend, monkeypatch):
    """注册中心按后端名取 adapter（mysql/postgres 惰性 import 生效）。"""
    import config.settings as settings
    from exec.engine import get_adapter

    monkeypatch.setattr(settings, "DB_BACKEND", backend)
    assert get_adapter().name == backend
    assert get_adapter(backend).name == backend


def test_get_adapter_rejects_unknown_with_dynamic_names(monkeypatch):
    """未知后端拒绝文案由注册表 keys 动态生成（含 postgres）。"""
    import config.settings as settings
    from exec.engine import get_adapter

    monkeypatch.setattr(settings, "DB_BACKEND", "oracle")
    with pytest.raises(ValueError, match="duckdb, mysql, postgres"):
        get_adapter()
