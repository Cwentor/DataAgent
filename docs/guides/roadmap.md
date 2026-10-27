# 演进里程碑

项目从规格驱动的 ChatBI 问数引擎起步，已完成向企业级 Data Agent（数据智能体）的演进：在"受控 DSL → 确定性 SQL"核心链路之上，叠加图式编排、沙箱分析、归因技能与多模型供应商网关。

| 阶段 | 能力 | 状态 |
| --- | --- | --- |
| 一期 | 语义 DSL、Mock 数仓、Golden 评测骨架 | ✅ |
| 二期 | LLM Agent 与启发式兜底双路径 | ✅ |
| 三期 | 同比/环比双窗口 CTE | ✅ |
| 四期 | 多事实表语义模型与跨表比率指标 | ✅ |
| 五期 | 表级、列级、行级 RLS | ✅ |
| 六期 | DSL 解释与可视化推荐 | ✅ |
| 七期 | Web 可视化 UI | ✅ |
| 八期 | 多指标同环比、窗口函数、日期补零、分组 Top-N | ✅ |
| 九期 | 意图路由与语义澄清反问 | ✅ |
| 十期 | 统一身份认证、principal 服务端绑定、作用域前移 | ✅ |
| 十一期 | 执行层资源治理、自愈、审计与结构化日志 | ✅ |
| 十二期 | 注入防御、只读白名单、登录限流、SQLite 会话、启动强校验、可观测性 | ✅ |
| 十三期 | Agent 能力深化：观察驱动重规划、对比型问题分解与跨步综合、反思层结果充分性自检 | ✅ |
| 十四期 | 企业级 DataAgent 升级层（`core/`）：StateGraph 六节点编排、沙箱代码解释器、归因技能包（熵下钻 / 分解树 / DTW / Holt-Winters / Shapley）、PII 脱敏与 ParquetRef 数据交换、裸 SQL 网关 | ✅ |
| 十五期 | 双栏交互式工作台与 SSE 流式编排：九类事件实时推送、任务 DAG 时间线、产物画布、HITL 澄清交互 | ✅ |
| 十六期 | 多模型供应商网关（`providers/`）：OpenAI Chat / Responses、Anthropic、Gemini 四协议适配，API Key 落盘加密，连通性探测与请求级模型切换 | ✅ |
| 十七期 | Agent 架构演进（`core/orchestrator/`）：LangGraph 单引擎收敛、interrupt 泛化与 L1-L4 自主性分级（Plan Mode）、Subagent fan-out 预算硬顶 | ✅ |
| 十八期 | 意图路由收敛与诚实兜底：澄清判定权上收 Planner、兜底准入制（诊断/基数/锚定指标三类直答，其余诚实拒答）、L3 意图-DSL 错位守卫、别名收编语义目录（`FieldMeta.aliases` 单一事实源）、Grounding 定向重试闭环、选项式澄清、答非所问率评测入 CI | ✅ |
| 十九期 | 能力边界扩展：治理即边界、三层同心圆取数架构（确定性核 / SQL 提升闸门 / 探索层审批门）、DSL 扩容（纯维度投影 / HAVING / 表达式指标）、多维枚举、分级透明作答（assumptions 假设标注）、安全视图层（禁列物理投影 + RLS 固化 + 连接加固）、沙箱 connect 旁路封堵、红线矩阵入 CI | ✅ |
| 二十期 | Gmall 电商数仓迁移（反向解析数据生成器：种子提取 / 会话行为链模拟 / 埋点 JSON 日志 / 解析入仓 4 张行为事实表）、语义单一事实源（`config/semantic.json` v2 全节登记，`semantic/catalog.py` import 时 json 直读）、交易域锚点切 `order_detail` 明细宽表 + 流量域 `QUERY_DOMAINS` 域锚点解析、枚举预路由确定性渲染（ENUMERATION 意图全量行清单截断分流）、降级水印区分枚举直答与 LLM 故障、审计字段扩展（`answered_by` / `planner_llm_error`）、结构化日志可选 `RotatingFileHandler` 落盘、测试目录与源码包一一对应（54 用例归位 14 个子目录）、LLM 流式握手期超时安全重试、报告视图轮次导航与锚点定位 | ✅ |
| 二十一期 | MySQL 执行后端支持（`DB_BACKEND=duckdb` / `mysql` 双后端）：`EngineAdapter` 协议抽象（只读连接 / 执行 / 取消 / 计划解析 / 异常归一 / 连接加固 / 灌数）+ 编译方言层 `DialectRenderer` 九原语（标识符引号 / 时间截断 / 时间平移 / HAVING 别名 / 补零 spine / 字符串转义）+ MySQL 建仓（DDL 转译 + 同源数据）+ 双后端一致性 parity（golden 全量 + 周边界 / 5 年递归补零 / comparison 配对 / 池复用）+ CI `mysql-parity` 门禁；DuckDB 产物逐字节不变（golden 锚点） | ✅ |
| 二十二期 | PostgreSQL 执行后端支持（三后端）：adapter 与 renderer 双注册表化（惰性工厂，新增后端 = 登记一行）+ PG 只读账号与会话加固（`default_transaction_read_only` / `standard_conforming_strings` / `lock_timeout` / `statement_timeout`）+ 方言差异转译（`date_add` 加法等价式、HAVING 底层表达式重放、spine 递归 CTE）+ parity 工具提取为 `tests/parity_utils.py` + CI `postgres-parity` 门禁 | ✅ |
| 二十三期 | ClickHouse 执行后端支持（四后端）：验证式加固（回读 `system.settings` 的 readonly 与预算，与 `MAX_SCAN_ROWS` / `QUERY_TIMEOUT_MS` fail-closed 比对）+ 错误码文本归一（159 超时 / 131 扫描熔断）+ `numbers(N)` + `addDays/addMonths/addYears` 补零 spine + MergeTree 建仓（`allow_nullable_key` / `Date32`）+ 审计笛卡尔积的方言 AST 归一修正 + `divide` 方言原语修 Decimal 除法截断（CH `S = S1`）+ CI `clickhouse-parity` 门禁（users.d 只读 profile） | ✅ |
| 二十四期 | 物理元数据面下沉 `EngineAdapter`（二十四期）：`physical_columns()` 按当前后端实读目录物理列（duckdb / mysql / pg 走 `information_schema.columns`，CH 走 `system.columns`）、物理类型归一收敛 `exec/engine/type_map.py`（未知类型 fail-closed，不静默降级）、`ensure_db()` 按后端分派（仅 duckdb 建仓，非 duckdb 库不可用即启动失败、禁止回退内置默认目录）、修 MySQL `?` 占位符在真实 pymysql 上的历史 bug | ✅ |
| 二十五期 | Hive 执行后端支持（五后端）：PyHive **HTTP transport**（避开 Windows 无官方 wheel 的 sasl，三平台统一安装）+ 方言层五原语（`trunc('MM'/'Q')` 等价式、`pmod(datediff,'1970-01-05')` 周一锚定、`posexplode(split(space(n)))` 补零 spine、`add_months` 月末钳制）+ CSV staging + `LOAD DATA` 建仓链路（反斜杠 N 作 null 标记、staging 双路径卷映射）+ HiveQL 特有语句族护栏黑名单 + `DESCRIBE` 分区段 fail-closed 解析 + CI `hive-parity` 门禁（单容器 HiveServer2 + 内嵌 Derby）；golden 锚点与既有四后端产物逐字节不变 | ✅ |
