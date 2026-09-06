"""登录失败指数退避限流（用户名 + IP）。

整改指令3-2：配置 db_path（默认取 settings.STATE_STORE_DB）时，失败计数
落盘 SQLite，使多 worker / 重启后限流状态一致，避免绕过限流。
"""

from __future__ import annotations

import threading
import time

from config import settings
from persistence.kvstore import SqliteKVStore


class LoginRateLimitError(RuntimeError):
    """登录暂时被限流。"""

    def __init__(self, retry_after: int) -> None:
        """携带重试等待秒数并生成用户可读消息（Carry retry-after seconds）。"""
        self.retry_after = retry_after
        super().__init__(f"登录失败次数过多，请 {retry_after} 秒后重试")


class LoginRateLimiter:
    """登录失败指数退避限流器：按 key 计数，超过阈值后进入指数退避封禁窗口。"""

    def __init__(
        self,
        max_failures: int = 5,
        base_seconds: float = 2.0,
        max_seconds: float = 300.0,
        db_path: str | None = None,
    ) -> None:
        """初始化限流参数；db_path 配置时失败计数经 SqliteKVStore 跨进程持久化。"""
        self.max_failures = max_failures
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        # 持久化层：配置路径时失败计数落盘（多 worker 一致）
        self._kv = SqliteKVStore(db_path, table="login_failures") if db_path else None

    def _persist(self, key: str, failures: int, last_failure: float) -> None:
        if self._kv is not None:
            self._kv.set(key, {"failures": failures, "last_failure": last_failure})

    def _load(self, key: str) -> tuple[int, float] | None:
        if self._kv is None:
            return None
        raw = self._kv.get(key)
        if raw is None:
            return None
        return int(raw["failures"]), float(raw["last_failure"])

    def check(self, key: str) -> None:
        """校验 key 是否处于封禁窗口，是则抛 LoginRateLimitError（含剩余等待秒数）。"""
        with self._lock:
            entry = self._failures.get(key) or self._load(key)
            if entry is None:
                return
            failures, last_failure = entry
            delay = min(
                self.base_seconds * (2 ** max(0, failures - self.max_failures)), self.max_seconds
            )
            # 跨进程持久化后必须使用墙钟时间（monotonic 不可跨进程比较）
            remaining = delay - (time.time() - last_failure)
            if failures >= self.max_failures and remaining > 0:
                raise LoginRateLimitError(max(1, int(remaining + 0.999)))

    def record_failure(self, key: str) -> None:
        """登记一次登录失败（Record a failed attempt，并落盘持久化）。"""
        with self._lock:
            failures, _ = self._failures.get(key, (0, 0.0)) or (0, 0.0)
            failures += 1
            now = time.time()
            self._failures[key] = (failures, now)
            self._persist(key, failures, now)

    def record_success(self, key: str) -> None:
        """登录成功清零失败计数（Reset the failure counter on success）。"""
        with self._lock:
            self._failures.pop(key, None)
            if self._kv is not None:
                self._kv.delete(key)


_default_limiter: LoginRateLimiter | None = None
_limiter_lock = threading.Lock()


def default_login_limiter() -> LoginRateLimiter:
    """进程内复用的默认限流器（参数由 settings 注入，可配）。"""
    global _default_limiter
    if _default_limiter is None:
        with _limiter_lock:
            if _default_limiter is None:
                _default_limiter = LoginRateLimiter(
                    max_failures=settings.AUTH_LOGIN_MAX_FAILURES,
                    base_seconds=settings.AUTH_LOGIN_BASE_SECONDS,
                    max_seconds=settings.AUTH_LOGIN_MAX_SECONDS,
                    db_path=settings.STATE_STORE_DB,
                )
    return _default_limiter
