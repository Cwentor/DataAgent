# 设计文档｜LLM 失联分层降级兜底（2026-09）

## 1. 背景与问题

LLM 不可用（429 限流/失联）时系统能力断崖式下跌：`planner_node` 退到 `_heuristic_plan` 模板分支，仅能匹配诊断/基数/硬锚定三类预置模板；模板外的 query 判 UNKNOWN 直接拒答。痛点：

1. **能力二分化无中间态**：只有"LLM 在线全能力"与"模板弱能力"两档，非模板问题直接拒答，放弃已有元信息与数据的价值（如"海南省的 GMV"——指标/维度都在语义目录，仅是无法自动拆解复合条件）；
2. **UNKNOWN 混淆两类情况**：真·语义不存在（数仓没有该指标/维度，该拒答）与假·解析失败（字段存在但 LLM 失联无法拆条件，不该拒答）混在一起，用户分不清是问错了还是系统临时降级；
3. **429 无重试**：`LLM_MAX_RETRIES` 是定义后无消费方的死配置，单次 429 即整体降级（2026-09-29 线上诊断实测：规划层 RateLimitError 429 → 静默退模板）。

## 2. 目标与非目标

**目标**

1. LLM 失联时三级降级：强匹配模板（保留）→ 弱解析交互确认（新增）→ NOT_EXIST 拒答（保留并增强文案）；
2. UNKNOWN 语义拆分：字段存在但无法自动拆解 → 交互确认后仍可产出分析；语义不存在 → 拒答并明确告知；
3. 429 退避重试，接活 `LLM_MAX_RETRIES` 死配置；
4. 降级产物强制水印 + 埋点可观测（降级命中/确认/拒绝计数）。

**非目标（范围外）**

- 降级模式的多步分析（环比/排名/多表 join/沙箱 analyze）——结构上禁止，非提示词约束；
- 会话级降级状态机（每轮独立判定，本轮降级下轮 LLM 恢复自然走主链路）；
- LLM 恢复主动探测；前端新 UI（双通道全部复用现有组件）；
- `classify_intent` 枚举与意图评测契约改动（8/8 锚点零回归）。

## 3. 设计

### 3.1 UNKNOWN 二次判定（planner 兜底分支内，零评测回归）

- `classify_intent`/`IntentType`/意图评测**完全不动**；`_heuristic_plan` 返回 None（UNKNOWN）后，planner 新增二次判定 `_degraded_parse_plan(query, profile)`：
  - `profile.anchor_fields` 含**至少一个指标锚**（语义目录指标，如 gmv/order_amount/refund_amount）→ **PARSE_DEGRADE**：进入弱解析（3.2）；
  - 无指标锚（全无锚点，或只识别到维度）→ **NOT_EXIST**：现有 `blocked_reason` 拒答路径，文案增强——区分"未识别到任何锚点"与"识别到维度【X】但未识别到指标"；
- 复合问句（指标锚+维度限定共存，现状判 UNKNOWN，见 intent.py 复合问句注释）是弱解析的主要来源场景。

### 3.2 弱解析与"不猜条件"的边界

- DSL 组装新增 `_degraded_query_dsl(profile, filters)`（扩展 `_scalar_dsl`）：
  - 指标聚合复用 `_SCALAR_FIELD_AGG` 语义映射；维度分组/筛选仅用用户确认的条件；
  - 时间窗：显式时间解析（`parse_explicit_time_window`）优先，缺省 2024-05 锚并在报告明示口径（复用 `_default_scope_note` 机制）；
  - pay_status=SUCCESS 口径适配照抄 `_scalar_dsl`；
- **筛选条件来源白名单（强制）**：①显式时间解析结果；②用户在审批卡/澄清中确认或补充的内容；③数据枚举值精确匹配（问题中的取值词与 `profile_enum_values` 枚举精确命中，如"海南"∈province 枚举——数据真实存在不算猜）。枚举匹配不到的筛选值一律留白交用户补充；
- **维度用法判定**：问题中该维度取值命中枚举（如"海南省"）→ 作筛选条件（province=海南）；问题为"各/按 + 维度"形态（如"各省份的 GMV"）→ 作分组维度（group by）。两者皆非 → 维度留白交用户在审批卡确认用法；
- **降级计划结构强制**：`[query(带 DSL), synthesize]` 两步，无 analyze（沙箱）步——禁多步组合是结构约束而非提示词约束；执行链仍有 DSL 契约（extra=forbid）+ 执行前审计门 + L3 审批门三重护栏。

### 3.3 双通道路由（确认交互）

- **单一候选口径**（指标+时间+筛选均可确定）→ 产出降级计划 → **plan_review 审批卡**（既有 interrupt 机制），卡上标注"⚠️ AI 规划暂不可用：以下查询条件为规则推断，请确认"——批准执行 / 修改输入补充条件（既有修改指令闭环）/ 拒绝终止；
- **候选歧义**（筛选值多候选或缺失）→ **选项式澄清**（既有 `clarification_options` 机制）：列出候选口径或请求补充；用户回答后经 `human_reply` 合并（clarify_node 既有结构）重新弱解析，唯一化后进审批卡；
- 澄清后仍无法唯一化 → **单轮澄清 + 二次解析，仍歧义即拒答（防循环）**，回落 NOT_EXIST 拒答（不无限循环）；轮次计数由 state 新增字段 `clarification_rounds` 承载（planner 澄清挂起时递增，`_plan_gate` resume 合并答复时再递增，planner 消费判定超限）；用户点选澄清选项（回执"维度=值"形态）经 `_degraded_parse` 补充段解析采纳为**已确认筛选条件**（白名单第②类"用户确认内容"），二次解析直接唯一化进审批卡——label 需经维度词表反查命中且 value 精确命中枚举，否则忽略补充（宁缺毋滥）；
- 前端零改动：审批卡与选项澄清组件均已存在并被消费。

