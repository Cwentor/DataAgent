# AGENTS.md

本文件面向 AI 编码代理（Cursor, Windsurf, Trae, Claude Code 等）与协作者，规定项目的技术栈约束、工程标准、架构铁律与协作流程。

---

## 交互与沟通规范 (Agent Behavior)

- **语言要求**：除代码、变量名、英文错误日志、Commit 规范外，所有思考过程、方案解释、终端反馈和代码审阅**必须一律使用简体中文**。
- **边界划分**：
  - 解释与设计沟通：简体中文。
  - 核心名词首提建议双语：如“行级安全控制 (RLS)”、“语义目录 (Semantic Catalog)”。
  - 代码、测试函数名、英文注释与提交信息：严格使用英文。
- **严禁私自越权**：
  - **LLM 产出的 SQL 永不直接执行**（十九期修订）：必须经提升闸门
    （`core/retrieval/sql_lift.py`）转译为 DSL 契约后由确定性编译器重新生成，
    或经探索层审批门（第四类 interrupt）后在按 principal 生成的安全视图上受
    治理执行（`core/retrieval/exploration.py`——表名重写 + 三护栏 + PII 脱敏）；
    一切执行只发生在治理管道内。
  - 严禁在未更新 `semantic/catalog.py` 的前提下引入未声明字段。

---

## 项目简介与核心架构

**DataAgent** 是一款规格驱动（Spec-Driven）的企业级 Data Agent：
自然语言 -> 结构化 DSL(JSON) -> 确定性 SQL 编译器 -> DuckDB 执行。

- **架构核心铁律**：LLM 仅允许产出强契约受控 JSON（DSL），严禁直接生成裸 SQL，从根源上杜绝幻觉、越权与 SQL 注入。
- **自愈闭环**：执行层报错与编译精确异常允许反哺给 LLM 重写 DSL 自愈（受控重试上限），兜底模式严禁隐式吞错。

---

## 运行环境（Conda 虚拟环境）

- **环境管理器**：Miniconda / Anaconda（`conda 26.x+`）
- **虚拟环境名**：`dataagent`
- **Python 版本**：`3.12.x`（`requires-python = ">=3.11"`，锁定 3.12）
- **依赖管理**：conda 管理 Python 运行时 + pip 安装开发依赖（`requirements-dev.txt`）

### 环境配置与激活

```bash
# 创建并初始化环境
conda create -n dataagent python=3.12 -y
conda activate dataagent
pip install -r requirements-dev.txt
```

> 注：本机历史环境名仍为 `futurebi`（更名前创建，位于 `C:\Users\<user>\.conda\envs\futurebi`），
> 在其重建/重命名为 `dataagent` 之前，用 `conda activate futurebi` 亦可进入，二者等效。
> 执行任何命令前必须确认已激活上述环境之一。

## 常用命令

```bash
# 运行测试
python -m pytest -q

# 代码格式检查（black）
black --check .

# 代码格式自动修复
black .

# Lint 检查（ruff）
ruff check .

# Lint 自动修复
ruff check --fix .

# 重建本地数仓（幂等，Gmall 电商模型：业务表 + 埋点日志解析入仓）
python -m mock.init_duckdb

# 重建埋点日志解析链路的种子数据（仅种子变更时需要，需 imt/gmall.sql）
python -m mock.gmall.seed_extract

# 评测（oracle / agent 双模式）
python -m eval.eval_runner
python -m eval.eval_runner --pipeline agent

# 启动 Web 可视化 UI（默认 8000）
python -m web.server 8000
```

## 目录结构

