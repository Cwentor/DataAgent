# 设计文档｜分析报告叙述化修复（2026-09）

## 1. 背景与问题

用户在 Web 工作台提交诊断类问题（"分析一下 2024 年 5 月第二周比第一周 GMV 下滑的原因，按地区定位"），得到的"分析报告"呈现为两节原始数据 dump：

- 小节标题为英文步骤 id（`factor_decomposition`、`region_drilldown`）；
- 正文是 Python repr 形态的嵌套 dict/list（`week1 = {'gmv': 616872.81, ...}`、长浮点未舍入）；
- 比率指标被误作金额渲染（`gmv_change_pct = -0.00 万元`）；
- 无任何文字叙述与结论。

用户期望：**LLM 基于已获取的数据理解分析，产出便于理解的文字表述**（叙述 + 精简人读化表格），原始 dict/JSON 一律不进报告。

## 2. 根因链（2026-09-29 诊断实测）

```
aiaa 中转（deepseek-v4-flash）报告长文生成耗时 > 60s
→ providers/adapters.py 六处 _http_post(timeout=60) 硬编码读超时
→ ProviderTimeoutError: 请求超时（诊断脚本实测复现）
→ _synthesize_with_llm 捕获异常返回 None（无失败原因透出）
→ synthesize_node 静默落入确定性兜底渲染（无降级标注，用户无感知）
→ _summary_analyst_markdown 对 LLM Coder 自由结构的 summary：
   a) title 为英文（Coder 自拟或 step.id 缺省，nodes.py:1386）→ 英文小节；
   b) metrics 含嵌套 dict/list → isinstance(v, (int,float)) 为假 → 直接 f"{k} = {v}" repr；
   c) _metric_human 仅排除计数键，比率键误转万元；
   d) 浮点值不舍入。
→ 用户看到一坨乱数据
```

佐证事实：

- `config/settings.py` 的 `PROVIDER_TIMEOUT`（默认 60）、`LLM_TIMEOUT`（默认 60）**定义后无任何消费方**，配置面与实现脱节；
- 内置沙箱模板的 summary title 本为中文（如 `驱动因子分解`），截图中的英文 title 说明该次为 LLM Coder 自由编写的分析代码（`gmv_change_pct=-0.0` 亦为其计算错误），summary 结构不可预知；
- LLM 商业分析师综合层（`SYNTHESIZER_SYSTEM` 四段式）与 Grounding 数值溯源闭环均已存在，问题不在"缺能力"，而在"能力未被走通 + 兜底渲染质量失控"。

## 3. 目标与非目标

**目标**

1. LLM 叙述链路稳定走通：报告综合类长生成请求不再被 60s 硬编码超时误杀；
2. LLM 综合失败时降级可见：兜底报告头部明示"确定性模板渲染 + 原因"（诚实优先原则）；
3. 兜底渲染人话化：无 LLM 时报告也达到"叙述 + 精简表格"水平，严禁 repr dump；
4. 配置面兑现：`PROVIDER_TIMEOUT` 真正生效，新增 `SYNTHESIZER_TIMEOUT`。

**非目标（范围外）**

- 流式（SSE）输出改造（方案 C，后续演进）；
- `_analysis_material` 素材结构化/截断策略调整（另立议题）；
- `_degraded_report` 降级简报路径的渲染重构（已具降级语义）；
- 前端 UI 改动（Markdown 渲染管线无需变更；工作区既有 `stream-ui.js`/`style.css` 未提交改动不属本分支）；
- Grounding 阈值调整（实施期仅做全链路验证，不预设改参数）。

## 4. 设计

### 4.1 providers 网关超时治理（治本）

- `config/settings.py`：
  - 新增 `SYNTHESIZER_TIMEOUT: int`（env `SYNTHESIZER_TIMEOUT`，默认 180；小于等于 0 时回落 180 防御）；
  - `PROVIDER_TIMEOUT` 保持默认 60，语义升级为"网关默认读超时"。
- `providers/adapters.py`：
  - 六处 `_http_post(..., timeout=60)` 硬编码改为缺省读 `settings.PROVIDER_TIMEOUT`；
  - `BaseAdapter.chat_text` 增加 keyword-only 参数 `timeout: int | None = None`，`None` 时使用上述默认，非空时透传到对应协议端点的 `_http_post` 调用；各协议适配器（OpenAI Chat / OpenAI Responses / Anthropic / Gemini）同步透传。
