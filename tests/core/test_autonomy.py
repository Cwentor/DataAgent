"""自主性分级 L1-L4 行为矩阵（M2 Task 10；规格 §5.2）。

单元测试覆盖：档位契约 / 默认值 / 越界拒绝 / L4 通知降级。
plan_review 三分支（approve / edit / reject）在 tests/test_langgraph_parity.py
经图运行时覆盖（interrupt 原语必须在图执行上下文中才可挂起/恢复）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.orchestrator.autonomy import AUTONOMY_LEVELS, AutonomyPolicy, maybe_interrupt
from core.orchestrator.state import AgentState


def _state(level: str) -> AgentState:
    return AgentState(user_query="q", session_id="s", autonomy_level=level)  # type: ignore[arg-type]


def test_levels_are_the_documented_four():
    assert AUTONOMY_LEVELS == ("L1", "L2", "L3", "L4")


def test_l2_default_in_state_contract():
    """默认档随 settings（生产 L2 计划确认；测试环境经 conftest 置 L4）。"""
    from config import settings

    assert AgentState(user_query="q").autonomy_level == settings.AGENT_DEFAULT_AUTONOMY


def test_state_rejects_unknown_level():
    with pytest.raises(ValidationError):
        AgentState(user_query="q", autonomy_level="L5")  # type: ignore[arg-type]


def test_state_rejects_extra_fields():
    with pytest.raises(ValidationError):
        AgentState(user_query="q", nonexistent_field="x")


def test_policy_rejects_unknown_level():
    with pytest.raises(ValidationError):
        AutonomyPolicy(level="L5")  # type: ignore[arg-type]


def test_l4_downgrades_review_to_notification():
    """L4 全自动：审批门降级为 hitl_request 富化载荷通知（auto_resolved=True），
    返回默认批准动作直通，零中断。"""
    from core.orchestrator import events as ev

    seen: list[dict] = []
    token = ev.set_observer(seen.append)
    try:
        state = _state("L4")
        result = maybe_interrupt(
            state,
            {"kind": "plan_review", "plan_steps": [], "summary": "s"},
            trigger="plan_review",
        )
    finally:
        ev.reset_observer(token)
    assert result == {"action": "approve", "instruction": None}
    assert len(seen) == 1
    event = seen[0]
    assert event["event"] == "hitl_request"
    assert event["payload"]["kind"] == "plan_review"
    assert event["payload"]["auto_resolved"] is True


def test_l1_l3_real_interrupt_raises_outside_graph():
    """L1-L3 走真中断：interrupt() 在图上下文外抛错（图内挂起由 parity 测试覆盖）。"""
    with pytest.raises(BaseException):  # noqa: B017 - langgraph 内部异常类型不稳定
        maybe_interrupt(
            _state("L2"),
            {"kind": "plan_review", "plan_steps": [], "summary": "s"},
            trigger="plan_review",
        )
