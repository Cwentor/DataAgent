# 评审报告｜架构审计：pi-agent-harness 对齐差距分析（2026-09）

## 背景与目标

对齐 [baryonlabs/pi-agent-harness](https://github.com/baryonlabs/pi-agent-harness) 的多角色 Agent
架构设计（5 核心角色 + Skill + Chain/Optimizer 编排），在**不改变 DataAgent 现有基础**
（DSL 契约铁律 / 确定性编译器 / 防御栈 / StateGraph 编排）的前提下，补齐 Agent 必要能力。

Harness 基线领域描述（输入）：

> An enterprise-grade autonomous DataAgent ecosystem: specializes in schema inspection with
> dynamic profiling, dialect-aware and read-only SQL generation, sandboxed EXPLAIN
> execution-plan safety validation, automated tabular data-quality assertions, and chart
> rendering, orchestrated with evaluator-optimizer loops and multi-turn error self-correction.

Harness 关键机制参考（orchestrator-template）：chain 委托、producer-reviewer 循环
（evaluator-optimizer 等价物）、验证门 + 有界修复（Bounded Repair，最多 2~3 次）、
run manifest 审计轨迹、HITL 检查点、复杂度分诊。

---

## 一、角色映射审计（Harness 五角色 ↔ DataAgent 现状）

| Harness 角色 | DataAgent 等价物 | 现状结论 |
| --- | --- | --- |
| SchemaAgent（元数据探查 + 动态 profiling） | `schema_digest()` 静态注入 Planner（core/orchestrator/nodes.py:55）+ 语义目录 SSOT | 基本对齐；动态 profiling（字段样本值/基数）缺失，列后续项 |
| SqlEngineer（方言感知只读 SQL 生成） | Planner 产 DSL → `compiler.compile_sql` 确定性编译 | **强于基线**：LLM 仅产 DSL 契约 JSON，编译器保证方言与只读，杜绝 SQL 幻觉 |
| GuardrailAgent（执行前安全 + EXPLAIN 审计） | 裸 SQL 网关（core/retrieval/guardrails.py）+ `assert_read_only_sql` 四层防线（exec/guards.py:237）+ EXPLAIN ANALYZE 扫描熔断（exec/guards.py:418） | 大部分对齐；缺结构化裁决输出（APPROVED/REJECTED + findings）与笛卡尔积检测，见行动项 1 |
| DataQAAgent（结果断言） | **缺失**。critic_node 只检"产物存在性"（nodes.py:722），不检数据质量 | 最大缺口，见行动项 2 |
| VizAgent（图表渲染） | `present/viz.recommend_viz` 确定性规则 + 沙箱 `save_echarts_spec` | 对齐（ECharts option 级渲染契约已前端落地） |

编排模式映射：StateGraph 条件边 = chain 委托；`critic_node` 重规划 ≤ `MAX_RETRIES=3`
= evaluator-optimizer 有界修复；`clarify` HITL 中断恢复 = 检查点；`tool_calls` +
`scratchpad` + 事件总线 = run manifest 审计轨迹。**编排骨架无需重构**。

## 二、职责过载审计（上下文污染与幻觉风险）

1. **planner_node 单点承担规划 + DSL 起草 + 口径纠偏**：DSL 笔误规范化
   （`_normalize_dsl_draft`，nodes.py:314）与区域词展开散落在编排层，Planner 提示词
   （prompts.py:16）已内嵌同规则——提示词与代码双写同一规则集，存在漂移风险。
   评估：当前规模可接受，规则单点化列后续项。
2. **synthesize_node 承担报告渲染 + 降级简报 + 图表去重 + 口径说明**（nodes.py:1037，
   约 500 行）：职责过载最重的节点，但输出契约严格（四段式分析师叙事），暂不动。
3. **无 LLM 安全裁决角色**：GuardrailAgent/DataQAAgent 不引入 LLM，全部确定性实现——
   与 harness 的 LLM reviewer 不同，但更符合本项目"LLM 仅产 DSL"铁律，安全审计由
   确定性代码承担是**架构优势而非缺口**。

## 三、工具缺失（Skill Gaps，按四层级）

| 层级 | 缺失能力 | 入参规格 | 处置 |
| --- | --- | --- | --- |
| 元数据探查 | 字段动态 profiling（样本值/基数/分布） | `profile_column(field) -> {samples, distinct_estimate}` | 后续项（本轮不做，成本/收益低） |
| 高危拦截 | 笛卡尔积（无条件 JOIN / 逗号 JOIN）静态检测 | `audit_sql(sql) -> Finding[]` | **本轮落地**（行动项 1） |
| 执行计划分析（EXPLAIN） | 审计结果结构化：扫描行数已熔断但未随产物输出审计轨迹 | `GuardrailFinding(check, severity, message, suggestion)` | **本轮落地**（行动项 1） |
| 结果断言 | 全部四项：空结果、NULL 率、负值异常、维度组合唯一性 | `run_quality_assertions(columns, rows, dsl) -> QaReport` | **本轮落地**（行动项 2） |
| 无界输出 | LIMIT 缺失检测 | 同 `audit_sql` | **本轮落地**（compiler 现已保证除纯标量外必有 LIMIT，审计作为不变式防御） |

## 四、容错机制缺口（重试链路断裂点）

1. **断裂点（实锤）：重规划时 LLM 看不到失败原因。** `dsl_query_node` 失败后
   `error_context.record()` 并回 `plan`（nodes.py:464-493），但 `planner_prompt`
   （prompts.py:143）只接收 `user_query + schema_digest`，`error_context.errors`
   从未进入重规划上下文——LLM 重规划=盲重试，自愈成功率被无谓拉低。
   → **行动项 3**：`planner_prompt` 增补最近错误摘要小节。
2. critic_node 的 LLM Reflector `trace_digest`（nodes.py:753）只取 tool_calls 摘要，
   不含 `error_context.errors`——反思层同样看不见失败历史。随行动项 3 一并补。
3. harness 的"验证门先行、REJECTED 不执行"语义：现状是"执行时熔断 + 异常字符串
   喂回"，等价但审计不可见。行动项 1 的结构化裁决补齐可见性。
4. QA error 级发现不自动改路由（不自动否决空结果——空集可能是合法答案，D3 修复
   已在报告层诚实陈述）：QA 发现只增强 critic 信息面与报告，避免误杀与额度空烧。

## 五、最小改动落地路径（3 项行动）

1. **GuardrailAgent 结构化执行前审计**（`exec/audit.py`）：静态审计编译产物 SQL——
   只读形态 / 笛卡尔积 / 无界输出三类检查，输出结构化 `GuardrailFinding` 列表；
   REJECTED 级在 `execute_sql` 内抛 `GuardrailRejected`（继承 `SqlExecutionError`，
   与现有自愈循环零改动兼容）；WARNING 级随 `ExecutionResult.findings` 输出，
   编排层经事件 output 与 ParquetRef 携带至报告层。全链路（编排器 + web service）自动生效。
2. **DataQAAgent 结果断言层**（`core/retrieval/quality.py`）：四项确定性断言
   （empty_result / null_rate / negative_metric / dimension_uniqueness），
   `execute_dsl_query` 执行后调用，findings 挂 `ParquetRef.audit` 契约字段（可选、
   向后兼容）；synthesize 确定性报告新增"数据质检"小节消费之。
3. **重规划错误上下文接线**：`planner_prompt` 增加 `error_context` 参数注入最近
   失败摘要；critic LLM trace 一并携带；离线/启发式路径行为不变。

## 六、验证与回归

- 新增单测：`tests/test_exec_audit.py`（笛卡尔积/无界输出/结构化裁决）、
  `tests/test_quality_assertions.py`（四断言边界）、编排器接线用例并入
  `tests/test_orchestrator.py`；
- 质量门：`black --check .` / `ruff check .` / `python -m pytest -q` 全绿；
- 回归锚点：oracle 评测 25/25（`python -m eval.eval_runner`）、确定性种子 42 不变。

## 七、后续项处置记录（2026-09-11 同分支续作）

1. **字段动态 profiling——已落地**（commit d989709）：`core/retrieval/profiling.py`
   对低基数（≤30）字符串字段 `SELECT DISTINCT` 探查实际取值，进程级缓存 +
   逐字段失败降级；`schema_digest` 注入"可取值"清单，Planner 不再臆造过滤
   字面值。仅 LLM 规划路径探查，离线环境零副作用。
2. **web service 链路接入 DataQA——已落地**（commit a7f6d29）：
   `run_guarded_query` 结果构建前跑同一 `run_quality_assertions`，
   `GuardedQueryResult.qa_findings` 随 query_metric / trend_analysis 工具
   data + meta 双透传；两条取数链路（编排器/web）质检同源同格式。
3. **Planner 规则双写单点化——评估后维持现状（有意冗余）**：提示词内嵌
   DSL 纪律（PLANNER_SYSTEM）与 `_normalize_dsl_draft` 代码规范化是"预防 +
   治疗"的防线纵深，而非漂移缺陷：规范化层宽容接受常见笔误，契约层
   （网关 validate，extra=forbid）是唯一裁决者，错误喂回自愈——三层职责
   各自独立可单测。强行单点化（由代码生成提示词或反之）会引入生成链路
   的间接性，成本高于收益。结论：不改代码，处置记录在案。

