# 架构与设计

## 架构总览

DataAgent 在"受控 DSL → 确定性 SQL"核心链路之上，叠加图式编排、沙箱分析、归因技能与多模型供应商网关，构成企业级 Data Agent：

```mermaid
flowchart LR
    U[用户自然语言] --> WEB[Agent 对话流工作台 / SSE 流式]
    WEB --> ORCH{编排器<br>LangGraph 六节点}
    ORCH -->|规划 + 枚举 profiling| AG[意图路由 +<br>LLM / 启发式 Agent]
    ORCH -->|取数| QRY[受控查询链路<br>执行前审计 + 结果断言]
    ORCH -->|分析| SBX[沙箱代码解释器]
    ORCH -->|归因| SKL[技能包<br>下钻 / 分解 / 时序 / Shapley]
    ORCH -->|综合 + 质检小节| PRES[商业分析师报告<br>与可视化推荐]
    AG --> DSL[QueryDSL 契约]
    QRY --> SCOPE[生成前最小权限作用域]
    SCOPE --> GUARD[权限守卫]
    GUARD --> CMP[确定性 SQL 编译器]
    CMP --> GA[GuardrailAgent<br>执行前审计门]
    GA --> EXEC[执行层资源治理与自愈]
    EXEC --> DB[(DuckDB)]
    DB --> QA[DataQAAgent<br>结果断言]
    QA --> RET[PII 脱敏 + ParquetRef<br>audit 审计面]
    RET --> SBX
    LLMGW[多模型供应商网关<br>providers/] -.-> AG
    AUD[审计与指标] -.-> WEB
```

编排器的六节点为：澄清 HITL → 规划 → 受控取数 → 沙箱分析 → 反思重规划（≤3 次受控自愈）→ 综合报告；计划为步骤 DAG，全过程经 SSE 以九类事件实时推送。重规划时最近失败摘要（error_context）注入 Planner 提示词——自愈是针对性修正而非盲重试。

**意图路由收敛与诚实兜底（十八期）**：澄清判定权上收 Planner（clarification 契约，支持选项式反问）；兜底执行准入制——仅诊断、基数（如「有多少个省份」）与硬锚定指标三类意图可确定性直答（附缺省口径说明），其余意图置 `blocked_reason` 走诚实拒答报告（原因 + 已识别锚点 + 能力清单，全程零 LLM 调用）；L3 意图-DSL 错位守卫在执行前拦截「基数意图 + 金额聚合」式错位查询；意图词表以 `FieldMeta.aliases` 为单一事实源。

**枚举预路由确定性渲染（二十期）**：ENUMERATION 意图在编排入口即预路由为确定性渲染路径——全量行清单直答（超过阈值时截断分流），完全绕过 LLM 调用链路，零幻觉零延迟；降级水印区分「枚举预路由直答」与「LLM 故障降级」两种场景，用户侧文案分流可见。

## 三层同心圆取数架构（十九期）

能力边界 = 治理管道边界（只读 + RLS + 敏感数据保护 + 资源上限），不是 DSL 契约表达力。LLM 产出的 SQL 永不直接执行：L1 确定性核（Planner 直产 DSL）覆盖常规问数；L2 提升闸门（`core/retrieval/sql_lift.py`，sqlglot）把超契约 SQL 转译回 DSL 契约由确定性编译器重编译——拒升 ≠ 拒答，精确清单喂回自愈 ≤2 次；L3 探索层（`core/retrieval/exploration.py`）把不可提升 SQL 经第四类审批门（allow_once / allow_session / deny）后在按 principal 生成的安全视图（`security/views.py`，禁列物理投影 + RLS 固化 + 连接加固）上受治理执行，报告醒目标注"探索查询产出"。分级透明作答：诚实 = 不虚构 + 假设透明（`assumptions` 契约字段，报告头部呈现），二轮歧义转带假设作答，拒答降为最后手段。沙箱 `connect/database` 调用永久封死——取数权只在执行层。

