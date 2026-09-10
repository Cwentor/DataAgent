"""轻量 StateGraph 引擎：节点注册 / 条件边 / 中断恢复 / 迭代护栏。

范式对齐 LangGraph StateGraph（零框架依赖实现）：
- 节点：``Callable[[AgentState], AgentState]``——接收全局状态，返回增量
  应用后的新状态；
- 边：无条件边（add_edge）+ 条件边（add_conditional_edges，路由函数读
  state.phase 决定下一节点）；
- 中断（HITL）：路由到 ``END`` 且 phase=clarify 时图暂停；``resume`` 以
  补充 human_reply 的状态重入，从 clarify 之后继续；
- 护栏：单次 run 最大迭代步数（iteration 计数），超限强制终止并标记。

图执行为确定性状态机循环：每步取当前节点执行 -> 应用增量 -> 路由，
直到 END / 中断 / 护栏触发。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from core.orchestrator import events
from core.orchestrator.state import AgentState

NodeFn = Callable[[AgentState], AgentState]
RouterFn = Callable[[AgentState], str]

# 保留路由名（不可作为节点名）
RESERVED = frozenset({"END"})


class GraphError(Exception):
    """图定义/执行错误。"""


@dataclass
class _Edge:
    from_node: str
    to_node: str | None = None  # None 表示条件边
    router: RouterFn | None = None
    route_map: dict[str, str] = field(default_factory=dict)


class StateGraph:
    """有状态图执行器（单图单事务；线程安全由调用方保证）。"""

    def __init__(self, *, max_iterations: int = 24) -> None:
        self._nodes: dict[str, NodeFn] = {}
        self._edges: list[_Edge] = []
        self._entry: str | None = None
        self._max_iterations = max_iterations
        self._interrupted_at: str | None = None  # HITL 暂停节点（resume 起点）

    # ---- 定义期 API -------------------------------------------------------
    def add_node(self, name: str, fn: NodeFn) -> StateGraph:
        if name in RESERVED:
            raise GraphError(f"节点名不可为保留名: {name}")
        if name in self._nodes:
            raise GraphError(f"节点重复定义: {name}")
        self._nodes[name] = fn
        return self

    def set_entry(self, name: str) -> StateGraph:
        if name not in self._nodes:
            raise GraphError(f"入口节点未注册: {name}")
        self._entry = name
        return self

    def add_edge(self, from_node: str, to_node: str) -> StateGraph:
        if to_node not in self._nodes and to_node != "END":
            raise GraphError(f"边指向未注册节点: {to_node}")
        self._edges.append(_Edge(from_node=from_node, to_node=to_node))
        return self

    def add_conditional_edges(
        self, from_node: str, router: RouterFn, route_map: dict[str, str]
    ) -> StateGraph:
        for key, target in route_map.items():
            if target not in self._nodes and target != "END":
                raise GraphError(f"条件边 {key}->{target} 指向未注册节点")
        self._edges.append(_Edge(from_node=from_node, router=router, route_map=route_map))
        return self

    # ---- 执行期 API -------------------------------------------------------
    def run(self, state: AgentState) -> AgentState:
        """执行图：从入口开始循环，直到 END / 中断（phase=clarify）/ 护栏。"""
        if self._entry is None:
            raise GraphError("图未设置入口节点")
        current: str | None = self._entry
        while current is not None and current != "END":
            state.iteration += 1
            if state.iteration > self._max_iterations:
                return state.apply(
                    phase="done",
                    report=(state.report or "") + "\n[编排器] 迭代步数超限，强制终止。",
                )
            node_fn = self._nodes[current]
            events.emit_step_start(current)
            state = node_fn(state)
            # HITL 中断语义：clarify 阶段挂起等待用户回复（记录暂停节点供 resume）
            if state.phase == "clarify":
                self._interrupted_at = current
                return state
            current = self._route(current, state)
        return state

    def _route(self, from_node: str, state: AgentState) -> str | None:
        """从 from_node 出发按边定义路由；多条边取第一条匹配。"""
        for edge in self._edges:
            if edge.from_node != from_node:
                continue
            if edge.router is not None:
                key = edge.router(state)
                target = edge.route_map.get(key)
                if target is None:
                    raise GraphError(f"路由函数返回未映射键: {key!r}")
                return None if target == "END" else target
            return edge.to_node
        raise GraphError(f"节点缺少出边: {from_node}")

    def resume(self, state: AgentState) -> AgentState:
        """HITL 恢复：携带 human_reply 重入图（从暂停节点出边继续）。

        恢复语义与 LangGraph 一致：中断节点本身不重复执行——它的产出
        （澄清问题）已在中断前写入状态；用户答复在此合并进查询后路由。
        """
        if state.phase != "clarify":
            raise GraphError(f"仅 clarify 状态可恢复: {state.phase}")
        if state.human_reply:
            state = state.apply(
                user_query=f"{state.user_query}（用户补充：{state.human_reply.strip()}）",
                human_reply=None,
                clarification=None,
                phase="plan",
            )
        start_node = self._interrupted_at or self._entry
        if start_node is None:
            raise GraphError("图未设置入口节点，无法恢复")
        next_node = self._route(start_node, state)
        if next_node is None:
            return state
        current = next_node
        while current is not None and current != "END":
            state.iteration += 1
            if state.iteration > self._max_iterations:
                return state.apply(phase="done")
            events.emit_step_start(current)
            state = self._nodes[current](state)
            if state.phase == "clarify":
                return state
            current = self._route(current, state)
        return state


__all__ = ["AgentState", "GraphError", "StateGraph"]
