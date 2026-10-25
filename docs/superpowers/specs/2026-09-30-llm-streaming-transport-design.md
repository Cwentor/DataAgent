# 设计文档｜LLM 网关层流式传输改造（客户端聚合）（2026-09）

## 背景与动机

2026-09-30 排查确认：生产配置的 LLM 网关（`https://aiaaa.cc/v1`，openai_chat 协议，模型
`deepseek-v4-flash-0731`）对长 prompt（Planner 系统提示词 + schema 摘要）的非流式请求
**稳定触发 60s 读超时**（真实 Planner 提示词复现 3/3 全部 `ProviderTimeoutError`）。

直接原因是现有传输层 `_http_post`（`providers/adapters.py`）用 `http.client` 整包读回：
`resp.read()` 必须等上游把**完整响应体**生成完毕，`PROVIDER_TIMEOUT=60s` 是对"整包读完"
的硬限制；长 prompt + 慢网关下极易触顶。失败后被 `_llm_json` 捕获降级确定性兜底，
且拒答文案无条件建议"配置 LLM 模型"（误导），故障本身不落 audit.jsonl（不可追溯）。

切换 `stream=true` 后超时语义从"整包必须 60s 内读完"变为"不能长时间沉默"：
首包及时到达、后续增量块持续流动，总耗时可以远超 60s 而不触发块间超时。
客户端把 SSE 增量聚合为完整字符串后，照常走既有
`UnifiedChatResponse` → `extract_json` → `_llm_json` 契约。

## 目标与非目标

### 目标

1. providers 适配器传输层支持 SSE 流式：openai_chat / openai_responses / anthropic
   三协议实现，客户端聚合为完整 content，上层调用点（编排 `_llm_json`、agent 管道
   NL2DSL / 反思 / 综合、意图路由）**零改动透明受益**；
2. 流式开关为**供应商级字段**（`providers.json` 每供应商 `stream: bool`，默认 false），
   前端供应商表单可配置；
3. 流式超时语义：块间空闲复用 `PROVIDER_TIMEOUT`，另加全局总时长上限
   `PROVIDER_STREAM_MAX_SECONDS`（默认 300s）防无限流；
4. 移除 Gemini 协议（枚举 / 适配器 / 工厂表 / 前端选项），保留三大主流协议；
5. 流式失败路径全部映射到标准错误族（与现有非流式一致），严禁部分内容当完整内容用。

### 非目标（YAGNI）

- 前端逐字流式 UI（本次聚合在服务端完成，前端仍收完整结果）；
- 调用点级选择性流式（开关只到供应商粒度）；
- gemini 存量配置迁移工具（生产 providers.json 无 gemini 条目，加载时跳过即可）；
- LLM 调用失败落 audit.jsonl / metrics 的可观测性改造（另行立项）。

## 详细设计

### 1. 配置面

**`providers/models.py`**

- `ProviderConfig` 新增字段 `stream: bool = False`（存量配置文件缺省即 false，
  行为完全不变；pydantic 自动兼容）；
- `ApiProtocol` 枚举移除 `GEMINI`。

**`config/settings.py`**

- 新增 `PROVIDER_STREAM_MAX_SECONDS: int = int(os.getenv("PROVIDER_STREAM_MAX_SECONDS", "300"))`；
- 块间空闲超时**复用现有 `PROVIDER_TIMEOUT`**（默认 60s），不新增配置项：
  `http.client` 的 socket 超时对 `resp.readline()` 天然按"距上一块到达的时间"计时，
  正是流式超时的自然语义。

**前端 `web/static`**

- 供应商编辑表单新增"流式"开关（checkbox），供应商列表展示流式标识；
- 协议下拉移除 gemini 选项；
- `/api/providers` 读写契约透传 `stream` 字段。

### 2. 传输层：共享 SSE 读取 + 三协议 delta 提取器（`providers/adapters.py`）

**共享读取器 `_http_post_sse(...)`**

新增生成器函数，与 `_http_post` 平级：

- 入参：`url / payload / headers / timeout（块间空闲）/ max_seconds（总上限）/ api_key`；
- 出参：`Iterator[dict]`——逐条产出 SSE `data:` 行解析出的 JSON 对象；
- 复用 `_validate_outbound_url` 协议白名单校验与 `_build_headers` 鉴权头构造；
- 首包 HTTP 状态非 2xx：读取错误体按现有错误码映射抛错
  （401→AuthenticationError，429→RateLimitError，其余≥400→ProviderError，含 `_brief` 摘要）；
