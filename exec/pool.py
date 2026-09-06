"""只读连接池（P0-6）：替换 web.service 每次请求新建/关闭连接的写法。

背景（审计 P0-6）：旧实现每次 run_query 都 duckdb.connect(DB_PATH, read_only=True)
用完即关——无复用、无连接上限、无请求排队信号量，多个超大查询可同时各占一个连接
且各跑一轮 EXPLAIN ANALYZE，单机实例可被打满。exec/guards.py 有"护栏"但缺"闸门"。

本模块提供固定容量只读连接池：
- 容量固定（max_connections），连接惰性创建、用完归还复用；
- acquire() 在池满且已达容量上限时阻塞排队（等价请求闸门）；
- 全局并发信号量（BoundedSemaphore）由 web.service 叠加使用，形成"排队 + 熔断"双保险。
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import duckdb

from config import settings


class ReadOnlyConnectionPool:
    """固定容量的只读 DuckDB 连接池（线程安全）。"""

    def __init__(self, db_path: Path | str, max_connections: int = 4) -> None:
        """初始化容量与待还队列；连接惰性创建（Lazy connection creation）。"""
        if max_connections < 1:
            raise ValueError("max_connections 必须 >= 1")
        self._db_path = str(db_path)
        self._max = max_connections
        self._queue: queue.Queue[duckdb.DuckDBPyConnection] = queue.Queue(maxsize=max_connections)
        self._created = 0
        self._lock = threading.Lock()
        self._closed = False

    def acquire(self) -> duckdb.DuckDBPyConnection:
        """取一个连接；池满且达容量上限时阻塞排队，直到有连接归还。"""
        if self._closed:
            raise RuntimeError("连接池已关闭")
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._created < self._max:
                conn = duckdb.connect(self._db_path, read_only=True)
                self._created += 1
                return conn
        # 已达容量上限：阻塞等待归还（等价并发闸门）
        return self._queue.get()

    def release(self, conn: duckdb.DuckDBPyConnection) -> None:
        """归还连接；池已关闭则直接关闭连接。"""
        if self._closed:
            conn.close()
            return
        self._queue.put(conn)

    def close(self) -> None:
        """关闭池与全部连接（幂等）。"""
        self._closed = True
        while True:
            try:
                self._queue.get_nowait().close()
            except queue.Empty:
                break

    @property
    def size(self) -> int:
        """当前创建的连接数（<= 容量）。"""
        return self._created

    @property
    def capacity(self) -> int:
        """最大连接容量（Maximum pool capacity）。"""
        return self._max

    def __enter__(self) -> ReadOnlyConnectionPool:
        """支持 with 上下文管理（Context-manager entry）。"""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """退出时关闭连接池（Close the pool on exit）。"""
        self.close()


# --------------------------------------------------------------------------- #
# 进程内默认连接池（供工具层 / web 层复用，避免各自重复建池）
# --------------------------------------------------------------------------- #
_default_pool: ReadOnlyConnectionPool | None = None
_pool_lock = threading.Lock()


def default_pool() -> ReadOnlyConnectionPool:
    """进程内复用的默认只读连接池（惰性初始化，线程安全）。

    容量取 settings.DB_POOL_SIZE；供 tools.builtins._query_core 在未注入
    连接时使用，保证工具层与 web.service 共享同一连接池。
    """
    global _default_pool
    if _default_pool is None:
        with _pool_lock:
            if _default_pool is None:
                _default_pool = ReadOnlyConnectionPool(settings.DB_PATH, settings.DB_POOL_SIZE)
    return _default_pool