```
semantic/   语义目录 + DSL 契约（Single Source of Truth）
compiler/   DSL -> SQL 确定性编译器
agent/      NL -> DSL Agent（LLM + 启发式兜底 + 意图路由 + 多轮记忆 + 多工具调度）
exec/       SQL 执行层（P0/P1 资源治理：执行前审计门 / 超时取消 / 扫描行数熔断 / LIMIT 硬上限 /
            只读连接池 / 查询结果缓存）
core/retrieval/ 追加（十九期）：sql_lift.py 提升闸门（LLM SQL -> DSL 转译，
            拒升精确清单喂回自愈）；exploration.py 探索执行器（审批门 + 安全
            视图受治理执行）；二者构成三层同心圆取数架构的 L2/L3 层

eval/       Golden 评测骨架与用例（oracle / agent 双模式）+ 意图路由评测（intent_eval）
mock/       确定性 Gmall 电商 mock 数仓（DuckDB）：mock/init_duckdb.py 入口 +
            mock/gmall/ 生成器（种子表提取 / 会话行为链模拟 / 埋点 JSON 日志 /
            日志解析入仓；反向解析规格见 docs/plans/2026-10-06-gmall-mock-reverse-spec.md）
present/    展示层（解释 + 可视化推荐）
security/   权限控制（表级/列级/行级 RLS + 生成前作用域收窄 + views.py 会话级
            安全视图：禁列物理投影 / RLS 固化 / 连接加固）
auth/       统一身份认证（JWT + Session + 登录限流）
audit/      审计快照 + 结构化日志 + 可观测性指标（/api/metrics）
persistence/ 可选 SQLite KV 状态外置（Session / 澄清槽位 / 限流共享）
core/       Data Agent 核心（企业级升级层，见下方四包说明）
providers/  多模型供应商网关（四协议适配 / API Key 加密存储 / 连通性探测）
config/     全局配置（settings.py + semantic.json + policies.json + providers.json）
tests/      单元测试
tools/      生产工具包（工具注册中心、内置工具等）
web/        Web 服务与工作台（FastAPI 服务 + service 编排链路 + static 前端）
```

### core/ 四包（Data Agent 升级层）

```
core/retrieval/     检索门面：DSL 管道 typed Tool 化（execute_dsl_query -> ParquetRef）、
                    PII 脱敏导出、裸 SQL 网关守卫（GuardrailViolation）、
                    动态 profiling（低基数字段枚举值注入规划上下文）、
                    DataQA 结果断言（空结果 / NULL 率 / 负值 / 维度唯一性，
                    发现随 ParquetRef.audit 输出，编排与 web 双链路同源）
core/orchestrator/  图式编排（LangGraph 单引擎，M4 收敛）：六节点 + interrupt 泛化
                    （clarify/plan_review/high_risk 审批门）+ L1-L4 自主性分级
                    （L3 = 计划确认 + 沙箱代码执行高危确认；L1 step_confirm 逐节点
                    确认未落地）+ Subagent fan-out（受限任务卡 + 工具白名单执行路径
                    裁剪 + 四节点子图 + 预算硬顶）+ 反思重规划 ≤3 次自愈 +
                    自愈错误上下文注入 Planner；Planner/Coder/Reflector 提示词与
                    Few-Shot
core/sandbox/       沙箱代码解释器：AST 静态守卫 + 限权 runner（模块白名单 import +
                    workspace 受限 open）+ 可插拔后端（SANDBOX_BACKEND 配置：默认
                    subprocess / auto 探测 Docker 优先 / docker 显式强隔离）
core/skills/        归因技能包：熵下钻 / 指标分解树（乘法对数链式 + 加法）/
                    DTW / Holt-Winters 异常检测 / Shapley 值归因
```

依赖方向铁律：orchestrator -> retrieval / sandbox / skills / agent；retrieval 不依赖
orchestrator；sandbox 不感知业务语义；skills 只依赖 numpy / pandas + 标准库（pandas 属
开发依赖，随沙箱数值栈一并安装）。

## 语言与交互铁律 (Strict Language & Output Rules)

