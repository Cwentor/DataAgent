"""DuckDB 适配器测试（二十四期 T2：物理列读取）。"""

from __future__ import annotations

import duckdb
import pytest

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
