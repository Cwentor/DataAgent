# 执行中指示器 + 中断检测改造计划

## 问题根因（已定位）

1. **指示太弱**：`workspace.css:277-281` 的 `.chat-thinking::after` 是「10px 实心圆 + 2px 均匀边框」挂 `spin` 动画——**旋转对称图形转起来肉眼无感**；且只有一行小字「正在分析…」，无耗时信息。
2. **中断无感知**：`app.js:659-668` 的 `__stream_end__` 处理不区分「正常完成」与「意外结束」，意外结束时只把 `running` 置 false，**不追加任何时间线条目**——thinking 行凭空消失，用户不知道任务断了。更糟的是 `stream.js` 的 `fetch`/`reader.read()` **没有任何超时**，连接半死（休眠/代理丢包/TCP 半开）时 read 永不 reject，重试逻辑不触发，UI 永久停在"正在分析"。

## 关键设计依据

- 服务端每 `AGENT_RUN_KEEPALIVE_SECONDS`（15s，`settings.py:100`）发 `: ping` 注释帧，由 SSE 订阅线程驱动（`runs.py:344-353` → `server.py:695-698`），**与编排线程无关**——LLM 慢调用（最坏 120s）期间心跳照发。
- 因此前端「120s 收不到任何 chunk」≈ **连接确死**（15s×8 次心跳未达），不误判慢任务。活动信号取 **chunk 到达**（含 ping），而非业务事件。
- 服务端 `runs.py:349` 另有「300s 无编排事件」守卫兜底「连接活着但编排卡死」；两层互补，300s 保持不动（LLM 合法间隔可达 120s，降低会误报）。

---

## 改动清单

### 1. `web/static/js/stream.js` — chunk 级看门狗（核心）
- `open()` 内新增 `lastChunkAt` + 看门狗定时器（`opts.stallTimeoutMs` 可配，默认 120000；传 0 关闭）。
- **活动信号**：`pump()` 的 `reader.read()` 回调入口（`:89`）一有 chunk 就刷新时间戳——`: ping` 心跳同样续命。
- 超时触发：`controller.abort()`（复用 `:118` 早退分支阻止重试）→ 调新增 `opts.onStall()` → 发 `__stream_end__` 收尾。
- 顺手修缺陷：`chunk.done` 分支 `resolveDone()`，让 `handle.done` 在所有终止路径都 resolve（当前只在 `abort()` resolve，`sidebar-ui.js:296` 的兜底是死代码）。

### 2. `web/static/app.js` — 中断上屏与状态收尾
- `streamHandlers(threadId)` 新增 `onStall`：仅活跃会话上屏 → 追加 `kind:"interrupt"` 时间线条目（文案说明 120s 无响应、任务可能在服务端继续、可切回重放）+ `setRunning(false)` + 归档 + toast。
- 新增 `markInterrupted()` 收尾函数（`onStall` 与 `__stream_end__` 共用）：把**未结束的工具块**（`!ended`）与 **plan 中 pending 步骤**标记为中断，避免永久停留"执行中/等待"。
- `__stream_end__` 分支增强：若 `running` 仍为 true 且末条不是 done/error → 视为意外结束，补一条中断条目（覆盖重试耗尽后的静默消失）。

### 3. `web/static/js/stream-ui.js` — 显著 spinner + 耗时计时
- `elThinking()`：改为真 spinner（缺口旋转圆环）+「正在分析」+ **已执行秒数**（如 `12s`），秒数持续增长是最直观的"还活着"信号。
- 模块级 ticker（1s）：随 `running` 启停；因 `render()` 每帧重建 thinking 节点（`:409-412`），ticker 每 tick 重新 `querySelector('[data-chat-thinking]')` 再写秒数。
- `elItemFor` 新增 `kind:"interrupt"` 分支 + `EST_H` 补高度（`elItemFor` 返回 null 会让 `reconcile` 崩，必须同步补）。
- `updateToolBlock` 的中断收尾复用既有 `[data-tool-id]:not([data-ended])` 选择器。

### 4. `web/static/workspace.css` — 视觉
- `.chat-thinking::after` 改为**真 spinner**：`border:2px solid var(--accent-soft); border-top-color: var(--accent)`（复用既有 `@keyframes spin`，`:396`），尺寸调至 13px，对齐时间线竖线（`::before`，`:273-276`）。
- 新增 `.chat-thinking .elapsed`：`color: var(--faint); font-size:11.5px; font-variant-numeric: tabular-nums`（数字等宽不跳动）。
- 新增 `.chat-interrupt`：警示色卡片（复用 `--danger-soft`/`--danger` 配色与 `.act-err` 视觉语言），延续时间线左竖线。

### 5. 后端（小幅加固，不改默认行为）
- `web/runs.py:349-350` 的硬编码 300 改为 `settings.AGENT_RUN_IDLE_TIMEOUT_SECONDS`（默认 300），`RunRegistry.__init__` 接受参数以便测试注入（沿用既有 `max_runs`/`ttl_seconds` 模式）；文案中的 "300s" 一并参数化。

---

## 测试与验收

- **后端**：`tests/test_web_runs.py` 新增 idle 超时用例（monkeypatch 时钟，仿 `test_query_cache.py:35` 的打桩范式）；确认既有 22 项全绿。
- **前端逻辑**：用 Node + 最小桩验证 stall 看门狗（chunk 续命 / 超时触发 onStall / abort 清理）与 interrupt 条目渲染，仿上一轮 `store.js`/`canvas.js` 的验证方式。
- **浏览器手工验证**：
  - 正常链路：确认 spinner 在转、秒数在涨、`: ping` 持续续命不误报（跑一轮长任务观察 >120s）；
  - 中断链路：DevTools 把 `/api/v1/agent/chat/stream` 设为 offline / suspend，确认 120s 后时间线出现中断卡、thinking 行消失、按钮恢复"开始分析"、未结束工具块被标记中断。
- **提交门槛**：`black --check .`、`ruff check .`、`python -m pytest -q` 全绿。

## 非目标（本期不做）
- 不降低服务端 300s 编排空闲阈值（LLM 合法间隔可达 120s，降低会误报）；
- 不修后端三个已知隐患（`LLM_TIMEOUT`/`PROVIDER_TIMEOUT` 死配置、`exec/pool.py` 连接池 `get()` 无超时、`export_to_parquet` 无预算）——它们是独立的可靠性问题，建议另开任务。
