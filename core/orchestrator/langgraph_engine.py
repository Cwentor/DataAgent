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
from core.orchestrator.agent import (
    route_from_clarify,
    route_from_critic,
    route_from_plan,
)
from core.orchestrator.autonomy import maybe_interrupt
from core.orchestrator.nodes import (
    clarify_node,
    code_exec_node,
    critic_node,
    dsl_query_node,
    planner_node,
    synthesize_node,
)
from core.orchestrator.state import AgentState
from core.orchestrator.subagent import (
    SubagentState,
    SubagentTask,
    _task_id_tagged,
    run_subagent,
)

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


def _plan_gate(state: AgentState) -> AgentState:
    """plan_review 审批门（M2 Plan Mode，规格 §5.1）：多步 DAG 或含 analyze
    步骤才触发；L1 强制触发。真中断 / L4 直通由 maybe_interrupt 分流。

    phase 约定：approve 后保持 planner 产出的 phase（供 route_from_plan 分流）；
    edit 置 phase="plan"（回 planner 重规划）；reject 置 phase="done"。
    三种去向由 route_from_plan_gate 条件边分流，不得混用 route_from_plan。
    gate 无 LLM 调用、无事件发射——resume 会从头重执行本节点。

    十八期：Planner LLM 判定歧义（clarification 契约）=> 在此挂起等待用户
    答复，恢复后合并答复回 plan 重规划。回归锚点：此前 gate 对 phase=clarify
    直接透传，而 plan→clarify 无图边——clarification 分支从未真正中断，
    会落 critique 空转重规划直至迭代护栏强制终止。
    """
    if state.phase == "clarify" and state.clarification:
        resume_value = interrupt(
            {
                "kind": "clarify",
                "clarification": state.clarification,
                "options": list(state.clarification_options),
            }
        )
        # 合并语义逐字对齐 _clarify_gate（user_query 追加"（用户补充：…）"）；
        # plan_steps 必须置空（终审 Important #3）：重规划场景下旧计划会让
        # 路由/反思拿旧状态行事，用户答复被静默吞掉
        return state.apply(
            user_query=f"{state.user_query}（用户补充：{str(resume_value).strip()}）",
            human_reply=None,
            clarification=None,
            clarification_rounds=state.clarification_rounds + 1,
            plan_steps=[],
            phase="plan",
        )
    multi_step = len(state.plan_steps) > 1 or any(s.kind == "analyze" for s in state.plan_steps)
    forced = state.autonomy_level == "L1"
    # planner 产出计划后会把 phase 直接置为首个待执行步骤（query/analyze/critique），
    # 不能以 phase=="plan" 判定——以"有计划且非终态/挂起相位"为触发前提
    if state.plan_reviewed:
        return state  # L2：本轮已审批，自愈重规划不再打断（规格 §5.2）
    if not state.plan_steps or state.phase in {"done", "clarify", "plan_review"}:
        return state
    if not (multi_step or forced):
        return state
    resume = maybe_interrupt(
        state,
        {
            "kind": "plan_review",
            "plan_steps": [s.model_dump() for s in state.plan_steps],
            "summary": state.plan_steps[0].goal if state.plan_steps else "",
        },
        trigger="plan_review",
    )
    if resume.get("action") == "reject":
        if state.answered_by == "degraded_confirmed":
            # 降级可观测：用户拒绝降级推断计划（设计 §3.5 degrade_rejected）
            events.emit_event("degrade", {"outcome": "rejected", "query": state.user_query[:200]})
        # 规格 §5.1 ③：终止并如实报告，不产出
        return state.apply(
            phase="done",
            no_data_reason="用户拒绝了分析计划，未执行任何查询",
            report="用户拒绝了分析计划，本次未执行任何查询、未产出任何结论。",
        )
    if resume.get("action") == "edit":
        # 用户已审批本轮（L2 审批一次）；指令交 planner 消费（planner 清除之）
        return state.apply(
            plan_reviewed=True,
            plan_edit_instruction=resume.get("instruction"),
            phase="plan",
        )
    # approve / edit 均置审批标记（edit 的重规划执行自动，不再审批）
    return state.apply(plan_reviewed=True)


