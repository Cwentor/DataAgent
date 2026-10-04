"""SQL 提升闸门单测（十九期 M4）：可升构造执行等价 + 拒升矩阵。

验收铁律：每条可升 SQL 必须满足「原始执行结果 == 提升后 DSL 重编译执行结果」
（sorted 行集判等，真值断言，mock 数仓）——纯结构断言不算通过。
"""

from __future__ import annotations

from compiler.sql_compiler import compile_sql
from core.retrieval.sql_lift import lift_sql
from semantic.dsl_schema import QueryDSL


def _assert_lift_equivalent(conn, sql: str) -> QueryDSL:
    """提升 -> 契约校验 -> 重编译 -> 执行等价（sorted 行集判等）。"""
    result = lift_sql(sql)
    assert result.ok, (sql, [r.to_dict() for r in result.rejections])
    dsl = QueryDSL.model_validate(result.dsl)
    compiled = compile_sql(dsl)
    original_rows = sorted(map(repr, conn.execute(sql).fetchall()))
    lifted_rows = sorted(map(repr, conn.execute(compiled).fetchall()))
    assert original_rows == lifted_rows, (sql, compiled, original_rows[:3], lifted_rows[:3])
    return dsl


def test_lift_simple_aggregate_equivalent(conn):
    _assert_lift_equivalent(
        conn,
        "SELECT SUM(f.order_amount) AS gmv FROM fact_orders f " "WHERE f.pay_status = 'SUCCESS'",
    )


def test_lift_count_distinct_and_group_join_equivalent(conn):
    dsl = _assert_lift_equivalent(
        conn,
        "SELECT p.category AS category, COUNT(DISTINCT f.user_id) AS buyers "
        "FROM fact_orders f JOIN dim_product p ON p.product_id = f.product_id "
        "WHERE f.pay_status = 'SUCCESS' GROUP BY p.category "
        "ORDER BY buyers DESC LIMIT 10",
    )
    assert dsl.metrics[0].agg.value == "count_distinct"


def test_lift_time_window_becomes_time_filter(conn):
    dsl = _assert_lift_equivalent(
        conn,
        "SELECT SUM(f.order_amount) AS gmv FROM fact_orders f "
        "WHERE f.order_time >= TIMESTAMP '2024-06-01 00:00:00' "
        "AND f.order_time < TIMESTAMP '2024-07-01 00:00:00'",
    )
    assert dsl.time_filter is not None
    assert dsl.time_filter.absolute.start.isoformat() == "2024-06-01"
    assert dsl.time_filter.absolute.end.isoformat() == "2024-07-01"


def test_lift_having_in_between_equivalent(conn):
    _assert_lift_equivalent(
        conn,
        "SELECT p.brand AS brand, SUM(f.order_amount) AS gmv "
        "FROM fact_orders f JOIN dim_product p ON p.product_id = f.product_id "
        "WHERE f.order_amount BETWEEN 10 AND 5000 "
        "GROUP BY p.brand HAVING SUM(f.order_amount) > 100 "
        "ORDER BY gmv DESC LIMIT 5",
    )
    # HAVING 提升后引用指标别名（编译期语义等价由执行断言保证）


def test_lift_distinct_projection_equivalent(conn):
    dsl = _assert_lift_equivalent(
        conn,
        "SELECT DISTINCT p.brand AS brand FROM fact_orders f "
        "JOIN dim_product p ON p.product_id = f.product_id ORDER BY brand ASC LIMIT 100",
    )
    assert dsl.metrics == []
    assert [d.field for d in dsl.dimensions] == ["brand"]


def test_lift_pure_reference_cte_inlined(conn):
    dsl = _assert_lift_equivalent(
        conn,
        "WITH base AS (SELECT * FROM fact_orders) "
        "SELECT SUM(base.order_amount) AS gmv FROM base "
        "WHERE base.pay_status = 'SUCCESS'",
    )
    assert dsl.metrics[0].field == "order_amount"


