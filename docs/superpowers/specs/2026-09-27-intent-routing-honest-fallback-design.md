# 设计文档｜意图路由收敛与诚实兜底（十八期）：高可信度优先（2026-09）

## 一、背景与决策记录

### 1.1 驱动力：三次事故链

近期连续暴露的同源问题，根源均为"兜底逻辑越权"与"意图理解缺位"：

1. **澄清门误拦**（已修复）：`clarify_node` 确定性字符规则（`len>=12 && has_metric`）
   把"有多少个省份"这类事实型短问句强制拦截进固定文案澄清；
2. **兜底答非所问**（已部分修复）：澄清放行后 Planner LLM 调用失败，静默落到
   启发式兜底规划——其对非诊断问题只有一种模板 `_scalar_dsl`（固定
   `sum(order_amount) as gmv`），产出"问省份数、答 115.69 万元 GMV"的离谱结果；
   且 LLM 失败对用户完全静默（仅服务端 WARNING）；
3. **渲染层缺陷**（已修复）：单行单列答案无条件万元化，省份数 9 会渲染为
   "查询答案：0.00 万元"。

### 1.2 核心原则转向（用户拍板）

从"追求高答复率"转向"追求高可信度（Accuracy & Faithfulness）"：

- **宁可中断/拒答，绝不猜口径兜底**：错误的答案比任务中断的后果更严重；
- **不能为了给结果而给结果**：没有就是没有，有就是有，结论必须依据真实
  数据库内容，禁止虚构任何数据和报告内容；
- **查析两阶段分离**：Stage 1 确定性查数（LLM/规则只产 DSL，由真实数据库
  执行）；Stage 2 分析只用 Stage 1 实际查出的结构化数据，报告数值必须有
  物理出处（Grounding）。

### 1.3 头脑风暴关键决策

| 决策点 | 结论 |
| --- | --- |
| 兜底作答边界 | 准入制：仅意图可确定的问题（诊断/基数/硬锚标量）可答，其余诚实拒答 |
| 实施范围 | **两期推进**：一期"止血 + 诚实"（本期），二期"字典底座 + 校验闭环" |
| 置信度机制 | 用离散分级（hard/llm/none）替代 LLM 自报连续置信度（校准性差不可靠） |
| Grounding 校验 | 一期**只标注不拦截**（防死循环/超时），拦截重试闭环放二期 |
| 澄清归属 | 离线退役字符规则；澄清统一由 LLM 在场时的 Planner 契约负责 |

## 二、现状差距分析

| 目标能力 | 现状资产 | 差距 |
| --- | --- | --- |
| 查析两阶段分离 | 架构已满足（DSL 确定性执行 → Parquet 物化 → 综合只吃真实数据） | 缺 Grounding 数值溯源校验 |
| 语义层分类（维度/指标/基数） | `semantic/catalog.py` 字段白名单 + label + 枚举 profiling；基数/指标/诊断三类兜底雏形 | 缺显式意图分类器；缺指标别名词表（硬匹配） |
| 错位拦截 | 字段白名单、审计门、时间域守卫 | 缺"意图-DSL 一致性"校验 |
| 诚实拒答 | `_no_data_report`（无数据诚实报告，哲学一致） | 缺"无法理解"诚实报告与能力清单引导 |
| 反幻觉 Prompt | `SYNTHESIZER_SYSTEM` 四段纪律 + 反契约防御 | 本期加强即可 |
| Golden 评测 | `eval/` 骨架（oracle/agent 双模式） | 缺"答非所问率"专项用例 |
| 降级可见化 | scratchpad 有记录但用户不可见 | 缺报告级标注 |

## 三、一期设计（六节）

### 3.1 意图分类器（显式化，硬匹配优先）

新增独立模块 `core/orchestrator/intent.py`，把散落的隐式分类收编为显式契约：

```python
class IntentType(str, Enum):
    DIAGNOSTIC = "diagnostic"        # 为什么下滑/归因
    CARDINALITY = "cardinality"      # 有多少个省份
    METRIC_SCALAR = "metric_scalar"  # 5月退款金额是多少
    UNKNOWN = "unknown"              # 无法确定

@dataclass(frozen=True)
class IntentProfile:
    intent: IntentType
    anchor_fields: tuple[str, ...]   # 硬命中的语义字段
    confidence: str                  # "hard" | "llm" | "none"
```

