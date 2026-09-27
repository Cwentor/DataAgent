"""Subagent 运行时（M3 B 线，规格 §6）：受限任务卡 + 四节点受限子图 + 预算硬顶。

设计纪律：
- 刻意不做 clarify（不能反问用户）、不做 synthesize（报告权在父图）——
  子图为 plan_task -> query -> analyze -> summarize 四节点，节点函数复用主图；
- 子图强制 L4 自主性（零中断）；
- 无 LLM 降级为确定性兜底路径——离线可运行可单测；
- 白名单：allowed_tools 必须是 tools.registry 注册中心的子集（构造即校验）；
- 预算硬顶：步数经 recursion_limit（LangGraph 原生超步护栏）、超时经看门狗线程；
  超时后放弃等待但不强杀工作线程（Python 无安全强杀），残留执行由 exec/ 自身
  熔断兜底回收，结果被丢弃；
- 失败不炸主图：一切异常转 failed 报告回传（规格 §6.4）。
"""

from __future__ import annotations

import operator
import threading
from collections.abc import Callable
from typing import Annotated, Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END
from langgraph.graph import StateGraph as LGStateGraph
from pydantic import Field

from core.orchestrator.state import (
    AgentState,
    SubagentBudget,
    SubagentReport,
    SubagentTask,
)


class SubagentState(AgentState):
    """子图状态：继承 AgentState 契约；subagent_reports 通道用 append reducer
    （规格 §4.2 唯一 reducer 例外），父图侧为普通 list（native 契约）。"""

    subagent_reports: Annotated[list[SubagentReport], operator.add] = Field(default_factory=list)


def _task_id_tagged(observer: Callable[[dict], None] | None, task_id: str):
    """子任务事件标签器：payload 统一补 task_id 后转发主观察者（Task 15 汇流依赖）。"""
    if observer is None:
        return None

    def tagged(event: dict) -> None:
        observer({**event, "payload": {**event.get("payload", {}), "task_id": task_id}})

    return tagged


def _subagent_summarize(state: SubagentState) -> SubagentState:
    """summarize 替身：压缩 scratchpad 为 findings 候选；报告权在父图（规格 §6.2）。"""
    findings = [f"{i + 1}. {line}" for i, line in enumerate(list(state.scratchpad)[-10:])]
    return state.apply(scratchpad=findings, phase="done")


def _build_subagent_app(checkpointer: Any = None):
    """四节点受限子图：plan_task -> query -> analyze -> summarize，节点复用主图。"""
    from core.orchestrator.langgraph_engine import _observed

    g = LGStateGraph(SubagentState)
    g.add_node("plan_task", _observed("plan", planner_node_ref()))
    g.add_node("query", _observed("query", dsl_query_node_ref()))
    g.add_node("analyze", _observed("analyze", code_exec_node_ref()))
    g.add_node("summarize", _observed("synthesize", _subagent_summarize))
    g.set_entry_point("plan_task")
    g.add_edge("plan_task", "query")
    g.add_edge("query", "analyze")
    g.add_edge("analyze", "summarize")
    g.add_edge("summarize", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


def planner_node_ref():
    """延迟解析主图节点（避免与 langgraph_engine 的模块级互相 import）。"""
    from core.orchestrator.nodes import planner_node

    return planner_node


def dsl_query_node_ref():
    from core.orchestrator.nodes import dsl_query_node

    return dsl_query_node


def code_exec_node_ref():
    from core.orchestrator.nodes import code_exec_node

    return code_exec_node


def _last_artifact_audit(final: dict[str, Any]) -> dict[str, Any]:
    """从 datasets 的 ParquetRef.audit 提取 {guard, qa}；无产物时返回空壳。"""
    datasets = final.get("datasets") or {}
    for ref in datasets.values():
        audit = ref.get("audit") if isinstance(ref, dict) else getattr(ref, "audit", None)
        if isinstance(audit, dict) and "guard" in audit:
            return {"guard": audit.get("guard", "ok"), "qa": audit.get("qa", [])}
    return {"guard": "ok", "qa": []}


def _findings_from_state(final: dict[str, Any]) -> list[str]:
    """结构化摘要：scratchpad 压缩结果（父图唯一消费物）。"""
    scratch = final.get("scratchpad") or []
    return [str(x) for x in scratch[-10:]]


def run_subagent(task: SubagentTask, *, thread_id: str, observer=None) -> SubagentReport:
    """执行一次受限子任务并回传结构化报告（失败不炸主图）。"""
    app = _build_subagent_app()
    state = SubagentState(
        user_query=task.goal,
        session_id=thread_id,
        turn_id=task.task_id,
        autonomy_level="L4",  # 子图强制 L4：零中断
        **(
            {"history_digest": str(task.context_slice.get("digest", ""))}
            if task.context_slice.get("digest")
            else {}
        ),
    )
    config = {
        "configurable": {
            "thread_id": thread_id,
            "observer": _task_id_tagged(observer, task.task_id),
        },
        # 步数预算：四节点 + 余量；超限 GraphRecursionError -> timeout 收敛
        "recursion_limit": max(2, task.budget.max_steps + 1),
    }
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["result"] = app.invoke(state, config)
        except Exception as exc:  # 节点异常 / exec 看门狗超时
            box["error"] = exc
            box["is_budget"] = isinstance(exc, GraphRecursionError)

    worker = threading.Thread(target=_run, daemon=True, name=f"subagent-{task.task_id}")
    worker.start()
    worker.join(task.budget.timeout_seconds)
    if worker.is_alive():
        return SubagentReport(
            task_id=task.task_id,
            status="timeout",
            findings=[],
            artifacts=[],
            audit={"guard": "ok", "qa": []},
            metrics={"timeout_seconds": task.budget.timeout_seconds},
        )
    if "error" in box:
        if box.get("is_budget"):
            # 步数预算耗尽：与超时同语义收敛（规格 §6.4 预算硬顶）
            return SubagentReport(
                task_id=task.task_id,
                status="timeout",
                findings=["budget exhausted (max_steps)"],
                artifacts=[],
                audit={"guard": "ok", "qa": []},
                metrics={"budget_max_steps": task.budget.max_steps},
            )
        # 失败不炸主图（规格 §6.4）：转 failed 报告，由父图 critique 决定重派
        return SubagentReport(
            task_id=task.task_id,
            status="failed",
            findings=[f"subagent failed: {box['error']}"],
            artifacts=[],
            audit={"guard": "ok", "qa": []},
            metrics={},
        )
    final = box["result"]
    return SubagentReport(
        task_id=task.task_id,
        status="done",
        findings=_findings_from_state(final),
        artifacts=list(final.get("artifacts") or []),
        audit=_last_artifact_audit(final),
        metrics={"budget_max_steps": task.budget.max_steps},
    )


__all__ = [
    "SubagentBudget",
    "SubagentReport",
    "SubagentState",
    "SubagentTask",
    "run_subagent",
]
