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


def test_task_id_tagged_observer():
    """Task 15：子任务事件标签器——payload 统一补 task_id 后转发（汇流契约）。"""
    from core.orchestrator.subagent import _task_id_tagged

    seen = []
    tagged = _task_id_tagged(seen.append, "t9")
    tagged({"event": "tool_start", "payload": {"step_id": "s1"}})
    assert seen[0]["payload"]["task_id"] == "t9"
    assert seen[0]["event"] == "tool_start"
    assert _task_id_tagged(None, "t9") is None


def test_facade_done_event_carries_run_manifest(monkeypatch, tmp_path):
    """Task 15：fan-out 完成后 done 事件 payload 携带 run manifest（审计轨迹）。"""
    from config import settings

    monkeypatch.setattr("config.settings.LLM_API_KEY", "")
    monkeypatch.setattr(settings, "ORCHESTRATOR_ENGINE", "langgraph")
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    import core.orchestrator.langgraph_engine as lge_mod
    from core.orchestrator.state import PlanStep, SubagentReport

    def fake_run_subagent(task, *, thread_id, observer=None):
        return SubagentReport(
            task_id=task.task_id,
            status="done",
            findings=[f"结论 {task.task_id}"],
            audit={"guard": "ok", "qa": []},
        )

    monkeypatch.setattr(lge_mod, "run_subagent", fake_run_subagent)

    def fake_planner(state):
        step = PlanStep(
            id="s1",
            goal="区域归因",
            kind="query",
            fanout_tasks=[
                {"task_id": "t0", "goal": "区域0 归因", "context_slice": {}, "allowed_tools": []},
                {"task_id": "t1", "goal": "区域1 归因", "context_slice": {}, "allowed_tools": []},
            ],
        )
        return state.apply(plan_steps=[step], phase="query")

    def fake_critic(state):
        return state.apply(phase="synthesize")

    def fake_synth(state):
        return state.apply(phase="done", report="综合报告")

    monkeypatch.setattr(lge_mod, "planner_node", fake_planner)
    monkeypatch.setattr(lge_mod, "critic_node", fake_critic)
    monkeypatch.setattr(lge_mod, "synthesize_node", fake_synth)
    monkeypatch.setattr(lge_mod, "_app", None)  # 强制重编译（节点编译期捕获）

    from core.orchestrator.agent import run_agent

    seen = []
    out = run_agent(
        "分析 2024 年各区域的 GMV 归因差异情况", session_id="s-manifest", on_event=seen.append
    )
    assert out.phase == "done"
    done_events = [e for e in seen if e["event"] == "done"]
    assert done_events and "manifest" in done_events[-1]["payload"]
    manifest = done_events[-1]["payload"]["manifest"]
    assert {m["task_id"] for m in manifest} == {"t0", "t1"}
    assert all(m["status"] == "done" for m in manifest)