def test_lift_without_limit_gets_default_and_note(conn):
    result = lift_sql(
        "SELECT SUM(f.order_amount) AS gmv FROM fact_orders f WHERE f.pay_status = 'SUCCESS'"
    )
    assert result.ok
    assert result.dsl["limit"] == 100
    assert result.notes, "语义收窄必须记入 notes（Review Focus #3）"


def test_lift_without_where_becomes_unbounded_warning_not_error(conn):
    result = lift_sql("SELECT SUM(f.order_amount) AS gmv FROM fact_orders f")
    assert result.ok
    assert result.dsl["filters"] == []


# --------------------------------------------------------------------------- #
# 拒升矩阵（精确原因；M4-T2 补全后此处只保留冒烟）
# --------------------------------------------------------------------------- #
def test_lift_rejects_unparseable_text():
    result = lift_sql("这不是 SQL")
    assert not result.ok
    assert result.rejections[0].clause in ("parse", "statement")


def test_lift_rejects_cte_with_computation():
    result = lift_sql(
        "WITH t AS (SELECT product_id, SUM(1) AS c FROM fact_orders GROUP BY product_id) "
        "SELECT SUM(t.product_id) AS x FROM t"
    )
    assert not result.ok
    assert "CTE" in result.rejections[0].construct


def test_lift_rejects_case_when():
    result = lift_sql(
        "SELECT CASE WHEN f.pay_status = 'SUCCESS' THEN 1 ELSE 0 END AS flag FROM fact_orders f"
    )
    assert not result.ok
    assert "Case" in result.rejections[0].construct or "CASE" in result.rejections[0].construct


def test_lift_rejects_or_predicate():
    result = lift_sql(
        "SELECT SUM(f.order_amount) AS gmv FROM fact_orders f "
        "WHERE f.pay_status = 'SUCCESS' OR f.pay_status = 'FAILED'"
    )
    assert not result.ok
    assert "OR" in result.rejections[0].construct


def test_lift_rejects_count_star():
    result = lift_sql("SELECT COUNT(*) AS cnt FROM fact_orders f")
    assert not result.ok
    assert "COUNT(*)" in result.rejections[0].construct


def test_lift_rejects_union_root():
    result = lift_sql(
        "SELECT SUM(f.order_amount) AS gmv FROM fact_orders f "
        "UNION SELECT SUM(f.order_amount) AS gmv FROM fact_orders f"
    )
    assert not result.ok
    assert result.rejections[0].clause == "statement"


def test_lift_rejects_window_function():
    result = lift_sql(
        "SELECT f.order_time AS order_time, SUM(f.order_amount) OVER (ORDER BY f.order_time) AS cum "
        "FROM fact_orders f"
    )
    assert not result.ok


def test_lift_rejects_limit_over_contract():
    result = lift_sql("SELECT SUM(f.order_amount) AS gmv FROM fact_orders f LIMIT 99999")
    assert not result.ok
    assert "LIMIT" in result.rejections[0].construct


def test_lift_rejects_subquery_in_where():
    result = lift_sql(
        "SELECT SUM(f.order_amount) AS gmv FROM fact_orders f "
        "WHERE f.user_id IN (SELECT u.user_id FROM dim_user u)"
    )
    assert not result.ok
    assert any("where" == r.clause for r in result.rejections)


def test_lift_rejects_function_wrapped_column():
    result = lift_sql("SELECT SUM(UPPER(f.pay_status)) AS x FROM fact_orders f")
    assert not result.ok


def test_lift_rejects_aggregate_without_alias():
    result = lift_sql("SELECT SUM(f.order_amount) FROM fact_orders f")
    assert not result.ok
    assert "别名" in result.rejections[0].reason


def test_lift_rejects_join_condition_mismatch():
    result = lift_sql(
        "SELECT p.category AS c, SUM(f.order_amount) AS gmv FROM fact_orders f "
        "JOIN dim_product p ON p.brand = f.order_amount GROUP BY p.category"
    )
    assert not result.ok
    assert "连接条件" in result.rejections[0].reason


