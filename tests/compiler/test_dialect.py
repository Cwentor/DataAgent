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


# --------------------------------------------------------------------------- #
# PostgreSQL 方言渲染器（二十二期 spec D7/D8/D17）
# --------------------------------------------------------------------------- #


class TestPostgresRenderer:
    def test_quotes_are_double_quotes(self):
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        assert r.quote_ident("gmv") == '"gmv"'
        assert r.quote_ident('a"b') == '"a""b"'
        assert r.quote_column("f", "order_id") == "f.order_id"

    def test_backslash_not_doubled_in_string_escape(self):
        """spec D7：PG 在 standard_conforming_strings=on 下反斜杠即字面量，翻倍反而引入多余字符。"""
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        assert r.escape_string_literal("a\\b") == "'a\\b'"
        assert r.escape_string_literal("a'b") == "'a''b'"

    def test_timestamp_literal_native(self):
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        assert (
            r.timestamp_literal(datetime(2025, 1, 1, 0, 0, 0)) == "TIMESTAMP '2025-01-01 00:00:00'"
        )
        assert r.timestamp_literal("2025-01-01") == "TIMESTAMP '2025-01-01'"

    def test_date_trunc_native_all_granularities(self):
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        for g in ("day", "week", "month", "quarter"):
            assert r.date_trunc(g, "f.order_time") == f"date_trunc('{g}', f.order_time)"

    def test_spine_uses_generate_series_with_pg_interval(self):
        """spec D8：generate_series 原生集合函数，步长转译为 PG INTERVAL 字面量。"""
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        body = r.spine_cte_sql(
            "day", "2025-01-01 00:00:00", "2025-01-03 00:00:00", "INTERVAL 1 DAY"
        )
        assert body == (
            "SELECT gs AS \"day\" FROM generate_series(TIMESTAMP '2025-01-01 00:00:00', "
            "TIMESTAMP '2025-01-03 00:00:00', INTERVAL '1 day') AS gs"
        )
        assert r.with_keyword() == "WITH"

    def test_spine_rejects_unknown_interval_form(self):
        """Review Focus #4：步长字面量超出编译器枚举形态即 CompileError，严禁静默拼接。"""
        import pytest

        from compiler.dialect.postgres import PostgresRenderer
        from compiler.errors import CompileError

        with pytest.raises(CompileError, match="INTERVAL"):
            PostgresRenderer().spine_cte_sql(
                "day", "2025-01-01 00:00:00", "2025-01-03 00:00:00", "INTERVAL 2 FORTNIGHT"
            )

    def test_date_add_additive_form(self):
        """spec D17：PG 无 date_add 函数，以加法等价 + INTERVAL 字面量转译。"""
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        assert r.date_add("f.order_time", "INTERVAL 1 YEAR") == "f.order_time + INTERVAL '1 year'"
        assert r.date_add("f.order_time", "INTERVAL 3 MONTH") == "f.order_time + INTERVAL '3 month'"


def test_date_add_byte_identical_on_anchored_dialects():
    """golden 锚点保护：DuckDB/MySQL 的 date_add 渲染与迁移前内联产物逐字节一致。"""
    from compiler.dialect.duckdb import DuckDbRenderer
    from compiler.dialect.mysql import MySqlRenderer

    assert DuckDbRenderer().date_add("d.ts", "INTERVAL 1 YEAR") == "date_add(d.ts, INTERVAL 1 YEAR)"
    assert (
        MySqlRenderer().date_add("d.ts", "INTERVAL 1 MONTH") == "date_add(d.ts, INTERVAL 1 MONTH)"
    )


def test_get_renderer_supports_postgres():
    """注册表化后 postgres 取得 PostgresRenderer（name 与 sqlglot 方言名一致）。"""
    import config.settings as settings

    old = settings.SQL_DIALECT
    settings.SQL_DIALECT = "postgres"
    try:
        assert get_renderer().name == "postgres"
    finally:
        settings.SQL_DIALECT = old


