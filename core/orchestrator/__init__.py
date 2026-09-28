"""orchestrator 包：LangGraph 单引擎图式编排（十七期 M4 收敛）。

节点与状态：
- state.AgentState：编排状态契约（session/turn/trace、plan、tool 轨迹、
  scratchpad、artifacts、datasets、error_context、blocked_reason、
  clarification_options、autonomy_level 等），Pydantic extra=forbid 强契约；
- langgraph_engine：LangGraph 装配（六节点 + clarify/plan_review interrupt 审批门
  + Subagent fan-out 并发）；
- nodes：Clarify（HITL 中断）/ Planner（任务分解）/ DSLQuery（受控取数）/
  CodeExec（沙箱分析）/ Critic（反思重规划）/ Synthesize（综合报告 + Grounding 溯源）；
- intent：编排层确定性意图四分类（词源 FieldMeta.aliases，兜底准入制）；
- autonomy：L1-L4 自主性分级 interrupt 分流；subagent：受限任务卡子图运行时；
- grounding：LLM 报告数值溯源校验（定向重试 1 次，仍超阈降级确定性渲染）；
- events：九类 SSE 事件总线（contextvar 观察者）。

确定性兜底：无 LLM 配置时走确定性启发式规划（离线可运行、可单测），
与项目"确定性优先"哲学一致。
"""
