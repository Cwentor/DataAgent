"""双引擎等价性测试（M0-M4 全程作为回归锚点）。

M0 阶段职责：
- 验证 AgentState（Pydantic, extra="forbid"）可直接作为 LangGraph 的 state schema 编译；
- 固化 extra="forbid" 契约不被迁移放松（设计文档 §3.2 迁移纪律）。

后续任务（Task 4/5/8/10/13）在此文件追加：interrupt/resume 往返、双引擎事件
逐类等价、recursion_limit 校准、checkpointer 跨重启恢复、plan_review 三分支。
"""

from __future__ import annotations

from langgraph.graph import END
from langgraph.graph import StateGraph as LGStateGraph

from core.orchestrator.state import AgentState


def _minimal_state(**overrides) -> AgentState:
    base: dict = {
        "user_query": "上月销售额是多少",
        "session_id": "s1",
        "turn_id": "t1",
        "autonomy_level": "L2",  # Plan Mode 用例显式 L2（conftest 默认 L4）
    }
    base.update(overrides)
    return AgentState(**base)


def test_agentstate_compiles_as_langgraph_schema():
    """AgentState 直接过 langgraph StateGraph schema 推断并跑通单节点图。"""
    g = LGStateGraph(AgentState)
    g.add_node("noop", lambda s: s)
    g.set_entry_point("noop")
    g.add_edge("noop", END)
    app = g.compile()
    out = app.invoke(_minimal_state(), {"configurable": {"thread_id": "t"}})
    assert out["user_query"] == "上月销售额是多少"
    assert out["phase"] == "plan"


def test_agentstate_extra_forbid_unchanged():
    """迁移红线：extra="forbid" 契约在 LangGraph 平移前后都不得放松。"""
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        _minimal_state(nonexistent_field="x")


# ---------------------------------------------------------------------------
# Task 4：六节点图 LangGraph 同构编译 + interrupt 泛化（clarify 门）
# ---------------------------------------------------------------------------