**语义单一事实源（二十期 M-P2）**：`config/semantic.json` 是唯一静态业务事实源——字段/标签/别名/连接规则/指标口径（metrics[]，含 DSL shape）/支付口径（paid_filter）/缺省窗口/反思概念/枚举值标签/大区映射/维度成员种子等一律登记 json，`semantic/catalog.py` 为 import 时 json 直读（缺必要节快速失败），严禁在手写代码（heuristic/nodes/glossary/prompts/labels）里新增业务映射。

## 多角色架构对齐（pi-agent-harness）

对标 pi-agent-harness 的多角色团队架构（5 核心角色 + Chain/Evaluator-Optimizer 编排），DataAgent 的角色落位如下。与 Harness 的差异点：**安全裁决与结果质检由确定性代码承担而非 LLM**——符合"LLM 仅产 DSL"铁律，结论可复现、可单测、零幻觉。

| 角色 | 职能 | DataAgent 实现 | 核心文件 |
| --- | --- | --- | --- |
| SchemaAgent | 元数据探查 + 动态 profiling | 语义目录 SSOT 静态注入 + 低基数字段枚举值探查（进程级缓存、失败降级） | `semantic/catalog.py`、`core/retrieval/profiling.py` |
| SqlEngineer | 方言感知只读 SQL 生成 | Planner 产 DSL 契约 JSON → 确定性编译器生成 SQL（LLM 永不产出裸 SQL，强于基线） | `core/orchestrator/nodes.py`、`compiler/sql_compiler.py` |
| GuardrailAgent | 执行前安全与执行计划审计 | 裸 SQL 网关四层防线 + 执行前静态审计（笛卡尔积/只读结构 REJECTED、无界输出 WARNING）+ EXPLAIN ANALYZE 扫描熔断 | `core/retrieval/guardrails.py`、`exec/audit.py`、`exec/guards.py` |
| DataQAAgent | 结果断言与数据质量质检 | 四类确定性质检（空结果/NULL 率/负值/维度唯一性），双取数链路同源 | `core/retrieval/quality.py`、`tools/builtins/_query_core.py` |
| VizAgent | 图表渲染与叙事 | 确定性图表推荐（number/line/bar/pie/pivot/table + ECharts option 契约）+ 商业分析师四段式报告 | `present/viz.py`、`core/orchestrator/nodes.py`（synthesize） |
| Orchestrator | Chain-of-Delegation 主编排 | LangGraph 条件边（chain 委托）+ critic 重规划 ≤3（evaluator-optimizer 有界修复）+ interrupt 泛化审批门（clarify / plan_review）+ L1-L4 自主性分级 + Subagent fan-out + 事件总线审计轨迹 | `core/orchestrator/` |

## 分层职能架构图

