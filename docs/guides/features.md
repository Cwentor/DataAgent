# 特性总览

本文件是 DataAgent 的完整特性清单，按演进阶段整理。README 只保留最关键特色，各期细节在此展开。分期索引与设计文档入口见[演进里程碑](roadmap.md)与 [docs 总索引](../README.md)。

## 基础能力（一至十三期）

ChatBI 问数引擎阶段打下的地基，以下能力贯穿至今。

- **语义层与契约**（一、八期）：语义 DSL 与字段白名单目录，Pydantic V2 + `extra="forbid"` 强契约，字段、操作符、聚合均为受限枚举；多指标同环比、窗口函数、日期补零、分组 Top-N
- **Agent 双路径**（二期）：LLM 严格校验与确定性启发式兜底，离线零 Key 可用，LLM 失败安全降级（degraded 标志可见）
- **编译语义**（三、四期）：同比环比双窗口 CTE、多事实表语义模型与跨表比率指标
- **权限体系**（五、十期）：表级、列级、行级 RLS；统一身份认证（JWT + Session）；principal 服务端绑定与生成前作用域收窄
- **解释与可视化**（六期）：DSL 中文业务解释与图表自适应推荐
- **Web 服务与工作台**（七、十五期）：FastAPI + uvicorn 服务层，查询 / 编排 / 异步任务端点；双栏交互式工作台、SSE 九类事件实时推送、任务 DAG 时间线、产物画布、HITL 澄清交互
- **执行层治理**（十一期）：只读白名单、超时取消、扫描行数熔断、返回行数上限、SQL 自愈、连接池与查询缓存
- **注入防御与加固**（十二期）：只读 AST 校验、登录限流、SQLite 会话外置、启动强校验、可观测性
- **Agent 能力深化**（十三期）：观察驱动重规划（调度轨迹喂回规划器继续决策，Max Steps 硬预算）、对比型问题分解为多次单实体查询并跨步对比作答、反思层结果充分性自检（必要时受控追加一次查询）
- **可观测**（十一、十二期）：审计快照、结构化 JSON 日志（request_id 贯穿、error/warning/info 分级纪律）、QPS 与分位数指标

## 企业级升级层（十四期，core/ 四包）

在核心链路之上叠加的 Data Agent 能力层。

- **图式编排（初版）**：StateGraph 六节点（澄清 HITL、规划、受控取数、沙箱分析、反思重规划、综合报告），计划为步骤 DAG
- **GuardrailAgent 执行前审计**：编译产物 SQL 经 sqlglot AST 静态审计——笛卡尔积（逗号 / CROSS / 无 ON JOIN）与只读结构违规 **REJECTED 执行前熔断**（拒绝原因进入自愈上下文），无界输出 WARNING 审计轨迹；结构化裁决（check / severity / message / suggestion）
- **DataQAAgent 结果断言**：空结果 / NULL 率 / 非负指标负值 / 聚合维度组合唯一性四类确定性质检，编排与 web 双取数链路同源；发现随 `ParquetRef.audit` 经 SSE 事件、critic 反思视野、报告"数据质检"小节与结构化日志全链路可见（不误杀不吞错）
- **SchemaAgent 动态 profiling**：低基数（≤30）字符串字段实际取值探查并注入规划上下文（"可取值: a|b|c"），Planner 不再臆造过滤字面值；进程级缓存 + 失败降级，离线环境零副作用
- **沙箱代码解释器**：AST 静态守卫（黑名单 import / 调用 / dunder 逃逸）+ 限权 runner（模块白名单 import、workspace 受限 open）+ 可插拔后端（Docker `--net=none --cap-drop=ALL` 强隔离 / 子进程兜底），聚合矩阵 ≤100 行防数据外泄
- **归因技能包**：维度熵 / 信息增益下钻、乘法对数链式指标分解树（GMV=UV×CR×AOV）、加法差额分解、DTW 相似性、Holt-Winters 异常检测、Shapley 值公平归因——全部与已知解析解对拍
- **数据交换协议**：DSL 查询结果 PII 脱敏（列名启发式 + 值形态正则，确定性哈希掩码）后物化为 ParquetRef（sha256 可审计），沙箱零网络零 DB socket
- **裸 SQL 网关**：字符串载荷 / SQL 键 / 自由文本 SQL 形态在网关层直接拒绝（`GuardrailViolation`），"LLM 永不产出裸 SQL"三重防线