class TestCompilePostgresDialect:
    """端到端：PostgreSQL 方言编译产物结构断言（sqlglot postgres 可解析即语法合法）。"""

    def test_month_groupby_uses_native_date_trunc(self):
        import sqlglot

        sql = _compile_dsl(_month_gmv_dsl(), "postgres")
        assert "date_trunc('month'" in sql
        sqlglot.parse_one(sql, read="postgres")

    def test_fill_gaps_compiles_to_generate_series(self):
        import sqlglot

        sql = _compile_dsl(_fill_gaps_dsl(), "postgres")
        assert sql.startswith("WITH __spine")
        assert "generate_series" in sql
        assert "INTERVAL '1 day'" in sql
        sqlglot.parse_one(sql, read="postgres")

    def test_comparison_uses_additive_date_shift(self):
        """comparison 配对在 PG 走加法平移（无 date_add 函数依赖，spec D17）。"""
        import sqlglot

        dsl = _month_gmv_dsl()
        dsl["time_filter"]["comparison"] = "yoy"
        sql = _compile_dsl(dsl, "postgres")
        assert "date_add(" not in sql
        assert "+ INTERVAL '1 year'" in sql
        sqlglot.parse_one(sql, read="postgres")


# --------------------------------------------------------------------------- #
# HAVING 指标引用方言原语（二十二期 D18）：PG 语义禁止 HAVING 引用输出别名
# --------------------------------------------------------------------------- #


def _having_gmv_dsl() -> dict:
    """HAVING 聚合后过滤 DSL（形态取自 golden_dataset 用例 [23]）。"""
    return {
        "metrics": [
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
        ],
        "dimensions": [{"field": "category1_name", "alias": None}],
        "filters": [],
        "order_by": [{"field": "gmv", "direction": "desc"}],
        "having": [{"field": "gmv", "operator": "gt", "value": 1000.0}],
        "limit": 100,
    }


def test_having_metric_ref_alias_on_anchored_dialects():
    """golden 锚点保护：DuckDB/MySQL 的 HAVING 指标引用为输出别名（逐字节不变）。"""
    from compiler.dialect.duckdb import DuckDbRenderer
    from compiler.dialect.mysql import MySqlRenderer

    assert DuckDbRenderer().having_metric_ref("gmv", "SUM(f.x)") == '"gmv"'
    assert MySqlRenderer().having_metric_ref("gmv", "SUM(f.x)") == "`gmv`"


def test_having_metric_ref_replays_expr_on_postgres():
    """spec D18：PG 语义禁止 HAVING 引用输出别名，重放底层聚合表达式。"""
    from compiler.dialect.postgres import PostgresRenderer

    r = PostgresRenderer()
    assert r.having_metric_ref("gmv", "SUM(f.split_total_amount)") == "SUM(f.split_total_amount)"


def test_compile_having_postgres_no_bare_alias():
    """端到端：HAVING 编译产物在 PG 下重放聚合表达式（无裸别名引用）。"""
    import sqlglot

    sql = _compile_dsl(_having_gmv_dsl(), "postgres")
    assert 'HAVING "gmv"' not in sql
    assert "HAVING SUM(f.split_total_amount) > 1000.0" in sql
    sqlglot.parse_one(sql, read="postgres")


def test_compile_having_anchored_dialects_unchanged():
    """端到端：DuckDB/MySQL 的 HAVING 产物保持输出别名引用（golden 不变）。"""
    assert 'HAVING "gmv" > 1000.0' in _compile_dsl(_having_gmv_dsl(), "duckdb")
    assert "HAVING `gmv` > 1000.0" in _compile_dsl(_having_gmv_dsl(), "mysql")


# --------------------------------------------------------------------------- #
# ClickHouse 方言渲染器（二十三期 spec D6/D7）
# --------------------------------------------------------------------------- #


