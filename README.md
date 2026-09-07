<div align="center">
  <img src="docs/assets/logo.svg" width="112" alt="FutureBI Logo"/>

  # FutureBI

  **企业级 ChatBI（Data Agent）：自然语言 → 受控 DSL → 确定性 SQL → DuckDB**

  LLM 只产出受控 JSON，绝不直接生成裸 SQL —— 零幻觉、零注入、零随意 Join。

  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white"/>
    <img alt="Pydantic" src="https://img.shields.io/badge/Pydantic-V2-E92063?style=flat-square&logo=pydantic&logoColor=white"/>
    <img alt="DuckDB" src="https://img.shields.io/badge/DuckDB-%E6%9C%AC%E5%9C%B0%E6%95%B0%E4%BB%93-FFF000?style=flat-square&logo=duckdb&logoColor=black"/>
    <img alt="Code Style" src="https://img.shields.io/badge/Code%20Style-black-000000?style=flat-square"/>
    <img alt="Lint" src="https://img.shields.io/badge/Lint-ruff-261230?style=flat-square&logo=ruff&logoColor=white"/>
    <img alt="License" src="https://img.shields.io/badge/License-MIT-green?style=flat-square"/>
    <img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Cat-Drink/FutureBI/ci.yml?branch=master&style=flat-square&label=CI"/>
  </p>

  **一句话读懂它**：把大模型关进契约的笼子——模型只负责"理解问题"，数据永远由确定性代码产出。

</div>

---

## 🧠 核心链路（一图流）

```text
🗣️ 自然语言提问
      │
      ▼
🧭 意图路由 ──── 五分类分流：问数 / 口径解释 / 澄清反问 / 闲聊 / 系统操作
      │
      ▼
📐 受控 DSL ──── LLM 仅产出契约内 JSON（extra="forbid"），支持多轮指代继承
      │
      ▼
⚙️ 确定性编译 ── 字段白名单 + 受控 JOIN + 表/列/行级 RLS 强制注入
      │
      ▼
🛡️ 受控执行 ─── 只读 AST 校验 / 超时中断 / 扫描行数熔断 / 返回行数上限
      │
      ▼
📊 可解释交付 ── 中文话术 + 图表自适应推荐 + SQL 溯源 + 全链路审计
```

## ✨ 特性

- **受限 DSL 契约**：Pydantic V2 + `extra="forbid"`，字段、操作符、聚合均为受限枚举
- **确定性编译**：SQL 只由编译器生成，支持聚合/比率/时间/窗口/补零/Top-N/同比环比
- **纵深安全**：统一认证（JWT + Session）+ 生成前作用域 + 表/列/行级权限守卫（RLS）
- **受控执行**：只读白名单、超时取消、扫描行数熔断、返回行数上限、SQL 自愈
- **对话式体验**：意图路由、多轮指代继承（"那华南呢？"）、口径澄清与槽位回填、多工具编排
- **观察驱动重规划**：执行后把调度轨迹喂回规划器继续决策（继续查 / 作答 / 反问 / 终止），受 Max Steps 硬预算约束；对比型问题自动分解为多次单实体查询并跨步对比作答；反思层在调度终止后自检结果充分性（必要时受控追加一次查询）
- **可解释交付**：DSL → 中文话术 + 图表自适应推荐 + Web UI，零前端框架
- **可观测**：全链路审计快照、结构化日志、QPS/分位数指标

## 🚀 快速开始

```bash
conda activate futurebi
pip install -r requirements-dev.txt
python -m mock.init_duckdb
python -m web.server 8000
```

> 🌐 启动后用浏览器打开 <http://127.0.0.1:8000> 即可体验；完整的安装、评测与离线自检步骤见 **[快速开始](docs/quickstart.md)**。

---

## 📦 部署与使用指南

### 一、前置要求

| 依赖项 | 版本要求 |
|---|---|
| Python | `>=3.11`，项目锁定 `3.12` |
| 环境管理器 | Miniconda / Anaconda（`conda 26.x+`） |
| 包管理器 | pip |

### 二、克隆与安装

```bash
# 克隆仓库
git clone <repository-url>
cd FutureBI

# 创建并激活 conda 环境
conda create -n futurebi python=3.12 -y
conda activate futurebi

# 安装依赖
pip install -r requirements-dev.txt
```

### 三、初始化数仓

```bash
# 幂等初始化本地 DuckDB 数仓
python -m mock.init_duckdb
```

### 四、配置 LLM

```bash
# 从模板创建配置文件
copy .env.example .env
```

编辑 `.env`，至少配置以下关键变量：

```ini
# LLM 接入（必填）
LLM_API_KEY=your-key-here
LLM_BASE_URL=https://api.openai.com/v1   # 或 DeepSeek / Kimi / Ollama
LLM_MODEL=gpt-4o-mini

# 执行层资源治理
QUERY_TIMEOUT_MS=30000         # 语句超时（毫秒）
MAX_SCAN_ROWS=10000000         # 扫描行数熔断上限
MAX_RESULT_ROWS=20000          # 返回行数硬上限
SQL_SELF_HEAL_MAX_RETRIES=1    # SQL 自愈重试次数

# 认证（生产环境必须配置）
AUTH_ENABLED=1
AUTH_JWT_SECRET=<强随机密钥>    # python -c "import secrets;print(secrets.token_hex(32))"
AUTH_STRICT=1                  # 严格生产安全模式
```

