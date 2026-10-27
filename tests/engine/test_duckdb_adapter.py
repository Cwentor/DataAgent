"""DuckDB 适配器测试（二十四期 T2：物理列读取；二十六期 T2：估算预检）。"""

from __future__ import annotations

import duckdb
import pytest

from exec.engine.base import ScanPrecheck
from exec.engine.duckdb_adapter import DuckDbAdapter


@pytest.fixture()
def memory_conn():
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE order_detail ("
        "  order_id BIGINT,"
        "  order_time TIMESTAMP,"
        "  split_total_amount DECIMAL(16,2),"
        "  note VARCHAR"
        ")"
    )
    conn.execute("CREATE TABLE _field_metadata (field VARCHAR, dtype VARCHAR)")
    yield conn
    conn.close()


def test_physical_columns_reads_warehouse(memory_conn):
    out = DuckDbAdapter().physical_columns(memory_conn)
    assert out == {
        "order_detail": {
            "order_id": "int",
            "order_time": "timestamp",
            "split_total_amount": "float",
            "note": "str",
        }
    }


def test_physical_columns_skips_underscore_tables(memory_conn):
    """下划线前缀元数据表不进目录（保持二十期前语义）。"""
    out = DuckDbAdapter().physical_columns(memory_conn)
    assert all(not table.startswith("_") for table in out)


def test_physical_columns_reuses_injected_connection(memory_conn):
    """注入连接由调用方持有生命周期——方法内不得关闭。"""
    adapter = DuckDbAdapter()
    adapter.physical_columns(memory_conn)
    # 连接仍可用即说明未被关闭
    assert memory_conn.execute("SELECT 1").fetchone() == (1,)


def test_physical_columns_closes_own_connection(monkeypatch):
    """conn=None 时自建连接，方法返回前必须关闭（防连接泄漏）。"""
    closed: list[bool] = []
    real = duckdb.connect(":memory:")

    class _Conn:
        def execute(self, sql, params=None):
            return real.execute(sql, params or [])

        def close(self):
            closed.append(True)
            real.close()

    adapter = DuckDbAdapter()
    monkeypatch.setattr(adapter, "connect_readonly", lambda: _Conn())
    out = adapter.physical_columns()
    assert out == {}  # 空库
    assert closed == [True]


# --------------------------------------------------------------------------- #
# 估算预检（二十六期 T2：explain_scan_estimate）
# --------------------------------------------------------------------------- #


@pytest.fixture()
def big_conn(tmp_path):
    """文件库 + 重新打开：只有落盘并重开的表，`count(*)` 才走 `COLUMN_DATA_SCAN`
    元数据快路径（内存表或刚建的表会老老实实 `SEQ_SCAN`，测不出目标形态）。"""
    db_path = tmp_path / "precheck.duckdb"
    writer = duckdb.connect(str(db_path))
    writer.execute("CREATE TABLE t AS SELECT i AS id, i % 7 AS grp FROM range(100000) tbl(i)")
    writer.close()
    conn = duckdb.connect(str(db_path), read_only=True)
    yield conn
    conn.close()


class _RecordingConn:
    """记录 SQL 的连接代理：`DuckDBPyConnection.execute` 只读，没法 monkeypatch。"""

    def __init__(self, real):
        self._real = real
        self.sqls: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.sqls.append(sql)
        return self._real.execute(sql, *args, **kwargs)

    def interrupt(self):
        return self._real.interrupt()


def test_explain_scan_estimate_is_planning_only(big_conn):
    """只发 `EXPLAIN`，不得发 `EXPLAIN ANALYZE`（后者会把查询真的跑一遍）。"""
    conn = _RecordingConn(big_conn)
    out = DuckDbAdapter().explain_scan_estimate(conn, "SELECT * FROM t WHERE grp = 1", 5000)

    assert out.estimated is True
    assert out.usable is True
    assert out.operator == "SEQ_SCAN"
    assert conn.sqls, "必须真的发了计划查询"
    assert all(sql.startswith("EXPLAIN ") for sql in conn.sqls), conn.sqls
    assert not any(sql.startswith("EXPLAIN ANALYZE") for sql in conn.sqls), conn.sqls


def test_explain_scan_estimate_unusable_for_count_star(big_conn):
    """`count(*)` 走元数据快路径，报的 `~1 row` 是"输出 1 行"，不可当估算用。"""
    out = DuckDbAdapter().explain_scan_estimate(big_conn, "SELECT count(*) FROM t", 5000)
    assert out.usable is False
    assert out.operator == ""


def test_explain_scan_estimate_unusable_for_dummy_scan(big_conn):
    """`SELECT 1` 无真实扫描算子，fail-closed。"""
    out = DuckDbAdapter().explain_scan_estimate(big_conn, "SELECT 1", 5000)
    assert out.usable is False


def test_scan_precheck_usable_requires_positive_rows():
    """`rows=0` 一律不可用——"测不到"不许被当成"扫了 0 行"。"""
    assert (
        ScanPrecheck(rows=0, measurable=True, estimated=True, operator="SEQ_SCAN").usable is False
    )
    assert ScanPrecheck(rows=5, measurable=True, estimated=True, operator="SEQ_SCAN").usable is True
    # 实测口径不参与"跳过"判定
    assert ScanPrecheck(rows=5, measurable=True, estimated=False, operator="").usable is False