class TestClickHouseRenderer:
    """ClickHouse 方言九原语（二十三期 spec D6/D7）+ 桶数计算边界。"""

    def _r(self):
        from compiler.dialect.clickhouse import ClickHouseRenderer

        return ClickHouseRenderer()

    def test_quote_ident_escapes_backtick(self):
        assert self._r().quote_ident("a`b") == "`a``b`"

    def test_quote_column(self):
        assert self._r().quote_column("t", "col") == "`t`.`col`"

    def test_timestamp_literal(self):
        assert (
            self._r().timestamp_literal("2025-06-01 00:00:00")
            == "toDateTime('2025-06-01 00:00:00')"
        )

    def test_date_trunc_four_granularities(self):
        r = self._r()
        assert r.date_trunc("day", "d") == "toStartOfDay(d)"
        assert r.date_trunc("week", "d") == "toMonday(d)"  # 周一锚定（spec D6）
        assert r.date_trunc("month", "d") == "toStartOfMonth(d)"
        assert r.date_trunc("quarter", "d") == "toStartOfQuarter(d)"

    def test_date_trunc_unknown_granularity_rejected(self):
        import pytest

        from compiler.errors import CompileError

        with pytest.raises(CompileError):
            self._r().date_trunc("hour", "d")

    def test_date_add_passthrough(self):
        # 编译器 INTERVAL 1 DAY 形态在 CH 合法，透传拼接（spec D6）
        assert self._r().date_add("d", "INTERVAL 1 DAY") == "d + INTERVAL 1 DAY"

    def test_having_metric_ref_uses_alias(self):
        assert self._r().having_metric_ref("gmv", "sum(x)") == "`gmv`"

    def test_spine_day_1826_buckets(self):
        # 5 年 day 粒度：2021-01-01 ~ 2025-12-31（编译器 end_incl 已回退末桶）
        sql = self._r().spine_cte_sql(
            "ts", "2021-01-01 00:00:00", "2025-12-31 00:00:00", "INTERVAL 1 DAY"
        )
        assert sql == (
            "SELECT addDays(toDateTime('2021-01-01 00:00:00'), number) AS `ts` "
            "FROM numbers(1826)"
        )

    def test_spine_week_step_normalized_to_7_days(self):
        sql = self._r().spine_cte_sql(
            "ts", "2021-01-04 00:00:00", "2021-02-01 00:00:00", "INTERVAL 1 WEEK"
        )
        assert "addDays(" in sql and "number * 7" in sql and "FROM numbers(5)" in sql

    def test_spine_quarter_3month_buckets(self):
        # 2021-01-01 起步长 3 个月至 2021-10-01（末桶起点）：4 个桶
        # （01-01 / 04-01 / 07-01 / 10-01，闭区间含端点）
        sql = self._r().spine_cte_sql(
            "ts", "2021-01-01 00:00:00", "2021-10-01 00:00:00", "INTERVAL 3 MONTH"
        )
        assert "addMonths(toDateTime('2021-01-01 00:00:00'), number * 3)" in sql
        assert "FROM numbers(4)" in sql

    def test_spine_month_end_boundary(self):
        # 月历算术边界：1/31 起步长 1 个月，2 月无 31 日——桶数按锚点日未满计
        # （e.day < s.day 差一个月）：2021-01-31 ~ 2021-03-01 → 2 桶
        sql = self._r().spine_cte_sql(
            "ts", "2021-01-31 00:00:00", "2021-03-01 00:00:00", "INTERVAL 1 MONTH"
        )
        assert "FROM numbers(2)" in sql

    def test_spine_unknown_interval_form_rejected(self):
        import pytest

        from compiler.errors import CompileError

        with pytest.raises(CompileError):
            self._r().spine_cte_sql(
                "ts", "2021-01-01 00:00:00", "2021-02-01 00:00:00", "INTERVAL 1 HOUR"
            )

    def test_spine_negative_window_rejected(self):
        import pytest

        from compiler.errors import CompileError

        with pytest.raises(CompileError):
            self._r().spine_cte_sql(
                "ts", "2021-02-01 00:00:00", "2021-01-01 00:00:00", "INTERVAL 1 DAY"
            )

    def test_with_keyword_non_recursive(self):
        assert self._r().with_keyword() == "WITH"

    def test_escape_string_literal_backslash_family(self):
        # 反斜杠转义系：\ 翻倍 + ' 写作 \'（spec D6，与 MySQL '' 口径不同）
        assert self._r().escape_string_literal("a'b\\c") == "'a\\'b\\\\c'"


