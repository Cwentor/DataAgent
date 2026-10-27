"""PostgreSQL 双后端一致性测试（二十二期 spec D14）：同一 DSL 两后端编译+执行，结果必须一致。

运行条件：设置 POSTGRES_TEST_HOST/PORT/USER/PASSWORD/DATABASE 指向已执行
``python -m mock.init_postgres`` 的测试库；未设置 POSTGRES_TEST_HOST 时整文件
跳过（本地开发不跑 PG，parity 由 CI postgres-parity job 承担——spec §9 R1）。

比对口径与 test_dual_backend_parity.py 同源（tests/parity_utils.py，勿改）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.parity_utils import SLOW_SQLS as _SLOW_SQLS
from tests.parity_utils import abs_window_dsl as _abs_window_dsl
from tests.parity_utils import assert_rows_equal as _assert_rows_equal
from tests.parity_utils import assert_timeout_discards_connection as _assert_timeout_discards
from tests.parity_utils import compile_dsl as _compile_dsl

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_HOST"),
    reason="需要 PostgreSQL 测试库（POSTGRES_TEST_* 环境变量）",
)


def _test_dsn() -> dict:
    """测试库连接参数（POSTGRES_TEST_* 环境变量；独立于运行时 POSTGRES_* 配置）。"""
    return {
        "host": os.environ["POSTGRES_TEST_HOST"],
        "port": int(os.getenv("POSTGRES_TEST_PORT", "5432")),
        "user": os.getenv("POSTGRES_TEST_USER", "postgres"),
        "password": os.getenv("POSTGRES_TEST_PASSWORD", ""),
        "database": os.getenv("POSTGRES_TEST_DATABASE", "gmall"),
    }


@pytest.fixture(scope="module")
def pg_conn():
    from exec.engine.postgres_adapter import PostgresAdapter

    conn = PostgresAdapter(_test_dsn()).connect_readonly()
    yield conn
    conn.close()


def _run_dsl(dsl, duck_conn, pg_conn):
    """同一 DSL 双后端编译执行（走 execute_sql 全护栏链路）。"""
    from exec.engine import get_adapter
    from exec.engine.postgres_adapter import PostgresAdapter
    from exec.guards import execute_sql

    return {
        "duckdb": execute_sql(
            duck_conn,
            _compile_dsl(dsl, "duckdb"),
            adapter=get_adapter("duckdb"),
            max_result_rows=20000,
        ),
        "postgres": execute_sql(
            pg_conn,
            _compile_dsl(dsl, "postgres"),
            adapter=PostgresAdapter(_test_dsn()),
            max_result_rows=20000,
        ),
    }


def test_golden_dsls_parity(conn, pg_conn):
    """golden_dataset 全量 DSL 双后端一致（spec D14 主断言）。"""
    golden = json.loads(
        (Path(__file__).parents[1] / "eval" / "golden_dataset.json").read_text(encoding="utf-8")
    )
    # 单轮条目带顶层 dsl；多轮条目（M 系列）为 turns[] 嵌套——两层都覆盖
    dsls = [item["dsl"] for item in golden if "dsl" in item] + [
        turn["dsl"] for item in golden for turn in item.get("turns", []) if "dsl" in turn
    ]
    assert dsls, "golden_dataset 未解析出任何 DSL"
    for dsl in dsls:
        results = _run_dsl(dsl, conn, pg_conn)
        _assert_rows_equal(results["duckdb"].rows, results["postgres"].rows)


def test_week_boundary_parity(conn, pg_conn):
    """跨周边界分桶一致（周日 2021-01-03 / 周一 2021-01-04）。

    DuckDB 与 PG 的 date_trunc('week') 均为周一锚定——本用例窗口横跨两周边界，
    口径漂移即行集合不一致。
    """
    results = _run_dsl(_abs_window_dsl("week", "2021-01-01", "2021-01-15"), conn, pg_conn)
    rows = results["duckdb"].rows
    assert rows, "DuckDB 侧应产出两周分桶"
    _assert_rows_equal(rows, results["postgres"].rows)


def test_fill_gaps_five_years_parity(conn, pg_conn):
    """day 粒度 5 年窗口（1826 行）。

    PG generate_series 原生集合函数无递归深度限制（MySQL 需
    cte_max_recursion_depth 承载——PG 侧该负担不存在，spec D8）。
    """
    results = _run_dsl(
        _abs_window_dsl("day", "2021-01-01", "2026-01-01", fill_gaps=True), conn, pg_conn
    )
    assert len(results["postgres"].rows) == 1826
    assert len(results["duckdb"].rows) == 1826
    _assert_rows_equal(results["duckdb"].rows, results["postgres"].rows)


def test_comparison_time_dim_parity(conn, pg_conn):
    """comparison 按位配对（PG 加法平移 + INTERVAL '1 year' 语义）双后端一致。"""
    dsl = _abs_window_dsl("day", "2025-06-01", "2025-07-01")
    dsl["time_filter"]["comparison"] = "yoy"
    results = _run_dsl(dsl, conn, pg_conn)
    _assert_rows_equal(results["duckdb"].rows, results["postgres"].rows)


def test_pool_reuse_session_still_readonly():
    """连接池归还复用后会话加固不失效。

    PG 断言口径：current_setting('transaction_read_only') == 'on'
    （MySQL 版断言 @@transaction_read_only == 1）。
    """
    from exec.engine.postgres_adapter import PostgresAdapter
    from exec.pool import ReadOnlyConnectionPool

    adapter = PostgresAdapter(_test_dsn())
    pool = ReadOnlyConnectionPool(adapter, max_connections=1)
    conn = pool.acquire()
    conn2 = None
    try:
        pool.release(conn)
        conn2 = pool.acquire()  # 复用同一连接
        with conn2.cursor() as cur:
            cur.execute("SELECT current_setting('transaction_read_only')")
            assert cur.fetchone()[0] == "on"
    finally:
        if conn2 is not None:
            pool.release(conn2)
        pool.close()


def test_timeout_discards_connection():
    """超时后的连接必须被废弃而非归还复用（2026-10-27 审计 §9.3 的真实后端验证）。

    被测契约与 ``adapter.cancel`` 是否有效**无关**：看门狗在超时分支无条件
    ``mark_unusable``，废弃是必须的（取消只是让 worker 尽早退出，取消成功也不
    允许复用）。PG 侧取消已由 ``psycopg.Connection.cancel()`` 承载（二十六期补强，
    见 ``postgres_adapter`` 模块 docstring）；若退化为空操作，Linux 上会撞
    fd 复用竞争，本用例断言 ``pool.size == 1`` 恰好把它抓出来。
    """
    from exec.engine.postgres_adapter import PostgresAdapter

    _assert_timeout_discards(PostgresAdapter(_test_dsn()), _SLOW_SQLS["postgres"])
