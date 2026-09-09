"""编排器单测：图引擎语义 / 状态契约 / HITL 中断恢复 / 端到端确定性路径。

覆盖（对应企业级 Data Agent 需求 §2.A + §4 步骤 3/4）：
- StateGraph：条件路由、HITL 中断与 resume、迭代护栏、缺出边报错；
- AgentState：契约冻结（extra=forbid）、token 预算修剪、错误上下文上限；
- 六节点端到端（确定性兜底路径，真实 mock 数仓 + 沙箱）：
  诊断问题 -> 多步日志 + 沙箱产物 + 报告；澄清门 HITL 两段式。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.orchestrator.agent import run_agent
from core.orchestrator.graph import GraphError, StateGraph
from core.orchestrator.state import MAX_RETRIES, AgentState, ToolRecord


# --------------------------------------------------------------------------- #
# 图引擎
# --------------------------------------------------------------------------- #
def test_graph_conditional_routing_and_end():
    graph = StateGraph()
    graph.add_node("a", lambda s: s.apply(phase="analyze"))
    graph.add_node("b", lambda s: s.apply(phase="done", report="ok"))
    graph.set_entry("a")
    graph.add_conditional_edges(
        "a", lambda s: "go_b" if s.phase == "analyze" else "stop", {"go_b": "b", "stop": "END"}
    )
    graph.add_edge("b", "END")
    final = graph.run(AgentState(user_query="x"))
    assert final.report == "ok" and final.iteration == 2


def test_graph_hitl_interrupt_and_resume():
    graph = StateGraph()
    graph.add_node("ask", lambda s: s.apply(phase="clarify", clarification="请补充时间范围"))
    graph.add_node("plan", lambda s: s.apply(phase="done", report=f"计划基于: {s.user_query}"))
    graph.set_entry("ask")
    graph.add_edge("ask", "plan")
    graph.add_edge("plan", "END")

    paused = graph.run(AgentState(user_query="GMV 为什么下滑"))
    assert paused.phase == "clarify" and paused.clarification

    resumed = graph.resume(paused.apply(human_reply="2024 年 5 月上旬"))
    assert resumed.phase == "done"
    assert "2024 年 5 月上旬" in resumed.report


def test_graph_resume_requires_clarify_phase():
    graph = StateGraph()
    graph.add_node("a", lambda s: s)
    with pytest.raises(GraphError):
        graph.resume(AgentState(phase="plan"))


def test_graph_max_iteration_guard():
    loop_state = lambda s: s.apply(phase="plan")  # noqa: E731
    graph = StateGraph(max_iterations=5)
    graph.add_node("a", loop_state)
    graph.set_entry("a")
    graph.add_conditional_edges("a", lambda s: "self", {"self": "a"})
    final = graph.run(AgentState(user_query="x"))
    assert "强制终止" in final.report


def test_graph_missing_edge_raises():
    graph = StateGraph()
    graph.add_node("a", lambda s: s.apply(phase="done"))
    graph.set_entry("a")
    with pytest.raises(GraphError):
        graph.run(AgentState(user_query="x"))


# --------------------------------------------------------------------------- #
# 状态契约
# --------------------------------------------------------------------------- #
def test_agent_state_forbids_extra_fields():
    with pytest.raises(ValidationError):
        AgentState(user_query="x", rogue_field=1)


def test_error_context_retry_cap():
    state = AgentState(user_query="x")
    for i in range(MAX_RETRIES):
        assert state.error_context.record(f"err {i}") is True
    assert state.error_context.record("final") is False
    assert state.error_context.retries == MAX_RETRIES + 1


def test_tool_history_pruning():
    state = AgentState(user_query="x")
    for i in range(50):
        state.tool_calls.append(ToolRecord(tool="t", summary="s" * 5000))
    state.prune_tool_history(budget_chars=30_000)
    total = sum(len(r.summary) for r in state.tool_calls)
    assert total <= 30_000 + 5000  # 单条超预算时保留最后一条
    assert len(state.tool_calls) < 50


# --------------------------------------------------------------------------- #
# 六节点端到端（确定性兜底，真实 mock 数仓 + 沙箱）
# --------------------------------------------------------------------------- #
def test_run_agent_diagnostic_e2e(tmp_path, monkeypatch):
    """诊断问题端到端：多步日志 + 数据集 + 沙箱产物 + 报告 + ECharts。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    trace = run_agent(
        "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位", session_id="e2e"
    )
    assert isinstance(trace, dict) is False
    assert trace.phase == "done"
    # 多步日志：至少 取数 + 沙箱 两条成功轨迹
    ok_tools = [s for s in trace.steps if s["ok"]]
    assert any(s["tool"] == "execute_dsl_query" for s in ok_tools)
    assert any(s["tool"] == "run_code" for s in ok_tools)
    # 产物：summary + echarts
    kinds = {a["kind"] for a in trace.artifacts}
    assert "summary" in kinds and "echarts" in kinds
    # 报告含结论与自愈统计行
    assert "分析报告" in trace.report
    assert "执行轨迹" in trace.report


def test_run_agent_hitl_flow(tmp_path, monkeypatch):
    """歧义问题 -> 澄清中断 -> 用户答复 -> 完成全流程。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    paused = run_agent("GMV呢？", session_id="hitl")
    assert not isinstance(paused, dict)
    assert paused.phase == "clarify" and paused.clarification

    final = run_agent(
        "GMV呢？",
        session_id="hitl",
        resume_state=paused.apply(human_reply="2024 年 5 月按省份的订单金额"),
    )
    assert final.phase == "done"
    assert any(s["ok"] for s in final.steps)


def test_run_agent_simple_query_path(tmp_path, monkeypatch):
    """非诊断问题走单查询路径：取数 + 综合即完成。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    trace = run_agent("2024 年 5 月成功支付订单的 GMV 总额是多少？", session_id="simple")
    assert trace.phase == "done"
    assert any(s["tool"] == "execute_dsl_query" and s["ok"] for s in trace.steps)