def _risk_gate(state: AgentState) -> AgentState:
    """高危确认门（L3，规格 §5.2）：即将执行沙箱代码（analyze 步骤含 LLM 产码）
    时挂起等待用户批准；L4 经 maybe_interrupt 直通（发 auto_resolved 通知事件）；
    L1/L2 不打断（保持现状，step_confirm 逐节点确认未实现，文档如实标注）。

    高危操作定义：编排链路中唯一的"执行模型生成代码"环节。确定性模板产码
    （code 为空）不属于高危，不打断。gate 无 LLM 调用、无事件发射——
    resume 会从头重执行本节点。
    """
    if state.autonomy_level not in {"L3", "L4"} or state.high_risk_approved:
        return state
    if not state.plan_steps or state.phase in {"done", "clarify", "high_risk"}:
        return state
    risky = [
        s for s in state.plan_steps if s.kind == "analyze" and s.status == "pending" and s.code
    ]
    if not risky:
        return state
    resume = maybe_interrupt(
        state,
        {
            "kind": "high_risk",
            "question": "分析步骤将执行由模型生成的代码（沙箱隔离运行），是否确认执行？",
            "steps": [{"id": s.id, "goal": s.goal} for s in risky],
        },
        trigger="high_risk",
    )
    if resume.get("action") == "reject":
        # 诚实终止：不产出分析结论，原因可见（与 plan_review reject 同语义）
        return state.apply(
            phase="done",
            no_data_reason="用户拒绝了沙箱代码执行，分析未完成",
            report="用户拒绝了沙箱代码执行，本次未执行模型生成代码、未产出分析结论。",
        )
    return state.apply(high_risk_approved=True)


def route_from_risk_gate(state: AgentState) -> Any:
    """risk_gate 条件边路由：reject 置 phase=done 直接收敛（严禁落入 analyze
    执行被拒代码）；其余（approve / L4 直通 / 非 L3 透传）进入沙箱分析。"""
    if state.phase == "done":
        return "plan_gate_end"
    return "analyze"


def route_from_plan_gate(state: AgentState) -> Any:
    """plan_gate 条件边路由：approve 交原 route_from_plan；edit 回 plan；
    reject 收敛；plan 携带 fanout_tasks 时转 fanout_orchestrator 汇聚节点。

    十八期（终审 Important #3）：clarify resume 后 phase=plan 且 plan_steps
    空、无 blocked_reason => 直接回 plan 节点重规划。严禁借道 critique——
    重规划场景下 datasets 已有旧数据，critic 会判三检通过直走 synthesize，
    静默吞掉用户对澄清的答复并拿旧数据出报告。
    """
    if state.phase == "plan" and state.plan_edit_instruction:
        return "plan"
    if (
        state.phase == "plan"
        and not state.plan_steps
        and not state.blocked_reason
        and not state.no_data_reason
    ):
        return "plan"
    if state.phase == "done":
        return "plan_gate_end"
    for step in state.plan_steps:
        if step.fanout_tasks:
            return "fanout_orchestrator"
    return route_from_plan(state)


# 主图 Send 并行的进程级并发闸：每 worker 各建信号量等于无闸——必须共享。
# 首次使用时按 settings 实例化（进程生命周期内固定；测试经 run_fanout 直驱路径
# 使用每次新建的闸以响应配置 monkeypatch）
_worker_gate: threading.BoundedSemaphore | None = None
_worker_gate_lock = threading.Lock()


def _get_worker_gate() -> threading.BoundedSemaphore:
    global _worker_gate
    with _worker_gate_lock:
        if _worker_gate is None:
            _worker_gate = threading.BoundedSemaphore(settings.SUBAGENT_MAX_PARALLEL)
        return _worker_gate


def _fanout_orchestrator(state: SubagentState, config: RunnableConfig) -> SubagentState:
    """fan-out 汇聚节点：收集 plan 携带的任务卡，直驱 run_fanout（并发闸在线程内），
    报告经 append reducer 通道并入状态后转 critique（充分性检查在 run_fanout 内完成）。

    不用 Send 多分支：多 worker 汇合后临界节点的执行次数语义不可控，
    单汇聚节点 + 线程并发语义等价且确定性可测（台账 Task 15 Ruling）。
    """
    observer = ((config or {}).get("configurable") or {}).get("observer")
    tasks = [
        SubagentTask.model_validate(t)
        for step in state.plan_steps
        for t in (step.fanout_tasks or [])
    ]
    if not tasks:
        return state.apply(phase="critique")
    reports = run_fanout(
        tasks, thread_id=str(config["configurable"]["thread_id"]), observer=observer
    )
    return state.apply(subagent_reports=state.subagent_reports + reports, phase="critique")