- **L1 硬匹配优先**：新增指标别名词表 `_METRIC_TERMS`（gmv/成交金额→order_amount、
  退款金额→refund_amount、订单量→order_id 等），收编现有 `_DIMENSION_TERMS`
  维度词表。词表命中 => `confidence="hard"`；
- **判定优先级**：诊断触发词 + 指标锚 => DIAGNOSTIC；量词模式 + 单一维度锚 =>
  CARDINALITY；指标硬锚 + 非诊断 => METRIC_SCALAR；其余 => UNKNOWN；
- **L2**：LLM 在场时由 Planner 在规划中隐式完成（**不加独立 LLM 调用**），
  计划契约增加可选 `intent` 字段（`{type, anchors}`）回传，经
  `_plan_from_llm` 契约化后存入 state 供 L3 守卫使用；LLM 未回传 intent 时
  视为 `confidence="llm"`（不硬拦截，靠现有契约校验与反思器护栏）；
- **L3 守卫判定依据（自审澄清）**：守卫使用 intent 模块的**确定性 L1 硬匹配
  分类结果**（对 user_query 独立重判），不依赖 LLM 回传的 intent——LLM 回传
  意图可能出错，据其拦截会误杀合法查询；LLM 回传的 intent 仅作诊断可观测。
  L1 硬匹配未命中（UNKNOWN）时守卫不启用（无判定依据，交由现有契约校验与
  反思器护栏）。
- **置信分级**：词表硬命中 = hard；LLM 判定 = llm；无任何锚点 = none（UNKNOWN）。

### 3.2 兜底准入收敛（止血核心）

`_heuristic_plan` 从"什么问题都猜 GMV"改为**准入制**：

| 意图 | 兜底行为 |
| --- | --- |
| DIAGNOSTIC | 诊断 DAG（现状保留） |
| CARDINALITY | count_distinct 直答（现有 `_count_dimension_dsl` 收编进 intent 模块） |
| METRIC_SCALAR（hard 锚） | `sum(锚定字段)` 直答——**`_scalar_dsl` 改造**：不再固定
  sum(gmv)，按锚点取数；缺时间窗口用缺省锚并在报告说明口径。**多指标锚**
  （如"GMV 和订单量各多少"）：每个锚定字段一个聚合度量，单数据集多列呈现，
  渲染层多行多列走既有预览表路径 |
| UNKNOWN | **不再产计划**：置 `blocked_reason`，诚实拒答（见 3.4） |

同时退役 `clarify_node` 离线字符规则（`len>=12 && has_metric`）——离线链路
统一为"能答则答（附缺省口径说明），不能答诚实拒答"的**单一出口**；澄清交互
统一由 LLM 在场时的 Planner 契约负责（`clarification` 字段 + HITL 中断）。

**行为变化（明示）**：离线"GMV呢？"不再触发固定文案澄清，而是按硬锚点直答
（缺省时间窗口在报告中说明）；"帮我看看"这类无锚问题从固定澄清改为诚实拒答。
现有 `test_run_agent_hitl_flow` 需按此改写（见 §5）。

### 3.3 L3 意图-DSL 错位拦截（确定性守卫）

`_run_query_step` 在网关强校验前新增一致性校验（LLM 与兜底两条路径都过）：

- **intent=CARDINALITY**：SELECT/METRICS 目标必须且只能是维度字段上的单一
  `count_distinct` 聚合，出现任何金额/计数类度量聚合（sum(gmv) 等）即拦截，
  不执行；**过滤条件（WHERE/filters）不受限制**——"有退款的省份有多少个"
  这类带 `refund_amount > 0` 过滤的问句合法（用户拍板的微调：守卫限制的是
  聚合与投影目标，不是过滤条件）；
- **intent=METRIC_SCALAR**：DSL 聚合字段必须 ⊆ 锚定字段集合，违规拦截；
- 拦截理由进 scratchpad 与事件流 => critic 短路 => 诚实报告。

### 3.4 诚实拒答报告（"没有就是没有"）

