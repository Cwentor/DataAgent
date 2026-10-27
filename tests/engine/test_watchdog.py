"""看门狗通用框架测试：超时触发 adapter.cancel 并抛 QueryTimeoutError。

不依赖任何真实引擎（FakeAdapter 模拟执行/取消/异常），验证引擎无关的
线程框架行为：正常执行、超时取消、异常透传。
"""

from __future__ import annotations

import time

import pytest

from exec.engine import connstate
from exec.engine.watchdog import run_with_timeout
from exec.errors import QueryTimeoutError


class _FakeConn:
    """可弱引用的假连接（用于验证超时后打标）。"""


class _FakeAdapter:
    """可编程假适配器：控制执行耗时与抛出的异常，记录 cancel 调用。"""

    name = dialect = "fake"

    def __init__(self, sleep_seconds: float = 0.0, fail_with: Exception | None = None) -> None:
        self._sleep = sleep_seconds
        self._fail = fail_with
        self.cancelled = False

    def run_query(self, conn, sql):
        time.sleep(self._sleep)
        if self._fail is not None:
            raise self._fail
        return ["a"], [[1]]

    def cancel(self, conn) -> None:
        self.cancelled = True


def test_run_with_timeout_ok_without_timeout():
    result = run_with_timeout(_FakeAdapter(), object(), "SELECT 1", None)
    assert result == (["a"], [[1]])


def test_run_with_timeout_ok_within_timeout():
    result = run_with_timeout(_FakeAdapter(sleep_seconds=0), object(), "SELECT 1", 1000)
    assert result == (["a"], [[1]])


def test_run_with_timeout_cancels_and_raises():
    adapter = _FakeAdapter(sleep_seconds=5.0)
    with pytest.raises(QueryTimeoutError, match="300"):
        run_with_timeout(adapter, object(), "SELECT 1", 300)
    assert adapter.cancelled is True


def test_run_with_timeout_cancel_failure_still_raises():
    """cancel 抛异常不影响超时分支继续抛 QueryTimeoutError（fail 路径不吞）。"""

    class _BadCancel(_FakeAdapter):
        def cancel(self, conn) -> None:
            self.cancelled = True
            raise RuntimeError("cancel failed")

    adapter = _BadCancel(sleep_seconds=5.0)
    with pytest.raises(QueryTimeoutError):
        run_with_timeout(adapter, object(), "SELECT 1", 100)
    assert adapter.cancelled is True


def test_run_with_timeout_passthrough_exception():
    with pytest.raises(ValueError, match="boom"):
        run_with_timeout(_FakeAdapter(fail_with=ValueError("boom")), object(), "SELECT 1", None)


def test_run_with_timeout_marks_connection_unusable():
    """超时后连接必须被打标（2026-10-27 审计 P0）。

    外部后端的 cancel 是空操作，超时时 worker 可能仍阻塞在同一连接上；不打标
    就会让 pool.release 把脏连接放回池，下一个请求与残留查询争用同一连接。
    """
    conn = _FakeConn()
    assert connstate.is_unusable(conn) is False
    with pytest.raises(QueryTimeoutError):
        run_with_timeout(_FakeAdapter(sleep_seconds=5.0), conn, "SELECT 1", 100)
    assert connstate.is_unusable(conn) is True


def test_run_with_timeout_does_not_mark_on_success():
    """正常执行不得打标：否则每次查询都会废弃连接，池退化为直连。"""
    conn = _FakeConn()
    run_with_timeout(_FakeAdapter(sleep_seconds=0), conn, "SELECT 1", 5000)
    assert connstate.is_unusable(conn) is False