def _execute_wave(tasks: list, *, thread_id: str, observer) -> list:
    """并发执行一批子任务（Send 语义的直驱等价实现，供 run_fanout 复用）。"""
    parallel_gate = threading.BoundedSemaphore(settings.SUBAGENT_MAX_PARALLEL)
    reports: list = []
    lock = threading.Lock()

    def _one(task: SubagentTask) -> None:
        with parallel_gate:
            report = run_subagent(
                task,
                thread_id=f"{thread_id}:sub:{task.task_id}",
                observer=_task_id_tagged(observer, task.task_id),
            )
        with lock:
            reports.append(report)

    threads = [
        threading.Thread(target=_one, args=(t,), daemon=True, name=f"fanout-{t.task_id}")
        for t in tasks
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(reports, key=lambda r: r.task_id)


def run_fanout(tasks: list, *, thread_id: str, observer=None) -> list:
    """并行 fan-out（规格 §6.3/§6.4）：并发上限 + 失败重派 ≤1 轮 + 缺口如实披露。

    失败/超时报告按 SUBAGENT_REDISPATCH_MAX 重派（task_id 加 :rd<n> 后缀）；
    重派仍失败则在 findings 如实披露缺口（诚实守卫延续），返回按 task_id 排序。
    """
    observer = observer if observer is not None else events.get_observer()
    by_id = {t.task_id: t for t in tasks}
    reports = _execute_wave(tasks, thread_id=thread_id, observer=observer)
    for round_no in range(settings.SUBAGENT_REDISPATCH_MAX):
        failed = [r for r in reports if r.status != "done"]
        if not failed:
            break
        redispatch = []
        for r in failed:
            origin = by_id.get(r.task_id.split(":rd")[0])
            if origin is not None:
                redispatch.append(
                    origin.model_copy(update={"task_id": f"{origin.task_id}:rd{round_no + 1}"})
                )
        if not redispatch:
            break
        redone = _execute_wave(redispatch, thread_id=thread_id, observer=observer)
        # 重派结果按源任务归并：源 id（剥 :rd 后缀）相同的失败报告被最新尝试替换
        latest = {}
        for r in reports:
            latest.setdefault(r.task_id.split(":rd")[0], r)
        for r in redone:
            latest[r.task_id.split(":rd")[0]] = r  # 最新尝试覆盖
        reports = list(latest.values())
    final = sorted(reports, key=lambda r: r.task_id)
    disclosed = []
    for r in final:
        source_id = r.task_id.split(":rd")[0]
        if r.status != "done":
            disclosed.append(
                r.model_copy(
                    update={
                        # 父图按源任务 id 消费；重派轮次信息留在 findings 里
                        "task_id": source_id,
                        "findings": [
                            *r.findings,
                            f"缺口披露：子任务 {source_id} 重派后仍 {r.status}，未取得该维度结论",
                        ],
                    }
                )
            )
        else:
            disclosed.append(r.model_copy(update={"task_id": source_id}))
    return disclosed


def run_fanout_serial(tasks: list, *, thread_id: str, observer=None) -> list:
    """native 引擎降级路径：串行 for 循环执行（新增能力皆可降级）。"""
    observer = observer if observer is not None else events.get_observer()
    reports = []
    for t in tasks:
        reports.append(
            run_subagent(
                t,
                thread_id=f"{thread_id}:sub:{t.task_id}",
                observer=_task_id_tagged(observer, t.task_id),
            )
        )
    return sorted(reports, key=lambda r: r.task_id)


def _compile(checkpointer: Any) -> Any:
    """装配六节点图 + clarify_gate：边结构与 native build_graph 同构。"""
    g = LGStateGraph(SubagentState)
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
    g.add_node("plan_gate", _plan_gate)
    g.add_node("plan_gate_end", lambda s: s)
    g.add_node("risk_gate", _risk_gate)
    g.add_node("fanout_orchestrator", _fanout_orchestrator)
    g.add_edge("fanout_orchestrator", "critique")
    g.add_edge("plan", "plan_gate")
    g.add_conditional_edges(
        "plan_gate",
        route_from_plan_gate,
        {
            "query": "query",
            "analyze": "risk_gate",
            "critique": "critique",
            "plan": "plan",
            "plan_gate_end": "plan_gate_end",
            "fanout_orchestrator": "fanout_orchestrator",
        },
    )
    g.add_edge("plan_gate_end", END)
    g.add_edge("query", "risk_gate")
    g.add_conditional_edges(
        "risk_gate",
        route_from_risk_gate,
        {"analyze": "analyze", "plan_gate_end": "plan_gate_end"},
    )
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
    state = SubagentState.model_validate(
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
