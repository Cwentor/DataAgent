"""异步查询任务管理器（生产化）单元测试。"""

from __future__ import annotations

import time

import pytest

from web.tasks import TaskManager


@pytest.fixture
def manager():
    m = TaskManager(max_workers=2, max_history=10, ttl_seconds=3600)
    yield m
    m.shutdown(wait=False)


def test_submit_success_flow(manager):
    """提交 -> pending/running -> success，结果与时间戳齐全。"""
    task_id = manager.submit(lambda a, b: a + b, 2, b=3)
    snap = manager.wait(task_id)
    assert snap is not None
    assert snap["status"] == "success"
    assert snap["result"] == 5
    assert snap["error"] is None
    assert snap["started_at"] is not None and snap["finished_at"] is not None
    assert snap["finished_at"] >= snap["started_at"]


def test_submit_failure_captured(manager):
    """任务函数异常 -> failed 状态 + error 文案，不向线程池外泄漏。"""

    def boom():
        raise ValueError("查询超时模拟")

    task_id = manager.submit(boom)
    snap = manager.wait(task_id)
    assert snap["status"] == "failed"
    assert "ValueError" in snap["error"] and "查询超时模拟" in snap["error"]
    assert snap["result"] is None


def test_snapshot_unknown_task(manager):
    assert manager.snapshot("nonexistent") is None
    assert manager.wait("nonexistent", timeout=0.01) is None


def test_history_cap_evicts_oldest_finished():
    """终态任务超过历史上限：最旧者被清理，进行中任务不受影响。"""
    m = TaskManager(max_workers=1, max_history=2, ttl_seconds=3600)
    try:
        ids = [m.submit(lambda i=i: time.sleep(0.01) or i) for i in range(4)]
        for tid in ids:
            m.wait(tid)
        m.cleanup()  # 手动触发惰性清理（清理时机在 submit）
        assert len(m._tasks) == 2  # 只保留最新 2 条终态任务
        assert m.snapshot(ids[0]) is None
        assert m.snapshot(ids[3]) is not None
    finally:
        m.shutdown(wait=False)


def test_ttl_cleanup(manager, monkeypatch):
    """终态任务超过 TTL 后在下次提交时被惰性清理。"""
    task_id = manager.submit(lambda: 1)
    manager.wait(task_id)
    assert manager.snapshot(task_id) is not None

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 7200)  # 快进 2h（TTL 1h）
    manager.submit(lambda: 2)  # 触发惰性清理
    assert manager.snapshot(task_id) is None


def test_concurrent_tasks_all_complete(manager):
    """并发提交 8 个任务：全部到达终态且结果正确。"""
    ids = [manager.submit(lambda i=i: i * 2) for i in range(8)]
    for i, tid in enumerate(ids):
        snap = manager.wait(tid)
        assert snap["status"] == "success"
        assert snap["result"] == i * 2
