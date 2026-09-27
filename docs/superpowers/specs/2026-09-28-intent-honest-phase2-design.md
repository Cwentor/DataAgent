# 设计文档｜意图路由与诚实兜底 二期：收尾、防虚构闭环与选项式澄清（2026-09）

## 一、背景与决策记录

### 1.1 承接

一期（`2026-09-27-intent-routing-honest-fallback-design.md`）落地了意图准入制、
诚实拒答与 Grounding 最小版（只标注不拦截），全量 755 测试绿、意图评测 8/8。
独立终审遗留 5 条 Minor deferred；规格 §六 预留五个二期方向。

### 1.2 头脑风暴关键决策

| 决策点 | 结论 |
| --- | --- |
| 二期范围 | 全量打包：Minor 收尾 + 别名收编 + Grounding 拦截闭环 + 选项式澄清 + 评测挂 CI |
| 中置信路径 | 不单列，融入选项式澄清——"LLM 可理解但存在口径歧义"时给选项反问而非拒答 |
| Grounding 重试失败策略 | 重试 1 次后仍超阈值 => **降级确定性渲染**（弃 LLM 叙事、保数据真实）——"宁可中断不给虚假结果"的直接延伸 |
| 前端改动 | 零改动——`elHitl` 已渲染 `item.options`（字符串数组 pill 按钮 + 自由输入兜底），本期只做后端契约 |
| 词表收编范围 | 仅 intent 用词表进语义目录；`_REFLECTOR_CONCEPT_FIELDS` 等其他散落词表不动（YAGNI） |

### 1.3 现状探索结论

- 前端 `web/static/js/stream-ui.js` 的 `elHitl` 已实现 `item.options` 渲染
  （pill 按钮 `dataset.value = o`，点击即以该文本答复）与自由输入兜底；
- CI（`.github/workflows/ci.yml`）已挂 golden eval 双模式，评测自动化只需
  追加 `intent_eval` 一行；
- `agent/slotfill.py` 是 agent 链路的槽位澄清（时间窗/未定义指标），与编排
  链路 clarify 并行存在，本期不合并。

## 二、设计（六节）

### 2.1 五条 Minor 收尾（一期终审遗留）

1. **守卫审计面**：`_run_query_step` 的 mismatch 分支补 `emit_tool_start`
   配对 + 拦截说明进 notes；全部 DSL 变体被拦时 `ToolRecord.ok=False`；
2. **code_exec 短路**：`code_exec_node` 开头与 `no_data_reason` 并列检查
   `blocked_reason`，拒答路径零沙箱执行、零产物发射；
3. **拒答报告实体引用**（一期规格 §3.4 补齐）：`_cannot_answer_report` 增加
   "已识别锚点"行——UNKNOWN 时明确"未在语义目录识别到任何指标或维度"并
   复述用户原问句；
4. **能力清单分组**：`capability_catalog_lines` 把 `unit_price` 从"金额指标"
   剔除，单列"其他"；
5. **parity 链式覆盖**：roundtrip mock 恢复轮返回两步计划，恢复
   clarify-resume → plan_review interrupt → approve 全链断言。

### 2.2 别名全量收编进语义目录

- `FieldMeta` 新增 `aliases: tuple[str, ...] = ()`；`COLUMNS` 逐字段登记
  别名（从 `intent.py` 的 `_METRIC_TERMS`/`DIMENSION_TERMS` 全量迁移）；
  `config/semantic.json` 与 `semantic/catalog_loader.py` 同步加载；
- `intent.py` 词表改为**从 `COLUMNS` 动态构建**（模块级构建一次缓存）：
  遍历 `COLUMNS`，`aliases` 项 -> 字段名映射（含长词优先排序依据）；
  intent 模块不再维护字面词表——新字段登记时别名随登记自动进入意图
  分类器，单一事实源（AGENTS.md 铁律精神）；
- 依赖方向：`semantic` 不依赖 `core`，`core/orchestrator/intent` 只读
  `semantic/catalog` ✓；
