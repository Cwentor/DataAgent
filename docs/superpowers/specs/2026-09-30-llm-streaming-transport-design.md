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
