"""只读连接池（P0-6）单元测试：复用 / 容量上限 / 排队 / 关闭。"""

from __future__ import annotations

import threading
import time

import duckdb
import pytest

from exec.engine import connstate
from exec.engine.duckdb_adapter import DuckDbAdapter
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
    pool = ReadOnlyConnectionPool(DuckDbAdapter(db_path), max_connections=2)
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
    pool = ReadOnlyConnectionPool(DuckDbAdapter(db_path), max_connections=2)
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
        ReadOnlyConnectionPool(DuckDbAdapter(db_path), max_connections=0)


def test_pool_acquire_after_close_raises(db_path):
    pool = ReadOnlyConnectionPool(DuckDbAdapter(db_path), max_connections=1)
    pool.close()
    with pytest.raises(RuntimeError):
        pool.acquire()


def test_pool_uses_read_only(db_path):
    """只读连接：写操作应被 DuckDB 拒绝。"""
    pool = ReadOnlyConnectionPool(DuckDbAdapter(db_path), max_connections=1)
    try:
        c = pool.acquire()
        with pytest.raises(duckdb.InvalidInputException):
            c.execute("CREATE TABLE x (id INTEGER)")
        pool.release(c)
    finally:
        pool.close()


# --------------------------------------------------------------------------- #
# 废弃语义（2026-10-27 审计 P0：超时后连接脏归还）
# --------------------------------------------------------------------------- #


def test_pool_release_discards_marked_connection(db_path):
    """被标记不可复用的连接不得入队：归还即关闭，容量回收，下次拿新连接。

    复现原缺陷：watchdog 超时后 worker 仍阻塞在同一连接上，若照常 release，
    下一个请求会拿到这条「仍有活跃查询」的连接。
    """
    pool = ReadOnlyConnectionPool(DuckDbAdapter(db_path), max_connections=1)
    try:
        c1 = pool.acquire()
        assert pool.size == 1
        connstate.mark_unusable(c1)
        pool.release(c1)

        # 脏连接不入队，而是被关闭并回收名额
        assert pool.size == 1, "容量应已通过补建恢复为 1"
        c2 = pool.acquire()
        assert c2 is not c1, "废弃后必须拿到新连接，不能复用脏连接"
        assert connstate.is_unusable(c2) is False
        pool.release(c2)
    finally:
        pool.close()


def test_pool_discard_wakes_blocked_waiter(db_path):
    """废弃后必须补建，否则阻塞在 acquire 的等待者永远等不到归还。"""
    pool = ReadOnlyConnectionPool(DuckDbAdapter(db_path), max_connections=1)
    try:
        c1 = pool.acquire()
        got: list = []
        done = threading.Event()

        def _worker():
            conn = pool.acquire()  # 池满，阻塞等待
            got.append(conn)
            done.set()
            pool.release(conn)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        time.sleep(0.2)
        assert not done.is_set()

        pool.discard(c1)  # 废弃而不是归还：等待者必须被唤醒
        assert done.wait(3.0), "废弃后等待者未被唤醒（会永久阻塞）"
        assert got and got[0] is not c1
        t.join(1.0)
    finally:
        pool.close()


def test_pool_discard_returns_capacity_when_reconnect_fails(db_path):
    """补建失败时容量名额必须退回，否则池会逐步缩容到 0。"""

    class _FlakyAdapter(DuckDbAdapter):
        fail_next = False

        def connect_readonly(self):
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("后端暂不可用")
            return super().connect_readonly()

    adapter = _FlakyAdapter(db_path)
    pool = ReadOnlyConnectionPool(adapter, max_connections=2)
    try:
        c1 = pool.acquire()
        adapter.fail_next = True
        pool.discard(c1)
        # 容量退回：下一次 acquire 仍能建连，池不会因一次失败永久少一条
        c2 = pool.acquire()
        assert c2 is not None
        pool.release(c2)
    finally:
        pool.close()


def test_pool_discard_is_idempotent_on_closed_pool(db_path):
    """池关闭后废弃连接不应抛错（关闭路径与废弃路径可能重叠）。"""
    pool = ReadOnlyConnectionPool(DuckDbAdapter(db_path), max_connections=1)
    c = pool.acquire()
    pool.close()
    pool.discard(c)  # 不抛即通过


def test_timeout_through_pool_discards_connection(db_path):
    """端到端：execute_sql 超时 -> 打标 -> 归还即废弃（审计 §9.3 的 CI 兜底）。

    真实后端版本在 tests/test_*_parity.py::test_timeout_discards_connection
    （需容器）；本用例用 DuckDB 把「watchdog 打标 + pool.release 分流」这条链
    在无容器环境也钉住——否则一旦有人把 release 改回无条件入队，默认 CI 全绿
    也发现不了。
    """
    from exec.errors import QueryTimeoutError
    from exec.guards import execute_sql

    adapter = DuckDbAdapter(db_path)
    pool = ReadOnlyConnectionPool(adapter, max_connections=1)
    try:
        conn = pool.acquire()
        with pytest.raises(QueryTimeoutError):
            execute_sql(
                conn,
                "SELECT sum(range) FROM range(20000000000)",
                statement_timeout_ms=300,
                adapter=adapter,
            )
        assert connstate.is_unusable(conn) is True, "超时后连接未被标记为不可复用"

        pool.release(conn)  # 调用方不感知状态，池自动转废弃
        assert pool.size == 1, "废弃后容量名额未回收"

        fresh = pool.acquire()
        try:
            assert fresh is not conn, "池把超时连接重新发出（P0 回归）"
            assert connstate.is_unusable(fresh) is False
        finally:
            pool.release(fresh)
    finally:
        pool.close()