- 迁移等价性红线：收编后 `classify_intent` 对既有测试语料（一期全部
  intent/编排/评测用例）的判定结果必须逐一不变。

### 2.3 Grounding 定向重试闭环（Selective Self-Correction）

- 触发阈值不变：不可溯源数值 >3；
- **定向重试 1 次**（上限硬编码）：把不可溯源清单 + 原素材喂回 LLM，
  修正指令（"仅允许引用素材中的数值，以下数值无法溯源，请修正或删除"），
  重发 `reflection` 事件（"报告数值溯源失败，定向重写"）可观测；
- 重试产物再次过 `grounding_review`：仍超阈值 => **放弃 LLM 报告，降级
  确定性分析师渲染**（确定性渲染天然 grounded）+ warning 日志；
- 重试在 `synthesize_node` 内同步完成，不新增 state 字段、不跨节点；
- 重试调用的 prompt 复用 `SYNTHESIZER_SYSTEM`（追加修正指令小节）。

### 2.4 选项式澄清（后端契约 + 中置信路径）

- **Planner 契约升级**：`clarification` 允许两种形态——纯字符串（旧契约，
  兼容）或 `{"question": "...", "options": ["...", ...]}`；`planner_node`
  澄清分支规范化两种形态；options 非法（非字符串数组/含非字符串项）时
  宽容丢弃，保留 question；
- **状态契约**：`AgentState` 新增 `clarification_options: list[str]`
  （默认空列表）；
- **链路透传**：`_plan_gate` 的 clarify interrupt payload 增加 `options`
  字段；web 层 `hitl_request` payload 透传 `options`；前端 `elHitl`
  已就绪零改动；
- **提示词纪律**（`PLANNER_SYSTEM`）：
  - 输出 clarification 时尽量同时给出 2-4 个候选口径选项，每个选项是
    一句可直接作为答复发送的完整表述（如「按含退款的净销售额口径」）；
  - 中置信路径：问题可理解但存在口径歧义（如"销售额"可能指含/不含
    退款、多指标同名）时，**优先选项澄清而非拒答**；
- 兜底路径（离线）不产选项：拒答报告与直答路径均无 LLM，clarification
  为空语义不变。

### 2.5 评测挂 CI

`ci.yml` 的 `test` 与 `nightly` job 在既有 golden eval 之后各追加一行
`python -m eval.intent_eval`。

## 三、数据契约变更

| 变更 | 位置 | 说明 |
| --- | --- | --- |
| `aliases: tuple[str, ...] = ()` | `FieldMeta` | 字段业务别名（意图分类词源） |
| aliases 加载 | `semantic.json` / `catalog_loader` | 与既有字段元数据同源加载 |
| `clarification_options: list[str] = []` | `AgentState` | 选项式澄清载荷 |
| hitl_request payload `options` | web 层 | 前端已消费，纯增量 |
| Planner `clarification` 允许对象形态 | `PLANNER_SYSTEM` | 兼容纯字符串旧形态 |

## 四、测试计划

- Minor 五条各自 RED→GREEN（审计面断言 ToolRecord.ok/notes、code_exec
  零沙箱执行 spy、拒答报告锚点行、capability 分组、parity 全链断言）；
- 别名收编等价性：一期全部 intent/编排/评测用例判定逐一不变的回归
  （直接复跑既有测试即覆盖）；loader 加载 aliases 的契约测试；
- Grounding 闭环：重试成功（第二次 grounded => LLM 报告保留）、重试后
  仍超阈值 => 确定性渲染、重试事件可观测（mock `_synthesize_with_llm`
  序列）；重试上限恰好 1 次；
- 选项式澄清：planner 双形态规范化单测、options 非法宽容、payload 透传
  （web 层 hitl_request 断言 options）、前端消费不回归（契约即字符串数组）；
- 全量回归红线：pytest / black / ruff / eval.intent_eval 全绿。

## 五、范围边界（三期候选）

`_REFLECTOR_CONCEPT_FIELDS` 等其余散落词表收编、维度取值提取（"海南省"->
'海南' 的确定性转换）、多轮澄清状态机编排化、答非所问率的长期监控面板。