- **全局输出语言**：除了代码块、变量名、SQL 关键字、终端命令、Git Commit 信息及原始错误堆栈外，所有思考过程（Thinking/Chain of Thought）、问题拆解、技术方案设计、改动说明与交互对话，**必须且仅能使用简体中文**。
- **英文输入时的语言锁定**：即使用户的提问包含英文、粘贴了全英文的报错日志（Traceback）或引用了英文技术文档，**回复与分析依然必须使用简体中文**，严禁不自觉切换为英文输出。
- **术语规范**：
  - 核心计算机与数据架构术语优先采用业界通行中文译名；
  - 首次出现或易歧义的概念建议双语对照，例如：“行级数据权限 (Row-Level Security, RLS)”、“抽象语法树 (AST)”、“模式校验 (Schema Validation)”；
  - 严禁对代码实体进行拼音化或意译（如代码中的变量名 `moving_avg`、`catalog`、`fill_gaps` 必须保持原样英文）。
- **代码注释规范**：新增或修改 Python 代码中的 docstring 和行内注释，统一使用简洁清晰的**简体中文**进行说明（专有名词保留英文）。
- **Git 提交信息**: 除了类似feat,fix,debug等之类的Git开发术语，其他和项目相关的描述和表达，如果不是必要的专业术语需要使用英文，其他通用表达统一使用**简体中文**进行说明

## 关键约定

- 所有新增逻辑字段必须登记在 `semantic/catalog.py` 的 `COLUMNS` 白名单，否则编译器拒绝；
- DSL 模型一律 `extra="forbid"`，Agent 只能产出契约内字段；
- 评测锚点 `AS_OF_DATE = 2025-12-31`（数据域 2021-01-01 ~ 2025-12-31）、随机种子 42，保证确定性可复现；
- DSL 进阶语义：窗口指标 `WindowMetric`（cumsum/moving_avg，需时间维度）、日期补零 `fill_gaps`（需时间维度+明确时间窗口，支持 day/week/month/quarter）、分组 `TopN`（ROW_NUMBER 分区过滤）；编译器对 comparison / top_n / fill_gaps / window 的互斥组合显式抛 `CompileError`；
- SQL 执行层（`exec/`）：statement_timeout 用线程看门狗 + `conn.interrupt()` 取消；扫描行数上限用 `EXPLAIN ANALYZE` 预检熔断；LIMIT 硬上限对返回行数做防御性熔断；执行前审计门（`exec/audit.py`）对编译产物做静态审计——笛卡尔积 / 只读结构违规 REJECTED 熔断（`GuardrailRejected`，拒绝原因可自愈）、无界输出 WARNING（真实边界由返回行数硬上限承担，勿升级为 REJECTED）；编译/引擎精确报错会喂回 LLM 重写 DSL 自愈（至少 1 次，`SQL_SELF_HEAL_MAX_RETRIES`），确定性兜底模式下透传原始报错；
- 结果断言层（`core/retrieval/quality.py`）：执行后、导出前四类确定性质检（空结果 / NULL 率 / 非负指标负值 / 聚合维度组合唯一性），发现随 `ParquetRef.audit`（{guard, qa}）全链路可见（SSE 事件 / critic / 报告质检小节 / 分级日志），不自动否决执行；ratio/window 派生指标不做负值断言；
- 重规划自愈上下文：编排链路失败回 plan 时，`planner_prompt` 必须注入 `error_context`（最近失败摘要）——严禁让 LLM 盲重试；`error_context=None` 时提示词与旧契约逐字一致；
- 语义单一事实源（M-P2 语义去耦，2026-10）：**config/semantic.json 是唯一静态业务事实源**——
  字段/标签/别名/连接规则/指标口径（metrics[]，含 DSL shape）/支付口径（paid_filter）/缺省窗口/
  反思概念/枚举值标签/大区映射/维度成员种子等一律登记 json，semantic/catalog.py 为 import 时
  json 直读（缺必要节快速失败），**严禁在手写代码（heuristic/nodes/glossary/prompts/labels）里
  新增业务映射**；问法层词表（趋势/下钻/排他等触发词）统一放 agent/lexicon.py，不进 json。
  改 json 后跑 python -m semantic.catalog_loader 校验；评价断言分契约层（DSL/SQL 形态，
  --skip-snapshot）与快照层（结果集）两级。

