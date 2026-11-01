# 设计方案｜查询耗时可观测与慢案例回流评测（#23）（2026-11）

对应 issue：#23（查询耗时可观测与慢案例回流评测——生成质量治理，非 DBA 域慢查询优化）。

## 1. 背景与边界

同一自然语言问题可对应多种等价 DSL 写法，不同写法编译出的 SQL 执行速度不同，结果一致
但体验不同。责任在链路自身产出（LLM 选了哪条 DSL 路径），属于生成质量治理。

该风险目前是推演而非已观测——mock 数仓量级有限，DSL 写法差异在此规模上测不出显著快慢
差异。因此本设计只做两件事：**可观测先行**（耗时指标透出 + 慢案例标注）与**回流评测
流程**（人工复核后固化评测集）；编译器等价改写仅在真实规模出现证据后再立项，本次明确
不做。索引推荐 / 参数调优 / EXPLAIN 诊断面等 DBA 域能力维持边界不变。

## 2. 现状核实（已有覆盖，不必重做）

- `exec/guards.py` `execute_sql` 是全链路唯一 SQL 执行收口点（`core/retrieval/tools.py`
  与 `core/retrieval/exploration.py` 两条取数路径、tool_agent 经 executor 绑定均走到它），
  已产出 `ExecutionResult.duration_ms`（预检段 + 执行段合计）——但该值没有流向
  `AuditRecord` 与 `/api/metrics`，落盘的 `latency_ms` 是含 LLM 调用的全链路耗时，
  隔离不出 SQL 执行快慢；
- `audit/record.py` 已落盘耗时 / 返回行数 / 扫描行数，`audit/analysis.py` 已有
  P50 / P95 只读分析骨架（`routing_funnel_report`，conn 注入可测 + CLI）；
- audit_log 表无 `query_domain` / `db_backend` 列；store 幂等迁移机制现成
  （逐列对比 information_schema 后 ALTER 补齐）；
- 编译器 `_resolve_anchor(dsl)` 已可从 DSL 解析查询域锚点（跨域引用抛 CompileError，
  能执行成功的 DSL 必然已过编译），但为私有函数。

## 3. 选型对比矩阵

### D1 SQL 执行耗时采集路径

| 候选 | 机制 | 优点 | 缺点 | 裁决 |
|---|---|---|---|---|
| A. ExecutionResult 层层透传 | duration_ms 进 ParquetRef.audit / AgentResult 汇总上抛 | 契约显式 | 三条执行链逐层加传参，侵入面大；自愈重试的多次执行难逐次留痕 | 否决 |
| B. contextvar 收集器（#22 同模式） | `exec/usage.py` 收集器，execute_sql 成功路径自登记 | 零传参覆盖全部执行点；per-execution 明细天然可留；与 llm_usage（#22）同构，绑定/清理模式已验证 | 隐式上下文依赖 | **采纳** |
| C. execute_sql 直接调 MetricsRegistry | exec 层直接打点进程指标 | 路径最短 | 底层包依赖倒挂（exec → audit.metrics）；AuditRecord 拿不到 per-query 明细 | 否决 |

### D2 query_domain 维度来源

| 候选 | 裁决 |
|---|---|
| A. exec 层推导 | 否决——exec 不感知业务语义（与 sandbox 同款铁律） |
| B. 分析侧事后从 audit_log.dsl JSON 现场推导 | 否决——metrics 内存打点发生在执行期拿不到该值；读侧每次重复推导 |
| C. 写侧一次推导落盘列（resolve_anchor 公开化） | **采纳**——SQL group by 友好，读侧零逻辑 |

维度键取锚点表名（order_detail / fact_page_view 等），即 `catalog.QUERY_DOMAINS` 的
自然键，不新造域名映射；探索层 SQL 无 DSL，domain 落 NULL（诚实缺省）。

### D3 audit_log 落盘形态

| 候选 | 裁决 |
|---|---|
| A. 仅标量列（sql_latency_ms） | 缺自愈重试的 per-execution 明细 |
| B. 仅 JSON 明细列 | SQL 聚合不便（按行算分位数是分析主路径） |
| C. 混合：`query_domain` / `db_backend` / `sql_latency_ms` 三标量列 + `sql_exec` JSON 明细列 | **采纳**——标量承载聚合，JSON 承载明细，与 #22 的 llm_usage JSON 列同构 |

`sql_latency_ms` 口径 = 本问答**最后一次成功执行**的耗时（与落盘的最终 SQL 对应）；
失败执行（超时 / 熔断）不记耗时（到熔断为止的时长有偏，熔断事件已由
`circuit_breakers` 计数覆盖）。`duration_ms` 含扫描预检段，属治理开销，snapshot 文档
注明口径，不虚构"纯执行耗时"。

