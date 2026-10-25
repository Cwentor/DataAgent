"""方言层测试：DuckDB 渲染与重构前逐字节一致（golden 锚点保护）+ 注册中心行为。"""

from __future__ import annotations

from datetime import datetime

from compiler.dialect import get_renderer
from compiler.dialect.duckdb import DuckDbRenderer


def test_duckdb_renderer_snippets():
    """DuckDB 各原语输出与迁移前内联渲染逐字节一致。"""
    r = DuckDbRenderer()
    assert r.quote_ident("gmv") == '"gmv"'
    assert r.quote_ident('a"b') == '"a""b"'
    assert r.quote_column("f", "order_id") == "f.order_id"
    assert r.timestamp_literal(datetime(2025, 1, 1, 0, 0, 0)) == "TIMESTAMP '2025-01-01 00:00:00'"
    assert r.timestamp_literal("2025-01-01") == "TIMESTAMP '2025-01-01'"
    assert r.date_trunc("month", "f.order_time") == "date_trunc('month', f.order_time)"
    assert r.escape_string_literal("a'b") == "'a''b'"
    assert r.with_keyword() == "WITH"
    spine = r.spine_cte_sql("day", "2025-01-01 00:00:00", "2025-01-03 00:00:00", "INTERVAL 1 DAY")
    assert spine == (
        "SELECT UNNEST(generate_series(TIMESTAMP '2025-01-01 00:00:00', "
        "TIMESTAMP '2025-01-03 00:00:00', INTERVAL 1 DAY)) AS \"day\""
    )


def test_resolve_dialect_explicit_overrides_backend(monkeypatch):
    """SQL_DIALECT 显式配置优先；缺省跟随 DB_BACKEND（只测解析，不实例化 mysql）。"""
    import config.settings as settings
    from compiler.dialect import resolve_dialect

    monkeypatch.setattr(settings, "SQL_DIALECT", "")
    monkeypatch.setattr(settings, "DB_BACKEND", "duckdb")
    assert resolve_dialect() == "duckdb"
    monkeypatch.setattr(settings, "SQL_DIALECT", "mysql")
    assert resolve_dialect() == "mysql"
    monkeypatch.setattr(settings, "SQL_DIALECT", "")
    monkeypatch.setattr(settings, "DB_BACKEND", "mysql")
    assert resolve_dialect() == "mysql"


def test_get_renderer_rejects_unknown(monkeypatch):
    """未知方言 fail-fast（CompileError），不静默回退默认。"""
    import pytest

    import config.settings as settings
    from compiler.errors import CompileError

    monkeypatch.setattr(settings, "SQL_DIALECT", "oracle")
    with pytest.raises(CompileError, match="oracle"):
        get_renderer()


# --------------------------------------------------------------------------- #
# MySQL 方言渲染器（二十一期 spec D5/D6/D7/D8）
# --------------------------------------------------------------------------- #