- Gmall mock 数仓与语义域（二十期，2026-10）：mock 数仓为 Gmall 电商模型（种子表取自
  imt/gmall.sql 提取的 seed_data.json；用户/购物车/订单/支付/退款/评论由会话行为链模拟生成；
  埋点 JSON 日志落 logs/gmall_applog/ 后经解析链路入仓 4 张行为事实表）。交易域锚点 =
  order_detail 明细宽表（冗余品牌/类目链/用户/省份/订单状态），订单数口径 = count_distinct(order_id)；
  流量域 fact_page_view/fact_action/fact_display/fact_start 为独立查询域（QUERY_DOMAINS 域锚点，
  跨域查询 CompileError，流量域 time_field 用 page_view_time/action_time 等）。确定性三原则不变：
  种子 42、锚点 AS_OF_DATE=2025-12-31（数据域 2021-01-01 ~ 2025-12-31）、单 rng 顺序消费——改生成器逻辑须先跑
  行数/指纹对照并重固化 golden（eval/rebuild_golden.py）。

- 提交前确保 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿。

- 能力边界与三层取数架构（十九期，2026-10）：能力边界 = 治理管道边界（只读 + RLS + 敏感数据保护 + 资源上限），不是 DSL 契约表达力；诚实 = 不虚构 + 假设透明（assumptions 契约字段，报告头部呈现），拒答降为最后手段——二轮歧义转"带假设作答"，严禁静默猜口径；三层同心圆：L1 确定性核（Planner 直产 DSL）/ L2 提升闸门（拒升精确清单喂回自愈 ≤2 次，拒升 ≠ 拒答）/ L3 探索层（第四类 interrupt 审批门 allow_once/allow_session/deny；L4 自动放行硬边界 = SQL 无主体禁列；deny = 诚实告知）；探索 SQL 永不原文执行（表名重写到 sec_* 安全视图 + 三护栏 + PII 脱敏）；沙箱 `connect/database` 调用永久封死（取数权只在执行层）；意图词表新增枚举/基数触发词时须同步 `intent_golden.json` 锚点；
- 意图路由与诚实兜底（`core/orchestrator/intent.py` + `nodes.py`，十八期）：兜底准入制——仅诊断/基数/硬锚定指标三类意图可确定性直答（附缺省口径说明），其余意图置 `blocked_reason` 诚实拒答（critic/synthesize 短路，零 LLM 调用）；L3 意图-DSL 错位守卫依据确定性 L1 硬匹配（不信任 LLM 回传意图），CARDINALITY 守卫只限制聚合与投影目标、不限制过滤条件；Grounding 数值溯源重试上限 1 次、仍超阈值必须降级确定性渲染；意图词表以 `semantic/catalog` 的 `FieldMeta.aliases` 为单一事实源——新增字段时别名随登记自动生效，严禁在 intent.py 维护字面词表；选项式澄清经 `clarification_options` 状态字段透传（前端已消费）；指标词表的新增/修改必须同步登记 `semantic.json` 或内置 `COLUMNS` 并补意图评测用例；

## 评审落盘规范（Review Archive）

- 任何评审（PR 评审、代码审计、生产就绪度评审、安全评审等）的落盘文件**必须统一输出到 `docs/reviews/`**，严禁散落在项目根目录或其他目录；
- 文件命名遵循 `docs/reviews/README.md` 中的规范：`yyyymmdd-` 归档日期前缀 + kebab-case 英文类型与主题（如 `20260905-pr-review-<主题>.md`、`20260906-audit-<主题>.md`、`20260906-readiness-<主题>.md`）；
- 文件首行标题必须遵循统一格式 `# 评审报告｜<评审类型>：<评审主题>（YYYY-MM）`，内容结构不强制统一；
- 归档规范与清单详见 [`docs/reviews/README.md`](docs/reviews/README.md)。

## Language Rule
- 无论代码、终端日志或测试用例中包含多少英文，你在调用工具之间输出的任何进度说明、思考陈述、计划与总结，**必须始终使用简体中文**。
- 禁止在中途输出如 "Now let me...", "Next, I will..." 等英文过渡句。
