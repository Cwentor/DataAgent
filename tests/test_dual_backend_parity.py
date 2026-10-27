"""双后端一致性测试（二十一期 spec D14）：同一 DSL 两后端编译+执行，结果必须一致。

运行条件：设置 MYSQL_TEST_HOST/PORT/USER/PASSWORD/DATABASE 指向已执行
``python -m mock.init_mysql`` 的测试库；未设置 MYSQL_TEST_HOST 时整文件跳过。

比对口径：
- 数值按相对误差 1e-6（DuckDB float vs MySQL DECIMAL 经 normalize_cell
  统一 float 后的精度差，Review Focus #4）；
- 行序无关（排序后逐行比对）：排序键 repr(row)——两引擎对同值行的
  tie-break 顺序不构成契约，行集合一致才是语义等价。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.parity_utils import SLOW_SQLS as _SLOW_SQLS

# 共享工具自 parity_utils 导入（二十二期提取）：别名保留下划线前缀，
# 本文件其余断言逻辑逐字不变（tests/ 为包，用包内绝对导入）
from tests.parity_utils import abs_window_dsl as _abs_window_dsl
from tests.parity_utils import assert_rows_equal as _assert_rows_equal
from tests.parity_utils import assert_timeout_discards_connection as _assert_timeout_discards
from tests.parity_utils import compile_dsl as _compile_dsl
from tests.parity_utils import norm_cell as _norm_cell  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.getenv("MYSQL_TEST_HOST"), reason="需要本地 MySQL 测试库（MYSQL_TEST_* 环境变量）"
)


def _test_dsn() -> dict:
    """测试库连接参数（MYSQL_TEST_* 环境变量；独立于运行时 MYSQL_* 配置）。"""
    return {
        "host": os.environ["MYSQL_TEST_HOST"],
        "port": int(os.getenv("MYSQL_TEST_PORT", "3306")),
        "user": os.getenv("MYSQL_TEST_USER", "root"),
        "password": os.getenv("MYSQL_TEST_PASSWORD", ""),
        "database": os.getenv("MYSQL_TEST_DATABASE", "gmall"),
    }


@pytest.fixture(scope="module")
def mysql_conn():
    from exec.engine.mysql_adapter import MySqlAdapter

    conn = MySqlAdapter(_test_dsn()).connect_readonly()
    yield conn
    conn.close()


def _run_dsl(dsl, duck_conn, mysql_conn):
    """同一 DSL 双后端编译执行（走 execute_sql 全护栏链路）。"""
    from exec.engine import get_adapter
    from exec.engine.mysql_adapter import MySqlAdapter
    from exec.guards import execute_sql

    return {
        "duckdb": execute_sql(
            duck_conn,
            _compile_dsl(dsl, "duckdb"),
            adapter=get_adapter("duckdb"),
            max_result_rows=20000,
        ),
        "mysql": execute_sql(
            mysql_conn,
            _compile_dsl(dsl, "mysql"),
            adapter=MySqlAdapter(_test_dsn()),
            max_result_rows=20000,
        ),
    }


def test_golden_dsls_parity(conn, mysql_conn):
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
        results = _run_dsl(dsl, conn, mysql_conn)
        _assert_rows_equal(results["duckdb"].rows, results["mysql"].rows)


def test_week_boundary_parity(conn, mysql_conn):
    """Review Focus #2：跨周边界分桶一致（周日 2021-01-03 / 周一 2021-01-04）。

    DuckDB date_trunc('week') 与 MySQL WEEKDAY 等价式都必须以周一开口径
    分桶——本用例窗口横跨两周边界，口径漂移即行集合不一致。
    """
    results = _run_dsl(_abs_window_dsl("week", "2021-01-01", "2021-01-15"), conn, mysql_conn)
    rows = results["duckdb"].rows
    assert rows, "DuckDB 侧应产出两周分桶"
    _assert_rows_equal(rows, results["mysql"].rows)


def test_fill_gaps_five_years_parity(conn, mysql_conn):
    """Review Focus #3：day 粒度 5 年窗口（1826 天 > MySQL 默认递归深度 1000）。

    连接加固的 cte_max_recursion_depth=100000 承载长窗口；深度不足会以
    3636 错误暴露（而非静默截断）。
    """
    results = _run_dsl(
        _abs_window_dsl("day", "2021-01-01", "2026-01-01", fill_gaps=True), conn, mysql_conn
    )
    assert len(results["mysql"].rows) == 1826
    assert len(results["duckdb"].rows) == 1826
    _assert_rows_equal(results["duckdb"].rows, results["mysql"].rows)


def test_comparison_time_dim_parity(conn, mysql_conn):
    """comparison 按位配对（date_add INTERVAL 语义）双后端一致。"""
    dsl = _abs_window_dsl("day", "2025-06-01", "2025-07-01")
    dsl["time_filter"]["comparison"] = "yoy"
    results = _run_dsl(dsl, conn, mysql_conn)
    _assert_rows_equal(results["duckdb"].rows, results["mysql"].rows)


def test_pool_reuse_session_still_readonly():
    """Review Focus #5：连接池归还复用后会话加固不失效。"""
    from exec.engine.mysql_adapter import MySqlAdapter
    from exec.pool import ReadOnlyConnectionPool

    adapter = MySqlAdapter(_test_dsn())
    pool = ReadOnlyConnectionPool(adapter, max_connections=1)
    conn = pool.acquire()
    conn2 = None
    try:
        pool.release(conn)
        conn2 = pool.acquire()  # 复用同一连接
        with conn2.cursor() as cur:
            cur.execute("SELECT @@transaction_read_only")
            assert cur.fetchone()[0] == 1
    finally:
        if conn2 is not None:
            pool.release(conn2)
        pool.close()


def test_timeout_discards_connection():
    """超时后的连接必须被废弃而非归还复用（2026-10-27 审计 §9.3 的真实后端验证）。

    MySQL 的 ``cancel`` 是空操作（KILL QUERY 需另开连接，适配器未封装），所以
    看门狗超时后 worker 线程仍阻塞在同一条连接上——若照常归还，下一个请求会
    拿到这条「仍有活跃查询」的连接，正是原 P0 的失败形态。
    """
    from exec.engine.mysql_adapter import MySqlAdapter

    _assert_timeout_discards(MySqlAdapter(_test_dsn()), _SLOW_SQLS["mysql"])
