# 设计方案｜扫描行预检两段式（duckdb 先行）

> - **状态**：设计草案，待评审（2026-10-27）。
> - **来源**：[../reviews/2026-10-27-audit-project-issues.md](../reviews/2026-10-27-audit-project-issues.md) §9.3 第一条、§9.5「预检开销」。
> - **实施计划**：待本设计评审通过后另立（`plans/` 同 stem）。
> - **分支**：建议 `feat/scan-precheck-two-stage`（自 master 切出）。

## 1. 背景与目标

`exec/guards.py::execute_sql` 在执行前做扫描行预检，预检实现是
`DuckDbAdapter.explain_scan_rows`：

```python
def explain_scan_rows(self, conn, sql, timeout_ms):
    plan_sql = "EXPLAIN ANALYZE " + sql          # ← 会把查询真的执行完
    _, plan_rows = run_with_timeout(self, conn, plan_sql, timeout_ms)
    ...
    return parse_scan_rows(plan_text), plan_rows_measurable(plan_text)
```

DuckDB 的 `EXPLAIN ANALYZE` 语义是「执行一遍并报实际行数」，不是「只看计划」。
于是**同一条查询被完整跑了两遍**：预检一遍拿行数，真正取结果再一遍。审计实测
冷查询延迟约 2×（33.8ms 直接执行 vs 29.3ms 预检）。

**目标（3 条）**：

1. 预检成本从「一次完整执行」降到「一次规划」，冷查询端到端延迟回到 ≈1×；
2. **不降低熔断有效性**——不允许因为省开销而放行本该熔断的查询；
3. 熔断口径的语义不悄悄漂移（`scan_rows` 到底是实测还是估算必须可分辨）。

非目标：不改 `MAX_RESULT_ROWS` / 超时护栏；不改 ClickHouse / Hive 的「诚实
不可计量」结论；不重构 `guards.execute_sql` 的整体结构。

## 0. 计划期实测纪要（2026-10-27，本机 `analytics_sandbox.duckdb`）

### 0.1 单查询：`EXPLAIN` 恒定是 `EXPLAIN ANALYZE` 的 1/2~1/3

warm，取 3 次最好值：

| 查询 | `EXPLAIN` | `EXPLAIN ANALYZE` | 直接执行 |
|---|---|---|---|
| `SELECT 1` | 0.24ms | 0.36ms | 0.12ms |
| 点查（带过滤） | 0.50ms | 0.72ms | 0.31ms |
| `count(*)` 全表 | 0.33ms | 0.38ms | 0.24ms |
| 分组聚合 | 0.56ms | 1.07ms | 0.50ms |

规律：`ANALYZE ≈ 规划 + 执行`，`EXPLAIN` 只含规划部分。

### 0.2 golden 全量 35 条：合计省 45%

| 口径 | 合计耗时 |
|---|---|
| `EXPLAIN`（第一段） | 38.1ms |
| `EXPLAIN ANALYZE`（现在的预检） | 171.7ms |
| 直接执行 | 125.6ms |
| **现在：ANALYZE + 执行** | **297.3ms** |
| **两段式：全部走第一段 + 执行** | **163.7ms** |

第一段只占 ANALYZE 的 22%。若 35 条全部命中跳过条件，端到端省 133.6ms（45%）。

### 0.3 估算偏差：双向都很大，且高估远大于低估

35 条 golden DSL 全部可编译、两种计划都能解析出行数（无一条缺失）：

| 指标 | 值 |
|---|---|
| 低估条数 | 6 / 35（17%） |
| 最大低估 | **3.21×**（est 3,755 → actual 12,060） |
| 最大高估 | **750.75×** |
| 中位比值 | 0.06（即典型情况下估算偏高约 16×） |

**这是本设计的核心约束**：估算绝不能当熔断值用——750 倍的高估会把合法查询
误熔断，而误熔断比不熔断更糟（直接拒绝正确查询）。

### 0.4 跳过率

| 阈值 `MAX_SCAN_ROWS` | 安全系数 | 可跳过第二段 |
|---|---|---|
| 10,000,000（默认） | 4 | 35 / 35 |
| 10,000,000（默认） | 8 | 35 / 35 |
| 100,000 | 4 | 35 / 35 |
| 100,000 | 8 | 32 / 35 |

默认阈值下第二段基本不会被触发；阈值收紧到十万级时仍有 91% 跳过率。

### 0.5 不可计量：`count(*)` 两种计划都拿不到行数

`SELECT count(*) FROM fact_display`（46.7 万行）在 `EXPLAIN` 与
`EXPLAIN ANALYZE` 下**都没有** `rows` 行——DuckDB 走元数据快路径。
所以「第一段拿不到估算」不是异常，而是必须显式处理的常态分支（见 D3）。

## 2. 现状与耦合点

