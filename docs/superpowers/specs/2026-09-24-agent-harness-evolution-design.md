# 设计文档｜Agent 架构演进（十七期）：底座迁移 + Plan Mode + Subagent fan-out（2026-09）

## 一、背景与决策记录

### 1.1 驱动力

本轮迭代的首要驱动力是**架构完整性**：对标 DSH / Claude Code / MimoCode / pi-agent-harness
的 harness 级完整度，使 DataAgent 成为可长期演进的系统底座，并承接 Agent 后续功能迭代
带来的**复杂任务要求与负荷**（长任务、多步规划、多智能体协作）——"规模化"指任务复杂度
增长而非服务部署扩展。

### 1.2 关键决策（头脑风暴澄清结论）

| 决策点 | 结论 |
| --- | --- |
| 首要驱动力 | 架构完整性（harness 级底座），非面试展示、非单点生产刚需 |
| 技术选型约束 | **解除"零框架依赖"铁律**：按收益与未来演进需要，允许引入成熟开源框架 |
| "规模化"含义 | Agent 承接的任务复杂度增长（长任务/多步/多智能体），非服务横向扩展 |
| 本轮范围 | **底座迁移 + 自主性线（A）+ 多智能体线（B）**；生态（Skills/Hooks/MCP）、上下文工程、主动性立后续里程碑 |
| 方案选型 | 方案一：LangGraph 迁移 + FastAPI + 原生原语构建 A/B 线（详见 §2） |

### 1.3 四个 Harness 的借鉴映射

| Harness | 借鉴的架构逻辑 | 本轮落点 |
| --- | --- | --- |
| DSH | interrupt/Command HITL、subagent 上下文隔离与 fan-out、结构化提问 | A 线审批门、B 线 SubagentRuntime |
| Claude Code | Plan Mode（设计先行审批门）、permission modes 自主性分级 | A 线 |
| MimoCode | 上下文预算与压缩意识 | 后续上下文工程线（本轮仅预算护栏） |
| pi-agent-harness | 复杂度分诊（triage）、run manifest 审计轨迹 | 审批门触发条件、子任务轨迹 |

## 二、方案对比与选型

| 方案 | 内容 | 结论 |
| --- | --- | --- |
| **方案一（选定）** | LangGraph 迁移 + FastAPI，在其原生原语（interrupt/Send/Checkpointer/subgraph）上构建 A/B 线，领域内核零改动 | 复杂任务所需的四大原语全部白拿；README 预留的"接口保留迁移路径"兑现；迁移是平移而非重写 |
| 方案二 | 自研 StateGraph 自补 checkpointing/interrupt/Send/subgraph | 在重造 LangGraph 打磨多年的轮子，与"允许成熟框架"决策相悖，弃 |
| 方案三 | asyncio 消息循环内核（pi 风格）彻底重构 | 丢弃已验证的六节点图资产（678 测试），风险最大，弃 |

## 三、总体架构与迁移原则

### 3.1 迁移后分层全景