- 循环 `resp.readline()` 解析 SSE：`data: {...}` 产出 JSON，读到 EOF 结束迭代；
  忽略空行与 `event:`/`comment:` 行（三协议 SSE 均兼容，事件类型由消费方按 chunk
  结构自行判别）。读取器自身不判别协议终止帧——终止校验归各协议消费方（见下），
  因为三协议的终止形态不同（`[DONE]` / `response.completed` / `message_stop`），
  Anthropic 流以 `message_stop` 后的正常 EOF 结束、不发 `[DONE]`；
- **总时长上限**：每收到一块检查 wall-clock 累计耗时，超过
  `PROVIDER_STREAM_MAX_SECONDS` 抛 `ProviderTimeoutError("流式总时长超限")`；
- **块间空闲超时**：socket timeout 触发 `TimeoutError` → `ProviderTimeoutError`
  （与 `_http_post` 同映射）。

**错误语义（诚实原则，流式特有）**

- **EOF 未收到协议终止帧（流中途断连）**：抛 `ProviderError`——半截 JSON 比失败更危险，
  绝不把部分内容当完整结果返回。终止帧由各协议消费方校验：
  openai_chat 校验收过 `data: [DONE]`，openai_responses 校验收过 `response.completed`，
  anthropic 校验收过 `message_stop`；EOF 先于终止帧到达即视为断连；
- **mid-stream 错误帧**（`data: {"error": ...}`）：按帧内 code/type 映射标准错误族
  （含 429 特征→RateLimitError），无特征→ProviderError；
- **终止时聚合 content 为空**：抛 `ProtocolError("流式响应未产出内容")`——DeepSeek 系
  网关会把内容放进 `reasoning_content`，本设计只聚合 `content`，空即失败走兜底，不猜。

**三协议 delta 提取（各适配器 `chat()` 内按 `provider.stream` 分支）**

| 协议 | 请求侧 | delta 位置 | usage 位置 | 终止帧 |
|---|---|---|---|---|
| openai_chat | payload 加 `"stream": true, "stream_options": {"include_usage": true}` | `choices[0].delta.content` | 尾部携带 `usage` 的 chunk | `data: [DONE]` |
| openai_responses | payload 加 `"stream": true` | 事件 `response.output_text.delta` 的 `delta` 字段 | `response.completed` 事件 | `response.completed` |
| anthropic | payload 加 `"stream": true`（`max_tokens` 照旧 4096） | `content_block_delta` 的 `delta.text` | `message_start`（input）+ `message_delta`（output） | `message_stop` |

- 聚合完成后**复用各适配器现有 `_build_response`**（JSON mode 解析链、Strict JSON
  注入、usage 构造均不变）；
- 请求侧 JSON Mode 参数（`response_format` / `text.format`）在流式下照常透传，
  被拒时的降级规则见下。

**降级重试（兼容挑剔中转站）**

- 流式请求 400 且报文命中流式特征（`stream`）或现有 `_UNSUPPORTED_JSON_HINTS`：
  自动回退**非流式**重试一次（`stream_options` 单独被拒时仅去掉该参数保流式）；
- 与现有"原生 JSON 参数被拒降级"同哲学：保证自定义中转站最大兼容；
- 回退事件记 `logger.info`（可观测但不告警）。

**保持不变**

- `test_connection` 保持非流式（极小 ping 请求无超时痛点，流式反增复杂度）；
- `UnifiedChatRequest` / `UnifiedChatResponse` 契约零改动；
- `providers/__init__.py` 的 `chat_text` 门面、`context.py` 分发代理零改动
  （`chat` 返回完整 `UnifiedChatResponse` 的接口形态不变）。

### 3. 移除 Gemini 协议

- `providers/models.py`：`ApiProtocol` 删 `GEMINI` 成员；
- `providers/adapters.py`：删 `GeminiAdapter` 类与 `ADAPTER_BY_PROTOCOL` 表项、`__all__`；
- **`providers/store.py` 加载防毒**：供应商条目 `protocol` 校验失败（含历史 gemini）
  时**跳过该条并 `logger.warning`**，不因单枚脏条目拒载整个 providers.json；
- 前端协议下拉删除 gemini 选项；
- 相关测试（GeminiAdapter 单测、工厂表断言）删除或改写。

### 4. 测试与验证

**单测（离线确定性，mock 传输）**

- `_http_post_sse` 解析纯函数化（行序列 → dict 迭代器独立可测），覆盖：
  - 正常聚合多 delta + `[DONE]`；
  - usage 尾包采集；
  - mid-stream 错误帧映射（含 429 特征）；
  - EOF 未收到协议终止帧 → ProviderError；
  - 终止时 content 为空 → ProtocolError（消费方语义）；
  - 块间空闲超时（mock readline 逐块延迟）；
  - 总时长超限；
  - 首包 401/429/400 错误体映射。
- 三协议 delta 提取器各自伪造 chunk JSON 单测（含 anthropic
  message_start/message_delta 的 usage 拼接）；