## 模型供应商网关（十六期，providers/）

- OpenAI Chat / OpenAI Responses / Anthropic / Google 四协议适配，JSON-Mode 平滑
- API Key 落盘加密不回传，列表响应永不回显密钥
- 工作台设置页可视化 CRUD 与连通性探测（全部供应商用户自建，无预置）
- SSE 编排支持请求级 `provider_id` / `model_id` 模型切换

## Agent 架构演进（十七期，core/orchestrator/）

- **LangGraph 单引擎收敛**：六节点语义移植 + checkpointer（`ORCHESTRATOR_CHECKPOINT_DB` 可选 SQLite 落盘）
- **interrupt 泛化与 L1-L4 自主性分级**：clarify / plan_review 审批门；L3 对沙箱执行模型产码二次确认（high_risk）；L4 直通
- **Subagent fan-out**：受限任务卡 + 工具白名单执行路径裁剪 + 四节点子图 + 预算硬顶
- **自愈错误上下文注入**：重规划时最近失败摘要（error_context）注入 Planner 提示词——自愈是针对性修正而非盲重试

## 意图路由收敛与诚实兜底（十八期）

- **澄清判定权上收 Planner**（clarification 契约，支持选项式反问——候选口径 pill 按钮点击即答复）
- **兜底准入制**：诊断 / 基数（有多少个省份）/ 硬锚定指标三类意图确定性直答（附缺省口径说明），其余意图置 `blocked_reason` 诚实拒答（原因 + 已识别锚点 + 能力清单，全程零 LLM 调用）
- **L3 意图-DSL 错位守卫**：执行前拦截"基数意图 + 金额聚合"式错位查询
- **Grounding 定向重试闭环**：LLM 报告数值溯源校验（量纲归一化容差），不可溯源超阈值携修正指令重写 1 次，仍不达标降级确定性渲染
- **意图词表单一事实源**：以 `FieldMeta.aliases` 为准，新增字段别名随登记自动生效；答非所问率评测入 CI（`intent_eval`）

## 能力边界扩展（十九期，三层同心圆）

能力边界 = 治理管道边界（只读 + RLS + 敏感数据保护 + 资源上限），不是 DSL 契约表达力。LLM 产出的 SQL 永不直接执行。

- **L1 确定性核**：Planner 直产 DSL 覆盖常规问数
- **L2 提升闸门**（`core/retrieval/sql_lift.py`，sqlglot）：超契约 SQL 转译回 DSL 契约由确定性编译器重编译；拒升 ≠ 拒答，精确清单喂回自愈 ≤2 次
- **L3 探索层**（`core/retrieval/exploration.py`）：不可提升 SQL 经第四类审批门（allow_once / allow_session / deny）后在按 principal 生成的安全视图（`security/views.py`，禁列物理投影 + RLS 固化 + 连接加固）上受治理执行，报告醒目标注"探索查询产出"
- **DSL 扩容**：纯维度投影 / HAVING / 表达式指标、多维枚举
- **分级透明作答**：诚实 = 不虚构 + 假设透明（`assumptions` 契约字段，报告头部呈现），二轮歧义转带假设作答，拒答降为最后手段
- **沙箱 connect 旁路封堵**：`connect/database` 调用永久封死——取数权只在执行层；红线矩阵入 CI（`boundary_eval`）

## Gmall 数仓与语义单一事实源（二十期）