```mermaid
flowchart TB
    subgraph L1[交互层 web/]
        WEBUI[Agent 对话流工作台<br>对话流活动时间线 + 顶部 Tab 产物视图（按会话绑定）]
        SSE[SSE 流式端点<br>九类事件实时推送]
        API[查询 / 编排 / 指标 API]
    end

    subgraph L2[编排层 core/orchestrator]
        CLARIFY[clarify 澄清 HITL]
        PLAN[plan 规划<br>LLM JSON 计划 + 启发式兜底<br>+ 自愈错误上下文]
        QUERY[query 受控取数调度]
        ANALYZE[analyze 沙箱分析调度]
        CRITIC[critique 反思<br>三检 + LLM Reflector<br>重规划 ≤3 次]
        SYNTH[synthesize 综合报告<br>四段式叙事 + 质检小节]
    end

    subgraph L3[智能层 agent/ + core/skills]
        ROUTER[意图路由 + 槽位回填]
        NL2DSL[NL → DSL 双路径<br>LLM 严格校验 / 启发式]
        HEAL[SQL 自愈重写器]
        SKILLS[归因技能包<br>熵下钻 / 分解树 / DTW / Shapley]
    end

    subgraph L4[数据访问层 core/retrieval + tools/]
        GW[裸 SQL 网关守卫<br>GuardrailViolation]
        PROF[动态 profiling<br>低基数字段枚举探查]
        TOOL[typed Tool<br>execute_dsl_query → ParquetRef]
        EXPORT[PII 脱敏 + Parquet 物化]
    end

    subgraph L5[语义与编译层 semantic/ + compiler/]
        CATALOG[字段白名单目录<br>SSOT]
        DSLC[QueryDSL 契约<br>extra=forbid]
        CMP[确定性 SQL 编译器<br>窗口 / 补零 / TopN / 同环比]
    end

    subgraph L6[执行与治理层 exec/ + security/ + audit/]
        GAUD[执行前审计门<br>笛卡尔积 REJECTED<br>无界输出 WARNING]
        EXECG[资源护栏<br>超时中断 / 扫描熔断 / LIMIT 上限]
        QA[DataQA 结果断言<br>空结果 / NULL 率 / 负值 / 唯一性]
        RLS[表级 / 列级 / 行级 RLS]
        OBS[审计快照 + 结构化日志 + 指标]
    end

    subgraph L7[存储与沙箱层]
        DB[(DuckDB 数仓)]
        SBX2[沙箱代码解释器<br>AST 守卫 + 限权 runner<br>子进程默认 / Docker 可插拔]
        PQ[(ParquetRef<br>workspace/inputs)]
    end

    WEBUI --> SSE --> L2
    API --> L2
    CLARIFY --> PLAN --> QUERY --> ANALYZE --> CRITIC --> SYNTH
    QUERY --> GW --> CMP
    PLAN -.->|枚举值注入| PROF
    GW --> DSLC --> CATALOG
    CMP --> GAUD --> EXECG --> DB
    DB --> QA --> EXPORT --> PQ --> SBX2
    SBX2 --> SYNTH
    NL2DSL --> CMP
    HEAL -.->|重写 DSL| NL2DSL
    RLS --> GW
    OBS -.->|request_id 贯穿| L1
```

## 编排状态机（Chain-of-Delegation）

```mermaid
stateDiagram-v2
    [*] --> clarify
    clarify --> plan: 问题明确
    clarify --> clarify: HITL 中断<br>等待用户补充（resume 恢复）
    plan --> query: 计划含取数步骤
    plan --> analyze: 纯分析计划
    query --> analyze: 取数成功
    query --> plan: 取数失败（自愈）<br>错误上下文喂回 Planner
    analyze --> critique: 分析完成
    analyze --> plan: 沙箱失败（自愈）
    critique --> plan: 重规划（≤3 次<br>evaluator-optimizer 有界修复）
    critique --> synthesize: 三检通过 / 额度耗尽如实报告
    synthesize --> [*]: phase=done

    note right of query
        执行前审计门（GuardrailAgent）：
        笛卡尔积/只读违规 REJECTED 熔断
        REJECTED 原因进入自愈上下文
    end note
    note right of critique
        反思层决策：
        完整性 / 正确性 / 现实一致性
        LLM Reflector 可选增强
    end note
```

## 受控查询防御管线

每一次取数（编排链路与 web 链路同源）经过的完整防线序列与失败处置：

```mermaid
flowchart LR
    DSL[QueryDSL 载荷] --> V1[网关校验<br>裸 SQL / 非法形态 / 契约重建<br>失败: GuardrailViolation]
    V1 --> V2[RLS 策略注入<br>表/列/行级]
    V2 --> V3[确定性编译<br>字段白名单 + 受限操作符<br>失败: CompileError]
    V3 --> V4[执行前审计门<br>笛卡尔积/只读结构 REJECTED<br>无界输出 WARNING]
    V4 --> V5[EXPLAIN ANALYZE 预检<br>扫描行数熔断<br>失败: MaxRowsScannedExceeded]
    V5 --> V6[受控执行<br>超时中断 + LIMIT 硬上限]
    V6 --> V7[DataQA 结果断言<br>空结果 / NULL 率 / 负值 / 维度唯一性<br>发现分级留痕 + 随产物输出]
    V7 --> V8[PII 脱敏 + Parquet 物化<br>sha256 可审计]
    V8 --> REF[ParquetRef<br>audit={guard, qa} 审计面]

    V3 -.编译/执行报错.-> HEAL[SQL 自愈重写<br>错误喂回 LLM 改写 DSL<br>上限 SQL_SELF_HEAL_MAX_RETRIES]
    V5 -.熔断原因.-> HEAL
    HEAL -.重写后重过全链路.-> V1
```