- 降级重试单测：流式 400（stream 特征）→ 回退非流式成功；`stream_options` 被拒 →
  仅去参保流式重试成功；
- Gemini 移除回归：工厂表无 GEMINI、store 跳过 gemini 条目且告警；
- 存量非流式路径全部测试保持绿（基线 755+）。

**真连通冒烟（手动，非 CI）**

- 对 `aiaa` 网关开启 `stream: true` 后跑真实 Planner 提示词（`_llm_json` 同款入参），
  验证聚合 content 可被 `extract_json` 解析、无超时。

## 兼容性与风险

| 风险 | 缓解 |
|---|---|
| 网关不支持 stream 参数 | 400 特征命中即自动回退非流式（重试一次），行为可预期 |
| DeepSeek 系网关内容进 reasoning_content | 只聚合 content，空即抛错走兜底（诚实降级，可见 warning） |
| 存量配置兼容 | `stream` 默认 false，不开则行为与现状逐字节一致 |
| 无限流拖死服务 | 总时长上限 `PROVIDER_STREAM_MAX_SECONDS` 兜底 |
| 评测确定性 | 流式只改变传输不分片方式，temperature/采样不变，eval 不受影响 |

## 验收标准

1. `aiaa` 供应商开启流式后，真实 Planner 提示词调用成功率恢复（连续 3 次无超时）；
2. 上层代码（core/orchestrator、agent/）零 diff；
3. 流式关闭时全量测试与非流式行为不变（755+ 绿）；
4. 流式异常路径（断连/空内容/超限/网关拒流式）全部映射标准错误族并被上层兜底消化；
5. gemini 协议从前端、枚举、适配器、测试中完全移除，脏配置不拒载。

---

## 技术栈选型对比（复盘矩阵，2026-10-26 补记）

> 本节为 2026-10-26 统一补记的复盘性选型对比：候选与理由是基于本项目约束与生态现状的分析结论，非当时讨论的历史记录。既定要求（用户确认）：后续各期设计文档在设计期即输出选型对比矩阵（样板：二十三期 ClickHouse spec §4）。

### S1 长 prompt 读超时瓶颈的解法：供应商原生 SSE 流式 + 客户端聚合

| 候选 | 超时语义（治本程度） | 改动面与契约相容性 | 故障行为与资源占用 | 结论 |
|------|----------------------|----------------------|----------------------|------|
| **开启 `stream=true` 消费供应商原生 SSE，客户端聚合为完整 content（选定）** | 从"整包 60s 读完"变为"块间不长时间沉默"，总耗时合法地远超 60s | 传输层内聚改造，`UnifiedChatResponse`/`extract_json`/`_llm_json` 契约零改动，上层零 diff | 块间空闲超时 + 总时长上限双保险，悬挂可收敛 | ✅ 直接命中根因（整包读语义误杀慢网关长生成），且 temperature/采样不变，golden 逐字节红线与 eval 确定性不受影响 |
| 仅调大读超时（`PROVIDER_TIMEOUT` 60 → 600） | 整包读语义不变，长尾延迟仍会触顶，只是阈值右移 | 零改动 | 故障悬挂时间整体放大 10 倍，看门狗/空闲预算被迫陪绑 | ❌ 治标不治本：60s 是"整包读完"的语义限制而非时长不够，上一期报告长生成实测已证明调参不可靠 |
| 异步任务化 + 前端轮询/SSE 查询任务状态 | 无同步读超时问题 | 需任务存储、状态机、web 层与前端全链改造，编排契约重构 | 后台任务生命周期管理复杂，泄漏面大 | ❌ 侵入编排与 web 全链路，与"上层零改动"目标背道而驰，复杂度远超痛点 |
| 更换低延迟模型/供应商绕开慢网关 | 换汤不换药，新网关长生成仍可能超 60s | providers.json 配置级切换，零代码 | 不可控于己方 | ❌ 生产网关与模型（deepseek-v4-flash）为既定选型，换供应商属逃避工程问题且有成本与能力回归风险 |

### S2 流式聚合位置：providers 网关层服务端聚合，上层零改动透明受益