| 位置 | 现状 | 本设计是否改动 |
|---|---|---|
| `exec/engine/duckdb_adapter.py:43` `explain_scan_rows` | 固定 `EXPLAIN ANALYZE`，返回 `(rows, measurable)` | **改**（内部两段式，签名不变） |
| `exec/engine/plan_text.py` `parse_scan_rows` | 正则 `(\d[\d,]*)\s+rows`，`~` 前缀也能匹配 → 估算与实测不可区分 | **改**（新增估算解析，旧函数语义不动） |
| `exec/guards.py` 预检分支 | 命中缓存跳过；未命中调 adapter，`measurable` 才缓存 | **改**（区分两段、只缓存实测） |
| `exec/guards.py` 扫描行缓存 | 键 = 后端作用域 + SQL，带 TTL（审计 §9.1 已修） | 不改结构，改写入条件 |
| `exec/guards.py::ExecutionResult.scan_rows` | 单一数值，下游进审计 | **改**（新增 `scan_rows_estimated`） |
| `audit/store.py` 审计表 `scan_rows` | 单列 BIGINT | **本期不改**（见 D6） |
| mysql / postgres adapter | 同样用 `EXPLAIN ANALYZE`，同样跑两遍 | **本期不改**（见 D5） |
| clickhouse / hive adapter | 诚实返回 `(0, False)` | 不改 |

## 3. 方案

```
                    ┌─ 命中缓存（实测值） ────────────────→ 用缓存值判熔断
                    │
收到 SQL → 查缓存 ──┤
                    │        est 可解析 且 est×SAFETY < 阈值
                    └─ 第一段 EXPLAIN ──┬────────────────→ 放行（estimated=True）
                                        │
                                        └─ 否则 ─ 第二段 EXPLAIN ANALYZE
                                                    → 拿实测值（estimated=False）
                                                    → 写缓存
                    ↓
             scan_rows > max_scan_rows ? 熔断 : 执行
```

判定式（`SF` = `SCAN_PRECHECK_SAFETY_FACTOR`）：

```python
if est is not None and est * SF < max_scan_rows:
    scan_rows, estimated = est, True          # 第一段放行，不执行
else:
    scan_rows, estimated = analyze_exact(...), False   # 第二段
```

放行的安全性论证：放行条件是 `est × SF < 阈值`。只要该查询的
`实测 / 估算 ≤ SF`，就有 `实测 ≤ est × SF < 阈值`，即**放行不会漏熔断**。
`SF = 8` 已覆盖实测最大低估 3.21×（§0.3）。这个性质依赖数据分布，所以 D2 把
「系数可配 + 定期校准」写成了验收项，而不是把 8 当成永久常数。

## 4. 关键决策

### D1 估算只做「跳过判据」，绝不做「熔断值」

实测最大高估 750×（§0.3）。若把估算直接当 `scan_rows` 比阈值，一条实际只扫
几百行的查询会因为估算几十万行被熔断——**误杀合法查询**。因此估算只参与
「是否可以不做第二段」的判断，一旦需要熔断判定，必须用实测值。

对应地，放行分支里 `scan_rows` 记录的是估算值，只用于审计留痕与日志，且已由
判定式保证 `< 阈值 / SF`，不可能触发熔断。

### D2 安全系数默认 8，配置化

`SCAN_PRECHECK_SAFETY_FACTOR` 默认 8。依据：golden 35 条实测最大低估 3.21×，
留约 2.5 倍余量。取 2 的幂便于心算，也避免「4 刚好卡住实测边界」的脆弱性。

该系数与数据分布强相关，需在以下情况重新校准（写入 §6 验收）：
数据量级变化超过一个数量级、新增带高选择性过滤的典型问法、DuckDB 大版本升级。

### D3 估算不可解析时回退第二段，并保留「不可计量」告警

§0.5 已证 `count(*)` 类查询两种计划都拿不到行数。处理规则：

| 第一段结果 | 动作 |
|---|---|
| 解析出 `~N rows` | 按 D1/D2 判定是否跳过 |
| 无任何行数行（不可计量） | **必须**走第二段，不得视为 `est = 0` 放行 |
| 第二段也不可计量 | 维持现状：`measurable=False`，不缓存，告警去重一次 |

这条是本次改动最容易写错的地方：把「测不到」当成「扫了 0 行」正是
`requirements.txt` 里 duckdb 2.0 事故注释所记录的失败模式。

### D4 只缓存实测值，估算不进缓存

缓存键结构（作用域 + SQL + TTL）不变，但**只在第二段写入**。理由：第一段本身
只有 0.3~0.6ms，缓存它没有收益；而一旦把估算写进以 `scan_rows` 为名的缓存，
后续 TTL 窗口内所有查询都会拿着估算值做判定，等于把 D1 的红线绕过——与审计
§9.1「缓存过时致熔断静默失效」是同一类错误。

### D5 本期只改 duckdb

MySQL `EXPLAIN`（不带 ANALYZE）与 PostgreSQL `EXPLAIN` 同样给估算行数，
原理上两段式对它们成立，但三者计划文本格式、解析器、估算质量各不相同
（`parse_mysql_scan_rows` / `parse_pg_scan_rows`）。本期只改默认后端 duckdb，
把「协议怎么变、判据怎么定」先钉死；mysql/pg 另立一期，复用本设计的判据与
安全系数方法论，各自校准系数。