def test_lift_rejects_half_open_time_window_only_start():
    result = lift_sql(
        "SELECT SUM(f.order_amount) AS gmv FROM fact_orders f "
        "WHERE f.order_time >= TIMESTAMP '2024-06-01 00:00:00'"
    )
    assert not result.ok
    assert "时间窗口" in result.rejections[0].reason


def test_lift_rejects_detail_rows_without_distinct():
    result = lift_sql("SELECT f.order_id AS oid FROM fact_orders f")
    assert not result.ok
    assert "明细行" in result.rejections[0].reason


def test_lift_rejects_multi_time_field_windows():
    """评审收口：多时间字段窗口对无法用单 time_filter 契约表达，严禁静默覆盖。"""
    result = lift_sql(
        "SELECT SUM(f.order_amount) AS gmv FROM fact_orders f "
        "JOIN dim_user u ON u.user_id = f.user_id "
        "WHERE f.order_time >= TIMESTAMP '2024-06-01 00:00:00' "
        "AND f.order_time < TIMESTAMP '2024-07-01 00:00:00' "
        "AND u.register_time >= TIMESTAMP '2024-06-01 00:00:00' "
        "AND u.register_time < TIMESTAMP '2024-07-01 00:00:00'"
    )
    assert not result.ok
    assert "多时间字段" in result.rejections[0].construct


def test_lift_rejects_duplicate_time_window():
    """评审收口：同字段重复区间条件无法取交集语义，严禁 dict 覆盖错译。"""
    result = lift_sql(
        "SELECT SUM(f.order_amount) AS gmv FROM fact_orders f "
        "WHERE f.order_time >= TIMESTAMP '2024-06-01 00:00:00' "
        "AND f.order_time >= TIMESTAMP '2024-06-15 00:00:00' "
        "AND f.order_time < TIMESTAMP '2024-07-01 00:00:00'"
    )
    assert not result.ok
    assert "重复时间窗口" in result.rejections[0].construct


def test_lift_rejects_non_equi_join_predicate():
    """评审收口：ON 含非等值谓词（无处安放）必须拒升，严禁静默丢弃扩大结果集。"""
    result = lift_sql(
        "SELECT p.category AS c, SUM(f.order_amount) AS gmv FROM fact_orders f "
        "JOIN dim_product p ON p.product_id = f.product_id AND f.order_amount > 100 "
        "GROUP BY p.category"
    )
    assert not result.ok
    assert result.rejections[0].clause == "join"
    assert "GT" in result.rejections[0].construct


def test_lift_rejects_oversized_sql():
    """评审收口：输入长度上限先于解析生效（spec §3.3 保守性优先）。"""
    sql = "SELECT f.order_id AS oid FROM fact_orders f WHERE " + " OR ".join(
        ["f.order_id = 1"] * 3000
    )
    assert len(sql) > 10_000
    result = lift_sql(sql)
    assert not result.ok
    assert result.rejections[0].clause == "parse"
    assert "oversized" in result.rejections[0].construct


def test_lift_rejects_parse_timeout(monkeypatch):
    """评审收口（spec §3.3）：解析超时一律拒升，严禁无限等待。"""
    import time as _time

    import core.retrieval.sql_lift as sql_lift_mod

    def _slow_parse(*args, **kwargs):
        _time.sleep(1.0)
        raise AssertionError("超时后不应继续等待解析结果")

    monkeypatch.setattr(sql_lift_mod.sqlglot, "parse_one", _slow_parse)
    monkeypatch.setattr(sql_lift_mod, "_PARSE_TIMEOUT_S", 0.2)
    result = lift_sql("SELECT SUM(f.order_amount) AS gmv FROM fact_orders f")
    assert not result.ok
    assert result.rejections[0].clause == "parse"
    assert result.rejections[0].construct == "timeout"