- `providers/context.py`：`DispatchingAdapter.chat_text` 增加 `timeout` 参数并转发给真实适配器。
- `providers/__init__.py`：`chat_text` 门面签名同步增加 `timeout`。
- `core/orchestrator/nodes.py`：`_synthesize_with_llm` 与 `_degraded_report` 中的 `chat_text(...)` 调用传 `timeout=settings.SYNTHESIZER_TIMEOUT`（180s 仍在 web 链路 `AGENT_RUN_IDLE_TIMEOUT_SECONDS=300` 空闲预算内）。
- 其余调用方（Planner / Coder / Router / NL2DSL 等）不改动，自动继承"默认超时可配置"的修复。

### 4.2 LLM 综合失败降级可见化

- `_synthesize_with_llm` 返回值改为 `tuple[str | None, str | None]`（报告文本, 失败原因分类）：
  - 异常 → 原因取 `{type(exc).__name__}: {exc}` 摘要（如 `ProviderTimeoutError: 请求超时`）；
  - 空输出 → `空输出`；
  - 反契约（JSON 形态）→ `输出反契约（JSON 形态）`；
  - 成功 → `(text, None)`。
- `synthesize_node` 确定性兜底路径：当本次曾尝试 LLM 综合且失败（`failure_reason` 非空）时，报告头部注入降级标注（复用 `_degradation_banner` 引用块款式，可与其叠加）：
  > ⚠️ 本次报告由确定性模板生成：LLM 商业分析师综合不可用（原因：<failure_reason>）。以下数值均来自真实查询结果，叙述深度有限。
- Grounding 重试失败导致的 `llm_report=None` 同样带原因（`数值溯源重写后仍超阈值`）。
- `_degraded_report` 路径维持现状（其存在本身即降级语义）。

### 4.3 兜底渲染人话化（`_summary_analyst_markdown` 防御式重写）

**小节标题中文化**

- 新增 `_section_title(summary, state)`：
  1. title 含中文字符 → 直接使用；
  2. title 为英文（step.id / Coder 自拟）→ 在 `state.plan_steps` 中按 id 匹配，取 `PlanStep.goal`（中文）；
  3. 仍无法确定 → 通用名"归因分析"；
- `_summary_analyst_markdown` 增加 `state` 参数（仅 synthesize 兜底路径一处调用，签名变更影响可控）。

**metrics 渲染规则（禁止 repr）**

| 值类型 | 渲染方式 |
| --- | --- |
| 标量（int/float/str） | 键名中文化后 `标签：值` 直出 |
| dict（如 week1/week2 同构组） | 多个同构 dict 值合并渲染为对比表格（行=组名中文化，列=指标并集） |
| list[dict]（如 province_delta_top、metrics["table"]） | 渲染为 Markdown 表格（列=键并集，限 10 行，超出注明总行数） |

- 标签解析顺序：`semantic/catalog` 的字段中文名（FieldMeta.label / 覆写目录）→ 键名规则映射（`*_pct/*_rate/share/ratio` 为比率，含 `count/orders/buyers/quantity` 为计数，其余默认金额）→ 原键名兜底；
- 数值格式化三分（修复 `gmv_change_pct = -0.00 万元`）：
  - 计数：原值（整数不带小数）；
  - 比率：百分比一位小数（`-33.5%`）；
  - 金额：万元两位小数（`61.69 万元`）；
  - 浮点一律舍入，禁止长小数透出。

**渲染隔离**

- 单个 summary 渲染抛异常时捕获，该小节降级输出"（该分析产物无法渲染，原始文件已留存工作区）"，不拖垮整份报告、严禁回退为 raw dump。

### 4.4 Coder 提示词软约束（双保险）

- `core/orchestrator/prompts.py` Coder 纪律区补充三条：
  1. `save_summary` 的 `title` 必须为简体中文业务短语（如"驱动因子分解"）；
  2. `metrics` 只放标量，明细数据放 `table` 参数；
  3. 严禁把嵌套 dict/list 塞入 metrics。
- 渲染层硬防御（4.3）兜住 LLM 不遵守的情况；不改沙箱 `save_summary` API 契约。

### 4.5 数据流（修复后）

```
诊断意图 → 取数/沙箱分析 → summary 产物
  → LLM 综合（timeout=SYNTHESIZER_TIMEOUT=180s）
     ├─ 成功 → Grounding 审查 → 叙述报告（现状四段式）
     └─ 失败（超时/反契约/空输出/溯源超阈值）
        → 确定性渲染（人话化：中文小节 + 对比表/归因表 + 数值三分格式化）
        → 头部降级标注（原因）
```

## 5. 错误处理

- **超时**：`ProviderTimeoutError` 映射不变；synthesize 侧捕获后 reason 入报告标注；
- **配置非法**：`SYNTHESIZER_TIMEOUT <= 0` 回落默认 180；
- **渲染防御**：summary 任意畸形结构（缺键 / 深层嵌套 / 混合类型）不抛出、不 dump；
- **透传兼容**：`chat_text` 的 `timeout` 为可选参数，旧调用点零改动即可运行。