```
┌─────────────────────────────────────────────────────────────┐
│  L1 交互层  FastAPI + uvicorn（asyncio）                     │
│      端点路径/认证/SSE 九类事件契约 —— 全部保持，前端零改动    │
├─────────────────────────────────────────────────────────────┤
│  L2 编排层  LangGraph StateGraph（迁移自自研图）              │
│      六节点语义原样平移 + interrupt 泛化 + Send fan-out      │
│      + SqliteSaver Checkpointer（长任务断点续航）             │
├─────────────────────────────────────────────────────────────┤
│  L2.5 子智能体层（新增）                                      │
│      SubagentRuntime：受限任务卡 + 工具子集白名单            │
│      + 独立 scratchpad + 结构化报告回传                      │
├─────────────────────────────────────────────────────────────┤
│  L3-L7 领域内核 —— 零改动                                     │
│      agent/（NL→DSL）· core/retrieval · core/sandbox         │
│      core/skills · semantic/ · compiler/ · exec/ · security/ │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 三条迁移原则（设计纪律）

1. **引擎替换，语义保持**：只替换图引擎与 HTTP 引擎。六节点业务语义、防御栈、自愈
   语义原样平移。验收标准：oracle 25/25 不动、678 单测全绿、SSE 九类事件逐字节兼容。
2. **契约不放松**：`AgentState` 保持 `extra="forbid"`（LangGraph 原生支持 Pydantic
   state schema）；新增字段（subagent 报告、自主性偏好、计划审批状态）一律契约登记；
   依赖方向铁律不变（orchestrator → retrieval/sandbox/skills/agent）。
3. **新增能力皆可降级**：Plan Mode 审批门、subagent fan-out 在离线/无 Key/低配环境
   必须可关闭并回落到现有单图路径（延续"确定性兜底"哲学）。

### 3.3 不动清单

| 组件 | 理由 |
| --- | --- |
| DSL 契约 / 确定性编译器 / catalog SSOT | 铁律核心，与编排引擎无关 |
| 防御栈（网关/审计门/熔断/RLS/DataQA） | 挂在工具与执行层，不在图引擎 |
| `core/sandbox`、`core/skills` 算法 | 领域资产，编排层只是调用方 |
| 前端工作台（原生 JS） | SSE 契约不变则零改动，仅做审批卡片等增量 UI |

## 四、底座迁移设计

### 4.1 图引擎映射表

| 自研机制 | LangGraph 等价物 | 迁移成本 |
| --- | --- | --- |
| `add_node(name, fn)`，fn(state)→新状态 | 同名 API，Pydantic state 原生支持 | 节点函数零改动 |
| `add_conditional_edges(from, router, map)` | 同名同参 API | 路由函数零改动（继续读 `state.phase`） |
| `phase=="clarify"` 特例中断 | `interrupt()` + `GraphInterrupt` | 泛化：clarify 与审批门共用一套机制 |
| `resume(state + human_reply)` | `invoke(Command(resume=payload), config)` | web 层 resume 端点传 Command |
| `iteration > 24` 护栏 | `config={"recursion_limit": N}` | 语义等价（超级步计数，需校准，见 §8） |
| 无持久化 | `SqliteSaver` Checkpointer | 新增能力（长任务断点续航） |
| 无 fan-out | `Send` API | B 线新增能力 |

### 4.2 关键设计决策

1. **事件总线不动**：保留自研 `events.py` 九类事件发射点（节点内 emit），不切换到
   LangGraph stream 模式。SSE 契约逐字节兼容，前端零改动。LangGraph 原生流式列为后续增强。
2. **状态合并语义保持**：平移初期不引入 reducer（行为与现状完全一致）；唯一例外是 B 线
   subagent 报告字段使用 append reducer（`Annotated[list, operator.add]`）。
3. **thread_id 约定**：`f"{user_id}:{session_id}"`，与 session bleeding 防护对齐；
   子任务线程 `f"{user_id}:{session_id}:sub:{task_id}"`。
4. **存储分工**：SQLite 会话记忆（last_dsl 继承）与 SqliteSaver 图执行快照各司其职，
   互不替代。

### 4.3 三阶段双引擎迁移路径（可回退）

```
阶段 0  依赖引入 + AgentState 平移为 LangGraph 可编译 schema（extra="forbid" 保持）
        + 六节点图同构编译（节点/路由函数零改动）
阶段 1  config 开关 ORCHESTRATOR_ENGINE = "native" | "langgraph"
        评测双跑：oracle 25/25 + agent 模式全量对比，SSE 事件流逐类 diff
阶段 2  FastAPI + uvicorn 替换 ThreadingHTTPServer：
        - 端点路径/认证语义/SSE 契约全部不变
        - 同步编排器经 run_in_threadpool 调度（LLM 与 DuckDB 均为阻塞 IO）
        - `python -m web.server 8000` 命令保持兼容（内部 uvicorn.run）
阶段 3  验收通过（678 测试 + 评测双绿）→ 删除旧引擎与开关，单引擎收敛
```

回退保障：阶段 1/2 期间旧引擎始终可用（开关一键回退），直到阶段 3 收敛。

## 五、A 线：Plan Mode 审批门 + 自主性分级

### 5.1 审批门交互流（复杂度分诊触发）

触发条件：plan 节点产出多步 DAG（`len(plan_steps) > 1` 或含 `analyze` 步骤）；单步直连
查询不触发。L1 模式强制触发。

```
plan 节点产出 PlanStep DAG
    → interrupt({kind: "plan_review", plan_steps, summary})
    → SSE hitl_request 事件（payload 扩展 kind 字段，向后兼容）
    → 前端渲染「分析计划审批卡片」：步骤 DAG 可视化 + 每步目标
    → 用户三选一：
       ① 批准 → resume={"action": "approve"} → 进执行
       ② 修改 → resume={"action": "edit", "instruction": "..."} → 回 plan 重规划
                （用户指令注入提示词，与 error_context 同一注入位）
       ③ 拒绝 → resume={"action": "reject"} → 终止并如实报告，不产出
