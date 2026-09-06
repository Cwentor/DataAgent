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
