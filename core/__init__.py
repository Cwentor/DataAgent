"""core 包：企业级 Data Agent 的四层核心（检索门面 / 编排器 / 沙箱 / 技能包）。

分层职责与安全边界（与 docs/plans/20260909-enterprise-data-agent-plan.md 一致）：
- ``core.retrieval``：既有问数防御链路（semantic -> compiler -> exec）的门面，
  把 DSL 查询包装为 typed Tool（execute_dsl_query -> ParquetRef），并守卫
  "LLM 永不产出裸 SQL"的确定性边界；
- ``core.orchestrator``：有状态图式编排（StateGraph 范式），六节点 + 条件边 +
  HITL 澄清中断 + 反思重规划；
- ``core.sandbox``：多租户沙箱代码解释器（AST 静态校验 + 可插拔执行后端 +
  Parquet 数据交换协议）；
- ``core.skills``：预置归因与分析技能（熵下钻 / 指标分解树 / DTW /
  Holt-Winters / Shapley）。

依赖方向铁律：orchestrator -> retrieval / sandbox / skills / agent(现有)；
retrieval 不依赖 orchestrator；sandbox 不感知业务语义；skills 只依赖数值栈。
"""