ClickHouse / Hive 明确不改：它们本就返回不可计量，没有「跑两遍」问题。

### D6 `ExecutionResult` 加 `scan_rows_estimated`，审计表本期不加列

`scan_rows` 的语义从「实测」变成「实测或估算」，必须在结果对象上可分辨，
否则下游（报表、评测、排障）会拿估算值当实测值做趋势对比。

但审计表**本期不加列**。理由是牵连面比预检本身还大，四处要同步改：
`audit/store.py::_INSERT_SQL`、`_migrate_schema`、`_record_params`、
以及 `scripts/audit_rebuild.py::_parse_line` 的字段白名单——漏一处就是
§8.3「两个入口各写一份」的重演。本期先用日志 + `ExecutionResult` 留痕，
审计列作为独立小项排期。

### D7 开关默认开启，保留一键回退

`SCAN_PRECHECK_TWO_STAGE`（默认 `1`）。置 `0` 时 `explain_scan_rows` 回到
「直接 `EXPLAIN ANALYZE`」的旧行为，用于线上出现疑似误熔断时快速定位
「是不是预检判据的问题」。开关只影响预检路径，不影响熔断与超时语义。

## 5. 明确不做

| 不做 | 原因 |
|---|---|
| 用估算值替代熔断值 | 最大高估 750×，会误杀（D1） |
| 把估算写进扫描行缓存 | 等于绕过 D1（D4） |
| 顺手改 mysql / postgres | 解析器各异，需各自校准，另立一期（D5） |
| 给审计表加 `scan_rows_source` 列 | 四处联动，风险大于收益，独立排期（D6） |
| 用 `EXPLAIN` 完全取代预检 | 见 D1 |
| 改 `max_result_rows` / `statement_timeout_ms` | 与预检无关，硬边界不动 |

## 6. 验收标准

**单测（`tests/exec/test_scan_precheck.py`，新增 6 条）**

1. 估算远低于阈值 → 只发 `EXPLAIN`，`EXPLAIN ANALYZE` **一次都不发**（用 spy 断言调用序列）；
2. 估算接近/超过阈值 → 发两条，熔断判定用实测值；
3. 估算不可解析（`count(*)` 元数据快路径）→ 走第二段，且告警只出一次；
4. 两段都不可计量 → `measurable=False`，**不写缓存**；
5. 第一段放行后 `scan_rows_estimated is True`，第二段为 `False`；
6. 缓存里只出现实测值：连续两次第一段放行同一 SQL，第二次仍走第一段（不因缓存变成实测）。

**变异验证（必须有，否则用例是空转）**

- 把判定式改成 `est < max_scan_rows`（去掉安全系数）→ 用例 1/2 必须失败；
- 把估算值写入缓存 → 用例 6 必须失败。

**回归**

- `tests/exec/test_exec.py` 既有扫描行 / 熔断 / 超时用例全绿；
- 四后端 parity 不回归（含本轮新增的超时用例）；
- 全量 `pytest -q` 基线不下降。

**性能（同一台机器、warm、35 条 golden 合计）**

| 指标 | 目标 |
|---|---|
| 预检+执行合计 | ≤ 200ms（当前 297.3ms） |
| 第一段耗时占 ANALYZE 比例 | ≤ 30%（当前 22%） |
| 第二段触发次数 | 默认阈值下 0 次（35 条） |

**安全系数校准**：随实现交付一次性脚本（或 `scripts/` 下的可复用命令），
输入为 golden 全量 DSL + 当前库，输出「低估条数 / 最大低估 / 最大高估 /
不同 SF 下的跳过率」，校准结果回写本文件 §0.3。

## 7. 配置项（`.env.example` 同步）

| 变量 | 默认 | 含义 |
|---|---|---|
| `SCAN_PRECHECK_TWO_STAGE` | `1` | 两段式开关；`0` 回退旧行为（D7） |
| `SCAN_PRECHECK_SAFETY_FACTOR` | `8` | 估算放大倍数，放行条件 `est × SF < MAX_SCAN_ROWS`（D2） |

## 8. 风险与遗留

| 风险 | 处置 |
|---|---|
| 安全系数随数据分布漂移而失效 | §6 校准脚本 + 明确的重新校准触发条件；系数可配置无需改码 |
| 估算低估超出 SF 导致漏熔断 | 漏熔断的后果是查询变慢而非错误（超时与结果行数硬线仍在），属可接受降级；日志记录 `estimated=True` 便于事后追溯 |
| 第一段 `EXPLAIN` 本身失败（语法/权限） | 与现状同路径：归一为引擎错误，走自愈重试 |
| 审计 `scan_rows` 语义混杂 | D6 的独立小项补齐前，用 `scan_rows_estimated` 在日志与结果对象区分 |

**遗留（本设计不覆盖，需另排）**：mysql/pg 两段式（D5）、审计
`scan_rows_source` 列（D6）、SSE 线程上限与审计锁拆分（审计 §9.3 其余项）。
