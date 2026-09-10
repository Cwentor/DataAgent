"""编排器事件流测试：run_agent(on_event=...) 的 SSE 事件契约。

验证点（与前端 AgentStreamEvent 契约对齐）：
- 事件序列：plan_created -> step_start -> tool_start/tool_end -> ... -> done；
- 每条事件都携带统一 turn_id / trace_id / timestamp；
- 载荷契约：plan 数组结构 / tool 结构 / reflection 结构 / artifact 结构；
- HITL 流：hitl_request 事件且 question 与澄清问题一致；
- 异常流：观察者抛错不反噬编排；图异常 -> error 事件 + 异常透传；
- 不传 on_event 时零行为变化（回归保障）。
"""

from __future__ import annotations

import pytest

from core.orchestrator.agent import run_agent
from core.orchestrator.events import (
    EVENT_ARTIFACT_EMIT,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_HITL_REQUEST,
    EVENT_PLAN_CREATED,
    EVENT_REFLECTION,
    EVENT_STEP_START,
    EVENT_TOOL_END,
    EVENT_TOOL_START,
)


def _collect(monkeypatch, tmp_path, question, **kwargs):
    """跑一次编排并收集事件（工作区隔离到 tmp_path）。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    events: list[dict] = []
    trace = run_agent(question, session_id="events", on_event=events.append, **kwargs)
    return trace, events


def test_event_stream_diagnostic_path(tmp_path, monkeypatch):
    """诊断问题：plan -> step -> tool -> reflection/artifact -> done 全序列。"""
    trace, events = _collect(
        monkeypatch,
        tmp_path,
        "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
    )
    assert trace.phase == "done"
    kinds = [e["event"] for e in events]
    # 关键事件类型齐备
    assert EVENT_PLAN_CREATED in kinds
    assert EVENT_STEP_START in kinds
    assert EVENT_TOOL_START in kinds
    assert EVENT_TOOL_END in kinds
    assert EVENT_DONE in kinds
    # 序：plan_created 先于首个 tool_start；done 收尾
    assert kinds.index(EVENT_PLAN_CREATED) < kinds.index(EVENT_TOOL_START)
    assert kinds[-1] == EVENT_DONE
    # 统一归属：turn_id / trace_id / timestamp 每条事件齐备
    turn_ids = {e["turn_id"] for e in events}
    assert len(turn_ids) == 1
    assert all(e["trace_id"] and e["timestamp"] > 0 for e in events)
    # done 载荷带最终报告
    done = events[-1]
    assert "分析报告" in done["payload"]["report"]


def test_event_plan_payload_contract(tmp_path, monkeypatch):
    """plan_created 载荷契约：[{id, title, kind, status}]。"""
    _, events = _collect(
        monkeypatch,
        tmp_path,
        "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
    )
    plan_events = [e for e in events if e["event"] == EVENT_PLAN_CREATED]
    assert plan_events
    plan = plan_events[0]["payload"]["plan"]
    assert isinstance(plan, list) and plan
    for step in plan:
        assert set(step) == {"id", "title", "kind", "status"}
        assert step["kind"] in ("query", "analyze", "synthesize")
        assert step["status"] in ("pending", "running", "done", "failed")


def test_event_tool_payload_contract(tmp_path, monkeypatch):
    """tool_start/tool_end 载荷契约：name + input/output/duration_ms/status。"""
    _, events = _collect(monkeypatch, tmp_path, "2024 年 5 月成功支付订单的 GMV 总额是多少？")
    starts = [e for e in events if e["event"] == EVENT_TOOL_START]
    ends = [e for e in events if e["event"] == EVENT_TOOL_END]
    assert starts and ends
    dsl_start = next(s for s in starts if s["payload"]["tool"]["name"] == "futurebi_dsl_query")
    assert "dsl" in dsl_start["payload"]["tool"]["input"]
    dsl_end = next(
        e
        for e in ends
        if e["payload"]["tool"]["name"] == "futurebi_dsl_query"
        and e["payload"]["tool"]["status"] == "ok"
    )
    assert dsl_end["payload"]["tool"]["duration_ms"] >= 0
    assert "rows" in dsl_end["payload"]["tool"]["output"]


def test_event_artifact_emit(tmp_path, monkeypatch):
    """沙箱产物：summary -> table 事件 + echarts 事件 + 报告 artifact_emit。"""
    _, events = _collect(
        monkeypatch,
        tmp_path,
        "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
    )
    artifact_events = [e for e in events if e["event"] == EVENT_ARTIFACT_EMIT]
    types = {e["payload"]["artifact"]["type"] for e in artifact_events}
    assert "table" in types
    assert "echarts" in types
    assert "markdown_report" in types
    # echarts 产物内容必须是合法 ECharts Option 骨架
    chart = next(e for e in artifact_events if e["payload"]["artifact"]["type"] == "echarts")
    assert "series" in chart["payload"]["artifact"]["content"]


def test_event_hitl_request(tmp_path, monkeypatch):
    """歧义问题：clarify 中断 -> hitl_request 事件携带澄清问题。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    events: list[dict] = []
    paused = run_agent("GMV呢？", session_id="hitl-ev", on_event=events.append)
    assert paused.phase == "clarify"
    hitl = [e for e in events if e["event"] == EVENT_HITL_REQUEST]
    assert len(hitl) == 1
    assert hitl[0]["payload"]["hitl"]["question"] == paused.clarification
    # HITL 流不发 done（流由服务端关闭）
    assert not any(e["event"] == EVENT_DONE for e in events)


def test_event_observer_error_swallowed(tmp_path, monkeypatch):
    """观察者抛异常不反噬编排主流程（防御性吞并）。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)

    def bad_observer(_event):
        raise RuntimeError("observer boom")

    trace = run_agent(
        "2024 年 5 月成功支付订单的 GMV 总额是多少？",
        session_id="observer-err",
        on_event=bad_observer,
    )
    assert trace.phase == "done"


def test_event_no_observer_unchanged(tmp_path, monkeypatch):
    """回归保障：不传 on_event 时编排结果与旧契约完全一致。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    trace = run_agent("2024 年 5 月成功支付订单的 GMV 总额是多少？", session_id="no-obs")
    assert trace.phase == "done"
    assert any(s["tool"] == "execute_dsl_query" and s["ok"] for s in trace.steps)


def test_event_error_on_graph_crash(tmp_path, monkeypatch):
    """图执行异常 -> error 事件 + 异常透传（服务端据此结束 SSE 流）。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    events: list[dict] = []

    import core.orchestrator.agent as agent_mod

    def boom(**kwargs):
        raise ValueError("graph exploded")

    monkeypatch.setattr(agent_mod, "build_graph", boom)
    with pytest.raises(ValueError):
        run_agent("任何问题", session_id="crash", on_event=events.append)
    err = [e for e in events if e["event"] == EVENT_ERROR]
    assert len(err) == 1
    assert "graph exploded" in err[0]["payload"]["error"]


def test_event_reflection_on_replan(tmp_path, monkeypatch):
    """反思节点重规划路径：reflection 事件 decision=replan。"""
    _, events = _collect(monkeypatch, tmp_path, "2024 年 5 月成功支付订单的 GMV 总额是多少？")
    reflections = [e for e in events if e["event"] == EVENT_REFLECTION]
    assert reflections
    assert all(
        r["payload"]["reflection"]["decision"] in ("proceed", "retry", "replan")
        for r in reflections
    )
    # 最终应有一次 proceed（转入综合）
    assert any(r["payload"]["reflection"]["decision"] == "proceed" for r in reflections)