def test_divide_byte_identical_on_anchored_dialects():
    """golden 锚点保护：DuckDB/MySQL/PG 除法渲染与重构前逐字节一致（除零 NULLIF）。

    除法原语（二十三期补）只承载"除零口径 + 精度口径"：锚点三方言输出必须
    与内联渲染逐字相同，否则 35 条 golden SQL 锚点（eval_runner sql_ok）失效。
    """
    from compiler.dialect.duckdb import DuckDbRenderer
    from compiler.dialect.mysql import MySqlRenderer
    from compiler.dialect.postgres import PostgresRenderer

    expected = "COALESCE(SUM(f.a), 0) / NULLIF(COUNT(DISTINCT f.b), 0)"
    for r in (DuckDbRenderer(), MySqlRenderer(), PostgresRenderer()):
        assert r.divide("COALESCE(SUM(f.a), 0)", "COUNT(DISTINCT f.b)") == expected, r.name


def test_clickhouse_divide_float_transpiled():
    """CH Decimal 除法 "S = S1"（截断不舍入）→ toFloat64 浮点除法对齐 DOUBLE 口径。

    CI clickhouse-parity 实锤：(cur.gmv - prev.gmv) / prev.gmv 在金额列
    DECIMAL(16,2) 下退化为 2 位小数（0.252180 → 0.25），与 DuckDB 的
    Decimal 除法返回 DOUBLE 不等价。
    """
    from compiler.dialect.clickhouse import ClickHouseRenderer

    out = ClickHouseRenderer().divide("(cur.`gmv` - prev.`gmv`)", "prev.`gmv`")
    assert out == ("toFloat64((cur.`gmv` - prev.`gmv`)) / NULLIF(toFloat64(prev.`gmv`), 0)")


def test_clickhouse_comparison_growth_uses_float_division():
    """端到端：comparison 增长率列在 CH 方言下走浮点除法（其余结构不变）。"""
    import sqlglot

    dsl = _month_gmv_dsl()
    dsl["time_filter"]["comparison"] = "yoy"
    sql = _compile_dsl(dsl, "clickhouse")
    assert "toFloat64(" in sql and "NULLIF(toFloat64(" in sql
    assert " AS `gmv_yoy`" in sql
    # 语法合法性：CH 方言可解析即形态成立
    assert sqlglot.parse_one(sql, read="clickhouse") is not None


def test_clickhouse_registry(monkeypatch):
    """方言注册表：SQL_DIALECT=clickhouse 返回 ClickHouseRenderer（惰性工厂）。"""
    import config.settings as settings
    from compiler.dialect.clickhouse import ClickHouseRenderer

    monkeypatch.setattr(settings, "SQL_DIALECT", "clickhouse")
    assert isinstance(get_renderer(), ClickHouseRenderer)


def test_three_existing_renderers_unchanged_byte_wise():
    """golden 红线：duckdb/mysql/postgres 三 renderer 关键产物逐字节不变（回归锚）。"""
    import config.settings as settings

    cases = {
        "duckdb": (
            lambda r: r.date_trunc("week", "o.order_time"),
            "date_trunc('week', o.order_time)",
        ),
        "mysql": (
            lambda r: r.date_trunc("week", "o.order_time"),
            "DATE_SUB(DATE(o.order_time), INTERVAL WEEKDAY(DATE(o.order_time)) DAY)",
        ),
        "postgres": (
            lambda r: r.date_add("d", "INTERVAL 1 DAY"),
            "d + INTERVAL '1 day'",
        ),
    }
    old = settings.SQL_DIALECT
    try:
        for name, (fn, expected) in cases.items():
            settings.SQL_DIALECT = name
            assert fn(get_renderer()) == expected, name
    finally:
        settings.SQL_DIALECT = old