**审计发现的数据流**：`ParquetRef.audit` → ① SSE `tool_end` 事件（前端数据审计 Tab）→ ② 编排 scratchpad（进入 critic 反思视野）→ ③ synthesize 报告"数据质检"小节（用户可见）→ ④ 结构化日志分级采集（采集器告警）。QA 发现不自动否决执行——空结果可能是合法答案，处置权在 critic 与报告层（不误杀、不吞错）。

## 核心原则

- **受限生成**：LLM 只输出契约内 JSON；Pydantic 模型使用 `extra="forbid"`。
- **确定性编译**：SQL 只由 `compiler/sql_compiler.py` 生成，不接受模型直接提交裸 SQL。
- **裸 SQL 三重防线**：字符串载荷 / SQL 键 / 自由文本 SQL 形态在网关层直接拒绝（`GuardrailViolation`），"LLM 永不产出裸 SQL"贯穿主链路与编排层。
- **语义白名单**：逻辑字段必须登记在 `semantic/catalog.py`，Join 由目录规则控制。
- **纵深防御**：生成前注入主体可见字段，生成后再经过表级、列级与行级策略校验。
- **执行前审计门**：编译产物 SQL 在执行前经静态审计（笛卡尔积 / 只读结构 REJECTED 熔断、无界输出 WARNING 轨迹）——审计作为编译器不变式防御，REJECTED 原因进入自愈上下文。
- **结果断言层**：执行后、展示前对原始结果做四类确定性质检，发现随 ParquetRef 审计面全链路可见（事件 / critic / 报告 / 日志），不自动改写路由。
- **受控自愈**：编译/执行报错与审计拒绝喂回重写（web 链路 ≤ `SQL_SELF_HEAL_MAX_RETRIES`，编排链路重规划 ≤3）；重规划时错误上下文注入 Planner——自愈是针对性修正而非盲重试。
- **沙箱隔离**：沙箱代码解释器经 AST 静态守卫 + 限权 runner（模块白名单 import、workspace 受限 open）+ 可插拔后端（默认子进程，可插拔 Docker `--net=none --cap-drop=ALL` 强隔离）执行；取数结果 PII 脱敏后物化为 ParquetRef（sha256 可审计），沙箱零网络、零 DB socket，聚合矩阵 ≤100 行防数据外泄。
- **可追溯交付**：查询结果同时提供 DSL、SQL、中文解释、可视化建议、审计记录与数据质检发现。

## 技术栈

| 组件 | 选型 |
| --- | --- |
| 语言 | Python 3.11+（项目环境使用 Python 3.12） |
| 数据校验 | Pydantic V2 |
| 本地数仓 | DuckDB |
| 图式编排 | LangGraph 单引擎（十七期 M4 收敛）：六节点 + interrupt 泛化 + checkpointer（`ORCHESTRATOR_CHECKPOINT_DB` 可选 SQLite 落盘） |
| 静态审计 | sqlglot AST（只读结构 / 笛卡尔积 / 无界输出） |
| 沙箱后端 | 子进程默认 + Docker 强隔离可插拔（`--net=none --cap-drop=ALL`） |
| 模型接入 | 多供应商网关：OpenAI Chat / Responses、Anthropic、Gemini 协议适配 |
| Web | FastAPI + uvicorn（十七期 M4 单引擎收敛）+ 原生 JS Agent 对话流工作台（vendored ECharts / PrismJS，零前端框架） |
| 质量 | pytest、black、ruff、Golden Dataset |

## 全链路能力