def test_clarify_interrupt_and_resume_roundtrip(monkeypatch):
    """clarify 中断 -> resume 恢复 -> 新计划 -> plan_review -> approve 全链。

    终审 M5：恢复 clarify→plan_review 链式覆盖（一期改写时丢失）。
    mock planner：首轮 clarification（挂起），恢复轮返回两步计划（触发
    plan_review 审批门），批准后收敛。
    """
    import core.orchestrator.langgraph_engine as lge_mod
    from core.orchestrator.langgraph_engine import invoke_langgraph, resume_langgraph
    from core.orchestrator.state import PlanStep

    two_step = [
        PlanStep(
            id="p1",
            goal="取上月销售额",
            kind="query",
            dsl={
                "metrics": [
                    {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                ],
                "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
                "time_filter": {
                    "range_type": "absolute",
                    "absolute": {"start": "2024-05-01", "end": "2024-05-15"},
                },
            },
        ),
        PlanStep(id="p2", goal="综合作答", kind="synthesize", depends_on=["p1"]),
    ]
    calls = iter(["clarify", "plan"])

    def _planner(state):
        if next(calls) == "clarify":
            return state.apply(phase="clarify", clarification="你关注哪个指标？")
        return state.apply(plan_steps=two_step, phase="query", answered_by="llm")

    monkeypatch.setattr(lge_mod, "planner_node", _planner)

    state = _minimal_state(user_query="什么是销售额？", autonomy_level="L2")
    state2, pending = invoke_langgraph(state, thread_id="u1:s1", observer=None)
    assert pending is not None and pending["kind"] == "clarify"
    assert state2.phase == "clarify"

    resumed, pending2 = resume_langgraph(
        state2,
        {"kind": "clarify", "resume_value": "按 2024-06 口径"},
        thread_id="u1:s1",
        observer=None,
    )
    # 恢复后产出两步计划 => 必然再遇 plan_review 审批门（M5 链式实锤）
    assert pending2 is not None and pending2["kind"] == "plan_review"
    approved, pending3 = resume_langgraph(
        resumed,
        {"kind": "plan_review", "resume_value": {"action": "approve", "instruction": None}},
        thread_id="u1:s1",
        observer=None,
    )
    assert pending3 is None
    assert approved.phase == "done"


def test_observer_reaches_nodes_inside_langgraph_threads():
    """M0 前置审计结论回归：观察者经 config.configurable 显式传递，
    节点在 LangGraph 执行线程内发射的 step_start 等事件必须可达，SSE 不许静默。"""
    from core.orchestrator.langgraph_engine import invoke_langgraph

    events_seen: list[dict] = []
    invoke_langgraph(
        _minimal_state(user_query="分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位"),
        thread_id="u1:s2",
        observer=events_seen.append,
    )
    assert events_seen, "observer lost inside langgraph executor threads"
    kinds = [e["event"] for e in events_seen]
    assert "step_start" in kinds
    # done/error 收尾事件由 run_agent 门面在引擎返回后补发（与 native 对齐），
    # 引擎层最后一个事件是业务事件（artifact_emit / reflection 等）
    assert "plan_created" in kinds


# ---------------------------------------------------------------------------
# Task 5：ORCHESTRATOR_ENGINE 双引擎开关 + 等价性验收
# ---------------------------------------------------------------------------


def test_recursion_limit_anchors_termination(monkeypatch):
    """护栏校准（Review Focus #3）：LangGraph 超级步护栏触发时与 native
    iteration>24 护栏同语义收敛——phase=done 且报告留痕"迭代步数超限"。

    limit=3 确定性触发：clarify -> clarify_gate -> plan 之后必然超限，
    不依赖兜底规划是否产生重规划（后者会使命题随启发式漂移）。
    """
    from config import settings
    from core.orchestrator.agent import run_agent

    monkeypatch.setattr(settings, "ORCHESTRATOR_RECURSION_LIMIT", 3)
    out = run_agent(
        "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
        session_id="s-limit",
        on_event=lambda e: None,
    )
    assert out.phase == "done"
    assert "迭代步数超限" in out.report


# ---------------------------------------------------------------------------
# Task 8：resume Command 化 + SqliteSaver 持久挂起（跨重启恢复）
# ---------------------------------------------------------------------------


def test_checkpointer_survives_process_restart(tmp_path, monkeypatch):
    """服务重启后 clarify 挂起仍在：同 thread_id + 同一 sqlite 文件可恢复（Review Focus #5）。

    十八期：clarify 触发改为 mock planner clarification（见 roundtrip 用例说明）。
    """
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    import core.orchestrator.langgraph_engine as lge_mod
    from core.orchestrator.langgraph_engine import invoke_langgraph, resume_langgraph

    calls = iter(["clarify", "done"])

    def _planner(state):
        if next(calls) == "clarify":
            return state.apply(phase="clarify", clarification="你关注哪个指标？")
        return state.apply(phase="done", report="恢复后规划完成（mock）")

    monkeypatch.setattr(lge_mod, "planner_node", _planner)

    db = tmp_path / "ckpt.sqlite"
    saver = SqliteSaver(sqlite3.connect(str(db), check_same_thread=False))
    state, pending = invoke_langgraph(
        _minimal_state(user_query="什么是销售额？"),
        thread_id="u9:restart",
        observer=None,
        checkpointer=saver,
    )
    assert pending and pending["kind"] == "clarify"

    # 模拟重启：全新 app 实例 + 同一 sqlite 文件 + 同 thread_id
    saver2 = SqliteSaver(sqlite3.connect(str(db), check_same_thread=False))
    resumed, pending2 = resume_langgraph(
        state,
        {"kind": "clarify", "resume_value": "按 2024-06 口径"},
        thread_id="u9:restart",
        observer=None,
        checkpointer=saver2,
    )
    # clarify 恢复后若产出多步计划，会再遇 plan_review 门（M2）：循环批准
    for _ in range(5):
        if pending2 is None or pending2.get("kind") != "plan_review":
            break
        resumed, pending2 = resume_langgraph(
            resumed,
            {
                "kind": "plan_review",
                "resume_value": {"action": "approve", "instruction": None},
            },
            thread_id="u9:restart",
            observer=None,
            checkpointer=saver2,
        )
    assert pending2 is None
    assert resumed.phase != "clarify"


# ---------------------------------------------------------------------------
# Task 10：plan_review 审批门三分支（规格 §5.1）
# ---------------------------------------------------------------------------


def _patch_two_step_plan(monkeypatch):
    """把 planner 固定为两步计划（query+analyze），plan_review 必触发。

    返回调用计数列表（len == planner 被执行次数）。
    """
    import core.orchestrator.langgraph_engine as lge_mod
    from core.orchestrator.state import PlanStep

    steps = [
        PlanStep(id="p1", goal="取上月销售额", kind="query"),
        PlanStep(id="p2", goal="环比归因", kind="analyze", depends_on=["p1"]),
    ]
    calls: list[int] = []

    def fake_planner(state):
        calls.append(1)
        # 对齐真实 planner 行为：消费清除修改指令；phase 置首步类型
        return state.apply(plan_steps=steps, plan_edit_instruction=None, phase=steps[0].kind)

    monkeypatch.setattr(lge_mod, "planner_node", fake_planner)
    return calls


def _stub_downstream(monkeypatch):
    """桩替换 query/analyze/critique/synthesize：审批门路由测试不触真实取数/沙箱。"""
    import core.orchestrator.langgraph_engine as lge_mod

    def fake_query(state):
        done = [
            s.model_copy(update={"status": "done"}) if s.kind == "query" else s
            for s in state.plan_steps
        ]
        return state.apply(plan_steps=done, phase="analyze")

    def fake_analyze(state):
        done = [
            s.model_copy(update={"status": "done"}) if s.kind == "analyze" else s
            for s in state.plan_steps
        ]
        return state.apply(plan_steps=done, phase="critique")

    def fake_critic(state):
        return state.apply(phase="synthesize")

    def fake_synth(state):
        return state.apply(phase="done", report="stub 报告")

    monkeypatch.setattr(lge_mod, "dsl_query_node", fake_query)
    monkeypatch.setattr(lge_mod, "code_exec_node", fake_analyze)
    monkeypatch.setattr(lge_mod, "critic_node", fake_critic)
    monkeypatch.setattr(lge_mod, "synthesize_node", fake_synth)


def _build_patched_app(monkeypatch):
    """打桩后编译全新图实例（节点在编译期捕获，必须先 patch 后编译）。"""
    _patch_two_step_plan(monkeypatch)
    _stub_downstream(monkeypatch)
    from core.orchestrator.langgraph_engine import build_langgraph_app

    return build_langgraph_app()


def test_plan_review_pending_maps_to_plan_review_phase(monkeypatch):
    """引擎路径：plan_review 挂起态必须以 phase=plan_review 的 AgentState 返回，
    与 clarify 挂起共用同一 pause 契约（facade / registry 零改动暂停）。"""
    import core.orchestrator.langgraph_engine as lge
    from core.orchestrator import agent as agent_mod

    app = _build_patched_app(monkeypatch)
    monkeypatch.setattr(lge, "_get_app", lambda: app)
    out = agent_mod._run_agent_langgraph_path(
        "分析 2024 年 5 月的 GMV 走势情况",
        session_id="s-pr",
        turn_id="t1",
        trace_id="tr1",
        human_reply=None,
        resume_state=None,
        autonomy_level="L2",
    )
    assert isinstance(out, AgentState)
    assert out.phase == "plan_review"
    assert len(out.plan_steps) == 2


def test_plan_review_approve_branch(monkeypatch):
    from core.orchestrator.langgraph_engine import invoke_langgraph, resume_langgraph

    calls = _patch_two_step_plan(monkeypatch)
    _stub_downstream(monkeypatch)
    from core.orchestrator.langgraph_engine import build_langgraph_app

    app = build_langgraph_app()
    state, pending = invoke_langgraph(
        _minimal_state(user_query="分析 2024 年 5 月的 GMV 走势情况"),
        thread_id="u1:prA",
        observer=None,
        app=app,
    )
    assert pending and pending["kind"] == "plan_review"

    resumed, pending2 = resume_langgraph(
        state,
        {"kind": "plan_review", "resume_value": {"action": "approve", "instruction": None}},
        thread_id="u1:prA",
        observer=None,
        app=app,
    )
    assert pending2 is None
    assert resumed.phase == "done"
    assert len(calls) == 1  # 批准不触发重规划


def test_plan_review_reject_branch(monkeypatch):
    from core.orchestrator.langgraph_engine import invoke_langgraph, resume_langgraph

    calls = _patch_two_step_plan(monkeypatch)
    from core.orchestrator.langgraph_engine import build_langgraph_app

    app = build_langgraph_app()
    state, _pending = invoke_langgraph(
        _minimal_state(user_query="分析 2024 年 5 月的 GMV 走势情况"),
        thread_id="u1:prB",
        observer=None,
        app=app,
    )
    resumed, pending2 = resume_langgraph(
        state,
        {"kind": "plan_review", "resume_value": {"action": "reject", "instruction": None}},
        thread_id="u1:prB",
        observer=None,
        app=app,
    )
    assert pending2 is None
    assert resumed.phase == "done"
    assert resumed.artifacts == []
    assert "拒绝" in (resumed.no_data_reason or "")
    assert len(calls) == 1  # 拒绝直接终止，不重规划


def test_plan_review_edit_branch_replans(monkeypatch):
    from core.orchestrator.langgraph_engine import invoke_langgraph, resume_langgraph

    calls = _patch_two_step_plan(monkeypatch)
    from core.orchestrator.langgraph_engine import build_langgraph_app

    app = build_langgraph_app()
    state, _pending = invoke_langgraph(
        _minimal_state(user_query="分析 2024 年 5 月的 GMV 走势情况"),
        thread_id="u1:prC",
        observer=None,
        app=app,
    )
    resumed, pending2 = resume_langgraph(
        state,
        {
            "kind": "plan_review",
            "resume_value": {"action": "edit", "instruction": "只看华东区域"},
        },
        thread_id="u1:prC",
        observer=None,
        app=app,
    )
    # L2 一次性审批（规格 §5.2）：edit 后重规划执行自动，不再二次审批
    assert pending2 is None
    assert len(calls) == 2  # planner 被再次调用（重规划发生）
    assert resumed.plan_edit_instruction is None  # 指令被 planner 消费后清除
    assert resumed.phase == "done"


def test_route_from_plan_gate_empty_plan_routing():
    """clarify resume 后的直连路由（终审 Important #3）。

    - resume 后 phase=plan 且 plan_steps 空、无 blocked => 直接回 plan 节点
      重规划，严禁借道 critique（重规划场景下 datasets 已有旧数据，critic
      会判三检通过直走 synthesize，静默吞掉用户对澄清的答复）；
    - blocked 拒答仍落 critique（critic 短路输出拒答报告）。
    """
    from core.orchestrator.langgraph_engine import route_from_plan_gate

    resumed = _minimal_state(
        user_query="x（用户补充：按品类）",
        plan_steps=[],
    ).apply(phase="plan")
    assert route_from_plan_gate(resumed) == "plan"

    blocked = _minimal_state(user_query="x", plan_steps=[]).apply(
        phase="plan", blocked_reason="无法从语义目录识别问题意图"
    )
    assert route_from_plan_gate(blocked) == "critique"
