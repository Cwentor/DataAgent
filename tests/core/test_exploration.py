"""探索执行器单测（十九期 M6）：视图重写 + TEMP 视图 + 治理执行。

验收铁律（spec §8 M6 / Review Focus）：
1. TEMP 视图在 **read_only 文件连接**上可用（生产连接池口径，内存连接不算数）；
2. 越权表（无 sec_* 视图）精确拒绝；
3. 探索结果经 PII 脱敏导出，audit.exploration=True；
4. 复用 execute_sql 三护栏（超行数熔断实证）。
"""

from __future__ import annotations

import duckdb
import pytest

from config import settings
from core.retrieval.exploration import (
    execute_exploration_query,
    exploration_risk,
    rewrite_sql_to_views,
)


@pytest.fixture()
def ro_conn():
    """read_only 文件连接（生产连接池口径；TEMP 视图可用性实证载体）。"""
    assert settings.DB_PATH.exists(), "请先 python -m mock.init_duckdb"
    conn = duckdb.connect(str(settings.DB_PATH), read_only=True)
    yield conn
    conn.close()


def test_rewrite_and_execute_equivalent_on_read_only(ro_conn, tmp_path):
    """基表 → sec_* 视图重写后执行，结果与原库直接执行一致。"""
    from security.views import install_secure_views

    original_sql = (
        "SELECT SUM(split_total_amount) AS gmv FROM order_detail WHERE order_status = '1002'"
    )
    direct = ro_conn.execute(original_sql).fetchall()

    rewritten, rejects = rewrite_sql_to_views(original_sql, "admin")
    assert rejects == []
    assert "sec_order_detail" in rewritten
    install_secure_views(ro_conn, "admin")  # TEMP 视图在 read_only 连接上安装
    via_view = ro_conn.execute(rewritten).fetchall()
    assert sorted(map(repr, direct)) == sorted(map(repr, via_view))


def test_rewrite_rejects_out_of_scope_table(ro_conn):
    """越权表（无视图）精确拒绝：restricted 引用 order_refund_info。"""
    rewritten, rejects = rewrite_sql_to_views(
        "SELECT COUNT(*) AS c FROM order_refund_info", "restricted"
    )
    assert rewritten is None
    assert any("order_refund_info" in r for r in rejects)


def test_rewrite_rejects_unknown_table():
    rewritten, rejects = rewrite_sql_to_views("SELECT * FROM evil_table", "admin")
    assert rewritten is None
    assert any("evil_table" in r for r in rejects)


def test_execute_exploration_query_exports_with_flag(ro_conn, tmp_path):
    """探索执行：结果物化 ParquetRef，audit.exploration=True、PII 脱敏管道生效。"""
    ref = execute_exploration_query(
        "SELECT province, split_total_amount FROM order_detail WHERE order_status = '1002' LIMIT 10",
        principal="admin",
        workspace=tmp_path,
        name="exploration_s1",
        query="验证探索执行",
        conn=ro_conn,
    )
    assert ref.rows > 0
    assert (ref.audit or {}).get("exploration") is True


def test_execute_exploration_respects_row_cap(ro_conn, tmp_path):
    """护栏复用：结果行数硬上限熔断（max_rows 透传 execute_sql）。"""
    from exec.guards import ResultLimitExceeded

    with pytest.raises(ResultLimitExceeded):
        execute_exploration_query(
            "SELECT order_id FROM order_detail",
            principal="admin",
            workspace=tmp_path,
            name="exploration_cap",
            query="行数熔断",
            conn=ro_conn,
            max_rows=1,
        )


def test_exploration_risk_detects_forbidden_columns():
    """L4 自动放行边界（Review Focus #3）：SQL 含 principal 禁列 → 敏感。"""
    sensitive_sql = "SELECT split_coupon_amount FROM order_detail"
    has_sensitive, tables = exploration_risk(sensitive_sql, "restricted")
    assert has_sensitive is True
    assert "order_detail" in tables

    safe_sql = "SELECT split_total_amount FROM order_detail"
    has_sensitive, tables = exploration_risk(safe_sql, "restricted")
    assert has_sensitive is False


def test_exploration_risk_case_insensitive_variants():
    """评审 HIGH #4：DuckDB 标识符大小写不敏感，敏感判定必须大小写规范化——
    大写/混写/带引号变体严禁绕过 L4 自动放行硬边界。"""
    for variant in (
        "SELECT SPLIT_COUPON_AMOUNT FROM order_detail",
        "SELECT Split_Coupon_Amount FROM order_detail",
        'SELECT "SPLIT_COUPON_AMOUNT" FROM order_detail',
        "SELECT f.SPLIT_COUPON_AMOUNT FROM order_detail f",
    ):
        has_sensitive, _tables = exploration_risk(variant, "restricted")
        assert has_sensitive is True, variant


def test_rewrite_computed_cte_allowed_and_tables_rewritten(ro_conn, tmp_path):
    """计算型 CTE 在探索层合法（提升闸门的保守内联不适用此处）；CTE 体表引用照常重写。"""
    from security.views import install_secure_views

    sql = (
        "WITH t AS (SELECT sku_id, SUM(1) AS c FROM order_detail GROUP BY sku_id) "
        "SELECT SUM(t.sku_id) AS x FROM t"
    )
    rewritten, rejects = rewrite_sql_to_views(sql, "admin")
    assert rejects == []
    assert "sec_order_detail" in rewritten
    install_secure_views(ro_conn, "admin")
    rows = ro_conn.execute(rewritten).fetchall()
    direct = ro_conn.execute(sql).fetchall()
    assert sorted(map(repr, rows)) == sorted(map(repr, direct))


def test_execute_exploration_rejects_write_statement(ro_conn, tmp_path):
    """红线锚点（M7）：写语句经探索执行器必须被只读白名单硬拦截
    （评审收口：异常类型由三选一收窄为 UnsafeSqlError）。"""
    from exec.guards import UnsafeSqlError

    with pytest.raises(UnsafeSqlError):
        execute_exploration_query(
            "DELETE FROM order_detail WHERE order_id = 1",
            principal="admin",
            workspace=tmp_path,
            name="exploration_write",
            query="写操作红线",
            conn=ro_conn,
        )