| 层级 | 关键能力 | 入口 |
| --- | --- | --- |
| 语义 | 聚合、比率、时间过滤、窗口指标、日期补零、分组 Top-N | `semantic/` |
| Agent | LLM / 启发式双路径、意图路由、RAG、澄清与多轮槽位回填、观察驱动重规划、对比分解综合、反思层、自愈错误上下文注入 | `agent/` |
| 诚实兜底 | 意图分类器（`FieldMeta.aliases` 词表硬匹配）、兜底准入制与诚实拒答、L3 意图-DSL 错位守卫、Grounding 定向重试闭环（重写 1 次 → 降级确定性渲染）、选项式澄清 | `core/orchestrator/intent.py`、`grounding.py`、`nodes.py` |
| 编排 | LangGraph 单引擎六节点、步骤 DAG 计划、interrupt 泛化（clarify / plan_review / high_risk 审批门）、L1-L4 自主性分级（L3 含沙箱代码执行高危确认）、Subagent fan-out（预算硬顶 + 工具白名单执行路径裁剪）、反思重规划 ≤3 次自愈（错误上下文感知）、checkpointer 可选落盘、九类 SSE 事件 | `core/orchestrator/` |
| 取数 | DSL 管道 typed Tool 化、PII 脱敏（列名启发式 + 值形态正则）、ParquetRef 物化（audit 审计面）、裸 SQL 网关守卫、动态 profiling（低基数字段枚举值） | `core/retrieval/` |
| 质检 | DataQA 四类结果断言（空结果 / NULL 率 / 负值 / 维度唯一性），编排与 web 双链路同源，发现分级留痕 | `core/retrieval/quality.py` |
| 执行前审计 | 笛卡尔积 / 只读结构 REJECTED 熔断、无界输出 WARNING 轨迹（GuardrailAgent 角色） | `exec/audit.py` |
| 沙箱 | AST 静态守卫、限权 runner、Docker/子进程可插拔后端 | `core/sandbox/` |
| 技能 | 熵下钻、乘法/加法指标分解树、DTW 相似性、Holt-Winters 异常检测、Shapley 值归因 | `core/skills/` |
| 模型网关 | 多供应商 CRUD（Key 落盘加密不回传）、连通性探测、请求级模型切换 | `providers/` |
| 编译 | 确定性 DuckDB SQL、同比/环比、多事实表受控 Join | `compiler/` |
| 执行 | 只读白名单、执行前审计门、超时取消、扫描/结果熔断、SQL 自愈、连接池 | `exec/` |
| 展示 | 商业分析师四段式报告、数据质检小节、中文业务解释、number/line/bar/pie/pivot/table 推荐 | `present/` `core/orchestrator/nodes.py` |
| 治理 | 认证、作用域、表/列/RLS、审计、结构化 JSON 日志（request_id 贯穿）、指标 | `auth/` `security/` `audit/` |
| 交付 | Agent 对话流工作台、健康检查、查询 API、编排端点、指标 API | `web/` |

## 可观测性

三条互补的可观测通道，全部以 request_id / session_id 贯穿：

1. **结构化日志**（`audit/logging.py`，JSON 单行输出）：级别纪律——`error` = 熔断 / 审计拒绝 / 自愈额度耗尽终止 / QA error 级发现（可告警）；`warning` = 可自愈报错 / 降级路径（NL→DSL 降级、profiling 降级、LLM 调用失败走兜底）；`info` = HITL 中断 / 编排完成摘要 / 自愈重写成功。覆盖执行层、检索层、编排层与 web 自愈链路的全部异常路径。可选 `RotatingFileHandler` 落盘（`AUDIT_LOG_FILE` 配置路径，二十期 P3）。
2. **SSE 事件流**（九类事件）：plan_created / step_start / tool_start / tool_end（含审计预览与 guard/qa findings）/ reflection / hitl_request / artifact_emit / done / error——前端任务时间线与数据审计 Tab 的数据源。
3. **审计快照与指标**（`audit/`）：查询审计记录、QPS / 分位数指标、自愈失败计数（`record_self_heal_failure`）。编排审计快照扩展 `answered_by` / `planner_llm_error` 字段（二十期 P3），区分枚举预路由直答与 LLM 故障降级两种场景；`/api/agent/run` 编排路由接入审计写入，审计闭环。

更多运行步骤、配置与接口说明见：

- [快速开始](quickstart.md)
- [环境配置](configuration.md)
- [API 参考](api.md)
