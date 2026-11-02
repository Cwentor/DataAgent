# 设计方案｜定时化风险报表最小闭环（issue #26，2026-11）

> - **状态**：设计定稿，随实施演进。
> - **issue**：[#26 定时化风险报表产品](https://github.com/Cwentor/DataAgent/issues/26)（提前启动切片）。
> - **分支**：`feat/risk-report-mvp`（自 master `9cd22b9` 切出）。

## 1. 背景与定位

issue #26 是 #25（定时预警推送）场景能力的产品化整合愿景，正文约定双前置（真实数据源接入、#25 订阅与巡检落地）满足前仅作留档。#25 当前仍 OPEN，两个前置均未满足。本期为用户决策的**提前启动切片**，交付风险报表的机制骨架，不等待前置。

#26 概念基线中风险报表 (risk report) 以"发现问题"为核心，区别于普通报表"呈现发生了什么"。围绕风险指标配判定规则，输出红黄绿状态与异常清单，结论前置、佐证在后，读者是不主动看数的角色。定时化分三层次，定时全量报表、定时增量巡检（告警）、事件驱动告警，从重到轻。

本期交付四件事。

1. 判定规则契约化登记（config 独立文件，调整走契约变更流程）；
2. 确定性巡检执行器（规则直产 DSL，经治理管道全护栏取数，不进 LangGraph）；
3. 风险报表确定性渲染（红黄绿状态、异常清单、结论前置、证据链附录，零 LLM 依赖）；
4. CLI 触发入口（外部 cron 调度，退出码可被监控感知）。

## 2. 范围界定

**本期不做**（后置件清单见 §9）。

- 订阅管理（谁在什么时间收到什么范围）；
- 进程内调度器（外部 cron 调 CLI 即本期定时化形态）；
- 投递渠道（webhook / 邮件 / IM 渠道抽象）；
- 前端报表存档视图；
- LLM 叙述化（报告纯确定性渲染，复用 synthesize 叙述化留作后续增量）。

**边界诚实声明**。mock 数仓为静态确定性数据（种子 42、锚定 AS_OF_DATE=2025-12-31），本期可完整验证**判定机制的确定性**（同一输入必出同一报告、规则调整仅改 json 即生效、红黄绿边界可测），无法验证**异动预警的业务价值**（数据不会真实异动，触发条件与 #25 同源）。这一边界与数据层边界分析（`2026-11-01-data-layer-scope-boundary-analysis.md`）的判定一致，本期不改变数据形态、不触碰确定性三原则、不触发 golden 重固化。

## 3. 选型对比矩阵

### 3.1 判定规则登记位置

| 候选 | 结论 | 理由 |
|---|---|---|
| semantic.json 新增节 | 否 | semantic.json 定位是静态业务事实源（字段、口径、词表），风险阈值是运营策略配置，混入稀释单一职责 |
| 独立 config/risk_rules.json 加专属 loader（采用） | 采用 | 有 policies.json 策略类配置独立文件加 refresh 的先例；fail-closed 校验照抄 catalog_loader 模式；规则调整的 diff 与评审走 json 变更流程，契合 #26 增量 5 |
| 代码内常量注册表 | 否 | 违背"判定规则调整走契约登记变更"的产品化要求，改动需发版 |

### 3.2 巡检取数路径

| 候选 | 结论 | 理由 |
|---|---|---|
| 规则引用指标 key，shape 直译为 DSL，经 execute_dsl_query 全护栏执行（采用） | 采用 | 指标口径单一事实源不重复登记（semantic.json metrics[] 已有 refund_rate 等 7 指标的 shape 契约）；与 L1 确定性核同构；RLS、扫描熔断、超时、PII 脱敏零旁路 |
| 规则内嵌完整 DSL | 否 | 指标口径双登记，metrics 节与规则文件必然漂移 |
| 复用 web.service.run_query | 否 | NL 入口走 Planner 路由，不确定性高，且绑定 web 层与审计编排语义 |
| LLM 生成 SQL 或手写裸 SQL | 永久禁止 | 架构铁律，裸 SQL 网关守卫直接拒绝 |

### 3.3 定时化形态

| 候选 | 结论 | 理由 |
|---|---|---|
| CLI 入口，外部 cron 调度（采用） | 采用 | 数据层边界分析"边界宣言"既定形态（稳定 CLI 供外部调度器）；零新增常驻进程；CI 与单机部署友好 |
| 进程内调度器线程 | 后置 | 属订阅调度切片，与无人值守和 interrupt 审批门的冲突纠缠，本期不碰 |
| OS 任务计划封装 | 否 | 平台差异大，超出项目边界 |

### 3.4 报告产物形态

| 候选 | 结论 | 理由 |
|---|---|---|
| 确定性 Markdown 落盘 logs/risk_reports/（采用） | 采用 | 零依赖、可 diff 可存档、渲染快照可测试 |
| 同时产 xlsx | 后置 | #28 链路面向查询结果集，报告级 xlsx 语义待定 |
| 落库加 API 供前端消费 | 后置 | 属订阅存档切片 |

## 4. 规则契约（config/risk_rules.json）

```json
{
  "version": 1,
  "rules": [
    {
      "key": "refund_rate_daily",
      "title": "退款率日巡检",
      "metric": "refund_rate",
      "query_domain": "trade",
      "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},
      "dimension": null,
      "thresholds": {"red": 0.05, "yellow": 0.03},
      "comparison": null
    },
    {
      "key": "gmv_dod_drop",
      "title": "GMV 日环比异动",
      "metric": "gmv",
      "query_domain": "trade",
      "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},
      "dimension": null,
      "thresholds": null,
      "comparison": {"direction": "drop", "red": 0.3, "yellow": 0.15}
    }
  ]
}
```

契约要点。

- `metric` 必须命中 `catalog.METRICS` 已登记 key，未知 key 快速失败；指标口径（公式、fields、shape）全部引用语义目录，规则文件零重复登记；
- 两类判定形态覆盖 #26 概念基线。绝对阈值 `thresholds`（当期值超红/黄线）与相对异动 `comparison`（当期对基线变化率超红/黄线），可单配可同配（同配时取更严状态）；
- `window.current` 显式登记日期，严禁"今天"类相对时间（锚定确定性）；`baseline: previous` 按粒度推算上一期（day 前一日、week 前一周周一对齐、month 前一月、quarter 前一季度）；
- `dimension` 可选（如 province），非空时按维度成员分组巡检，异常清单精确到成员；
- `query_domain` 本期仅支持 trade（交易域锚点 order_detail），字段预留流量域扩展；
- 窗口越界数据域（2021-01-01 至 2025-12-31）loader 拒绝。

## 5. 架构设计

### 5.1 模块布局（顶层新包 risk/）

```
risk/
  __init__.py     导出 load_rules / run_inspection / render_report
  rules.py        RiskRule 契约 + fail-closed loader + 窗口推算纯函数
  executor.py     DSL 组装 + 护栏取数 + 判定引擎 + 审计落盘
  report.py       确定性 Markdown 渲染 + 证据链附录
  __main__.py     CLI 入口（退出码三态）
config/risk_rules.json   种子规则
tests/risk/              rules / executor / report / cli 四组测试
```

依赖方向：risk 依赖 semantic/catalog（指标 shape）、core/retrieval（execute_dsl_query）、audit（AuditStore）；不依赖 agent、orchestrator、compiler 上层，risk 位于治理管道的消费侧。

### 5.2 执行器（risk/executor.py）

流程五步。

1. **DSL 组装**。规则 metric key 查 `catalog.METRICS` 取 shape，直译为 DSL metrics 条目（直译逻辑参照 `agent/heuristic.py` `_metric_from_shape` 先例，risk 内自实现，纯数据变换）；时间窗口用 `TimeFilter` 的 absolute 绝对窗口形态（`range_type=absolute`，start/end 为推算出的窗口边界，字段取规则 `time_field`，全部在 DSL 契约白名单内）；dimension 非空时加 dimensions；
2. **当期与基线取数**。两次 `execute_dsl_query(dsl, principal=, workspace=, name=, query=)`，全护栏（RLS、只读、扫描熔断、超时、PII 脱敏）零旁路，返回 ParquetRef（dsl、sha256、audit{guard,qa}）；
3. **数值提取**。从结果行提取指标标量（dimension 分组时按成员对齐当期与基线）；
4. **判定引擎**。绝对阈值与相对变化两类判定，输出 green/yellow/red 加触发理由（含数值、阈值、基线快照）；基线为零或缺失时异动不可判定，如实标注（诚实红线，严禁虚构变化率）；
5. **审计落盘**。每规则写一条 AuditRecord（prompt 带 `[risk]` 前缀与规则 key，dsl/sql 取当期查询），复用 AuditStore，维持审计连续性。

### 5.3 报告渲染（risk/report.py）

Markdown 模板确定性填充，章节顺序固定。

1. **总体结论**（前置）。巡检规则数、红黄绿计数、整体状态一句话；
2. **指标分区**。逐规则小节，状态表（指标、当期值、基线值、变化、状态）加判定说明；
3. **异常清单**。黄红项汇总表（级别、规则、对象、当期值、阈值或基线、触发理由）；
4. **证据链附录**。逐异常项引用 DSL、SQL、数据 sha256、guard/qa 发现、审计 request_id，全部取自 ParquetRef 与 AuditRecord 现成字段；
5. **口径与假设**。指标 definition（语义目录原文）、窗口口径、数据域锚定说明。

### 5.4 CLI（risk/__main__.py）

```
python -m risk --date 2025-12-31 --principal analyst
```

参数 `--rules`（默认 settings.RISK_RULES_PATH）、`--principal`（RLS 主体，默认 admin）、`--out`（默认 settings.RISK_REPORT_DIR）、`--date`（覆盖规则窗口当期日，便于重放历史日）。产出 `risk_report_YYYYMMDD.md`。退出码三态，全部绿为 0，存在红项或未知项（数据缺失、权限不足、执行失败，需人工核查）为 1，规则加载失败或巡检无法执行为 2（外部监控可感知）。

## 6. 治理哲学契合

- 判定规则契约化登记，与"LLM 不自由生成事实"同构，规则是配置事实不是模型意见；
- 结论可溯源，异常项带 DSL、SQL、sha256、审计 id 全链证据；
- 权限随执行走，巡检索经 apply_policy 注入 RLS，principal 参数即作用域声明，订阅化后订阅本身即作用域（后置件直接衔接）；
- 诚实红线，基线缺失不虚构变化率，报告如实标注不可判定项。

## 7. 与确定性三原则的关系

不触碰。种子 42 与数据生成零改动；锚定 AS_OF_DATE，规则窗口显式登记日期；报告渲染纯函数，同输入同输出。golden 评测零回归（新增代码全部在 risk/ 新包，不改动既有链路）。

## 8. 测试与验收

- rules 契约测试。缺字段、未知指标 key、窗口越界、红黄阈值倒挂、comparison 方向非法均拒绝且报错清单可用；
- executor 判定测试。红黄绿边界值、环比计算、基线缺失如实标注、维度分组对齐；
- report 快照测试。确定性输出逐字断言；
- CLI 冒烟。tmp 目录产物存在与退出码三态；
- 门禁。black、ruff、pytest 全绿，`python -m eval.eval_runner` 31 条双模式零回归；
- 端到端实跑。worktree 内 `python -m mock.init_duckdb` 后 `python -m risk --date 2025-12-31`，人工核验报告四要素；人为调低阈值重跑，异常清单相应变化。

## 9. 后置件清单（触发即立项）

| 后置件 | 触发条件 | 衔接点 |
|---|---|---|
| 订阅管理 + 进程内调度器 | #25 立项（真实数据源接入） | 规则契约已独立 json，订阅表复用 SqliteKVStore 模式，调度器按 #25 形态约束（确定性执行器不进 LangGraph） |
| 投递渠道（webhook 优先） | 同上 | 报告 Markdown 即投递载荷，webhook 遵循 #25 约束 |
| 前端报表存档视图 | 报告落库或 API 化后 | 渲染 Markdown 前端已有 MdLite 渲染器 |
| LLM 叙述化增强 | provider 稳定且有人读价值 | 报告分区结构即 synthesize 四段式同构，摘要节点可插拔 |
| 流量域风险规则 | 业务需要 | query_domain 字段已预留 |
