"""异步查询任务管理器（生产化）：提交 -> task_id -> 轮询取回结果。

同步 ThreadingHTTPServer 下，长查询占用请求线程直至完成；异步任务把
"执行"与"等待"解耦：POST /api/query/async 立即返回 task_id（202），
后台线程执行完整查询链路（run_query，含全部护栏与审计），客户端轮询
GET /api/tasks/<task_id> 取回状态与结果。

设计约束：
- 线程池容量独立于查询并发闸（MAX_CONCURRENT_QUERIES 是查询内层闸，
  任务池是外层调度）；任务函数直接复用 run_query，护栏不旁路；
- 任务快照与结果保存在进程内存：进程重启任务丢失（任务存活期短，
  持久化收益低；跨进程共享待引入消息队列时再演进）；
- 历史条目按 ASYNC_TASK_HISTORY 上限 + TTL 惰性清理，防无限增长；
- 任务函数异常被捕获进 FAILED 状态（error 字段），绝不向线程池外泄漏。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from config import settings

__all__ = ["TaskManager", "default_task_manager"]


class TaskManager:
    """线程池异步任务管理器：提交函数 -> task_id -> 状态/结果快照。"""

    def __init__(
        self,
        max_workers: int | None = None,
        max_history: int | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers or settings.ASYNC_TASK_MAX_WORKERS,
            thread_name_prefix="async-task",
        )
        self._max_history = max_history or settings.ASYNC_TASK_HISTORY
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.ASYNC_TASK_TTL_SECONDS
        self._tasks: dict[str, dict[str, Any]] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        """提交任务（立即返回 task_id）；任务在后台线程执行 fn(*args, **kwargs)。"""
        task_id = uuid.uuid4().hex
        self.cleanup()
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "status": "pending",
                "created_at": time.time(),
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self._events[task_id] = threading.Event()

        def _run() -> None:
            with self._lock:
                task = self._tasks.get(task_id)
                if task is not None:
                    task["status"] = "running"
                    task["started_at"] = time.time()
            try:
                result = fn(*args, **kwargs)
                status, payload, error = "success", result, None
            except Exception as exc:  # 任务异常进 FAILED，不向线程池外泄漏
                status, payload, error = "failed", None, f"{type(exc).__name__}: {exc}"
            with self._lock:
                task = self._tasks.get(task_id)
                if task is not None:
                    task["status"] = status
                    task["result"] = payload
                    task["error"] = error
                    task["finished_at"] = time.time()
                event = self._events.get(task_id)
            if event is not None:
                event.set()

        self._pool.submit(_run)
        return task_id

    def snapshot(self, task_id: str) -> dict[str, Any] | None:
        """读取任务快照；未知 task_id 返回 None。"""
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task is not None else None

    def wait(self, task_id: str, timeout: float = 30.0) -> dict[str, Any] | None:
        """阻塞等待任务终态（测试与同步降级用），超时返回当前快照。"""
        with self._lock:
            event = self._events.get(task_id)
        if event is not None:
            event.wait(timeout)
        return self.snapshot(task_id)

    def cleanup(self) -> None:
        """清理过期与超限的终态任务（submit 时惰性触发；运维/测试可手动调用）。"""
        with self._lock:
            self._cleanup_locked()

    def _cleanup_locked(self) -> None:
        """惰性清理：TTL 过期与超历史上限的终态任务（须持锁调用）。"""
        now = time.time()
        expired = [
            tid
            for tid, t in self._tasks.items()
            if t["status"] in ("success", "failed")
            and t["finished_at"] is not None
            and now - t["finished_at"] > self._ttl
        ]
        for tid in expired:
            self._tasks.pop(tid, None)
            self._events.pop(tid, None)
        # 超历史上限：从最旧开始丢弃终态任务
        overflow = len(self._tasks) - self._max_history
        if overflow > 0:
            for tid, t in sorted(self._tasks.items(), key=lambda kv: kv[1]["created_at"]):
                if overflow <= 0:
                    break
                if t["status"] in ("success", "failed"):
                    self._tasks.pop(tid, None)
                    self._events.pop(tid, None)
                    overflow -= 1

    def shutdown(self, wait: bool = True) -> None:
        """关闭线程池（进程退出 / 测试收尾）。"""
        self._pool.shutdown(wait=wait)


_default_manager: TaskManager | None = None
_default_lock = threading.Lock()


def default_task_manager() -> TaskManager:
    """进程内默认任务管理器（懒加载单例）。"""
    global _default_manager
    with _default_lock:
        if _default_manager is None:
            _default_manager = TaskManager()
        return _default_manager
