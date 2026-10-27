"""Hive 双后端一致性测试（二十五期 spec §8）：同一 DSL 两后端编译+执行，结果必须一致。

运行条件：设置 HIVE_TEST_HOST/PORT/USER/PASSWORD/DATABASE 指向已执行
``python -m mock.init_hive`` 的测试库；测试账号须为只读授权（仅 GRANT SELECT，
两账号分离见 spec §9）；未设置 HIVE_TEST_HOST 时整文件跳过（本地开发不跑
Hive，parity 由 CI hive-parity job 承担——与 22/23 期模式一致）。

Hive 与 CH 同属"无 EXPLAIN actual rows"一族（spec D3）：扫描预检诚实不可计量，
无服务端行数熔断专项（CH 的 numbers(20M) 熔断用例在 Hive 无对应物），改为
"预检 warning 留痕"断言。

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
    not os.getenv("HIVE_TEST_HOST"),
    reason="需要 Hive 测试库（HIVE_TEST_* 环境变量，只读授权账号）",
)


def _test_dsn() -> dict:
    """测试库连接参数（HIVE_TEST_* 环境变量；独立于运行时 HIVE_* 配置）。"""
    return {
        "host": os.environ["HIVE_TEST_HOST"],
        "port": int(os.getenv("HIVE_TEST_PORT", "10000")),
        "user": os.getenv("HIVE_TEST_USER", "dataagent_ro"),
        "password": os.getenv("HIVE_TEST_PASSWORD", ""),
        "database": os.getenv("HIVE_TEST_DATABASE", "gmall"),
    }


@pytest.fixture(scope="module")
def hive_conn():
    from exec.engine.hive_adapter import HiveAdapter

    conn = HiveAdapter(_test_dsn()).connect_readonly()
    yield conn
    conn.close()


def _run_dsl(dsl, duck_conn, hive_conn):
    """同一 DSL 双后端编译执行（走 execute_sql 全护栏链路）。"""
    from exec.engine import get_adapter
    from exec.engine.hive_adapter import HiveAdapter
    from exec.guards import execute_sql

    return {
        "duckdb": execute_sql(
            duck_conn,
            _compile_dsl(dsl, "duckdb"),
            adapter=get_adapter("duckdb"),
            max_result_rows=20000,
        ),
        "hive": execute_sql(
            hive_conn,
            _compile_dsl(dsl, "hive"),
            adapter=HiveAdapter(_test_dsn()),
            max_result_rows=20000,
        ),
    }


def test_golden_dsls_parity(conn, hive_conn):
    """golden_dataset 全量 DSL 双后端一致（主断言）。"""
    golden = json.loads(
        (Path(__file__).parents[1] / "eval" / "golden_dataset.json").read_text(encoding="utf-8")
    )
    dsls = [item["dsl"] for item in golden if "dsl" in item] + [
        turn["dsl"] for item in golden for turn in item.get("turns", []) if "dsl" in turn
    ]
    assert dsls, "golden_dataset 未解析出任何 DSL"
    for dsl in dsls:
        results = _run_dsl(dsl, conn, hive_conn)
        _assert_rows_equal(results["duckdb"].rows, results["hive"].rows)


def test_week_boundary_parity(conn, hive_conn):
    """跨周边界分桶一致（pmod/datediff 周一锚定等价式 vs DuckDB date_trunc('week')）。"""
    results = _run_dsl(_abs_window_dsl("week", "2021-01-01", "2021-01-15"), conn, hive_conn)
    rows = results["duckdb"].rows
    assert rows, "DuckDB 侧应产出两周分桶"
    _assert_rows_equal(rows, results["hive"].rows)


def test_fill_gaps_five_years_parity(conn, hive_conn):
    """day 粒度 5 年窗口（1826 行）——posexplode spine 实测口径（spec D7）。"""
    results = _run_dsl(
        _abs_window_dsl("day", "2021-01-01", "2026-01-01", fill_gaps=True), conn, hive_conn
    )
    assert len(results["hive"].rows) == 1826
    assert len(results["duckdb"].rows) == 1826
    _assert_rows_equal(results["duckdb"].rows, results["hive"].rows)


def test_comparison_time_dim_parity(conn, hive_conn):
    """comparison 按位配对（date_add/add_months 转译形态）双后端一致。"""
    dsl = _abs_window_dsl("day", "2025-06-01", "2025-07-01")
    dsl["time_filter"]["comparison"] = "yoy"
    results = _run_dsl(dsl, conn, hive_conn)
    _assert_rows_equal(results["duckdb"].rows, results["hive"].rows)


def test_scan_precheck_honestly_unmeasurable(hive_conn, caplog):
    """扫描预检诚实不可计量（spec D3）：warning 留痕而非静默当 0 行放行。

    Hive EXPLAIN 无 actual rows——explain_scan_rows 恒 (0, False)，护栏对
    每条查询 warning（plan 无 'N rows' 渲染），与 CH 同口径（无服务端熔断，
    兜底 = LIMIT 硬上限 + 客户端超时 + 部署配额）。
    """
    import logging

    from config import settings
    from exec.engine.hive_adapter import HiveAdapter
    from exec.guards import execute_sql

    with caplog.at_level(logging.WARNING, logger="exec.guards"):
        execute_sql(
            hive_conn,
            "SELECT COUNT(*) AS n FROM order_detail",
            adapter=HiveAdapter(_test_dsn()),
            # 与生产调用方一致显式传预算（None = 不启用预检护栏）
            max_scan_rows=settings.MAX_SCAN_ROWS,
            max_result_rows=1,
        )
    assert any("扫描行数预检不可计量" in r.message for r in caplog.records)


def test_pool_reuse_session_identity_stable():
    """连接池归还复用后身份回读仍一致（验证式加固在复用路径不失效）。

    Hive 无会话级只读设置面（spec D4）：只读防线由部署侧授权承担，连接层
    可验证面 = current_user 回读一致——复用连接必须保持同一身份。
    """
    from exec.engine.hive_adapter import HiveAdapter
    from exec.pool import ReadOnlyConnectionPool

    adapter = HiveAdapter(_test_dsn())
    pool = ReadOnlyConnectionPool(adapter, max_connections=1)
    conn = pool.acquire()
    conn2 = None
    try:
        pool.release(conn)
        conn2 = pool.acquire()  # 复用同一连接
        cur = conn2.cursor()
        try:
            cur.execute("SELECT current_user()")
            assert str(cur.fetchall()[0][0]) == _test_dsn()["user"]
        finally:
            cur.close()
    finally:
        if conn2 is not None:
            pool.release(conn2)
        pool.close()


def test_timeout_discards_connection():
    """超时后的连接必须被废弃而非归还复用（2026-10-27 审计 §9.3 的真实后端验证）。

    这是原 P0 在真实后端上**确定性**复现的那一条：PyHive 无 TCancelOperation
    封装，``cancel()`` 是空操作，看门狗抛超时后 worker 仍阻塞在同一连接上。
    慢查询首选 ``reflect`` 调 java Thread.sleep（不启 Tez 作业），被 UDF 黑名单
    拒绝时退回交叉连接。
    """
    from exec.engine.hive_adapter import HiveAdapter

    _assert_timeout_discards(HiveAdapter(_test_dsn()), _SLOW_SQLS["hive"])
