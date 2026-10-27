"""只读连接池（P0-6 + 二十一期引擎化）：固定容量、惰性建连、经 EngineAdapter 建连。

背景（审计 P0-6）：旧实现每次 run_query 都新建/关闭连接——无复用、无连接
上限、无请求排队信号量，多个超大查询可同时各占一个连接且各跑一轮
EXPLAIN ANALYZE，单机实例可被打满。exec/guards.py 有"护栏"但缺"闸门"。

本模块提供固定容量只读连接池：
- 容量固定（max_connections），连接惰性创建（adapter.connect_readonly）、
  用完归还复用；
- acquire() 在池满且已达容量上限时阻塞排队（等价请求闸门）；
- 全局并发信号量（BoundedSemaphore）由 web.service 叠加使用，形成"排队 + 熔断"双保险。

二十一期：连接创建下沉 EngineAdapter（duckdb 文件只读 / mysql 只读会话
加固），池框架（排队/复用/关闭）引擎无关。
"""

from __future__ import annotations

import queue
import threading
from typing import Any

from audit.logging import get_logger
from config import settings
from exec.engine import connstate
from exec.engine.base import EngineAdapter

logger = get_logger("exec.pool")


class ReadOnlyConnectionPool:
    """固定容量的只读连接池（线程安全，经 EngineAdapter 建连）。"""

    def __init__(self, adapter: EngineAdapter, max_connections: int = 4) -> None:
        """初始化容量与待还队列；连接惰性创建（Lazy connection creation）。"""
        if max_connections < 1:
            raise ValueError("max_connections 必须 >= 1")
        self._adapter = adapter
        self._max = max_connections
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_connections)
        self._created = 0
        self._lock = threading.Lock()
        self._closed = False

    def acquire(self) -> Any:
        """取一个连接；池满且达容量上限时阻塞排队，直到有连接归还。"""
        if self._closed:
            raise RuntimeError("连接池已关闭")
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._created < self._max:
                conn = self._adapter.connect_readonly()
                self._created += 1
                return conn
        # 已达容量上限：阻塞等待归还（等价并发闸门）
        return self._queue.get()

    def release(self, conn: Any) -> None:
        """归还连接；已被标记不可复用（超时/中断）则转废弃，不复用。

        调用方无需感知连接状态：``exec.engine.watchdog`` 在超时分支会给连接打标，
        此处自动分流。这是刻意选择——5 个归还点（web/service、tools/builtins、
        retrieval 的 exploration/profiling/tools）逐个改容易漏，而漏掉一处就等于
        留一条脏连接复用通道。
        """
        if connstate.is_unusable(conn):
            self.discard(conn)
            return
        if self._closed:
            conn.close()
            return
        self._queue.put(conn)

    def discard(self, conn: Any) -> None:
        """废弃连接：关闭并回收容量名额，必要时补建一条唤醒排队者。

        超时/中断后的连接上可能仍有活跃查询（外部后端 ``cancel`` 为空操作），
        归还复用会与残留查询争用同一连接，故直接关闭而非入队。

        容量名额必须回收，否则池会永久少一条可用连接、逐步缩容到 0。回收后若
        仍有阻塞在 ``acquire`` 的等待者，需要主动补建一条放回队列——否则它们
        等的是「有人归还」，而这条连接已经不会再归还了，会永久阻塞。
        """
        connstate.clear(conn)
        try:
            conn.close()
        except Exception:  # 关闭失败不影响容量回收与补位
            logger.warning("废弃连接关闭失败（忽略，容量照常回收）", exc_info=True)

        with self._lock:
            if self._created > 0:
                self._created -= 1
            if self._closed or self._created >= self._max:
                return
            # 预留名额后再建连（避免并发 acquire 同时越过容量上限）
            self._created += 1

        try:
            fresh = self._adapter.connect_readonly()
        except Exception:
            with self._lock:
                self._created -= 1
            logger.warning("废弃后补建连接失败，容量名额已退回（等待者继续排队）", exc_info=True)
            return
        self._queue.put(fresh)

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

    容量取 settings.DB_POOL_SIZE、引擎取 settings.DB_BACKEND（二十一期）；
    供 tools.builtins._query_core 与 web.service 共享，保证工具层与
    web.service 使用同一连接池。
    """
    global _default_pool
    if _default_pool is None:
        with _pool_lock:
            if _default_pool is None:
                from exec.engine import get_adapter  # 惰性导入保持 pool -> engine 单向依赖

                _default_pool = ReadOnlyConnectionPool(get_adapter(), settings.DB_POOL_SIZE)
    return _default_pool


def reset_default_pool() -> None:
    """关闭并清空进程内默认连接池（后端切换 / 多后端体检脚本用）。

    池按 DB_BACKEND 惰性绑定 adapter，后端切换后必须重建——否则仍持有上一
    后端的连接（例如体检脚本在同一进程内串行跑 duckdb → mysql → hive）。
    幂等：池不存在时为空操作。
    """
    global _default_pool
    with _pool_lock:
        if _default_pool is not None:
            _default_pool.close()
            _default_pool = None
