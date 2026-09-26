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
    base: dict = {"user_query": "上月销售额是多少", "session_id": "s1", "turn_id": "t1"}
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


def test_clarify_interrupt_and_resume_roundtrip():
    """clarify 中断 -> Command(resume) 恢复：同一原语，gate 节点无副作用。

    触发语料用确定性规则可命中澄清的问题（<12 字符且含指标词，见 clarify_node）。
    """
    from core.orchestrator.langgraph_engine import invoke_langgraph, resume_langgraph

    events_seen: list[dict] = []
    state = _minimal_state(user_query="什么是销售额？")
    state2, pending = invoke_langgraph(state, thread_id="u1:s1", observer=events_seen.append)
    assert pending is not None and pending["kind"] == "clarify"
    assert state2.phase == "clarify"

    resumed, pending2 = resume_langgraph(
        state2,
        {"kind": "clarify", "resume_value": "按 2024-06 口径"},
        thread_id="u1:s1",
        observer=events_seen.append,
    )
    assert pending2 is None
    assert resumed.phase in {"plan", "query", "analyze", "critique", "synthesize", "done"}


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


def test_native_and_langgraph_event_classes_equal(monkeypatch):
    """同一确定性兜底输入：双引擎事件类序列一致、终态 phase 一致（M0 验收门）。"""
    from config import settings
    from core.orchestrator.agent import run_agent

    def collect(engine: str):
        monkeypatch.setattr(settings, "ORCHESTRATOR_ENGINE", engine)
        seq: list[str] = []
        out = run_agent(
            "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
            session_id="s-parity",
            on_event=lambda e: seq.append(e["event"]),
        )
        return seq, getattr(out, "phase", None)

    seq_native, phase_native = collect("native")
    seq_lg, phase_lg = collect("langgraph")
    assert seq_native == seq_lg, f"event classes diverged:\n{seq_native}\n{seq_lg}"
    assert phase_native == phase_lg


def test_recursion_limit_anchors_termination(monkeypatch):
    """护栏校准（Review Focus #3）：LangGraph 超级步护栏触发时与 native
    iteration>24 护栏同语义收敛——phase=done 且报告留痕"迭代步数超限"。

    limit=3 确定性触发：clarify -> clarify_gate -> plan 之后必然超限，
    不依赖兜底规划是否产生重规划（后者会使命题随启发式漂移）。
    """
    from config import settings
    from core.orchestrator.agent import run_agent

    monkeypatch.setattr(settings, "ORCHESTRATOR_ENGINE", "langgraph")
    monkeypatch.setattr(settings, "ORCHESTRATOR_RECURSION_LIMIT", 3)
    out = run_agent(
        "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
        session_id="s-limit",
        on_event=lambda e: None,
    )
    assert out.phase == "done"
    assert "迭代步数超限" in out.report