### 3.4 429 退避重试（providers 网关层）

- `chat_text` 分发链路对 `RateLimitError` 统一退避重试：次数 = `min(LLM_MAX_RETRIES, 2)`（死配置接线，短退避 1s/3s）；仅对 429 重试，超时/网络异常不重试（综合已有 `SYNTHESIZER_TIMEOUT=180` 专用预算）；
- 规划/Coder/反思/综合全部调用点受益；
- 不做会话级降级状态：每轮请求独立判定，本轮重试失败仅本轮降级，下轮 LLM 恢复自然走主链路。

### 3.5 降级水印与可观测

- `answered_by` 新增取值：`degraded_confirmed`（L1-L3 弱解析计划经 plan_review 人工批准）；`degraded_auto`（L4 全自动，计划经 `maybe_interrupt` 自动批准直通、**未经人工确认**，水印必须如实区分——诚实铁律）；`_degradation_banner` 对应两套文案——confirmed："⚠️ 本次报告由降级模式生成（AI 规划暂不可用）：查询条件为规则推断并经人工确认，未经 LLM 完整语义理解"；auto："⚠️ 本次报告由降级模式生成（AI 规划暂不可用）：查询条件为规则推断，当前为全自动审批模式、未经人工确认，请谨慎采信"；现有 heuristic banner 与综合降级标注保留；
- 埋点（复用 audit/metrics 指标框架）：`MetricsRegistry.record_degrade(outcome)` 三计数器——`parse_hit`（弱解析/澄清命中，planner plan 与 clarify 首轮分支打点）/ `confirmed`（降级计划获批准执行，`_plan_gate` approve 分支打点，L4 自动批准同口径计入）/ `rejected`（用户拒绝或回落拒答，`_plan_gate` reject 分支与 planner 两处拒答打点），随 `GET /api/metrics` 以 `degrade_outcomes` 导出；事件侧 `emit_event("degrade", {outcome})` 双写（parse_hit/rejected），前端 `web/static/js/protocol.js` 的 `AgentEventType` 白名单纳入 `degrade`（SSE 帧不再被静默丢弃）。

### 3.6 数据流（修复后）

```
用户 Query
  ↓ planner_node
尝试 LLM 规划（429 → 网关退避重试 ≤2 次）
  ├ 成功 → LLM 计划（契约校验）→ 正常链路
  └ 失败 → _heuristic_plan(query)
      ├ DIAGNOSTIC/CARDINALITY/METRIC_SCALAR → 模板 DAG（answered_by=heuristic，水印保留）
      └ UNKNOWN → 二次判定 _degraded_parse_plan
          ├ 指标锚存在 → 弱解析（枚举值匹配筛选候选）
          │   ├ 唯一候选 → 降级计划 → plan_review 卡（降级标注）
          │   │           批准 → 执行 DSL → 确定性直答报告（answered_by=degraded_confirmed）
          │   └ 多候选/缺失 → clarification_options → 用户回答 → 重新弱解析
          │                  （唯一化 → plan_review；二轮仍歧义 → NOT_EXIST 拒答）
          └ 无指标锚 → NOT_EXIST 拒答报告（文案区分两类情况）
```

## 4. 错误处理

- 429 重试仍失败 → 正常进入模板/弱解析/拒答分流（与现状一致，只是多两次机会）；
- 弱解析 DSL 构造失败（跨表混合锚等，`_scalar_dsl` 既有 None 语义）→ 回落 NOT_EXIST 拒答；
- 用户拒绝降级计划 → 终止本轮（phase=done，如实告知），不自动改猜条件；
- 澄清后二次解析仍歧义 → NOT_EXIST 拒答（防循环，plan_edit_instruction 同类防循环经验）；
- 审计门/编译器对降级 DSL 一视同仁（违规照样 REJECTED，拒绝原因可自愈——降级模式无 LLM 自愈，直接回落拒答）。

## 5. 测试与验收

1. 单测（planner）：UNKNOWN+指标锚 → 弱解析；UNKNOWN+无锚 → NOT_EXIST；维度-only 锚 → NOT_EXIST 带增强文案；
2. 单测（DSL）：维度分组+用户确认筛选+缺省锚明示；枚举值精确匹配候选；枚举未命中留白不猜；跨表混合锚回落拒答；
3. 单测（路由）：唯一候选 → plan_review；多候选 → clarification_options；二轮歧义 → 拒答；
4. 单测（providers）：RateLimitError 退避重试（第一次 429 第二次成功）；次数上限；其他异常不重试；
5. 单测（水印）：degraded_confirmed 报告头部水印文案；
6. 意图评测 8/8 零回归；全量 pytest + black + ruff 全绿；
7. 端到端（LLM 桩置空）："海南省的GMV" → 澄清/审批 → 批准 → 带水印报告；"分析流量下降原因" → NOT_EXIST 拒答。

## 6. 风险与权衡

- **审批卡增加一次点击成本**：弱解析必须经用户确认（诚实原则），用"单一候选一键批准"降低摩擦；
- **枚举值匹配的召回有限**（只覆盖低基数字段）：高基数字段（门店名）筛选仍需用户手输——宁可留白确认，不猜；
- **429 重试拖长响应**（最多 +4s）：仅限流异常、次数封顶 2，权衡可接受；
- **answered_by 新值的下游兼容**：grep 全部消费点逐一适配（banner/埋点/测试断言）；
- **意图评测契约冻结**：二次判定在 planner 层实现而非 intent.py，评测零回归。
