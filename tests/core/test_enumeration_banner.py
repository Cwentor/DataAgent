"""降级水印文案分流测试（P2）。"""

from core.orchestrator.nodes import _degradation_banner
from core.orchestrator.state import AgentState


def test_banner_enumeration_is_neutral():
    """枚举预路由直答：中性文案，非故障告警。"""
    state = AgentState(
        session_id="s1", turn_id="t1", user_query="列举全部省份", answered_by="enumeration"
    )
    banner = _degradation_banner(state)
    assert "确定性路径直答" in banner
    assert "不可用" not in banner


def test_banner_llm_failure_is_alert():
    """真 LLM 故障降级：告警文案带原因，与枚举中性路径视觉可区分。"""
    state = AgentState(
        session_id="s1",
        turn_id="t1",
        user_query="GMV",
        answered_by="heuristic",
        planner_llm_error="timeout",
    )
    banner = _degradation_banner(state)
    assert "不可用" in banner
    assert "原因：timeout" in banner