| 候选 | 上层改动面 | 契约与 golden 相容性 | 复杂度与范围 | 结论 |
|------|------------|------------------------|----------------|------|
| **网关层聚合：SSE 增量在适配器内拼为完整 content，照常走 `_build_response` → `UnifiedChatResponse`（选定）** | core/orchestrator、agent/ 全部调用点零 diff | 上游契约零改动，JSON mode 解析链/Strict JSON 注入/usage 构造原样复用 | 改造收敛在 adapters.py 传输层一处 | ✅ 与 providers"多供应商网关屏蔽协议差异"的既有定位一致，收益全调用点共享（规划/Coder/反思/综合/NL2DSL） |
| 端到端逐字流式（前端打字机 UI，SSE 透传到浏览器） | 编排节点级流式透传 + SSE 事件协议扩展 + 前端渲染管线改造 | 流式分片使"整响应契约"失效，评测与断言链路需重设计 | 全栈改造，范围失控 | ❌ 本次非目标（YAGNI）明确排除：超时痛点在网关层，前端逐字渲染是另一个议题 |
| 调用点级选择性流式（每个调用点自决是否 stream） | 每个调用点加分支与配置判断 | 同一供应商在不同调用点行为不一致，难以推理 | 配置面爆炸（调用点 × 供应商） | ❌ 开关粒度到调用点无实际收益，行为不可预测，违背配置面最小化 |
| test_connection 也改流式保持"全流式"一致性 | 连通性探测同步改 | 无契约影响 | 极小 ping 请求套 SSE 解析纯属负担 | ❌ 文档已明确保持非流式：探测请求无超时痛点，流式反增复杂度 |

### S3 流式超时语义设计：块间空闲复用 PROVIDER_TIMEOUT + 全局总时长上限

| 候选 | 超时语义正确性 | fail-closed 资源保护 | 配置面成本 | 结论 |
|------|------------------|------------------------|--------------|------|
| **块间空闲 = `PROVIDER_TIMEOUT`（socket timeout 对 `readline()` 天然按"距上一块到达"计时）+ 总上限 `PROVIDER_STREAM_MAX_SECONDS=300`（选定）** | 精确匹配流式本质：慢而持续 = 合法，沉默 = 故障 | 双上限：卡死流被块间超时收割，无限慢流被总上限收割 | 零新增空闲配置（复用既有语义），仅加一个总上限 env | ✅ "不能长时间沉默"正是流式下超时的自然语义，且总上限兜住网关发心跳拖流的病态场景 |
| 仅整包总超时（对整个流式会话设一个 deadline） | 会误杀"慢但持续流动"的合法长生成，退化回整包读语义的老问题 | 有上界 | 一个配置 | ❌ 超时语义与流式价值自相矛盾——流式恰恰是为了让总耗时合法超过单次读超时 |
| 仅块间空闲、无总时长上限 | 块间语义正确 | 网关持续吐心跳/垃圾块时可无限挂起 | 少一个配置 | ❌ 无限流拖死服务的风险无兜底，违背 fail-closed 资源上限铁律 |
| 空闲与总上限各自独立新配置（`STREAM_IDLE_TIMEOUT` + 总上限） | 同选定项 | 同选定项 | 两个新 env，且空闲项与 `PROVIDER_TIMEOUT` 语义完全重合 | ❌ 冗余配置制造"两个 60s 谁生效"的认知负担，文档已明确复用不新增 |

### S4 流中断与不完整内容治理：fail-closed 标准错误族 + 挑剔网关回退重试一次

| 候选 | 数据完整性（诚实=不虚构） | 错误可诊断性 | 兼容性与复杂度 | 结论 |
|------|-----------------------------|----------------|------------------|------|
| **EOF 未收终止帧 → `ProviderError`；终止时 content 空 → `ProtocolError`；mid-stream 错误帧按 code 映射标准错误族；流式 400 命中 stream 特征 → 自动回退非流式一次（选定）** | 半截 JSON 绝不当完整结果，终止帧逐协议校验（`[DONE]`/`response.completed`/`message_stop`），空 content 走兜底不猜 | 全部归一到既有错误族（AuthenticationError/RateLimitError/ProviderError/ProtocolError），上层兜底与日志路径零新知识 | 回退重试与既有"原生 JSON 参数被拒降级"同哲学，仅一次、logger.info 可观测 | ✅ 与"严禁部分内容当完整内容用"及诚实降级原则一致，兼容挑剔中转站 |
| EOF 即视为完成，聚合半截内容返回 | 断连产生的残缺 JSON 喂 `extract_json` 轻则解析失败难定位、重则解析出错误契约 | 故障被伪装成偶发解析失败，无从追查 | 实现最省事 | ❌ 直接违反"半截 JSON 比失败更危险"的 fail-closed 红线，可能静默产出错误 DSL |
| 断连后自动重试多次直至成功 | 最终可得完整内容 | 掩盖网关质量问题；LLM 非幂等（采样），多次重试结果漂移 | 重试延迟与成本不可控 | ❌ 违反受控重试上限与预算约束，且非流式回退一次已覆盖"网关不支持流式"主因 |
| 返回部分内容 + 截断标记，交上层裁决 | 完整性信息显式化，但残缺 JSON 本身仍不可用 | 上层需逐点感知截断态 | `UnifiedChatResponse`/`_llm_json`/编排全链扩散新状态 | ❌ 契约污染全链路，而上层对残缺 JSON 没有任何合理消费方式，复杂度白付 |