## 6. 测试与验收

1. **providers 单测**：`chat_text` timeout 缺省取 `settings.PROVIDER_TIMEOUT`、显式传参覆盖；适配器透传（stub `_http_post` 捕获参数断言）；
2. **synthesize 单测**：
   - LLM 综合注入超时异常 → 兜底报告头部含降级标注与原因；
   - 反契约输出 → 同上；
   - LLM 成功 → 无标注；
3. **渲染快照断言**（`_summary_analyst_markdown`）：
   - 截图同款 summary（英文 title / 嵌套 metrics / metrics.table / 比率键）→ 输出含中文小节标题、Markdown 表格、百分比与万元格式、无 `{` 开头的 repr 片段；
   - 畸形 summary → 输出降级文案而非异常；
4. **实施期真实验证**：分支上用已配置供应商（aiaa）真实调用"综合 → Grounding 审查"全链路一次，确认超时修复后叙述报告可产出且不被溯源审查误杀；若误杀，记录证据另立议题（不在本分支扩范围）；
5. **回归**：`black --check .`、`ruff check .`、`python -m pytest -q` 全绿。

## 7. 风险与权衡

- **providers 公共接口变更**（`chat_text` 加参）：可选参数向后兼容，回归面由全量测试兜底；
- **180s 综合超时的等待体验**：SSE 链路下用户最长等待约 3 分钟才见兜底报告——权衡为"拿到叙述报告"优于"快速拿到模板报告"；可通过 env 调小；
- **渲染层标签映射的维护成本**：优先复用 semantic catalog 的 label（单一事实源），键名规则仅兜底，避免新增长字面词表；
- **Grounding 误杀可能性**：本轮只做实测取证，不预设调参，避免为通过率放松诚实约束。

---

## 技术栈选型对比（复盘矩阵，2026-10-26 补记）

> 本节为 2026-10-26 统一补记的复盘性选型对比：候选与理由是基于本项目约束与生态现状的分析结论，非当时讨论的历史记录。既定要求（用户确认）：后续各期设计文档在设计期即输出选型对比矩阵（样板：二十三期 ClickHouse spec §4）。

### S1 报告叙述的生产方式：LLM 综合 + 确定性人话化兜底（降级可见）

| 候选 | 叙述质量与可读性 | 失败行为（诚实性） | 确定性与成本 | 结论 |
|------|------------------|--------------------|--------------|------|
| **LLM 四段式综合 + 确定性兜底渲染 + 头部降级标注（选定）** | 主路径产出真叙述与结论；兜底侧中文小节 + 对比表/归因表，数值三分格式化，repr dump 被禁止 | 失败原因（超时/反契约/空输出/溯源超阈值）经 `failure_reason` 注入报告头部，不静默吞错 | 主路径 LLM 非确定但受 Grounding 数值溯源约束；兜底渲染纯确定性、零额外 LLM 调用 | ✅ 兼顾叙述深度与"诚实=不虚构+假设透明"，兜底数值仍全部来自真实查询结果 |
| 纯确定性模板渲染（弃用 LLM 综合） | 固定句式拼装上限低，复杂归因结论写不出 | 无失败面，但"伪叙述"易被误读为分析结论 | 完全确定、零 LLM 成本 | ❌ 弃用已建成的 SYNTHESIZER 四段式能力，叙述深度倒退，与"LLM 基于数据理解分析"的用户诉求直接相悖 |
| 纯 LLM 叙述（失败即报错拒出报告） | 表达上限最高 | 一次超时/反契约即把失败抛给用户，整次 run 的取数与沙箱成果作废 | 无兜底分支，端到端行为最难预期 | ❌ 违背"拒答降为最后手段"原则，可用性差且浪费已支付的计算成本 |
| LLM 失败无限重试直至成功 | 理论上最终可获叙述 | 重试时长不可控，SSE 下用户无限等待且无原因透出 | 重试不封顶，成本不可预算 | ❌ 违反自愈闭环"受控重试上限"铁律，也超出 `AGENT_RUN_IDLE_TIMEOUT_SECONDS=300` 空闲预算 |

### S2 providers 网关超时治理：分场景配置 + 显式透传

