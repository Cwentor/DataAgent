"""安全视图层单测（十九期 M5）：视图逃逸矩阵 + 与 guard RLS 的语义一致性。

验收铁律（spec §8 M5）：经视图查询时禁列物理不存在、行过滤无条件生效、
越权表无视图；视图定义与 guard 注入 RLS 后的编译查询结果必须一致（双保险
交叉验证）。
"""

from __future__ import annotations

import duckdb
import pytest

from compiler.sql_compiler import compile_sql
from security.guard import apply_policy
from security.views import install_secure_views
from semantic.catalog import COLUMNS
from semantic.dsl_schema import QueryDSL


def _make_conn():
    """可写内存连接 + 确定性 mock 数据（与 conftest.conn 同源）。"""
    from mock.init_duckdb import build_tables  # 测试内导入避免循环

    c = duckdb.connect(":memory:")
    build_tables(c)
    return c


def test_restricted_views_exclude_forbidden_columns_physically(conn):
    """禁列物理不存在于视图（非查询时拦截）。"""
    install_secure_views(conn, "restricted")
    cols = {row[0] for row in conn.execute("DESCRIBE sec_fact_orders").fetchall()}
    assert "discount_amount" not in cols
    assert "refund_amount" not in cols
    assert "order_amount" in cols  # 合法列仍在


def test_restricted_view_enforces_row_filter(conn):
    """行过滤固化在视图定义：restricted 只能看广东。"""
    install_secure_views(conn, "restricted")
    rows = conn.execute("SELECT DISTINCT province FROM sec_fact_orders").fetchall()
    assert {r[0] for r in rows} == {"广东"}


def test_out_of_scope_table_has_no_view(conn):
    """越权表不生成视图——引用即报错（表不存在）。"""
    install_secure_views(conn, "restricted")
    with pytest.raises(duckdb.Error):
        conn.execute("SELECT COUNT(*) FROM sec_fact_refunds").fetchone()


def test_admin_views_full_columns_no_row_filter(conn):
    """admin 视图全列、无行过滤（对照基线）。"""
    install_secure_views(conn, "admin")
    cols = {row[0] for row in conn.execute("DESCRIBE sec_fact_orders").fetchall()}
    expected = {m.column for m in COLUMNS.values() if m.table == "fact_orders"}
    assert expected <= cols
    provinces = {
        r[0] for r in conn.execute("SELECT DISTINCT province FROM sec_fact_orders").fetchall()
    }
    assert len(provinces) > 1


def test_view_semantics_match_guard_rls(conn):
    """双保险交叉验证：restricted 视图聚合 == guard 注入 RLS 后编译查询。"""
    install_secure_views(conn, "restricted")
    view_rows = conn.execute(
        "SELECT province, SUM(order_amount) FROM sec_fact_orders GROUP BY province ORDER BY 1"
    ).fetchall()

    dsl = apply_policy(
        QueryDSL.model_validate(
            {
                "metrics": [
                    {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                ],
                "dimensions": [{"field": "province"}],
            }
        ),
        "restricted",
    )
    compiled = compile_sql(dsl)
    guard_rows = conn.execute(compiled).fetchall()

    assert sorted(map(repr, view_rows)) == sorted(map(repr, guard_rows))


# --------------------------------------------------------------------------- #
# 连接级加固（M5-T2，spec §3.4）
# --------------------------------------------------------------------------- #
def test_harden_connection_blocks_external_access(conn):
    """加固后 ATTACH / COPY TO / read_csv 必须被 DuckDB 拒绝（连接层实测）。"""
    from security.views import harden_connection

    harden_connection(conn)
    with pytest.raises(duckdb.Error):
        conn.execute("ATTACH 'evil_external.duckdb' AS evil")
    with pytest.raises(duckdb.Error):
        conn.execute("COPY (SELECT 1) TO 'evil.csv'")
    with pytest.raises(duckdb.Error):
        conn.execute("SELECT COUNT(*) FROM read_csv_auto('nonexistent.csv')")


def test_harden_connection_idempotent(conn):
    """重复加固幂等（lock_configuration 后重复 SET 不抛错）。"""
    from security.views import harden_connection

    harden_connection(conn)
    harden_connection(conn)
