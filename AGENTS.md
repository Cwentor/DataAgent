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
  - 严禁绕过 DSL 试图直接拼接生成裸 SQL。
  - 严禁在未更新 `semantic/catalog.py` 的前提下引入未声明字段。

---

## 项目简介与核心架构

**FutureBI** 是一款规格驱动（Spec-Driven）的企业级 ChatBI / Data Agent：
自然语言 -> 结构化 DSL(JSON) -> 确定性 SQL 编译器 -> DuckDB 执行。

- **架构核心铁律**：LLM 仅允许产出强契约受控 JSON（DSL），严禁直接生成裸 SQL，从根源上杜绝幻觉、越权与 SQL 注入。
- **自愈闭环**：执行层报错与编译精确异常允许反哺给 LLM 重写 DSL 自愈（受控重试上限），兜底模式严禁隐式吞错。

---

## 运行环境（Conda 虚拟环境）

- **环境管理器**：Miniconda / Anaconda（conda 26.x+）
- **虚拟环境名**：`futurebi`
- **Python 版本**：`3.12.x`（`requires-python = ">=3.11"`，锁定 3.12）
- **依赖管理**：conda 管理 Python 运行时 + pip 安装开发依赖（`requirements-dev.txt`）

### 环境配置与激活

```bash
# 创建并初始化环境
conda create -n futurebi python=3.12 -y
conda activate futurebi
pip install -r requirements-dev.txt
```

> 注：本机环境已建好，位置 `C:\Users\<user>\.conda\envs\futurebi`；
> 用 `conda activate futurebi` 即可进入。
> 执行任何命令前必须确认处于 (futurebi) 环境。

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

# 重建本地数仓（幂等，用于生成 DuckDB 文件）
python -m mock.init_duckdb

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
agent/      NL -> DSL Agent（LLM + 启发式兜底）
exec/       SQL 执行层（P0/P1 资源治理：超时取消 / 扫描行数熔断 / LIMIT 硬上限）
eval/       Golden 评测骨架与用例
mock/       确定性 mock 数仓（DuckDB）
present/    展示层（解释 + 可视化推荐）
security/   权限控制（表级/列级/行级 RLS）
config/     全局配置
tests/      单元测试
tools/      生产工具包（工具注册中心、内置工具等）
web/        Web 可视化 UI（service + server + static 前端）
```

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
- 评测锚点 `AS_OF_DATE = 2024-06-30`、随机种子 42，保证确定性可复现；
- DSL 进阶语义：窗口指标 `WindowMetric`（cumsum/moving_avg，需时间维度）、日期补零 `fill_gaps`（需时间维度+明确时间窗口，仅 day/month）、分组 `TopN`（ROW_NUMBER 分区过滤）；编译器对 comparison / top_n / fill_gaps / window 的互斥组合显式抛 `CompileError`；
- SQL 执行层（`exec/`）：statement_timeout 用线程看门狗 + `conn.interrupt()` 取消；扫描行数上限用 `EXPLAIN ANALYZE` 预检熔断；LIMIT 硬上限对返回行数做防御性熔断；编译/引擎精确报错会喂回 LLM 重写 DSL 自愈（至少 1 次，`SQL_SELF_HEAL_MAX_RETRIES`），确定性兜底模式下透传原始报错；
- 提交前确保 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿。

## 评审落盘规范（Review Archive）

- 任何评审（PR 评审、代码审计、生产就绪度评审、安全评审等）的落盘文件**必须统一输出到 `docs/reviews/`**，严禁散落在项目根目录或其他目录；
- 文件命名遵循 `docs/reviews/README.md` 中的规范：`yyyymmdd-` 归档日期前缀 + kebab-case 英文类型与主题（如 `20260905-pr-review-<主题>.md`、`20260906-audit-<主题>.md`、`20260906-readiness-<主题>.md`）；
- 文件首行标题必须遵循统一格式 `# 评审报告｜<评审类型>：<评审主题>（YYYY-MM）`，内容结构不强制统一；
- 归档规范与清单详见 [`docs/reviews/README.md`](docs/reviews/README.md)。

