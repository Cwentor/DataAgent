"""ClickHouse 双后端一致性测试（二十三期 spec §6）：同一 DSL 两后端编译+执行，结果必须一致。

运行条件：设置 CLICKHOUSE_TEST_HOST/PORT/USER/PASSWORD/DATABASE 指向已执行
``python -m mock.init_clickhouse`` 的测试库；**测试账号必须为 readonly profile**
（readonly=1 + max_rows_to_read=MAX_SCAN_ROWS + max_execution_time=
QUERY_TIMEOUT_MS/1000，与 users.d 模板一致——harden 验证式校验 fail-closed）；
未设置 CLICKHOUSE_TEST_HOST 时整文件跳过（本地开发不跑 CH，parity 由 CI
clickhouse-parity job 承担——与二十二期模式一致）。

比对口径与 test_dual_backend_parity.py 同源（tests/parity_utils.py，勿改）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.parity_utils import abs_window_dsl as _abs_window_dsl
from tests.parity_utils import assert_rows_equal as _assert_rows_equal
from tests.parity_utils import compile_dsl as _compile_dsl

pytestmark = pytest.mark.skipif(
    not os.getenv("CLICKHOUSE_TEST_HOST"),
    reason="需要 ClickHouse 测试库（CLICKHOUSE_TEST_* 环境变量，readonly profile 账号）",
)


def _test_dsn() -> dict:
    """测试库连接参数（CLICKHOUSE_TEST_* 环境变量；独立于运行时 CLICKHOUSE_* 配置）。"""
    return {
        "host": os.environ["CLICKHOUSE_TEST_HOST"],
        "port": int(os.getenv("CLICKHOUSE_TEST_PORT", "8123")),
        "user": os.getenv("CLICKHOUSE_TEST_USER", "dataagent_ro"),
        "password": os.getenv("CLICKHOUSE_TEST_PASSWORD", ""),
        "database": os.getenv("CLICKHOUSE_TEST_DATABASE", "gmall"),
    }


@pytest.fixture(scope="module")
def ch_conn():
    from exec.engine.clickhouse_adapter import ClickHouseAdapter

    conn = ClickHouseAdapter(_test_dsn()).connect_readonly()
    yield conn
    conn.close()


def _run_dsl(dsl, duck_conn, ch_conn):
    """同一 DSL 双后端编译执行（走 execute_sql 全护栏链路）。"""
    from exec.engine import get_adapter
    from exec.engine.clickhouse_adapter import ClickHouseAdapter
    from exec.guards import execute_sql

    return {
        "duckdb": execute_sql(
            duck_conn,
            _compile_dsl(dsl, "duckdb"),
            adapter=get_adapter("duckdb"),
            max_result_rows=20000,
        ),
        "clickhouse": execute_sql(
            ch_conn,
            _compile_dsl(dsl, "clickhouse"),
            adapter=ClickHouseAdapter(_test_dsn()),
            max_result_rows=20000,
        ),
    }


def test_golden_dsls_parity(conn, ch_conn):
    """golden_dataset 全量 DSL 双后端一致（主断言）。"""
    golden = json.loads(
        (Path(__file__).parents[1] / "eval" / "golden_dataset.json").read_text(encoding="utf-8")
    )
    # 单轮条目带顶层 dsl；多轮条目（M 系列）为 turns[] 嵌套——两层都覆盖
    dsls = [item["dsl"] for item in golden if "dsl" in item] + [
        turn["dsl"] for item in golden for turn in item.get("turns", []) if "dsl" in turn
    ]
    assert dsls, "golden_dataset 未解析出任何 DSL"
    for dsl in dsls:
        results = _run_dsl(dsl, conn, ch_conn)
        _assert_rows_equal(results["duckdb"].rows, results["clickhouse"].rows)


def test_week_boundary_parity(conn, ch_conn):
    """跨周边界分桶一致（toMonday 周一锚定 vs DuckDB date_trunc('week')）。"""
    results = _run_dsl(_abs_window_dsl("week", "2021-01-01", "2021-01-15"), conn, ch_conn)
    rows = results["duckdb"].rows
    assert rows, "DuckDB 侧应产出两周分桶"
    _assert_rows_equal(rows, results["clickhouse"].rows)


def test_fill_gaps_five_years_parity(conn, ch_conn):
    """day 粒度 5 年窗口（1826 行）——numbers(N) spine 实测口径（spec D7）。"""
    results = _run_dsl(
        _abs_window_dsl("day", "2021-01-01", "2026-01-01", fill_gaps=True), conn, ch_conn
    )
    assert len(results["clickhouse"].rows) == 1826
    assert len(results["duckdb"].rows) == 1826
    _assert_rows_equal(results["duckdb"].rows, results["clickhouse"].rows)


def test_comparison_time_dim_parity(conn, ch_conn):
    """comparison 按位配对（CH 透传 INTERVAL 1 YEAR 加法形态）双后端一致。"""
    dsl = _abs_window_dsl("day", "2025-06-01", "2025-07-01")
    dsl["time_filter"]["comparison"] = "yoy"
    results = _run_dsl(dsl, conn, ch_conn)
    _assert_rows_equal(results["duckdb"].rows, results["clickhouse"].rows)


def test_scan_limit_circuit_breaker(ch_conn):
    """profile max_rows_to_read 服务端硬熔断（spec D3）：numbers(20M) 超预算报错。

    CH 口径为多表读取总和：numbers(20000000) 读 2000 万行 > MAX_SCAN_ROWS（1000 万）
    → 服务端错误码 131 → 文本归一为 SqlExecutionError（含熔断文案）。
    """
    from exec.engine.clickhouse_adapter import ClickHouseAdapter
    from exec.errors import SqlExecutionError
    from exec.guards import execute_sql

    with pytest.raises(SqlExecutionError, match="max_rows_to_read"):
        execute_sql(
            ch_conn,
            "SELECT count() FROM numbers(20000000)",
            adapter=ClickHouseAdapter(_test_dsn()),
            max_result_rows=1,
        )


def test_pool_reuse_session_still_readonly():
    """连接池归还复用后验证式加固不失效（system.settings 只读值仍为 readonly>=1）。"""
    from exec.engine.clickhouse_adapter import ClickHouseAdapter
    from exec.pool import ReadOnlyConnectionPool

    adapter = ClickHouseAdapter(_test_dsn())
    pool = ReadOnlyConnectionPool(adapter, max_connections=1)
    conn = pool.acquire()
    conn2 = None
    try:
        pool.release(conn)
        conn2 = pool.acquire()  # 复用同一连接
        result = conn2.query("SELECT value FROM system.settings WHERE name = 'readonly'")
        assert int(result.result_rows[0][0]) >= 1
    finally:
        if conn2 is not None:
            pool.release(conn2)
        pool.close()