```

审批门复用 `interrupt()` 泛化机制：clarify 与 plan_review 是同一中断原语的两个 kind，
图结构不分叉。挂起期间状态由 Checkpointer 持久化——服务重启后审批门仍在（自研图做不到
的能力增强）。

### 5.2 自主性分级（四档，对标 permission modes）

| 等级 | 行为 | 适用场景 |
| --- | --- | --- |
| L1 每步确认 | 每个关键节点执行前 interrupt | 演示、初次信任建立 |
| L2 计划确认（默认） | 仅 plan 后审批一次；执行自动，clarify 照常 | 日常交互分析 |
| L3 高危确认 | 计划自动批准；仅高危操作前中断（导出/大扫描/跨域） | 受信用户提效 |
| L4 全自动 | 零中断，全部降级为通知事件 | 定时任务、批处理 |

实现：单一图结构 + `maybe_interrupt(policy, payload)` 帮助函数——按会话级 autonomy_level
决定真中断还是带默认值直通；L4 下审批门降级为 plan_created 事件的富化载荷。会话偏好随
会话存储持久化。

### 5.3 SSE 契约扩展（向后兼容）

`hitl_request` payload 增加 `kind: "clarify" | "plan_review"` 字段——旧前端忽略新 kind
不崩溃，新前端按 kind 渲染审批卡片。前端增量 UI 两处：审批卡片 + 侧栏自主性设置。

## 六、B 线：Subagent 运行时与 fan-out 编排

### 6.1 任务卡与报告契约

对标 DSH/Claude Code 的 subagent，但针对数据域做**上下文切片**改造（数据 Agent 的上下文
是 schema/DSL，切片比继承更有效——subagent 不见父会话全史、不见兄弟中间状态）。

- `SubagentTask`（extra="forbid"）：task_id / goal / **context_slice**（schema digest 片段、
  相关过滤条件、上轮 DSL 引用）/ **allowed_tools**（工具子集白名单，从主注册中心取子集）/
  budget（步数 ≤6 / token / 超时）。
- `SubagentReport`（extra="forbid"）：status（done/failed/timeout）/ findings（结构化
  摘要，父图唯一消费物）/ artifacts / **audit 与 ParquetRef 同源同格式** / metrics。

### 6.2 内核形态：四节点受限子图

Subagent 刻意不做 clarify（不能反问用户）、不做 synthesize（报告权在父图）。内核 =
`plan_task → query → analyze → summarize` 四节点子图，复用主图节点函数与全部防御栈。
子图与主图共享 Checkpointer（trace 贯穿可回放），强制 L4 自主性（子图内零中断）。
无 LLM 时降级为确定性单步取数——离线可运行可单测。

### 6.3 三种 fan-out 编排模式

1. **并行归因（map-reduce）**：异动分析按维度拆分（区域/品类/渠道）→ Send × N →
   reducer 汇总 → critique 充分性检查 → synthesize 综合报告。
2. **多假设并行验证**：候选假设（数据质量/结构性/外部因素）并行取证据 → critique 裁决。
3. **对比型问题升级**：现有"对比分解与跨步综合"从串行升级为并行子任务 + 汇总综合。

### 6.4 并发控制与失败语义

- 并发上限默认 4（config 可调）：兼顾 LLM 限流与 DuckDB 单文件写锁。
- 预算硬顶：步数 ≤6、token 预算、超时复用现有看门狗；超限报 timeout。
- **失败不炸主图**：单 subagent 失败回传 failed 报告；critique 新增"子报告充分性检查"，
  决定重派（≤1 轮）或降级单线程补做；重派仍失败则在报告层如实披露缺口（诚实守卫延续）。
- 可观测：子任务 tool_start/tool_end 带 task_id 汇入主 SSE 流，前端时间线按子任务分组；
  全部子任务轨迹随 done 事件构成 run manifest。

## 七、错误处理与自愈语义映射

| 现有机制 | 迁移后 | 备注 |
| --- | --- | --- |
| query 失败 → error_context.record() → 条件边回 plan（重规划 ≤3） | 原样平移 | 节点函数零改动 |
| 编译/引擎报错 → SQL 自愈重写（SQL_SELF_HEAL_MAX_RETRIES） | 不动 | 工具层机制 |
| iteration > 24 护栏 | recursion_limit（初始 64，M0 校准） | 超级步语义差异需校准 |
| clarify 挂起无超时 | Checkpointer 持久化挂起，重启后仍在 | 能力增强 |
| 节点异常透传终止 | 原样平移（节点内 try/except 不变） | 行为一致 |

## 八、测试与评测策略

- **迁移验收门**：678 单测全绿 + oracle 25/25 + agent 模式评测零回退 + SSE 九类事件
  逐类 diff 为空。
- **双跑对比**：M0/M1 期间双引擎跑 `eval.eval_runner` 双模式，输出对比表。
- **新增测试**：interrupt/resume 三分支往返 · L1-L4 行为矩阵 · checkpointer 重启恢复 ·
  fan-out 结果合并的顺序无关确定性（种子固定）· subagent 失败/超时/重派语义 · 并发上限护栏。
- **评测扩展（推荐）**：Golden 集新增 2-3 个复杂多步用例（并行归因类），作为 A/B 线验收锚点。
- **前置审计项**：`events.py` 线程安全（FastAPI 多 worker 下确认）；`exec/` 连接池在
  subagent 并发读下的安全性。

## 九、依赖与版本锁定

- 运行时新增：`langgraph`、`langgraph-checkpoint-sqlite`、`langchain-core`（被动）、
  `fastapi`、`uvicorn`；开发依赖新增 `httpx`（TestClient 底层）。
- **0.x 生态版本迭代快，全部精确 pin**（`langgraph==x.y.z`），升级走独立 PR + 全量回归门。
- README 选型哲学章节同步改写：从"零框架依赖"演进为"**领域内核零框架（4 依赖不变）+
  编排/交互层拥抱成熟框架**"。

## 十、里程碑

```
M0 底座同构：依赖引入 + AgentState 平移 + 六节点图 LangGraph 编译（feature flag 双引擎）
M1 交互升级：FastAPI 替换 + SSE 契约回归 + resume 端点 Command 化
M2 A 线落地：Plan Mode 审批门 + hitl_request kind 扩展 + 前端审批卡片 + 自主性分级
M3 B 线落地：SubagentRuntime（任务卡/四节点子图/预算）+ Send fan-out + critique 充分性检查
M4 验收收敛：双跑对比 + 评测扩展 + 删除旧引擎与开关 + 文档全面更新（roadmap 十七期）
```

依赖关系：M0 → M1 → {M2, M3（可并行）} → M4。

## 十一、风险清单

| 风险 | 缓解 |
| --- | --- |
| LangGraph/langchain-core 0.x API 不稳 | 精确 pin 版本；升级独立 PR + 全量回归门 |
| recursion_limit 与现有 iteration 护栏语义差异 | M0 校准 + 单测锚定 |
| FastAPI 并发下事件总线线程安全 | 前置审计 events.py，必要时加锁/换线程安全队列 |
| DuckDB 连接池 subagent 并发竞争 | 前置审计 exec 连接池；并发上限 4 兜底 |
| Pydantic state 频繁 validate 开销 | tool_results 已有 60k 字符预算修剪，保持 |

## 十二、后续里程碑展望（本轮不做，立档备忘）

- **C 生态线**：Skills SOP 打包（方法论 = 提示词模板 + 编排模板 + 工具组合，按意图动态
  加载）、Hooks 生命周期（pre/post query 钩子）、受控 MCP 网关（外部工具过 args_schema
  校验 + 审计，不破坏白名单铁律；可评估 langchain-mcp-adapters）。
- **D 上下文工程线**：长会话压缩（摘要化）、工作记忆/情景记忆/语义记忆分层、token 预算管理。
- **E 主动性线**：定时巡检（指标异动自动检测 + 预警报告）、订阅式报告、proactive agent。
- **LangGraph 原生流式**：stream_mode 升级（token 级流式输出）作为事件总线的后续增强。
