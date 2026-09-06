<h1 align="center">FutureBI</h1>

<p align="center">
  <b>企业级 ChatBI（Data Agent）—— 自然语言 → 受限 DSL → 确定性 SQL → DuckDB</b><br/>
  LLM 只产出受控 JSON，绝不直接生成裸 SQL：零幻觉、零注入、零随意 Join。
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white"/>
  <img alt="Pydantic" src="https://img.shields.io/badge/Pydantic-V2-E92063?style=flat-square&logo=pydantic&logoColor=white"/>
  <img alt="DuckDB" src="https://img.shields.io/badge/DuckDB-%E6%9C%AC%E5%9C%B0%E6%95%B0%E4%BB%93-FFF000?style=flat-square&logo=duckdb&logoColor=black"/>
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green?style=flat-square"/>
  <img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Cat-Drink/FutureBI/ci.yml?branch=master&style=flat-square&label=CI"/>
</p>

FutureBI 是一个规格驱动（Spec-Driven）的企业级 ChatBI / Data Agent 底座：以受限 DSL 为契约，
由确定性编译器产出 SQL，从机制上杜绝大模型幻觉与注入，并为查询提供解释、可视化、
权限治理、审计与可观测能力。

## ✨ 特性

- **受限 DSL 契约**：Pydantic V2 + `extra="forbid"`，字段、操作符、聚合均为受限枚举
- **确定性编译**：SQL 只由编译器生成，支持聚合/比率/时间/窗口/补零/Top-N/同比环比
- **纵深安全**：统一认证（JWT + Session）+ 生成前作用域 + 表/列/行级权限守卫
- **受控执行**：只读白名单、超时取消、扫描行数熔断、返回行数上限、SQL 自愈
- **可解释交付**：DSL → 中文话术 + 图表推荐 + Web UI，零前端框架
- **可观测**：全链路审计快照、结构化日志、QPS/分位数指标

## 🚀 快速开始

```bash
conda activate futurebi
pip install -r requirements-dev.txt
python -m mock.init_duckdb
python -m web.server 8000
```

浏览器打开 http://127.0.0.1:8000。更完整的安装、评测与离线自检步骤见 [快速开始](docs/quickstart.md)。

## 📦 项目结构

```text
FutureBI/
├── semantic/     # 语义层：受限 DSL 契约 + 字段目录
├── agent/        # NL -> DSL：LLM / 启发式双路径、意图路由、RAG、澄清
├── compiler/     # DSL -> 确定性 SQL
├── exec/         # SQL 执行层：超时 / 熔断 / 只读白名单 / 自愈
├── present/      # 解释 + 可视化推荐
├── security/     # 权限：表级 / 列级 / 行级 RLS
├── auth/         # 身份认证：JWT + Session
├── audit/        # 审计快照 + 可观测性指标
├── web/          # Web UI / HTTP 服务 / 静态前端
├── eval/         # Golden 评测（19 用例，双模式）
├── mock/         # 确定性 DuckDB 数仓
├── tests/        # 17 个测试文件（159 用例）
└── docs/         # 详细文档
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