新增 `_cannot_answer_report`（与现有 `_no_data_report` 同哲学、同呈现通道）：

- 明确说明"无法从当前语义目录理解该问题"；
- 引用问题中未命中的实体（如"流量"不在目录）；
- **列出当前支持的维度清单与指标清单**（从 `semantic/catalog.py` 收集字段
  + 中文 label，按维度/指标分组）；
- 可行动建议（换问法 / 联系管理员检查模型服务可用性）。

走现有 `markdown_report` 事件通道，前端零改动。AgentState 新增
`blocked_reason: str | None` 字段承载拒答原因（与 `no_data_reason` 的
"数据缺失"语义严格区分）。

### 3.5 降级可见化 + 最小 Grounding 校验

- **降级标注**：AgentState 新增 `answered_by: str`（"llm" | "heuristic"）；
  兜底接管时报告顶部插入显著提示——"⚠️ 本次回答由离线兜底引擎生成
  （LLM 规划不可用），口径为确定性规则匹配结果"，让用户立刻知道这不是
  LLM 的理解；LLM 规划成功时不标注；
- **Grounding 校验（最小版，只标注不拦截）**：LLM 报告生成后，正则提取数值
  （万元/百分比/千分位/小数），与数据集真实数值及其万元换算/百分比形态
  比对；不可溯源数值数量超阈值（>3）时在报告尾部追加"数据溯源提示"小节
  并记 warning 日志。**一期不做拦截与重试**（防死循环/超时）；确定性渲染的
  兜底报告天然 grounded，不参与校验。

### 3.6 Golden 评测用例（防回归）

`eval/` 新增"答非所问率"专项用例（全部离线确定性运行）：

- 基数问题：答案必须含计数、不含金额（答非所问率断言）；
- 目录外实体问题（"流量怎么样"）：必须出现诚实拒答小节；
- 指标锚定："5月退款金额"断言数值等于数据库真实 `sum(refund_amount)`；
- UNKNOWN 问题：断言拒答报告结构与能力清单存在。

## 四、数据契约变更

| 变更 | 位置 | 说明 |
| --- | --- | --- |
| `blocked_reason: str \| None` | `AgentState` | 编排层"无法理解"的诚实原因（区别于 `no_data_reason` 的数据缺失） |
| `answered_by: str` | `AgentState` | 报告产出方式（"llm" / "heuristic"），降级标注依据 |
| `intent_type: str \| None`、`intent_anchors: list[str]` | `AgentState` | L3 守卫用意图上下文（平铺 str 契约，避免 state 反向依赖 intent 模块） |
| `intent`（可选） | Planner 计划 JSON | `{type, anchors}`，LLM 回传意图，`_plan_from_llm` 宽容读取 |

## 五、测试计划与影响面

**改写**：
- `test_run_agent_hitl_flow`："GMV呢？"离线行为变为硬锚直答；HITL 中断恢复
  e2e 改用"LLM 在场 + Planner 输出 clarification => 用户答复 => resume"的
  mock 链路验证；
- `_scalar_dsl` 相关单测：按锚点取数的断言更新。

**新增**：
- intent 分类单测（三类命中 + UNKNOWN + 词表优先级 + 过滤条件不误伤）；
- 错位拦截单测（CARDINALITY + 金额聚合 => 拦截；CARDINALITY + 合法 WHERE => 放行）；
- 诚实拒答 e2e（UNKNOWN 问题 => `blocked_reason` + 拒答报告 + 能力清单）；
- 降级标注 e2e（LLM 失败 mock => 报告含兜底提示）；
- Grounding 校验单测（可溯源数值不标注；编造数值 => 溯源提示小节）；
- Golden 评测用例（§3.6）。

**回归红线**：全量 pytest、black、ruff 全绿；现有诊断链路 e2e、审计门、
时间域守卫、QA 断言行为不变。

## 六、二期边界（本期不做）

别名全量收编进 `semantic/catalog`（本期先在 intent 模块内建最小词表）、
LLM 置信度校准、Grounding 拦截重试闭环（Selective Self-Correction）、
澄清选项式反问（[1]/[2] 选项卡）、评测自动化监控面板。
