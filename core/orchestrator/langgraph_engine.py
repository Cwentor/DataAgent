"""LangGraph 引擎适配层：六节点语义原样平移，节点/路由函数零改动复用。

设计纪律（设计文档 §3.2/§4.2，docs/plans/2026-09-26-agent-harness-evolution-m0-m4.md Task 4）：
- 事件总线不动：节点内仍用 events.emit_event（contextvar 观察者）；
  观察者经 config["configurable"]["observer"] 显式传入执行线程——contextvar
  不跨线程传播，这是 M0 前置审计（docs/reviews/20260926-audit-orchestration-concurrency.md）
  确认的必须项，不是防御性冗余；
- 中断泛化：clarify 与后续 plan_review 共用 interrupt() 原语，中断一律放
  独立轻量 gate 节点——LangGraph resume 会从头重执行 gate 所在节点，
  副作用（LLM 调用 / 事件发射）放在 gate 会重复触发；
- 状态合并语义：不引入 reducer，节点返回完整 AgentState（与自研图一致）；
- step_start 事件由图引擎发射（native 在 run 循环，本层在节点包装器），
  gate 节点不发射（native 无此节点，事件序列必须逐类等价）；
- iteration 计数口径与 native 对齐：仅在六个业务节点执行前 +1，gate 不计。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path as FsPath
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END
from langgraph.graph import StateGraph as LGStateGraph
from langgraph.types import Command, interrupt

from config import settings
from core.orchestrator import events
from core.orchestrator.agent import route_from_clarify, route_from_critic, route_from_plan
from core.orchestrator.nodes import (
    clarify_node,
    code_exec_node,
    critic_node,
    dsl_query_node,
    planner_node,
    synthesize_node,
)
from core.orchestrator.state import AgentState

ObserverFn = Callable[[dict[str, Any]], None]

# native 图的迭代护栏上限（graph.py 默认 max_iterations=24）；LangGraph 侧
# 以 recursion_limit 承担终止护栏（超级步口径，初始 64，Task 5 校准测试锚定）
NATIVE_MAX_ITERATIONS = 24


def _observed(node_name: str, fn: Callable[[AgentState], AgentState]):
    """节点包装器：从 LangGraph config 取观察者，在本执行线程内重设 contextvar。

    同时承担 native 图引擎 run 循环的两项职责：step_start 发射 + iteration 计数。
    """

    def wrapped(state: AgentState, config: RunnableConfig) -> AgentState:
        observer = ((config or {}).get("configurable") or {}).get("observer")
        token = events.set_observer(observer) if observer is not None else None
        try:
            events.emit_step_start(node_name)
            # iteration 计数口径与 native graph.run 逐节点 +1 一致（gate 不计）
            state = state.model_copy(update={"iteration": state.iteration + 1})
            return fn(state)
        finally:
            if token is not None:
                events.reset_observer(token)

    return wrapped


def _clarify_gate(state: AgentState) -> AgentState:
    """clarify 中断门：clarify_node 产出澄清问题后挂起；恢复时按 native
    graph.resume 的合并语义（user_query 追加"（用户补充：…）"）转 plan。

    gate 无 LLM 调用、无事件发射——LangGraph resume 会从头重执行本节点。
    """
    if state.phase != "clarify":
        return state
    resume_value = interrupt({"kind": "clarify", "clarification": state.clarification})
    # 合并语义逐字对齐 native graph.resume（graph.py L143-149）
    return state.apply(
        user_query=f"{state.user_query}（用户补充：{str(resume_value).strip()}）",
        human_reply=None,
        clarification=None,
        phase="plan",
    )


def _compile(checkpointer: Any) -> Any:
    """装配六节点图 + clarify_gate：边结构与 native build_graph 同构。"""
    g = LGStateGraph(AgentState)
    g.add_node("clarify", _observed("clarify", clarify_node))
    g.add_node("clarify_gate", _clarify_gate)
    g.add_node("plan", _observed("plan", planner_node))
    g.add_node("query", _observed("query", dsl_query_node))
    g.add_node("analyze", _observed("analyze", code_exec_node))
    g.add_node("critique", _observed("critique", critic_node))
    g.add_node("synthesize", _observed("synthesize", synthesize_node))
    g.set_entry_point("clarify")
    g.add_edge("clarify", "clarify_gate")
    g.add_conditional_edges("clarify_gate", route_from_clarify, {"plan": "plan"})
    g.add_conditional_edges(
        "plan",
        route_from_plan,
        {"query": "query", "analyze": "analyze", "critique": "critique"},
    )
    g.add_edge("query", "analyze")
    g.add_edge("analyze", "critique")
    g.add_conditional_edges(
        "critique", route_from_critic, {"plan": "plan", "synthesize": "synthesize"}
    )
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=checkpointer)


# 进程级单例：interrupt/Command(resume) 依赖同一 checkpointer 才能跨调用恢复。
_checkpointer: Any = None
_app: Any = None
_singletons_lock = threading.Lock()


def get_checkpointer() -> Any:
    """按配置返回 checkpointer（M1 Task 8）。

    ORCHESTRATOR_CHECKPOINT_DB 非空 -> SqliteSaver 持久化（长任务断点续航，
    服务重启后 clarify/plan_review 挂起仍可恢复）；否则 MemorySaver（进程内）。
    """
    global _checkpointer
    with _singletons_lock:
        if _checkpointer is None:
            db = settings.ORCHESTRATOR_CHECKPOINT_DB
            if db:
                FsPath(db).parent.mkdir(parents=True, exist_ok=True)
                _checkpointer = SqliteSaver(sqlite3.connect(str(db), check_same_thread=False))
            else:
                _checkpointer = MemorySaver()
        return _checkpointer


def build_langgraph_app(*, checkpointer: Any | None = None) -> Any:
    """编译一个新的 LangGraph 图实例（测试 / 需要独立 checkpointer 的调用方用）。"""
    return _compile(checkpointer if checkpointer is not None else get_checkpointer())


def _get_app() -> Any:
    """进程级复用的编译图（run_agent 路径；checkpointer 单例随图固化）。"""
    global _app
    if _app is None:
        _app = build_langgraph_app()
    return _app


def _config(thread_id: str, observer: ObserverFn | None) -> dict:
    return {
        "configurable": {"thread_id": thread_id, "observer": observer},
        "recursion_limit": settings.ORCHESTRATOR_RECURSION_LIMIT,
    }


def _extract(result: Any) -> tuple[AgentState, dict | None]:
    """invoke 结果 -> (AgentState, 中断 payload)。__interrupt__ 为 LangGraph 约定键。"""
    pending: dict | None = None
    if isinstance(result, dict):
        interrupts = result.get("__interrupt__") or []
        if interrupts:
            value = getattr(interrupts[0], "value", None)
            pending = dict(value) if isinstance(value, dict) else {"kind": "unknown", "raw": value}
    state = AgentState.model_validate(
        {k: v for k, v in result.items() if k not in {"__interrupt__", "__prev__"}}
    )
    return state, pending


def invoke_langgraph(
    state: AgentState,
    *,
    thread_id: str,
    observer: ObserverFn | None = None,
    recursion_limit: int | None = None,
    app: Any | None = None,
    checkpointer: Any | None = None,
) -> tuple[AgentState, dict | None]:
    """从初始状态执行图；clarify 挂起时返回 (中间态, 中断 payload)。

    observer 缺省时从当前 contextvar 取（run_agent 已注册包装观察者）——
    节点在同线程执行时事件不丢失，跨线程时由 config 传递兜底。
    """
    resolved_observer = observer if observer is not None else events.get_observer()
    cfg = _config(thread_id, resolved_observer)
    if recursion_limit is not None:
        cfg["recursion_limit"] = recursion_limit
    resolved_app = app or (
        build_langgraph_app(checkpointer=checkpointer) if checkpointer else _get_app()
    )
    result = resolved_app.invoke(state, cfg)
    return _extract(result)


def resume_langgraph(
    state: AgentState,
    resume_payload: dict,
    *,
    thread_id: str,
    observer: ObserverFn | None = None,
    recursion_limit: int | None = None,
    app: Any | None = None,
    checkpointer: Any | None = None,
) -> tuple[AgentState, dict | None]:
    """从挂起态恢复：resume_payload["resume_value"] 即 Command(resume=...) 注入值。"""
    resolved_observer = observer if observer is not None else events.get_observer()
    cfg = _config(thread_id, resolved_observer)
    if recursion_limit is not None:
        cfg["recursion_limit"] = recursion_limit
    resolved_app = app or (
        build_langgraph_app(checkpointer=checkpointer) if checkpointer else _get_app()
    )
    result = resolved_app.invoke(Command(resume=resume_payload["resume_value"]), cfg)
    return _extract(result)


__all__ = [
    "NATIVE_MAX_ITERATIONS",
    "build_langgraph_app",
    "get_checkpointer",
    "invoke_langgraph",
    "resume_langgraph",
]