- **Gmall 电商数仓迁移**：反向解析数据生成器（种子表提取 / 会话行为链模拟 / 埋点 JSON 日志 / 解析入仓 4 张行为事实表）；交易域锚点切 `order_detail` 明细宽表 + 流量域 `QUERY_DOMAINS` 域锚点解析，跨域查询 CompileError
- **语义单一事实源**：`config/semantic.json` 是唯一静态业务事实源——字段 / 标签 / 别名 / 连接规则 / 指标口径 / 支付口径 / 缺省窗口 / 反思概念 / 枚举值标签 / 大区映射 / 维度成员种子一律登记 json，`semantic/catalog.py` import 时 json 直读，严禁在手写代码里新增业务映射
- **枚举预路由确定性渲染**：ENUMERATION 意图在编排入口即预路由为确定性渲染路径——全量行清单直答（超过阈值时截断分流），完全绕过 LLM 调用链路，零幻觉零延迟；降级水印区分"枚举预路由直答"与"LLM 故障降级"
- **审计与日志扩展**：编排审计快照增加 `answered_by` / `planner_llm_error` 字段；结构化日志可选 `RotatingFileHandler` 落盘（`AUDIT_LOG_FILE`）
- **工程整备**：测试目录与源码包一一对应；LLM 流式握手期超时安全重试；报告视图轮次导航与锚点定位

## 多执行后端（二十一至二十五期）

`DB_BACKEND=duckdb|mysql|postgres|clickhouse|hive`（默认 duckdb 零破坏），五后端一契约。

- **引擎抽象层**（`exec/engine/`）：`EngineAdapter` 协议把连接 / 执行 / 取消 / 计划解析 / 异常归一 / 连接加固 / 灌数 / 物理元数据按引擎下沉；`DialectRenderer`（`compiler/dialect/`）承载方言差异（标识符引号 / 时间截断与平移 / HAVING 别名 / 补零 spine / 字符串转义）。新增后端 = 各登记一行，护栏框架与消费方零改动
- **建仓链路**：五后端复用 DuckDB DDL 运行时转译，源数据行级一致；Hive 走 CSV staging + `LOAD DATA`（PyHive HTTP transport）
- **物理元数据面**（二十四期）：`physical_columns()` 按当前后端实读目录物理列（duckdb / mysql / pg 走 `information_schema.columns`，CH 走 `system.columns`，Hive 逐表 DESCRIBE 解析）；类型归一收敛 `exec/engine/type_map.py`，未知类型 fail-closed 抛错不静默降级
- **各后端关键差异**：
  - MySQL：只读三防线（只读账号 + 会话 READ ONLY + 方言增量黑名单）、`max_execution_time` 超时、EXPLAIN ANALYZE 扫描熔断、WEEKDAY 周一对齐
  - PostgreSQL：`statement_timeout`（SQLSTATE 57014 归一）、`default_transaction_read_only` 加固、date_trunc / generate_series 全原生
  - ClickHouse：账号 profile 承载预算（readonly=1）、验证式加固（回读 `system.settings` fail-closed 比对）、`numbers(N)` + addDays/addMonths/addYears spine、驱动异常文本归一
  - Hive：无会话级只读 / 预算 / 取消面，客户端读超时 + 验证式 harden、`posexplode` 补零 spine、裸标识符反引号化、DESCRIBE 分区段 fail-closed
- **服务级 CI 门禁**：每个非默认后端一个 parity job——golden 全量跨后端一致性 + 周边界 / 递归补零 / comparison 配对 / 连接池复用用例 + 建仓冒烟；golden 评测锚定 DuckDB 方言不随引擎切换

## 交互式架构图（architecture-ui）

ReactFlow 分层交互画布，`architecture-ui/` 子项目，由 Pages workflow 自动构建部署。主链七层加横切支撑与质量保障两个旁挂组，节点与边口径对齐[架构与设计](architecture.md)；支持分层显隐、节点上下游高亮与纵横布局切换。

## 横切能力速查

| 能力 | 入口 |
| --- | --- |
| 统一认证（JWT + Session + 登录限流） | `auth/` |
| 表 / 列 / 行级 RLS 与安全视图 | `security/` |
| 审计快照、结构化日志、指标（/api/metrics） | `audit/` |
| 会话 / 槽位 / 限流 SQLite 外置 | `persistence/` |
| Golden 评测（oracle / agent 双模式）+ 意图评测 + 边界评测 | `eval/` |
| 确定性 Gmall mock 数仓（种子 42 + 锚点 2025-12-31） | `mock/` |
| 五后端本地 Docker 实测矩阵 | `scripts/backend_matrix.py` |
