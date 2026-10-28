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

二十七年（2026-10-28，延迟关闭）：``discard`` 的「关闭脏连接」不再立刻执行，
改为「先建新、后关旧 + 宽限期延迟关闭」。原因见 ``_DeferredCloser`` 与
``discard`` 的注释——外部后端 ``cancel`` 为空操作时 worker 可能仍阻塞在旧连接上，
立刻关闭会让操作系统马上复用该 fd 号，把紧接着新建的健康连接读坏（Linux CI 上
``postgres-parity`` 的真实失败）。
"""

from __future__ import annotations

import heapq
import queue
import threading
import time
from typing import Any

from audit.logging import get_logger
from config import settings
from exec.engine import connstate
from exec.engine.base import EngineAdapter

logger = get_logger("exec.pool")

# 延迟关闭队列上限：宽限期内若 ``discard`` 高频发生（连续超时），待关连接会堆积。
# 超过上限立刻关闭最早的，把 fd 占用压在有界范围内——延迟关闭是「避免 fd 立刻被
# 复用」的加固手段，不是无限期保留连接的缓存。
_MAX_DEFERRED = 64


def _close_quietly(conn: Any) -> None:
    """关闭连接并吞掉异常（关闭失败不影响容量回收与补位）。"""
    try:
        conn.close()
    except Exception:  # 关闭失败不影响容量回收与补位
        logger.warning("废弃连接关闭失败（忽略，容量照常回收）", exc_info=True)


class _DeferredCloser:
    """延迟关闭器：把脏连接的 ``close()`` 推迟一个宽限期再执行。

    为什么需要（2026-10-27 Linux CI ``postgres-parity`` 抓出的 fd 复用竞争）：

    外部后端的 ``adapter.cancel`` 是空操作（MySQL/CH/Hive 至今仍是），看门狗
    ``join(2.0)`` 返回时 worker 可能仍阻塞在旧连接的网络读上。此时立刻 ``close()``
    会马上释放该 fd 号，而 ``discard`` 紧接着新建的连接极可能拿到**同一个** fd 号
    ——仍阻塞的 worker 会抢读新连接的启动报文，把这条新建的健康连接读坏
    （PG 上表现为 ``lost synchronization with server``，新连接建不起来、
    容量名额退不回、池逐步缩容到 0）。

    ``discard`` 已用「先建新、后关旧」消除顺序竞争（建新时旧 fd 仍被占用，
    OS 必然分配不同号码）；本类再推迟关闭，让 worker 在 fd 真正释放前有时间退出，
    把残余窗口压到接近零。

    实现：单 daemon 线程 + 到期堆，线程数不随连接数增长。宽限期见
    ``settings.DB_POOL_DISCARD_GRACE_MS``，置 0 退化为「立刻关闭」（回退开关）。
    """

    def __init__(self) -> None:
        """初始化空队列；后台线程惰性启动（首次提交时才创建）。"""
        self._heap: list[tuple[float, int, Any]] = []
        self._seq = 0
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopped = False

    def submit(self, conn: Any, grace_ms: int) -> None:
        """提交一条待关闭连接；``grace_ms <= 0`` 时立刻关闭。"""
        if grace_ms <= 0:
            _close_quietly(conn)
            return
        deadline = time.monotonic() + grace_ms / 1000.0
        overflow: list[Any] = []
        with self._cond:
            if self._stopped:
                pass  # 已停止：不排队，落到锁外直接关（避免泄漏）
            else:
                self._seq += 1
                heapq.heappush(self._heap, (deadline, self._seq, conn))
                # 队列超限：立刻关闭最早到期的，保证 fd 占用有界
                while len(self._heap) > _MAX_DEFERRED:
                    overflow.append(heapq.heappop(self._heap)[2])
                self._start_locked()
                self._cond.notify()
                conn = None
        if conn is not None:
            _close_quietly(conn)
        for stale in overflow:
            _close_quietly(stale)

    def _start_locked(self) -> None:
        """启动后台线程（调用方须持锁）；线程已存活时为空操作。"""
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run, name="pool-deferred-close", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        """后台线程：等到最早一条到期就关闭它，直到收到停止信号。"""
        while True:
            pending: list[Any] = []
            stopping = False
            conn = None
            with self._cond:
                while True:
                    if self._stopped:
                        pending = [c for _, _, c in self._heap]
                        self._heap.clear()
                        stopping = True
                        break
                    if not self._heap:
                        self._cond.wait()
                        continue
                    deadline, _, candidate = self._heap[0]
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        self._cond.wait(remaining)
                        continue
                    heapq.heappop(self._heap)
                    conn = candidate
                    break
            if stopping:
                for leftover in pending:
                    _close_quietly(leftover)
                return
            _close_quietly(conn)

    def drain(self) -> None:
        """停止后台线程并立刻关闭全部待关连接（池关闭时调用，幂等）。"""
        with self._cond:
            self._stopped = True
            pending = [c for _, _, c in self._heap]
            self._heap.clear()
            self._cond.notify_all()
        for conn in pending:
            _close_quietly(conn)


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
        self._closer = _DeferredCloser()

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
        """废弃连接：回收容量名额、补建新连接，随后**延迟**关闭脏连接。

        超时/中断后的连接上可能仍有活跃查询（外部后端 ``cancel`` 为空操作），
        归还复用会与残留查询争用同一连接，故直接关闭而非入队。

        容量名额必须回收，否则池会永久少一条可用连接、逐步缩容到 0。回收后若
        仍有阻塞在 ``acquire`` 的等待者，需要主动补建一条放回队列——否则它们
        等的是「有人归还」，而这条连接已经不会再归还了，会永久阻塞。

        **顺序是关键：必须先建新、后关旧。** 反过来（先关旧再建新）会让操作系统
        立刻把刚释放的 fd 号分配给新连接，而超时后仍阻塞在旧连接上的 worker 会
        抢读新连接的启动报文——2026-10-27 Linux CI 的 ``postgres-parity`` 正是这么
        红的（``lost synchronization with server``，新连接建不起来、名额退不回）。
        建新时旧 fd 仍被占用，OS 必然分配不同号码，竞争从根上消失。

        关闭本身再交给 ``_DeferredCloser`` 推迟一个宽限期：worker 退出时机不可控
        （服务端超时秒级到分钟级不等），晚一点关能进一步压小残余窗口。
        """
        connstate.clear(conn)

        with self._lock:
            if self._created > 0:
                self._created -= 1
            need_refill = not self._closed and self._created < self._max
            if need_refill:
                # 预留名额后再建连（避免并发 acquire 同时越过容量上限）
                self._created += 1

        fresh = None
        if need_refill:
            try:
                fresh = self._adapter.connect_readonly()
            except Exception:
                with self._lock:
                    self._created -= 1
                logger.warning(
                    "废弃后补建连接失败，容量名额已退回（等待者继续排队）", exc_info=True
                )

        # 关旧必须在建新之后（见 docstring）；宽限期由配置控制，0 = 立刻关
        self._closer.submit(conn, settings.DB_POOL_DISCARD_GRACE_MS)

        if fresh is not None:
            self._queue.put(fresh)

    def close(self) -> None:
        """关闭池与全部连接（幂等）；同时排空延迟关闭队列。"""
        self._closed = True
        while True:
            try:
                self._queue.get_nowait().close()
            except queue.Empty:
                break
        # 待关连接也必须立刻关掉，否则宽限期内进程退出会泄漏 fd
        self._closer.drain()

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
