"""自主性分级（M2 Plan Mode，规格 §5.2）：单一图结构 + maybe_interrupt 帮助函数。

L1 每步确认 / L2 计划确认（默认）/ L3 高危确认 / L4 全自动（降级为通知事件）。
真中断用 langgraph interrupt()（在图执行上下文中挂起）；L4 返回默认批准值
直通——档位差异由各 gate 节点的触发条件承载，图结构不分叉。
"""

from __future__ import annotations

from typing import Literal

from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict

from core.orchestrator import events as ev
from core.orchestrator.state import AgentState

AUTONOMY_LEVELS = ("L1", "L2", "L3", "L4")


class AutonomyPolicy(BaseModel):
    """会话级自主性偏好（契约受控：extra="forbid"）。"""

    model_config = ConfigDict(extra="forbid")

    level: Literal["L1", "L2", "L3", "L4"] = "L2"


# 各触发点的直通默认动作（L4 / 后续直通档共用）：审批门语义下"不拦"即批准
_DEFAULT_ACTIONS: dict[str, dict] = {
    "plan_review": {"action": "approve", "instruction": None},
    "high_risk": {"action": "approve", "instruction": None},
    "step_confirm": {"action": "approve", "instruction": None},
    # 探索层审批门（M6）：L4 直通语义 = 单次允许（含敏感列时 gate 侧强制
    # 真中断，不经此默认动作——见 nodes._execute_exploration_step）
    "exploration": {"action": "allow_once"},
}


def maybe_interrupt(state: AgentState, payload: dict, *, trigger: str) -> dict:
    """按 state.autonomy_level 分流审批点。

    L1-L3：调 langgraph interrupt()——以异常形式挂起图，本函数不返回；
    恢复时本函数原样返回用户 resume 值（Command(resume=...) 注入）。
    L4：先发 hitl_request 通知事件（auto_resolved=True，审批门降级为通知），
    再返回默认批准动作直通。
    """
    if state.autonomy_level == "L4":
        ev.emit_event(ev.EVENT_HITL_REQUEST, {**payload, "auto_resolved": True})
        return _DEFAULT_ACTIONS[trigger]
    return interrupt(payload)  # type: ignore[no-any-return]
