"""orchestrator 包：有状态图式编排引擎（StateGraph 范式）。

节点与状态：
- state.AgentState：编排状态契约（session/turn/trace、plan、tool 轨迹、
  scratchpad、artifacts、error_context），Pydantic extra=forbid 强契约；
- graph.StateGraph：轻量图引擎（节点注册 / 条件边 / 中断恢复 / 迭代上限）；
- nodes：Clarify（HITL 中断）/ Planner（任务分解）/ DSLQuery（受控取数）/
  CodeExec（沙箱分析）/ Critic（反思重规划）/ Synthesize（综合报告）。

确定性兜底：无 LLM 配置时走确定性启发式规划（离线可运行、可单测），
与项目"确定性优先"哲学一致。
"""
