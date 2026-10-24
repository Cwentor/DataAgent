"""SqliteKVStore 多 worker 并发写加固单元测试（评审遗留风险项2）。

覆盖：
- 连接初始化后数据库处于 WAL 日志模式（并发读不阻塞写）；
- busy_timeout 按构造参数生效；
- 外部连接持写锁时，写入在 busy_timeout 窗口内等待锁释放后成功；
- 多实例（模拟多 worker）并发写同一 SQLite 文件不丢写、不报锁冲突。
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from persistence.kvstore import SqliteKVStore


def test_wal_journal_mode_enabled(tmp_path):
    """journal_mode=WAL：持久化属性落库，后续连接读取仍为 wal。"""
    db = tmp_path / "state.db"
    store = SqliteKVStore(db, table="t1")
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.close()
    # 新连接验证 WAL 已持久化到数据库文件
    verify = sqlite3.connect(db)
    assert verify.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    verify.close()


def test_busy_timeout_applied(tmp_path):
    """busy_timeout 走档位白名单：1234 非法档位被拒绝，1000/3000/5000 生效。"""
    with pytest.raises(ValueError):
        SqliteKVStore(tmp_path / "reject.db", table="t1", busy_timeout_ms=1234)
    store = SqliteKVStore(tmp_path / "state.db", table="t1", busy_timeout_ms=1000)
    assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1000
    store.close()


def test_set_waits_for_external_write_lock(tmp_path):
    """外部连接持写锁（BEGIN IMMEDIATE）时，set 在 busy_timeout 窗口内等待后成功。"""
    db = tmp_path / "state.db"
    store = SqliteKVStore(db, table="t1", busy_timeout_ms=3000)
    blocker = sqlite3.connect(db, timeout=0.1, check_same_thread=False)
    blocker.execute("BEGIN IMMEDIATE")  # 模拟其他 worker 正在写入

    def release_lock():
        time.sleep(0.1)
        blocker.commit()

    releaser = threading.Thread(target=release_lock)
    releaser.start()
    try:
        store.set("k", {"v": 1})  # 无 WAL/busy_timeout 时立即抛 database is locked
    finally:
        releaser.join()
        blocker.close()

    assert store.get("k") == {"v": 1}
    store.close()


def test_concurrent_workers_no_lost_write(tmp_path):
    """多实例并发写同一 SQLite 文件：全部成功、条数完整、无锁冲突异常。"""
    db = tmp_path / "state.db"
    n_workers, n_writes = 4, 25
    errors: list[Exception] = []

    def worker(wid: int) -> None:
        store = SqliteKVStore(db, table="shared")
        try:
            for i in range(n_writes):
                store.set(f"w{wid}-k{i}", {"wid": wid, "i": i})
        except Exception as exc:  # 锁冲突在此暴露
            errors.append(exc)
        finally:
            store.close()

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []
    reader = SqliteKVStore(db, table="shared")
    try:
        assert len(reader) == n_workers * n_writes
        assert reader.get("w0-k0") == {"wid": 0, "i": 0}
        assert reader.get(f"w{n_workers - 1}-k{n_writes - 1}") == {
            "wid": n_workers - 1,
            "i": n_writes - 1,
        }
    finally:
        reader.close()


@pytest.mark.parametrize("table", ["kv", "session", "slot"])
def test_wal_mode_across_named_tables(tmp_path, table):
    """不同业务表（会话/槽位/限流共用后端）各自实例均运行在 WAL 模式。"""
    store = SqliteKVStore(tmp_path / f"state-{table}.db", table=table)
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.close()


# --------------------------------------------------------------------------- #
# 并发首次创建重试（Windows 竞争回归，CI 2026-10-24）
# --------------------------------------------------------------------------- #
def test_init_retries_and_reconnects_on_readonly_contention(tmp_path, monkeypatch):
    """首次建库撞 readonly 竞争时必须**重建连接**重试成功。

    回归背景（CI 2026-10-24）：多线程同时 connect 一个尚未创建的 SQLite 文件，
    后到者拿到未写完文件头的库，SQLite 判为只读（该状态在连接上持久，
    原地重试无效）。此处桩掉第一次 connect，断言后续重建连接后正常可用。
    """
    import sqlite3

    real_connect = sqlite3.connect
    calls = {"n": 0}

    def flaky_connect(*args, **kwargs):
        if calls["n"] == 0:
            calls["n"] += 1
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", flaky_connect)
    store = SqliteKVStore(tmp_path / "state.db", table="t1")
    try:
        store.set("k", {"v": 1})
        assert store.get("k") == {"v": 1}
    finally:
        store.close()
    assert calls["n"] == 1


def test_init_retry_exhausted_raises_with_context(tmp_path, monkeypatch):
    """重试耗尽必须抛出并带上库路径，不得静默降级为可写假象。"""
    import sqlite3

    def always_readonly(*args, **kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(sqlite3, "connect", always_readonly)
    with pytest.raises(sqlite3.OperationalError, match="初始化存储失败"):
        SqliteKVStore(tmp_path / "state.db", table="t1")
