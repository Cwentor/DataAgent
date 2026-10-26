# 项目文档总索引与分类规范（docs/）

> 本文件是 `docs/` 的唯一分类与命名规范来源：新增文档一律按下述五类归档，
> 并在本文件 §四 索引登记。命名规范于 2026-10-26 全量统一（历史文件已迁移
> 改名，git 历史可追溯）。

## 一、目录分类

| 目录 | 类别 | 内容 | 命名 |
|------|------|------|------|
| guides/ | 稳定指南 | 面向使用者的长期文档（架构/快速开始/配置/API/安全/质量/路线图） | 稳定名，无日期 |
| designs/ | 设计方案 | 每期设计方案（Spec）与设计分析文档 | `YYYY-MM-DD-<topic>-design.md` / `-analysis.md` |
| plans/ | 实施计划 | 每期实施计划（步骤级勾选跟踪） | `YYYY-MM-DD-<topic>-plan.md` |
| tasks/ | 任务记录 | 每期任务级状态跟踪与执行日志 | `YYYY-MM-DD-<topic>-tasks.md` |
| reviews/ | 评审归档 | 评审落盘文件（类型词与标题格式见 [reviews/README.md](reviews/README.md)） | `YYYY-MM-DD-<type>-<topic>.md` |
| assets/ | 静态资源 | logo 等图片资源 | — |

## 二、命名规范（统一约束）

1. **日期**：一律 ISO `YYYY-MM-DD`（严禁 8 位紧凑格式 `yyyymmdd`）；
2. **主题**：kebab-case 英文（专有名词保留原形）；
3. **类型后缀**：`-design`（设计方案）/ `-plan`（实施计划）/ `-tasks`（任务记录）/
   `-analysis`（设计分析）；评审类型词（pr-review / audit / readiness 等）见
   reviews/README.md；
4. **三件套**：同 stem 成套（`<date>-<topic>-design|-plan|-tasks`），跨类目互链用
   相对路径（如 designs 内引用同 stem plan 写 `../plans/<stem>-plan.md`）；
5. **新增流程**：文档落盘 → 按类归档 → 在本文件 §四 登记一行；
6. **引用约定**：代码注释与文档中引用 docs 文件一律用仓库根相对路径
   （如 `docs/designs/2026-10-06-gmall-mock-reverse-design.md`）。

## 三、稳定指南入口

[快速开始](guides/quickstart.md) · [架构与设计](guides/architecture.md) ·
[环境配置](guides/configuration.md) · [Web UI 与 API](guides/api.md) ·
[安全模型](guides/security.md) · [质量保障](guides/quality.md) ·
[演进里程碑](guides/roadmap.md)

## 四、期次总索引

| 期次 | 主题 | 设计方案 | 实施计划 | 任务记录 |
|------|------|---------|---------|---------|
| 一至十六 | ChatBI 基础栈（无逐期存档） | 选型复盘见 [analysis](designs/2026-10-26-tech-stack-selection-retrospective-analysis.md) §0 映射 | — | — |
| 立项 | core/ 四包（编排/沙箱/技能包） | — | [plan](plans/2026-09-09-enterprise-data-agent-plan.md) | — |
| 穿插修复 | 指示器中断检测 | — | [plan](plans/2026-09-15-indicator-interrupt-detection-plan.md) | — |
| 穿插修复 | 实现缺口修复（四项） | — | [plan](plans/2026-09-28-implementation-gaps-fix-plan.md) | — |
| 十七期 | harness 演进 M0-M4（LangGraph 单引擎收敛） | [design](designs/2026-09-24-agent-harness-evolution-design.md) | [plan](plans/2026-09-26-agent-harness-evolution-m0-m4-plan.md) | — |
| 十八期一期 | 意图路由与诚实兜底 | [design](designs/2026-09-27-intent-routing-honest-fallback-design.md) | [plan](plans/2026-09-27-intent-routing-honest-fallback-plan.md) | — |
| 十八期二期 | 意图诚实二期（Grounding 拦截重试） | [design](designs/2026-09-28-intent-honest-phase2-design.md) | [plan](plans/2026-09-28-intent-honest-phase2-plan.md) | — |
| 穿插特性 | 报告叙述化 | [design](designs/2026-09-29-analysis-report-narrative-design.md) | [plan](plans/2026-09-29-analysis-report-narrative-plan.md) | — |
| 穿插特性 | 分层降级兜底 | [design](designs/2026-09-29-degraded-fallback-design.md) | [plan](plans/2026-09-29-degraded-fallback-plan.md) | — |
| 穿插特性 | LLM 流式改造 | [design](designs/2026-09-30-llm-streaming-transport-design.md) | [plan](plans/2026-09-30-llm-streaming-transport-plan.md) | — |
| 十九期 | 能力边界扩展（三层同心圆，M1-M6） | [design](designs/2026-10-02-capability-boundary-expansion-design.md) | m1-m6：`plans/2026-10-02~03-capability-boundary-m{1..6}-plan.md` | — |
| 穿插特性 | 握手期超时安全重试 | [design](designs/2026-10-05-llm-handshake-retry-design.md) | [plan](plans/2026-10-05-llm-handshake-retry-plan.md) | — |
| 穿插特性 | 枚举作答审计握手 | [design](designs/2026-10-07-enum-answer-audit-handshake-design.md) | [plan](plans/2026-10-07-enum-answer-audit-handshake-plan.md) | — |
| 二十期 | Gmall mock 数仓全量替换（jar 反向解析） | [design](designs/2026-10-06-gmall-mock-reverse-design.md) | — | — |
| 穿插特性 | 语义去耦（semantic.json 单一事实源） | [design](designs/2026-10-07-semantic-decoupling-design.md) | [plan](plans/2026-10-07-semantic-decoupling-plan.md) | — |
| 二十一期 | MySQL 执行后端 | [design](designs/2026-10-25-mysql-support-design.md) | [plan](plans/2026-10-25-mysql-support-plan.md) | [tasks](tasks/2026-10-25-mysql-support-tasks.md) |
| 二十二期 | PostgreSQL 执行后端 | [design](designs/2026-10-26-postgres-backend-design.md) | [plan](plans/2026-10-26-postgres-backend-plan.md) | [tasks](tasks/2026-10-26-postgres-backend-tasks.md) |
| 二十三期 | ClickHouse 执行后端（分支 `feat/clickhouse-hive-backend`，合流时 design 迁入 designs/） | — | — | — |
| 选型复盘 | 全期技术栈选型对比矩阵（一至二十二期补记） | [analysis](designs/2026-10-26-tech-stack-selection-retrospective-analysis.md) | — | — |

> 评审归档清单见 [reviews/README.md](reviews/README.md)。
> 运行期产物（`logs/`、根目录 `analytics_sandbox.duckdb`）不入 docs 分类体系。
