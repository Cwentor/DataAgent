"""编排器入口：装配六节点图并提供 run_agent 门面（Control Plane 主入口）。

图拓扑（条件路由）：
    clarify -> plan -> query -> analyze -> critique -> synthesize -> END
                    ^                    |
                    +------ replan ------+
                    ^                    |
                    +--- (重试耗尽) ------+ -> synthesize（如实报告）

- ``build_graph``：装配图（节点注册 + 条件边）；
- ``run_agent``：一次完整编排（可传 human_reply 恢复 HITL）；
- ``AgentTrace``：对上层（web/eval）暴露的最小结果面——报告 + 多步日志 +
  自愈记录 + 产物（ECharts 规格）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.orchestrator import events
from core.orchestrator.graph import StateGraph
from core.orchestrator.nodes import (
    clarify_node,
    code_exec_node,
    critic_node,
    dsl_query_node,
    planner_node,
    synthesize_node,
)
from core.orchestrator.state import AgentState


class AgentTrace(BaseModel):
    """一次编排的可观测产物（审计/前端/E2E 断言消费）。"""

    model_config = ConfigDict(extra="forbid")

    report: str = ""
    phase: str = "done"
    steps: list[dict[str, Any]] = Field(default_factory=list)
    scratchpad: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    self_heal_count: int = 0
    clarification: str | None = None
    session_id: str = "default"
    turn_id: str = "t1"
    trace_id: str = "tr1"

    def to_dict(self) -> dict[str, Any]:
        """序列化（审计与 API 响应）。"""
        return self.model_dump(mode="json")


def build_graph(*, max_iterations: int = 24) -> StateGraph:
    """装配六节点图：clarify -> plan -> query -> analyze -> critique -> synthesize。"""
    graph = StateGraph(max_iterations=max_iterations)
    graph.add_node("clarify", clarify_node)
    graph.add_node("plan", planner_node)
    graph.add_node("query", dsl_query_node)
    graph.add_node("analyze", code_exec_node)
    graph.add_node("critique", critic_node)
    graph.add_node("synthesize", synthesize_node)
    graph.set_entry("clarify")

    def route_from_clarify(state: AgentState) -> str:
        # clarify 后固定进入规划（HITL 中断时图在 clarify 暂停，不走此处）
        return "plan"

    def route_from_plan(state: AgentState) -> str:
        # 规划产出 query 步骤则取数；纯分析计划（无数据需求）直接进沙箱
        if any(s.kind == "query" and s.status == "pending" for s in state.plan_steps):
            return "query"
        if any(s.kind == "analyze" and s.status == "pending" for s in state.plan_steps):
            return "analyze"
        return "critique"

    def route_from_critic(state: AgentState) -> str:
        return state.phase  # plan(重规划) | synthesize

    graph.add_edge("clarify", "plan")
    graph.add_conditional_edges(
        "plan",
        route_from_plan,
        {"query": "query", "analyze": "analyze", "critique": "critique"},
    )
    graph.add_edge("query", "analyze")
    graph.add_edge("analyze", "critique")
    graph.add_conditional_edges(
        "critique", route_from_critic, {"plan": "plan", "synthesize": "synthesize"}
    )
    graph.add_edge("synthesize", "END")
    return graph


def run_agent(
    question: str,
    *,
    session_id: str = "default",
    turn_id: str | None = None,
    trace_id: str | None = None,
    human_reply: str | None = None,
    resume_state: AgentState | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> AgentTrace | AgentState:
    """运行一次编排（同步简化版；HITL 恢复经 resume_state 传入）。

    on_event：可选事件观察者（SSE 流式前端注入）。注册后编排过程实时发射
    plan_created / step_start / tool_* / reflection / artifact_emit 事件，
    终态补发 done/error 收尾事件；不传时零开销、行为与旧契约完全一致。
    """
    import uuid

    # turn/trace 标识先于观察者注册生成：保证每条流式事件都可归属到本次会话轮次
    resolved_turn = turn_id or f"t-{uuid.uuid4().hex[:8]}"
    resolved_trace = trace_id or f"tr-{uuid.uuid4().hex[:8]}"

    def _observer(raw: dict[str, Any]) -> None:
        """事件包装器：统一附加 turn_id / trace_id（前端 AgentStreamEvent 契约）。"""
        raw["turn_id"] = resolved_turn
        raw["trace_id"] = resolved_trace
        on_event(raw)

    token = events.set_observer(_observer) if on_event is not None else None
    try:
        final = _run_agent_inner(
            question,
            session_id=session_id,
            turn_id=resolved_turn,
            trace_id=resolved_trace,
            human_reply=human_reply,
            resume_state=resume_state,
        )
        if on_event is not None:
            # 终态收尾事件（复位观察者前发射，否则为 no-op）
            if final.phase == "clarify":
                events.emit_event(
                    events.EVENT_HITL_REQUEST,
                    {"hitl": {"question": final.clarification or "请补充分析需求"}},
                )
            else:
                events.emit_event(events.EVENT_DONE, {"report": final.report})
    except Exception as exc:
        if on_event is not None:
            events.emit_event(events.EVENT_ERROR, {"error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        if token is not None:
            events.reset_observer(token)

    if final.phase == "clarify":
        # HITL：返回含澄清问题的中间态（调用方展示问题 -> 收集答复 -> 再次调用）
        return final

    return AgentTrace(
        report=final.report,
        phase=final.phase,
        steps=[r.model_dump(mode="json") for r in final.tool_calls],
        scratchpad=final.scratchpad,
        artifacts=[a.model_dump(mode="json") for a in final.artifacts],
        self_heal_count=final.error_context.retries,
        clarification=final.clarification,
        session_id=final.session_id,
        turn_id=final.turn_id,
        trace_id=final.trace_id,
    )


def _run_agent_inner(
    question: str,
    *,
    session_id: str,
    turn_id: str,
    trace_id: str,
    human_reply: str | None,
    resume_state: AgentState | None,
) -> AgentState:
    """run_agent 的图执行主体（事件观察者生命周期由 run_agent 管理）。"""
    if resume_state is not None:
        graph = build_graph()
        return graph.resume(resume_state)
    state = AgentState(
        session_id=session_id,
        turn_id=turn_id,
        trace_id=trace_id,
        user_query=question,
        human_reply=human_reply,
        phase="clarify" if human_reply is None else "plan",
    )
    graph = build_graph()
    return graph.run(state)


__all__ = ["AgentTrace", "build_graph", "run_agent"]
