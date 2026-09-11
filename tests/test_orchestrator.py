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
    for _ in range(50):
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


def test_synthesize_consumes_datasets_for_pure_query(tmp_path, monkeypatch):
    """纯查询问题（无沙箱 analyze 步骤）报告必须消费取数结果。

    回归锚点：此前 synthesize 只认沙箱 summary 产物，基数/标量问题即使
    取数成功也输出"未能获得有效的分析产物"（用户可见的能力缺口）。
    """
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    trace = run_agent("2024 年 5 月成功支付订单的 GMV 总额是多少？", session_id="pureq")
    assert trace.phase == "done"
    assert "查询结果" in trace.report
    assert "未能获得有效的分析产物" not in trace.report
    # 标量聚合直接给答案行，且数值必须人读化（万元，R1 严禁 raw 字节面值直出）
    assert "查询答案：" in trace.report
    assert "万元" in trace.report
    assert "gmv =" not in trace.report


def test_tool_end_preview_rows_points_at_inputs_dir(tmp_path, monkeypatch):
    """tool_end 审计预览必须读到物化 Parquet（路径 = workspace/inputs/<ref.path>）。

    回归锚点：此前拼成 workspace/<ref.path> 导致 preview_rows 恒为空。
    """
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    events: list[dict] = []
    trace = run_agent(
        "2024 年 5 月成功支付订单的 GMV 总额是多少？", session_id="preview", on_event=events.append
    )
    assert trace.phase == "done"
    ends = [
        e
        for e in events
        if e["event"] == "tool_end" and e["payload"]["tool"]["name"] == "futurebi_dsl_query"
    ]
    assert ends
    for e in ends:
        preview = e["payload"]["tool"]["output"].get("preview_rows")
        assert preview, "预览行不应为空（物化文件在 workspace/inputs/ 下）"
        assert all(len(r) >= 1 for r in preview)


# --------------------------------------------------------------------------- #
# LLM DSL 草稿规范化（宽容接受，严格校验）
# --------------------------------------------------------------------------- #
def test_normalize_dsl_draft_repairs_common_llm_typos():
    """裸字符串维度 / time_range 笔误 / ge-le 操作符自动纠正为契约形态。"""
    from core.orchestrator.nodes import _normalize_dsl_draft

    d = _normalize_dsl_draft(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": ["province", {"field": "category"}],
            "time_range": {
                "range_type": "absolute",
                "absolute": {"start": "2024-05-01", "end": "2024-05-08"},
            },
            "filters": [
                {"field": "pay_status", "operator": "eq", "value": "SUCCESS"},
                {"field": "order_amount", "operator": "ge", "value": 10},
            ],
        }
    )
    assert d["dimensions"] == [{"field": "province"}, {"field": "category"}]
    assert "time_range" not in d and "time_filter" in d
    assert [f["operator"] for f in d["filters"]] == ["eq", "gte"]


def test_normalize_dsl_draft_passes_valid_payload_unchanged():
    """合法载荷规范化后语义不变（可继续通过网关契约校验）。"""
    from core.orchestrator.nodes import _normalize_dsl_draft
    from core.retrieval.guardrails import validate_dsl_payload

    d = _normalize_dsl_draft(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "province"}],
            "time_filter": {
                "range_type": "absolute",
                "absolute": {"start": "2024-05-01", "end": "2024-05-08"},
            },
        }
    )
    validate_dsl_payload(d, where="test")  # 不抛即通过契约


def test_planner_prompt_contract_examples_align_with_schema():
    """提示词中的 DSL 示例必须与真实契约对齐（防再次系统性带偏 LLM）。

    回归锚点：dimensions 示例是对象数组、时间字段名是 time_filter、
    操作符白名单不含 like/ge/le。
    """
    from core.orchestrator.prompts import PLANNER_SYSTEM

    assert 'dimensions: [{"field"' in PLANNER_SYSTEM
    assert "严禁写成裸字符串" in PLANNER_SYSTEM
    assert "time_filter" in PLANNER_SYSTEM
    assert '"time_range"' not in PLANNER_SYSTEM
    assert 'operator": "eq|ne|in|gt|gte|lt|lte|between"' in PLANNER_SYSTEM


# --------------------------------------------------------------------------- #
# 重规划自愈上下文（pi-agent-harness 对齐：行动项 3）
# --------------------------------------------------------------------------- #
def test_planner_prompt_injects_error_context():
    """planner_prompt 带 error_context：必须注入失败记录并要求针对性修正。"""
    from core.orchestrator.prompts import planner_prompt

    prompt = planner_prompt(
        "查 GMV", "- gmv (fact_orders.order_amount)", error_context="CompileError: 字段不存在"
    )
    assert "上次失败记录" in prompt
    assert "CompileError: 字段不存在" in prompt
    assert "严禁原样重复上一轮计划" in prompt


def test_planner_prompt_without_error_context_unchanged():
    """planner_prompt 不带 error_context：不出现失败记录小节（首轮规划不变）。"""
    from core.orchestrator.prompts import planner_prompt

    prompt = planner_prompt("查 GMV", "- gmv (fact_orders.order_amount)")
    assert "上次失败记录" not in prompt


def test_planner_node_feeds_error_context_to_llm(monkeypatch):
    """重规划时 planner_node 把最近失败摘要注入 LLM 提示词（断裂点修复实锤）。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState

    captured: dict[str, str] = {}
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())

    def fake_llm_json(llm, system, user):
        captured["user"] = user
        return None  # 走启发式兜底，重点在捕获提示词

    monkeypatch.setattr(nodes, "_llm_json", fake_llm_json)
    state = AgentState(user_query="查 GMV")
    state.error_context.record("CompileError: 字段 nonexistent 不在语义目录")
    nodes.planner_node(state)
    assert "上次失败记录" in captured["user"]
    assert "nonexistent 不在语义目录" in captured["user"]


def test_critic_trace_digest_carries_error_history(monkeypatch):
    """LLM 反思的执行轨迹必须包含自愈错误记录（反思层看得见失败历史）。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState, Artifact

    captured: dict[str, str] = {}
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())

    def fake_llm_json(llm, system, user):
        captured["user"] = user
        return {"verdict": "sufficient", "reasons": ["ok"]}

    monkeypatch.setattr(nodes, "_llm_json", fake_llm_json)
    state = AgentState(
        user_query="为什么下滑",
        datasets={"s1": {"path": "x.parquet", "rows": 1, "columns": ["gmv"]}},
        artifacts=[Artifact(kind="summary", name="s2", payload={"summary": {"title": "归因"}})],
    )
    state.error_context.record("沙箱执行失败: NameError")
    nodes.critic_node(state)
    assert "自愈错误记录" in captured["user"]
    assert "NameError" in captured["user"]
