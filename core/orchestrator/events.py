"""编排器事件总线：向外部观察者（SSE 前端 / 评测器）实时广播执行事件。

设计约束：
- 节点与图引擎只依赖 ``emit_event``（contextvar 注入的观察者回调），
  不感知 HTTP/SSE 传输细节（依赖方向：web -> orchestrator，反向零依赖）；
- 未注册观察者时 ``emit_event`` 为 no-op —— 同步链路（/api/agent/run、
  评测、单元测试）行为完全不变；
- 事件载荷契约与前端 ``AgentStreamEvent`` 类型对齐（web/static/js/protocol.js）：
  ``{event, timestamp, payload}``，turn_id / trace_id 由 run_agent 统一附加。

事件语义（编排层只发业务事实，不做传输层决策）：
- plan_created：规划产出/重写任务 DAG（payload.plan: [{id,title,status}]）；
- step_start：进入图节点（payload.step_id/step_title）；
- tool_start / tool_end：工具调用前后（DSL 取数 / 沙箱执行）；
- reflection：反思节点自愈决策（proceed/retry/replan）；
- artifact_emit：产物生成（summary / echarts / 报告）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any

from audit.logging import get_logger

logger = get_logger("core.orchestrator.events")

# 事件类型常量（与前端 AgentEventType 契约一一对应）
EVENT_PLAN_CREATED = "plan_created"
EVENT_STEP_START = "step_start"
EVENT_TOOL_START = "tool_start"
EVENT_TOOL_END = "tool_end"
EVENT_REFLECTION = "reflection"
EVENT_HITL_REQUEST = "hitl_request"
EVENT_ARTIFACT_EMIT = "artifact_emit"
EVENT_DONE = "done"
EVENT_ERROR = "error"

# 观察者回调：接收 {"event", "timestamp", "payload"} 字典；异常由 emit_event 吞并告警
ObserverFn = Callable[[dict[str, Any]], None]

_observer: ContextVar[ObserverFn | None] = ContextVar("agent_event_observer", default=None)

# 图节点 -> 面向用户的步骤标题（前端时间线展示）
NODE_TITLES: dict[str, str] = {
    "clarify": "需求澄清",
    "plan": "任务规划",
    "query": "数据取数（DSL → SQL）",
    "analyze": "沙箱分析（Python）",
    "critique": "反思与自愈",
    "synthesize": "报告综合",
}


def set_observer(fn: ObserverFn | None) -> Token:
    """在当前执行上下文注册事件观察者（返回 token 供 finally reset）。"""
    return _observer.set(fn)


def reset_observer(token: Token) -> None:
    """恢复观察者上下文（必须配对 set_observer 使用）。"""
    _observer.reset(token)


def emit_event(event: str, payload: dict[str, Any]) -> None:
    """发射一次编排事件；无观察者时 no-op，观察者异常不反噬编排主流程。"""
    fn = _observer.get()
    if fn is None:
        return
    try:
        fn({"event": event, "timestamp": int(time.time() * 1000), "payload": payload})
    except Exception as exc:  # pragma: no cover - 观察者自身错误的防御性兜底
        logger.warning(
            "event_observer_error", extra={"event": "event_observer_error", "error": str(exc)}
        )


def emit_step_start(node: str) -> None:
    """进入图节点：step_start（标题走 NODE_TITLES 映射，未知节点用原名）。"""
    emit_event(EVENT_STEP_START, {"step_id": node, "step_title": NODE_TITLES.get(node, node)})


def emit_plan(plan_steps: list[Any]) -> None:
    """规划产出/重写：把 PlanStep 列表契约化为前端 DAG 载荷。"""
    emit_event(
        EVENT_PLAN_CREATED,
        {
            "plan": [
                {"id": s.id, "title": s.goal, "kind": s.kind, "status": s.status}
                for s in plan_steps
            ]
        },
    )


def emit_tool_start(tool: str, step_id: str, args: dict[str, Any]) -> None:
    """工具调用开始（tool 名与前端契约对齐：futurebi_dsl_query / python_sandbox）。"""
    emit_event(EVENT_TOOL_START, {"tool": {"name": tool, "input": args}, "step_id": step_id})


def emit_tool_end(
    tool: str,
    step_id: str,
    *,
    ok: bool,
    duration_ms: float,
    summary: str = "",
    error: str | None = None,
    output: dict[str, Any] | None = None,
) -> None:
    """工具调用结束：状态 + 耗时 + 结果摘要（output 供前端展开查看）。"""
    emit_event(
        EVENT_TOOL_END,
        {
            "tool": {
                "name": tool,
                "input": {},
                "output": output or {"summary": summary},
                "duration_ms": round(duration_ms, 1),
                "status": "ok" if ok else "failed",
                "error": error,
            },
            "step_id": step_id,
        },
    )


def emit_reflection(observation: str, decision: str, reason: str) -> None:
    """反思/自愈决策（decision: proceed | retry | replan）。"""
    emit_event(
        EVENT_REFLECTION,
        {"reflection": {"observation": observation, "decision": decision, "reason": reason}},
    )


__all__ = [
    "EVENT_ARTIFACT_EMIT",
    "EVENT_DONE",
    "EVENT_ERROR",
    "EVENT_HITL_REQUEST",
    "EVENT_PLAN_CREATED",
    "EVENT_REFLECTION",
    "EVENT_STEP_START",
    "EVENT_TOOL_END",
    "EVENT_TOOL_START",
    "NODE_TITLES",
    "ObserverFn",
    "emit_event",
    "emit_plan",
    "emit_reflection",
    "emit_step_start",
    "emit_tool_end",
    "emit_tool_start",
    "reset_observer",
    "set_observer",
]