| 候选 | 配置灵活性 | 改动面与向后兼容 | 故障收敛（fail-closed） | 结论 |
|------|------------|------------------|--------------------------|------|
| **新增 `SYNTHESIZER_TIMEOUT`（默认 180，<=0 回落防御）+ `chat_text` 可选 keyword-only timeout 逐层透传（选定）** | 长文综合与常规调用各用各的超时，env 可调 | 六处硬编码改为读 `settings.PROVIDER_TIMEOUT`；可选参数向后兼容，Planner/Coder/Router 等旧调用点零改动 | 超时上限仍存在且兑现了闲置配置（180s < 300s 空闲预算），超时归一为 `ProviderTimeoutError` 可入降级标注 | ✅ 同时修复"配置面脱节"与"单点长请求被 60s 误杀"两个根因，契约演进零破坏 |
| 全局硬编码统一调大（六处 60 → 300） | 无任何配置能力 | 改动字面上最小 | Planner/Coder/Router 等全部调用方陪绑 300s，故障悬挂时间整体放大 | ❌ 治标不治本，`PROVIDER_TIMEOUT` 继续空转，小请求的超时保护被削弱，违背资源上限 fail-closed 语义 |
| synthesize 侧捕获超时后静默重试/忽略 | 与配置无关 | 需在调用方特判 | 超时被吞，故障不可观测 | ❌ 静默吞错直接违反"兜底模式严禁隐式吞错"，且 60s 硬编码根因原封不动 |
| 取消读超时（`timeout=None`） | 无配置负担 | 零改动 | 连接可无限悬挂，线程看门狗与空闲预算全被架空 | ❌ 违反资源治理与 fail-closed 铁律，Provider 挂起即拖死整个 run |

### S3 兜底渲染的标签与格式化事实源：semantic catalog 优先 + 键名规则兜底

| 候选 | 与单一事实源的一致性 | 维护成本 | 覆盖率 | 结论 |
|------|----------------------|----------|--------|------|
| **`FieldMeta.label`（semantic catalog）优先 → 键名规则（`*_pct/*_rate/share/ratio` 计比率、含 `count/orders/buyers/quantity` 计计数、其余金额）兜底 → 原键名直出（选定）** | 标签与业务口径同源于 semantic.json/目录，不产生第二套业务映射 | 词表零新增，键名规则只做格式分类、不携带业务语义 | 目录已登记字段全覆盖；Coder 自拟键退化为原键名可见，不虚构翻译 | ✅ 与 M-P2"语义单一事实源"约定一致，避免在手写渲染代码里新增业务映射 |
| 渲染层新增长字面词表（key→中文 dict） | 形成第二套映射，与 catalog 漂移风险高 | 每个新字段需双处登记，长期必然腐化 | 覆盖率取决于词表维护勤勉度，漏登记即漏翻译 | ❌ 正是文档第 7 节权衡中明确避开的方案，违反语义去耦铁律 |
| 维持英文键名直出（仅修数值格式化） | 无映射即无漂移 | 零成本 | 不覆盖中文标签诉求 | ❌ "中文小节 + 中文标签"的人话化目标落空，仅修一半问题 |
| 兜底路径再调一次 LLM 做标签翻译 | 翻译结果动态不可复现 | 每份兜底报告多一次 LLM 调用与延迟 | 依赖 LLM 可用性 | ❌ 兜底路径定义上发生在 LLM 不可用之后，自相矛盾，且引入幻觉风险（诚实=不虚构） |

### S4 LLM Coder summary 结构失控的治理：提示词软约束 + 渲染层硬防御

| 候选 | 契约侵入性 | 对 LLM 违规的鲁棒性 | 自愈/失败成本 | 结论 |
|------|------------|----------------------|----------------|------|
| **Coder 提示词补三条纪律（软约束）+ `_summary_analyst_markdown` 防御式重写（硬防御，单小节渲染异常隔离降级）（选定）** | 不改沙箱 `save_summary` API 契约，内置模板与既有分析代码零波及 | 任意畸形结构（缺键/深层嵌套/混合类型）不抛出不 dump，最坏降级为一行留痕说明 | 整份报告永不因单个小节崩溃，双层职责清晰 | ✅ 与"受控自愈 + 兜底可降级但必须标注"的分层哲学一致，即文档 4.4 的双保险既定方向 |
| 改 `save_summary` API 强 schema 校验（metrics 仅允许标量，违规即沙箱报错喂回重试） | 动沙箱工具契约，全部内置模板与历史用例需回归 | 从源头堵死嵌套结构，但 LLM 侧仍可能想出新畸形 | 每次违规消耗一次受控重试，极端情况烧尽重试预算仍无产物 | ❌ 契约侵入性高且与渲染层防御功能重叠；文档已明确"不改沙箱 save_summary API 契约" |
| 渲染失败 fail-fast 整份报告报错 | 零新契约 | 对畸形零容忍 | 单个小节畸形即抹掉全部取数/分析成果 | ❌ 与"拒答降为最后手段"冲突，可用性比修复前更差 |
| 渲染失败回退 raw dump（Python repr 原文） | 零成本 | 信息"完整"但不可读 | 无防御可言 | ❌ 即本次要修复的问题本身（两节原始数据 dump），属明令禁止项 |