class TestMySqlRenderer:
    def test_quotes_are_backticks(self):
        from compiler.dialect.mysql import MySqlRenderer

        r = MySqlRenderer()
        assert r.quote_ident("gmv") == "`gmv`"
        assert r.quote_ident("a`b") == "`a``b`"
        assert r.quote_column("f", "order_id") == "`f`.`order_id`"

    def test_backslash_doubled_in_string_escape(self):
        """spec D8 第二道防线：NO_BACKSLASH_ESCAPES 被覆盖时翻倍仍兜底。"""
        from compiler.dialect.mysql import MySqlRenderer

        r = MySqlRenderer()
        assert r.escape_string_literal("a\\b") == "'a\\\\b'"
        assert r.escape_string_literal("a'b") == "'a''b'"
        assert r.escape_string_literal("a\\'b") == "'a\\\\''b'"

    def test_timestamp_literal_cast_datetime(self):
        from compiler.dialect.mysql import MySqlRenderer

        r = MySqlRenderer()
        assert (
            r.timestamp_literal("2025-01-01 00:00:00") == "CAST('2025-01-01 00:00:00' AS DATETIME)"
        )
        assert (
            r.timestamp_literal(datetime(2025, 1, 1, 0, 0, 0))
            == "CAST('2025-01-01 00:00:00' AS DATETIME)"
        )

    def test_date_trunc_mappings(self):
        from compiler.dialect.mysql import MySqlRenderer

        r = MySqlRenderer()
        assert r.date_trunc("day", "f.order_time") == "DATE(f.order_time)"
        assert (
            r.date_trunc("week", "f.order_time")
            == "DATE_SUB(DATE(f.order_time), INTERVAL WEEKDAY(DATE(f.order_time)) DAY)"
        )
        assert (
            r.date_trunc("month", "f.order_time")
            == "DATE_SUB(DATE(f.order_time), INTERVAL DAYOFMONTH(DATE(f.order_time)) - 1 DAY)"
        )
        assert (
            r.date_trunc("quarter", "f.order_time")
            == "MAKEDATE(YEAR(f.order_time), 1) + INTERVAL (QUARTER(f.order_time) - 1) * 3 MONTH"
        )

    def test_spine_is_recursive_cte(self):
        from compiler.dialect.mysql import MySqlRenderer

        r = MySqlRenderer()
        body = r.spine_cte_sql(
            "day", "2025-01-01 00:00:00", "2025-01-03 00:00:00", "INTERVAL 1 DAY"
        )
        assert body.startswith("SELECT CAST('2025-01-01 00:00:00' AS DATETIME) AS `day`")
        assert "UNION ALL" in body and "FROM __spine" in body
        assert r.with_keyword() == "WITH RECURSIVE"


def _month_gmv_dsl() -> dict:
    """月粒度 GMV 最小 DSL（形态取自 eval/golden_dataset.json）。"""
    return {
        "metrics": [
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
        ],
        "dimensions": [{"field": "order_time", "alias": None}],
        "time_filter": {
            "granularity": "month",
            "range_type": "absolute",
            "relative": None,
            "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
            "comparison": "none",
            "reference_date": None,
            "time_field": "order_time",
        },
        "filters": [],
    }


def _fill_gaps_dsl() -> dict:
    """day 粒度补零 DSL（形态取自 eval/golden_dataset.json）。"""
    dsl = _month_gmv_dsl()
    dsl["time_filter"]["granularity"] = "day"
    dsl["fill_gaps"] = True
    return dsl


def _compile_dsl(dsl_payload: dict, dialect: str):
    import config.settings as settings
    from compiler.sql_compiler import compile_sql
    from semantic.dsl_schema import QueryDSL

    old = settings.SQL_DIALECT
    settings.SQL_DIALECT = dialect
    try:
        return compile_sql(QueryDSL.model_validate(dsl_payload))
    finally:
        settings.SQL_DIALECT = old


class TestCompileMysqlDialect:
    """端到端：MySQL 方言编译产物结构断言（sqlglot mysql 可解析即语法合法）。"""

    def test_month_groupby_uses_mysql_expr(self):
        import sqlglot

        sql = _compile_dsl(_month_gmv_dsl(), "mysql")
        assert "date_trunc(" not in sql
        assert "DAYOFMONTH" in sql
        sqlglot.parse_one(sql, read="mysql")

    def test_fill_gaps_compiles_to_recursive_cte(self):
        import sqlglot

        sql = _compile_dsl(_fill_gaps_dsl(), "mysql")
        assert sql.startswith("WITH RECURSIVE __spine")
        assert "generate_series" not in sql
        assert "UNNEST" not in sql
        sqlglot.parse_one(sql, read="mysql")

    def test_filter_string_uses_mysql_escape(self):
        dsl = _month_gmv_dsl()
        dsl["filters"] = [{"field": "order_status", "operator": "eq", "value": "已支付\\完成"}]
        sql = _compile_dsl(dsl, "mysql")
        assert "已支付\\\\完成" in sql  # 反斜杠翻倍
        assert "TIMESTAMP '" not in sql  # 时间字面量全部 CAST AS DATETIME

    def test_duckdb_output_unchanged_when_dialect_reset(self):
        """方言切换用完即还原：duckdb 默认输出不受 mysql 编译影响。"""
        before = _compile_dsl(_month_gmv_dsl(), "duckdb")
        _compile_dsl(_month_gmv_dsl(), "mysql")
        after = _compile_dsl(_month_gmv_dsl(), "duckdb")
        assert before == after
        assert "date_trunc('month'" in before