> **离线开发**：无需真实 API Key，运行本地模拟服务即可：
> ```bash
> python tools/mock_llm_server.py 8765
> set LLM_API_KEY=sk-mock
> set LLM_BASE_URL=http://127.0.0.1:8765/v1
> set LLM_MODEL=mock
> ```

### 五、启动服务

```bash
# 启动 Web UI（默认端口 8000）
python -m web.server 8000
```

服务启动后访问：`http://127.0.0.1:8000`

**内置演示账号**：

| 账号 | 口令 | 权限范围 |
|---|---|---|
| `admin` | `admin123` | 全表 |
| `analyst` | `analyst123` | 全表，仅 5 省行级权限 |
| `bob` | `bob123` | 受限：无退款表/敏感列，仅广东 |

### 六、验证部署

```bash
# 运行全部单元测试
python -m pytest -q

# 运行 Golden 评测（oracle 模式）
python -m eval.eval_runner

# 运行 Golden 评测（agent 模式）
python -m eval.eval_runner --pipeline agent

# 打印编译 SQL 查看详情
python -m eval.eval_runner --print-sql
```

### 七、代码质量检查

提交前确保以下检查全绿：

```bash
black --check .
ruff check .
python -m pytest -q
```

修复格式与 lint 问题：

```bash
black .
ruff check --fix .
```

### 八、API 使用示例

**登录获取 Token**：

```bash
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"analyst","password":"analyst123"}'
```

**执行查询**：

```bash
curl -X POST http://127.0.0.1:8000/api/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"query":"各品类成功订单的GMV分布？"}'
```

响应包含：DSL、SQL、列信息、行数、中文解释、可视化建议。

### 九、生产部署注意事项

1. **JWT 密钥**：必须替换为强随机密钥（`secrets.token_hex(32)`）
2. **启用严格模式**：`AUTH_STRICT=1` 时，弱密钥或关闭鉴权会拒绝启动
3. **绑定地址**：生产环境建议改为 `WEB_HOST=0.0.0.0`，非 localhost 自动进入严格模式
4. **会话共享**：多 worker 部署时配置 `AUTH_SESSION_DB` 使用 SQLite 落盘
5. **审计开关**：生产环境保持 `AUDIT_ENABLED=1`

---

## 📦 项目结构

```text
FutureBI/
├── semantic/     # 语义层：受限 DSL 契约 + 数据驱动字段目录
├── agent/        # NL -> DSL：LLM / 启发式双路径、意图路由、RAG、多轮记忆、重规划与反思
├── compiler/     # DSL -> 确定性 SQL
├── exec/         # SQL 执行层：只读 AST 校验 / 超时 / 熔断 / 连接池 / 自愈
├── tools/        # 多工具编排：查数 / 趋势 / 导出 / 口径解释
├── present/      # 解释 + 图表自适应推荐
├── security/     # 权限：表级 / 列级 / 行级 RLS（配置驱动）
├── auth/         # 身份认证：JWT + Session + 登录限流
├── audit/        # 审计快照 + 可观测性指标
├── web/          # Web UI / HTTP 服务 / 异步查询
├── eval/         # Golden 评测（25 用例，含多轮对话序列，双模式）
├── mock/         # 确定性 DuckDB 数仓
├── tests/        # 25 个测试文件（364 用例）
└── docs/         # 详细文档 + 评审归档
```

## 📚 文档

| 文档 | 内容 |
| --- | --- |
| [架构与设计](docs/architecture.md) | 架构总览、核心原则、技术栈、全链路能力 |
| [快速开始](docs/quickstart.md) | 环境准备、初始化、评测、Web UI、LLM 接入、离线自检 |
| [环境配置](docs/configuration.md) | 全部环境变量与生产注意事项 |
| [Web UI 与 API](docs/api.md) | 端点、鉴权与查询示例 |
| [安全模型](docs/security.md) | 认证、数据权限、演示账号 |
| [质量保障](docs/quality.md) | Golden 评测、CI、可复现锚点 |
| [演进里程碑](docs/roadmap.md) | 十二期能力演进 |
| [生产就绪评审](./docs/reviews/20260905-production-readiness-audit.md) | 生产就绪度评估与整改项 |
| [评审归档](./docs/reviews/README.md) | 历次评审落盘文件统一归档目录 |
| [工程约定](./AGENTS.md) | 面向 AI 协作者的运行环境与命令 |

## 🤝 贡献

欢迎通过 Issue 与 Pull Request 参与。开发前请阅读 [工程约定](./AGENTS.md)，提交前确保：

```bash
black --check .
ruff check .
python -m pytest -q
```

## 📄 License

[MIT](./LICENSE) © 2026 FutureBI contributors
