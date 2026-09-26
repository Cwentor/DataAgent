"""双引擎等价性测试（M0-M4 全程作为回归锚点）。

M0 阶段职责：
- 验证 AgentState（Pydantic, extra="forbid"）可直接作为 LangGraph 的 state schema 编译；
- 固化 extra="forbid" 契约不被迁移放松（设计文档 §3.2 迁移纪律）。

后续任务（Task 4/5/8/10/13）在此文件追加：interrupt/resume 往返、双引擎事件
逐类等价、recursion_limit 校准、checkpointer 跨重启恢复、plan_review 三分支。
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph as LGStateGraph

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