### D4 慢案例标注位置

| 候选 | 裁决 |
|---|---|
| A. metrics registry 在线标注 | 否决——进程内存重启即失，"历史 P95"必须基于落盘全量 |
| B. audit/analysis.py 离线只读报告（新增 query_latency_report） | **采纳**——与 routing_funnel_report 同构（只读 / conn 注入可测 / CLI），分析库即审计库 |
| C. 独立 scripts 一次性入口 | 标注逻辑应可复用进回流脚本，进 analysis；回流提取走 scripts（见 D5） |

慢案例定义：`(query_domain, db_backend)` 分组内 `sql_latency_ms > 组内历史 P95` 的
成功执行记录。分组样本 < `min_samples`（默认 20）时输出 `insufficient_sample` 标记
而**不标注任何慢案例**——样本不足时分位数无统计意义，严禁虚构显著性（诚实红线）。
只标注、不建议、不诊断。

### D5 慢案例回流评测形态

| 候选 | 裁决 |
|---|---|
| A. 提取后自动并入 golden | 否决——issue 明确"人工复核属实的案例固化进评测集"，自动并入绕过人工裁决 |
| B. `scripts/slow_case_backfill.py` 生成 golden 候选 JSON（SC 前缀 id + DSL 形态断言所需字段），人工复核后并入 `eval/golden_dataset.json` | **采纳** |

回流断言只锚定 DSL/SQL 形态（契约层），不锚定耗时数值——耗时会随数据量与机器漂移，
锚耗时等于锚噪声。

## 4. 改动面

1. `exec/usage.py`（新建）：`ExecUsageLedger` + contextvar + set/pop/snapshot，
   每次成功执行登记 `{duration_ms, scan_rows, row_count, backend}`；
2. `exec/guards.py`：`execute_sql` 成功路径登记（backend 读 `settings.DB_BACKEND`）；
3. `compiler/sql_compiler.py`：`_resolve_anchor` 公开化为 `resolve_anchor`
   （十九期 referenced_fields 公开化同款先例），内部调用点同步改名；
4. `audit/metrics.py`：`record_sql_exec(duration_ms, backend, domain)` +
   snapshot 新增 `query_latency` 节点（全局 / by_domain / by_backend 的
   count / p50 / p95 / max，窗口与 latency 同为最近 N 条；维度键均为有界集合，
   表锚点 6 个 × 后端 5 个，内存安全）；
5. `audit/record.py` + `audit/store.py`：AuditRecord 加
   `query_domain` / `db_backend` / `sql_latency_ms` / `sql_exec` 四字段，
   store 列类型 / DDL / INSERT / params 同步，幂等迁移自动补列；
6. `web/service.py`：出口取出 ledger snapshot → 推导 domain →
   registry 打点 → AuditRecord 落盘；`web/api.py` 编排链路同位置绑定
   （与 #22 UsageLedger 绑定点一致）；
7. `audit/analysis.py`：`query_latency_report`（分组分位数 + 慢案例清单 +
   insufficient_sample 守护）+ CLI `--report {intent,query_latency}`（默认 intent 兼容）；
8. `scripts/slow_case_backfill.py`：读审计库提取慢案例 → 生成 golden 候选 JSON；
9. 测试：exec 收集器绑定/登记/残留清理/多次执行、metrics 聚合、store 迁移补列、
   analysis 标注（超 P95 标注 / 样本不足不标注）、web 快照节点、回流脚本提取逻辑。

## 5. 验收对照（issue #23）

- [x] `/api/metrics` 出现查询耗时指标，与 audit 记录同源（同一登记动作双落）；
- [x] 慢案例在 audit 分析报告中可标注、可检视（query_latency_report + CLI）；
- [x] 慢案例回流评测集流程跑通（脚本生成候选 → 人工复核 → 并入 golden；
      mock 规模无真实慢案例时以合成审计库演示全流程，测试守护）；
- [x] 测试覆盖（指标聚合、标注逻辑、迁移、收集器）；
- [x] `black` / `ruff` / `pytest` 全绿（1462 passed + 28 skipped，2026-11-02 收口）。

## 6. 明确不做

- 索引自动推荐、参数调优、分区建议等物理层一切；
- 面向用户的调优建议输出（EXPLAIN 诊断面）；
- 估算类"优化"与耗时数值的虚构补齐；
- 编译器等价改写（无真实规模证据不立项）。
