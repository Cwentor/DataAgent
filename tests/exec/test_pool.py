"""只读连接池（P0-6）单元测试：复用 / 容量上限 / 排队 / 关闭。"""

from __future__ import annotations

import threading
import time

import duckdb
import pytest

from exec.pool import ReadOnlyConnectionPool


@pytest.fixture
def db_path(tmp_path):
    """临时 DuckDB 文件（含一张表），供连接池只读连接使用。"""
    p = tmp_path / "pool.duckdb"
    conn = duckdb.connect(str(p))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("INSERT INTO t VALUES (1), (2), (3)")
    conn.close()
    return p


def test_pool_acquire_release_reuses_connection(db_path):
    pool = ReadOnlyConnectionPool(db_path, max_connections=2)
    try:
        c1 = pool.acquire()
        assert c1.execute("SELECT count(*) FROM t").fetchone()[0] == 3
        pool.release(c1)
        c2 = pool.acquire()
        # 归还后复用同一个连接对象
        assert c2 is c1
        pool.release(c2)
        assert pool.size == 1
    finally:
        pool.close()


def test_pool_caps_concurrent_connections(db_path):
    """容量上限：池满后 acquire 阻塞排队，直到有连接归还。"""
    pool = ReadOnlyConnectionPool(db_path, max_connections=2)
    try:
        c1 = pool.acquire()
        c2 = pool.acquire()
        assert pool.size == 2
        acquired: list = []
        done = threading.Event()

        def _worker():
            c = pool.acquire()
            acquired.append(c)
            done.set()
            pool.release(c)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        time.sleep(0.2)
        assert not done.is_set()  # 无连接可还，第三路阻塞排队
        pool.release(c1)
        done.wait(2.0)
        assert done.is_set() and len(acquired) == 1
        pool.release(c2)
        t.join(1.0)
    finally:
        pool.close()


def test_pool_rejects_invalid_capacity(db_path):
    with pytest.raises(ValueError):
        ReadOnlyConnectionPool(db_path, max_connections=0)


def test_pool_acquire_after_close_raises(db_path):
    pool = ReadOnlyConnectionPool(db_path, max_connections=1)
    pool.close()
    with pytest.raises(RuntimeError):
        pool.acquire()


def test_pool_uses_read_only(db_path):
    """只读连接：写操作应被 DuckDB 拒绝。"""
    pool = ReadOnlyConnectionPool(db_path, max_connections=1)
    try:
        c = pool.acquire()
        with pytest.raises(duckdb.InvalidInputException):
            c.execute("CREATE TABLE x (id INTEGER)")
        pool.release(c)
    finally:
        pool.close()
