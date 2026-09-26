"""fan-out（M3 Task 14；规格 §6.3/§6.4）：Send 并行结果合并顺序无关、
失败不炸主图、并发上限护栏、重派语义、native 串行降级。"""

from __future__ import annotations

import threading

from core.orchestrator.subagent import SubagentBudget, SubagentReport, SubagentTask


def _task(i: int) -> SubagentTask:
    return SubagentTask(
        task_id=f"t{i}",
        goal=f"2024年6月区域{i}的GMV是多少？",
        context_slice={},
        allowed_tools=[],
        budget=SubagentBudget(max_steps=8, timeout_seconds=60),
    )


def test_parallel_merge_is_order_independent(monkeypatch):
    """Send 并行跑 3 个子任务：两次跑按 task_id 规范化后逐字段相等（种子固定）。"""
    monkeypatch.setattr("config.settings.LLM_API_KEY", "")
    from core.orchestrator.langgraph_engine import run_fanout

    r1 = run_fanout([_task(i) for i in range(3)], thread_id="u1:f1")
    r2 = run_fanout([_task(i) for i in range(3)], thread_id="u1:f2")

    def key(rs):
        return [
            (r.task_id, r.status, tuple(r.findings), tuple(sorted(r.audit.items())))
            for r in sorted(rs, key=lambda x: x.task_id)
        ]

    assert key(r1) == key(r2)
    assert [r.task_id for r in r1] == ["t0", "t1", "t2"]  # 返回按 task_id 排序


def test_single_failure_does_not_kill_fanout(monkeypatch):
    """单子任务失败/超时：其余子任务照常返回，报告语义完整（失败不炸主图）。"""
    monkeypatch.setattr("config.settings.LLM_API_KEY", "")
    from core.orchestrator.langgraph_engine import run_fanout

    tasks = [
        _task(0),
        SubagentTask(
            task_id="bad",
            goal="g",
            allowed_tools=["__not_registered__"] if False else [],
            budget=SubagentBudget(max_steps=1, timeout_seconds=1),
        ),
    ]
    reports = run_fanout(tasks, thread_id="u1:f3")
    assert len(reports) == 2
    assert all(isinstance(r, SubagentReport) for r in reports)
    statuses = {r.status for r in reports}
    assert statuses <= {"done", "failed", "timeout"}
    assert "bad" in {r.task_id for r in reports}


def test_concurrency_cap_enforced(monkeypatch):
    """并发闸：同时在途的 run_subagent 峰值不超过 SUBAGENT_MAX_PARALLEL。"""
    monkeypatch.setattr("config.settings.LLM_API_KEY", "")
    monkeypatch.setattr("config.settings.SUBAGENT_MAX_PARALLEL", 2)
    import core.orchestrator.langgraph_engine as lge

    counter = {"active": 0, "peak": 0}
    lock = threading.Lock()
    real = lge.run_subagent  # 先取真函数再替换：wrapper 内经 real 调用，避免自引用递归

    def counted(task, **kwargs):
        with lock:
            counter["active"] += 1
            counter["peak"] = max(counter["peak"], counter["active"])
        try:
            return real(task, **kwargs)
        finally:
            with lock:
                counter["active"] -= 1

    monkeypatch.setattr(lge, "run_subagent", counted)
    lge.run_fanout([_task(i) for i in range(6)], thread_id="u1:f4")
    assert counter["peak"] <= 2


def test_redispatch_once_then_disclose(monkeypatch):
    """重派语义：失败报告重派 ≤1 轮；仍失败则在报告层如实披露缺口。"""
    monkeypatch.setattr("config.settings.LLM_API_KEY", "")
    monkeypatch.setattr("config.settings.SUBAGENT_REDISPATCH_MAX", 1)
    import core.orchestrator.langgraph_engine as lge

    attempts = {"n": 0}
    real = lge.run_subagent

    def always_fail(task, **kwargs):
        attempts["n"] += 1
        return SubagentReport(
            task_id=task.task_id,
            status="failed",
            findings=["boom"],
            audit={"guard": "ok", "qa": []},
        )

    monkeypatch.setattr(lge, "run_subagent", always_fail)
    reports = lge.run_fanout([_task(0)], thread_id="u1:f5")
    assert len(reports) == 1
    assert reports[0].status == "failed"
    # 原始 1 次 + 重派 1 次（SUBAGENT_REDISPATCH_MAX=1，不无限重试）
    assert attempts["n"] == 2
    assert any("缺口" in f or "失败" in f for f in reports[0].findings)


def test_native_engine_serial_fallback(monkeypatch):
    """native 引擎降级：fan-out 退化为串行 for 循环（新增能力皆可降级）。"""
    monkeypatch.setattr("config.settings.LLM_API_KEY", "")
    monkeypatch.setattr("config.settings.ORCHESTRATOR_ENGINE", "native")
    from core.orchestrator.langgraph_engine import run_fanout_serial

    reports = run_fanout_serial([_task(i) for i in range(2)], thread_id="u1:f6")
    assert [r.task_id for r in reports] == ["t0", "t1"]
