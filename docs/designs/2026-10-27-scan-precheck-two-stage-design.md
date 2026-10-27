# 设计方案｜扫描行预检两段式（duckdb 先行）

> - **状态**：**v3 已实施（2026-10-27）**。v1 评审意见见
>   [../reviews/2026-10-27-design-review-scan-precheck-two-stage.md](../reviews/2026-10-27-design-review-scan-precheck-two-stage.md)，
>   修订对照见本文 §9；校准脚本实施期新发现见 §10（已回写 D3）；
>   实施期性能实测与口径修正见 §11。
> - **来源**：[../reviews/2026-10-27-audit-project-issues.md](../reviews/2026-10-27-audit-project-issues.md) §9.3 第一条、§9.5「预检开销」。
> - **实施计划**：[../plans/2026-10-27-scan-precheck-two-stage-plan.md](../plans/2026-10-27-scan-precheck-two-stage-plan.md)
>   （T1-T7 全部完成）；任务记录与执行日志见
>   [../tasks/2026-10-27-scan-precheck-two-stage-tasks.md](../tasks/2026-10-27-scan-precheck-two-stage-tasks.md)。
> - **分支**：`feat/five-backend-docker-matrix`（实际沿用审计收口分支；原计划的
>   `feat/scan-precheck-two-stage` 未单独创建）。

## 0. 实测纪要（2026-10-27，本机 `analytics_sandbox.duckdb`，duckdb 1.5.6）

### 0.0 复现口径

样本 = `eval/golden_dataset.json` 的 **35 条可编译 DSL**（28 条单轮 `dsl` + 3 条多轮的
2+2+3 = 7 个 turn）。注意该文件只有 31 个**条目**，按条目数数会得到 28，别对不上账。

口径：同一只读连接上，每条 SQL 依次跑 `EXPLAIN` / `EXPLAIN ANALYZE` / 直接执行，
行数解析沿用 `exec/engine/plan_text.parse_scan_rows`。下文 §0.2、§0.3、§0.4 的数字
均为**评审期独立复算确认**（复算值与原计划期口径的差异见各表）。

### 0.1 合计口径：第一段只占 ANALYZE 的 22%~26%

| 口径 | 计划期（warm，3 次最好值） | 评审期复算（单次） |
|---|---|---|
| `EXPLAIN`（第一段） | 38.1ms | 40.2ms |
| `EXPLAIN ANALYZE`（现在的预检） | 171.7ms | 155.0ms |
| 直接执行 | 125.6ms | 127.2ms |
| **现在：ANALYZE + 执行** | **297.3ms** | **282.2ms** |
| **两段式：全部走第一段 + 执行** | **163.7ms** | **167.4ms** |

合计省 41%~45%。**端到端是 ≈1.3×（规划成本仍在），不是 1×**——v1 写的「回到 ≈1×」
把规划成本当成 0 了，验收目标按 1.3× 定。

⚠️ 单条查询的 EXPLAIN/ANALYZE 比值噪声大于差异本身（复算里 `Q01` 出现
EXPLAIN 1.87ms > ANALYZE 1.60ms，首查绑定与计划缓存未热）。**只认合计口径**，
别拿单条查询下结论。

### 0.2 估算偏差：双向都大，且高估远大于低估

35 条全部可编译、两种计划都能解析出行数（无一条缺失）：

| 指标 | 值 |
|---|---|
| 低估条数 | 6 / 35（17%） |
| 最大低估 | **3.21×**（est 3,755 → actual 12,060） |
| 最大高估 | **750.75×**（`M3-T1`：est 3,003 → actual 4；单轮集最大 234.67×，`Q23`） |
| 中位比值 est/actual | 0.058（即典型情况下估算偏高约 17×） |

**这是本设计的核心约束**：估算绝不能当熔断值用——750 倍的高估会把合法查询
误熔断，而误熔断比不熔断更糟（直接拒绝正确查询）。

### 0.3 跳过率

| 阈值 `MAX_SCAN_ROWS` | 安全系数 | 可跳过第二段 |
|---|---|---|
| 10,000,000（默认） | 4 / 8 / 32 / 64 | **35 / 35**（全部） |
| 1,000,000 | 4 / 8 / 32 / 64 | **35 / 35**（全部） |
| 100,000 | 8 | 32 / 35 |
| 100,000 | 32 | **8 / 35** |
| 100,000 | 64 | 0 / 35 |

关键结论：**默认阈值下 SF 从 8 提到 32/64 不损失任何跳过率**（golden 最大估算
15,019，`15,019 × 64 = 96 万` 仍远低于 1000 万）。代价只出现在把阈值收紧到十万级
的场景，而那种场景本身就在用收益换安全，取舍方向一致（见 D2）。

### 0.4 低估的真实下界是 5×，不是 3.21×

DuckDB 对 `>` / `<>` / `CAST(col AS T)` 这类**比较型过滤统一套 20% 默认选择率**，
与真实命中率无关。实测（同一张 `order_detail`，15,019 行）：

| 查询 | 估算 | 实测 | 低估 |
|---|---|---|---|
| `WHERE split_total_amount > 0`（全命中） | 3,003 | 15,019 | **5.0×** |
| `WHERE split_total_amount > 0 AND sku_num > 0 AND order_price > 0` | 3,003 | 15,019 | **5.0×**（不叠加） |
| `SELECT * FROM fact_action WHERE item <> ''` | 5,941 | 29,709 | **5.0×** |

即：**一个「该字段几乎全命中」的普通比较过滤，低估立刻吃掉 SF=8 的 5/8，只剩
1.6× 余量**。`>`/`<>` 是 DSL `filters` 的常规算子，不是罕见形态。§0.2 的 3.21×
是 **golden 问法下的下界**，不能当分布上界用（D2 据此改口径）。

### 0.5 三种「有数字但不可信」的计划形态（v1 漏项，D3 必须覆盖）

| 形态 | 触发查询 | `EXPLAIN` 原文 | 现有正则 |
|---|---|---|---|
| 普通扫描 | 其余 golden | `SEQ_SCAN` + `~3,755 rows` | 匹配 ✅ |
| **元数据快路径** | `SELECT count(*) FROM fact_display`（46.7 万行） | 单算子 `COLUMN_DATA_SCAN` + **`~1 row`（单数！）** | 不匹配（只认 `rows`）→ 判不可计量 |
| **空结果折叠** | `SELECT * FROM order_info WHERE order_status='1001'` | 单算子 `EMPTY_RESULT`，**无任何行数行** | 不匹配 |

`count(*)` 在 `EXPLAIN` 与 `EXPLAIN ANALYZE` 下都拿不到扫描行数（DuckDB 走元数据
快路径，`ANALYZE` 只剩 Summary 块）。所以「第一段拿不到估算」不是异常，而是必须
显式处理的常态分支。

**真正的坑**：`~1 row` 是"读了元数据、输出 1 行"，不是"扫了 1 行"。如果新解析器
顺手支持了单数 `row`（很自然的实现选择），`count(*)` 就会被读成「估算 1 行」→
`1 × 8 < 1000 万` → 直接放行，全表 count 从此永不熔断，而且日志里是一句理直气壮的
「估算 1 行」。这就是 `requirements.txt` 里 duckdb 2.0 事故注释记录的失败模式换了个
马甲。D3 的规则必须按**算子类型**判可信度，不能只看数字在不在。

### 0.6 计划文本是二维布局：逐算子归属会算低（校准脚本实施期实测）

写校准脚本时踩到的真坑，必须写进设计，否则实现者会重犯：

DuckDB 把 join 的两个子算子**并排**画在同一批行上
（`│ SEQ_SCAN ││ SEQ_SCAN │`）。按"行"扫描、再把行数归属给最近算子名的解析器
在这里会崩，而且**错的方向危险**：

| 现象 | 实测 |
|---|---|
| 35 条 golden 里含 join 的条数 | **16 条**（逐算子归属版本把它们全判成"不可用"） |
| `Q10` 被算成 | est **34** / actual 2,024 → 低估 **59.5×**（真值 est 3,755，被丢掉的是 order_detail 那一盒；34 是 base_province 的估算） |

低估 59.5× 意味着 `34 × 32 = 1,088 < 1000 万` → **直接放行一条实际扫 2,024 行的查询**；
换成真实大表就是"该熔断却放行"。所以 D3 的解析规则改成**布局无关**的两步判定
（存在性 + 取最大），不做逐算子归属，详见 D3。

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
于是**同一条查询被完整跑了两遍**：预检一遍拿行数，真正取结果再一遍。

**目标（4 条）**：

1. 预检成本从「一次完整执行」降到「一次规划」，端到端延迟从 **≈2.2× 降到 ≈1.3×**
   （不是 1×：规划成本仍在，见 §0.1）；
2. **不降低熔断有效性**——不允许因为省开销而放行本该熔断的查询；
3. 熔断口径的语义不悄悄漂移（`scan_rows` 到底是实测还是估算必须可分辨）；
4. 估算有效性**可观测**——不能只有"上线那天"的保证（D4 的采样复核）。

非目标：不改 `MAX_RESULT_ROWS` / 超时护栏；不改 ClickHouse / Hive 的「诚实
不可计量」结论；不重构 `guards.execute_sql` 的整体结构。

## 2. 现状与耦合点

| 位置 | 现状 | 本设计是否改动 |
|---|---|---|
| `exec/engine/base.py:52` `EngineAdapter` 协议 | 7 方法，`explain_scan_rows -> tuple[int, bool]` | **改**（新增可选方法 `explain_scan_estimate` + `ScanPrecheck` 数据类；v1 漏列此文件） |
| `exec/engine/duckdb_adapter.py:43` `explain_scan_rows` | 固定 `EXPLAIN ANALYZE`，返回 `(rows, measurable)` | **不动**（保旧语义，作为第二段） |
| `exec/engine/duckdb_adapter.py` | — | **新增** `explain_scan_estimate`（只发 `EXPLAIN`，返回 `ScanPrecheck(estimated=True)`） |
| `exec/engine/plan_text.py` | `parse_scan_rows` 正则 `(\d[\d,]*)\s+rows`，`~` 前缀也能匹配 | **新增** `parse_estimated_scan_rows`（**布局无关**：扫描算子存在性 + 全文本 `~N row(s)` 取最大，见 D3），旧函数一字不动 |
| `exec/guards.py` 预检分支 | 命中缓存跳过；未命中调 adapter，`measurable` 才缓存 | **改**（两段式判定 + 采样 + 告警去重规则） |
| `exec/guards.py` 扫描行缓存 | 键 = 后端作用域 + SQL，带 TTL（审计 §9.1 已修） | 不改结构，改写入条件（只在第二段写） |
| `exec/guards.py::ExecutionResult` | 单一 `scan_rows` | **改**（新增 `scan_rows_estimated: bool = False`） |
| `config/settings.py` + `.env.example` | — | **改**（3 个配置项，见 §7） |
| `docs/guides/configuration.md` | 已登记 `MAX_SCAN_CACHE_SIZE` 等同族项 | **改**（补 3 项 + 跳过率/安全系数对照表） |
| `tests/audit/test_logging_coverage.py:77,96` | 两处 `monkeypatch.setattr(duckdb_adapter, "parse_scan_rows", ...)` | **改**（v1 漏列；两段式后这两条用例语义翻转，见 §6） |
| `scripts/scan_precheck_calibration.py` | **已落地**（v2 评审期产出）：参考解析器 + golden/合成两族采样 + 跳过率矩阵 + 建议 SF | 实施期把 `_parse_estimated_scan_rows` 搬进 `plan_text.py`，本脚本改为 import 复用 |
| `tests/exec/test_plan_text_estimate.py` | **已落地** 6 条：并排布局取最大 / `COLUMN_DATA_SCAN` / `EMPTY_RESULT` / 裸 `N rows` / Dummy Scan / 单复数 | 解析器已下沉 `plan_text.py`，断言一字未改（仅改 import） |
| `audit/store.py` 审计表 `scan_rows` | 单列 BIGINT | **本期不改**（见 D6） |
| mysql / postgres adapter | 同样用 `EXPLAIN ANALYZE`，同样跑两遍 | **本期不改**（见 D5） |
| clickhouse / hive adapter | 诚实返回 `(0, False)` | 不改（未实现 `explain_scan_estimate` → 探测不到 → 恒走第二段 = 今天行为） |

## 3. 方案

```
                    ┌─ 命中缓存（实测值） ─────────────────→ 用缓存值判熔断
                    │
收到 SQL → 查缓存 ──┤  ① 掷骰子命中采样（概率 SCAN_PRECHECK_AUDIT_RATE）→ 直接跳第二段
                    │
                    └─ 第一段 EXPLAIN ─→ est 可信 且 est×SF < 阈值 ─→ 放行（estimated=True）
                                          │
                                          └─ 否则 ─ 第二段 EXPLAIN ANALYZE
                                                      → 实测值（estimated=False）
                                                      → 写缓存
                    ↓
             scan_rows > max_scan_rows ? 熔断 : 执行
```

判定式（`SF` = `SCAN_PRECHECK_SAFETY_FACTOR`）：

```python
est = adapter.explain_scan_estimate(conn, sql, timeout)   # 只发 EXPLAIN
if est.usable and est.rows * SF < max_scan_rows:
    scan_rows, estimated = est.rows, True          # 第一段放行，不执行
else:
    scan_rows, measurable = adapter.explain_scan_rows(...)   # 第二段（旧行为）
    estimated = False
```

`est.usable` 的定义（**这是本设计的核心，不能简化成"能不能解析出数字"**，见 D3）：

```python
usable = (
    est.measurable            # 计划里有带 "~" 前缀的行数行
    and est.scan_operator     # 该行数行属于白名单里的真实扫描算子
    and est.rows >= 1         # 0 行估算按不可信处理（统计空集 DuckDB 通常已折叠为 EMPTY_RESULT）
)
```

放行的安全性论证：放行条件是 `est × SF < 阈值`。只要该查询的
`实测 / 估算 ≤ SF`，就有 `实测 ≤ est × SF < 阈值`，即**放行不会漏熔断**。
这个性质依赖数据分布：golden 实测最大低估 3.21×，但同库同表的普通比较过滤实测
已达 5.0×（§0.4）。所以 SF 取 **32**（不是 8），且 D4 加运行期采样复核，把"系数
够不够"从一次性假设变成可持续观测。

## 4. 关键决策

### D1 估算只做「跳过判据」，绝不做「熔断值」

实测最大高估 750×（§0.2）。若把估算直接当 `scan_rows` 比阈值，一条实际只扫
几百行的查询会因为估算几十万行被熔断——**误杀合法查询**。因此估算只参与
「是否可以不做第二段」的判断，一旦需要熔断判定，必须用实测值。

对应地，放行分支里 `scan_rows` 记录的是估算值，只用于审计留痕与日志，且已由
判定式保证 `< 阈值 / SF`，不可能触发熔断；同时 `scan_rows_estimated=True` 让
下游可分辨（D6）。

### D2 安全系数默认 32，配置化

`SCAN_PRECHECK_SAFETY_FACTOR` 默认 **32**（v1 是 8）。依据：

- golden 最大低估 3.21×，但那是 golden 问法的**下界**；同一张表上 `WHERE col > 0`
  全命中就是 5.0×（§0.4），一个普通比较过滤就能吃掉 SF=8 的 5/8；
- 默认阈值 1000 万下，SF 提到 32 甚至 64 **跳过率不变**（35/35，§0.3），
  即安全边际是白送的；
- 只有把 `MAX_SCAN_ROWS` 收紧到 10 万时才有代价（SF=32 → 8/35，SF=8 → 32/35）。
  那种场景本身是"用收益换安全"，方向一致；真要高跳过率可以显式调低 SF，
  配置项就是干这个的。

取 2 的幂便于心算。该系数与数据分布强相关，需在以下情况重新校准（写入 §6）：
数据量级变化超过一个数量级、新增带高选择性过滤的典型问法、DuckDB 大版本升级、
或 D4 采样复核持续报偏差。

### D3 估算可信度按「算子类型」判，且解析必须布局无关

v1 的规则是「无任何行数行 → 走第二段」，只覆盖了 `EMPTY_RESULT`。实测还有
`COLUMN_DATA_SCAN ~1 row` 这一形态（§0.5），它**有数字但不可信**。

**判定分两步，不做逐算子归属**（§0.6 的教训：并排 box 布局会让逐算子归属把
16/35 条 golden 判成不可用，且 `Q10` 低估 59.5×）：

```python
def parse_estimated_scan_rows(plan_text) -> tuple[int, str]:
    # ① 存在性：计划里出现过白名单扫描算子（子串匹配，并排布局照样命中）
    scan = _SCAN_OPERATOR_RE.search(plan_text)      # \b(SEQ_SCAN|INDEX_SCAN|...)\b
    if scan is None:
        return 0, ""                                 # COLUMN_DATA_SCAN / EMPTY_RESULT / 无扫描面
    # ② 取最大：全文本所有 "~N row(s)" 的最大值（含并排的每一盒）
    return max(_ROW_RE.findall(plan_text)), scan.group(1)
```

取最大而不是逐算子归属，安全性来自单调性：**各算子估算的最大值 ≥ 任一扫描算子的
估算值**，所以 `max × SF < 阈值` 仍然蕴含 `扫描估算 × SF < 阈值`，放行判定不会因
为取大而变得不安全；代价只是偶尔多走一次第二段（估算偏高时）。

| 第一段结果 | 动作 |
|---|---|
| 计划含白名单扫描算子（`SEQ_SCAN` / `INDEX_SCAN` / `TABLE_SCAN` / `PARQUET_SCAN` / `READ_PARQUET` / `READ_CSV` / `CSV_SCAN` / `DELIM_SCAN` / `JSON_SCAN` / `ARROW_SCAN`），且 `max(~N row(s)) ≥ 1` | 按 D1/D2 判定是否跳过 |
| `COLUMN_DATA_SCAN`（元数据快路径，`count(*)` 类）——无白名单算子 | **必须**走第二段 |
| `EMPTY_RESULT`（整树折叠、无行数行）——无白名单算子 | **必须**走第二段 |
| 无白名单扫描算子（含 `SELECT 1` 的 Dummy Scan） | **必须**走第二段（fail-closed） |
| 估算最大值 `~0 rows` | **必须**走第二段 |
| 第二段也不可计量 | 维持现状：`measurable=False`，不缓存，告警去重一次 |

四条实现约束：

1. **解析器必须要求 `~` 前缀**。不带 `~` 的裸 `N rows` 是 `EXPLAIN ANALYZE` 的实测
   口径，误读等于把实测当估算，判定链直接错位。
2. **不许按行归属算子**。`│ SEQ_SCAN ││ SEQ_SCAN │` 去边框后是
   `SEQ_SCANSEQ_SCAN`，逐行匹配必失败；退化成"只采信某一盒"就会低估（§0.6）。
   已有 6 条用例锁定：`tests/exec/test_plan_text_estimate.py`。
3. **第一段解析失败不得调用 `_warn_unmeasurable_once`**。该函数的去重集合按后端
   只记一次（`exec/guards.py:571`），一次普通的 `EMPTY_RESULT` 查询就会把额度消耗
   掉；等 duckdb 真的改了计划格式，告警已经哑了——正是
   `2026-10-24-repair-ci-duckdb2-scan-cap-bypass` 修的那个洞。告警只允许在
   **第二段也不可计量**时发。
4. **未知算子 fail-closed 是刻意的**：新增算子名只会让查询多走一次第二段
   （慢一点），不会让熔断失效。`SELECT 1` 的 Dummy Scan 也走第二段，代价 0.1ms 级，
   不值得为它加规则。

### D4 只缓存实测值 + 按比例采样复核

缓存键结构（作用域 + SQL + TTL）不变，但**只在第二段写入**。理由：第一段本身
只有 1ms 量级（35 条合计 40.2ms），缓存它没有收益；而一旦把估算写进以
`scan_rows` 为名的缓存，后续 TTL 窗口内所有查询都会拿着估算值做判定，等于把 D1
的红线绕过——与审计 §9.1「缓存过时致熔断静默失效」是同一类错误。

**新增采样复核（v1 缺）**：只缓存实测意味着跳过路径的查询**永远不会产生实测值**，
SF 是否还够用在运行期完全不可观测。因此加 `SCAN_PRECHECK_AUDIT_RATE`（默认
`0.01`，`0` = 关闭）：命中时**强制走第二段**，然后

- 照常按实测值做熔断判定（采样窗口内恢复完整熔断，不是"看一眼就算了"）；
- 记一条 `INFO`：`est` / `actual` / `actual/est` 比值 / SQL 哈希；
- 若 `actual > est * SF`，升 `WARNING`（`scan_precheck_estimate_drift`），
  提示按 §6 重新校准。

成本：1% 的请求多一次 `EXPLAIN ANALYZE`（≈4ms 量级），可忽略；收益是把
"系数够不够"变成持续可观测，而不是靠人记得去跑脚本。

### D5 本期只改 duckdb

MySQL `EXPLAIN`（不带 ANALYZE）与 PostgreSQL `EXPLAIN` 同样给估算行数，
原理上两段式对它们成立，但三者计划文本格式、解析器、估算质量各不相同
（`parse_mysql_scan_rows` / `parse_pg_scan_rows`）。本期只改默认后端 duckdb，
把「协议怎么变、判据怎么定」先钉死；mysql/pg 另立一期，复用本设计的判据与
安全系数方法论，各自校准系数。

ClickHouse / Hive 明确不改：它们本就返回不可计量，没有「跑两遍」问题；
未实现 `explain_scan_estimate` 的 adapter 由 guards 用 `getattr` 探测为 `None`
（与 `core/retrieval/exploration.py` 探测 `install_secure_views` 同款先例），
恒走第二段，行为与今天逐点一致。

### D6 协议走「新增可选方法」，不碰 `explain_scan_rows` 签名

v1 写「签名不变」又要「可分辨估算/实测」，两者不能同时成立（`tuple[int, bool]`
装不下第三个信息）。选定：

- `exec/engine/base.py` 新增 `ScanPrecheck` 数据类
  （`rows: int` / `measurable: bool` / `estimated: bool` / `operator: str`）与
  **可选**方法 `explain_scan_estimate(conn, sql, timeout_ms) -> ScanPrecheck`；
- 只有 `DuckDbAdapter` 实现它；`guards` 用
  `getattr(adapter, "explain_scan_estimate", None)` 探测，`None` → 老路径；
- `explain_scan_rows` 一字不动（CH/Hive/MySQL/PG 的既有断言
  `== (0, False)` 全部保留）；
- `ExecutionResult` 加 `scan_rows_estimated: bool = False`。

不选「改 `explain_scan_rows` 返回三元组」：那要动 5 个 adapter + 两处
`== (0, False)` 断言（`tests/engine/test_clickhouse_adapter.py:170`、
`tests/engine/test_hive_adapter.py:286`），收益只是少一个方法名。

审计表**本期不加列**。理由是牵连面比预检本身还大，四处要同步改：
`audit/store.py::_INSERT_SQL`、`_migrate_schema`、`_record_params`、
以及 `scripts/audit_rebuild.py::_parse_line` 的字段白名单——漏一处就是
§8.3「两个入口各写一份」的重演。本期先用日志 + `ExecutionResult` 留痕，
审计列作为独立小项排期。**日志字段必须与熔断日志区分**：熔断/第二段打
`scanned`，跳过分支打 `scan_rows_est`，禁止同一个 key 装两种含义。

### D7 开关默认开启，保留一键回退

`SCAN_PRECHECK_TWO_STAGE`（默认 `1`）。置 `0` 时完全不调
`explain_scan_estimate`，直接走 `EXPLAIN ANALYZE` 的旧行为，且
`scan_rows_estimated` 恒 `False`。用于线上出现疑似误熔断时快速定位
「是不是预检判据的问题」。开关只影响预检路径，不影响熔断与超时语义。

## 5. 明确不做

| 不做 | 原因 |
|---|---|
| 用估算值替代熔断值 | 最大高估 750×，会误杀（D1） |
| 把估算写进扫描行缓存 | 等于绕过 D1（D4） |
| 用 `EXPLAIN` 完全取代预检 | `EXPLAIN` 拿不到实测，一旦估算成为唯一依据，就无法区分「扫了 1000 万行」和「扫了 10 行」——熔断退化成概率猜测 |
| 把 `COLUMN_DATA_SCAN ~1 row` 当估算用 | 那是"输出 1 行"不是"扫 1 行"，`count(*)` 会永久逃逸熔断（§0.5） |
| 为 `SELECT 1` 的 Dummy Scan 加特例 | 走第二段只多 0.1ms，多一条规则多一个出错面（D3 约束 3） |
| 顺手改 mysql / postgres | 解析器各异，需各自校准，另立一期（D5） |
| 给审计表加 `scan_rows_source` 列 | 四处联动，风险大于收益，独立排期（D6） |
| 改 `max_result_rows` / `statement_timeout_ms` | 与预检无关，硬边界不动 |

## 6. 验收标准

**解析器单测（`tests/exec/test_plan_text_estimate.py`，**已落地 6 条**）**

针对 D3 参考实现，锁的都是"错就错在低估"的方向：

1. 并排 box 布局（join 双子算子同排）→ 取全场最大估算，不得被右侧小表带偏；
2. `COLUMN_DATA_SCAN ~1 row`（单数）→ 不可用；
3. `EMPTY_RESULT` → 不可用；
4. 裸 `N rows`（无 `~`）→ 解析结果 0（不可用）；
5. Dummy Scan（无白名单算子）→ 不可用；
6. `~1 row` 单数与 `~N rows` 复数两种渲染都要匹配（DuckDB 单行时用单数）。

实施期把解析器搬进 `exec/engine/plan_text.py` 后，本文件只改 import，**断言一字不改**。

**判定链单测（`tests/exec/test_scan_precheck.py`，**已落地 13 个用例函数 / 17 条**
——`test_unusable_estimate_falls_back_to_exact` 参数化 5 个不可用形态）**

判定链：

1. 估算远低于阈值 → 只发 `EXPLAIN`，`EXPLAIN ANALYZE` **一次都不发**（spy 断言调用序列）；
2. 估算接近/超过阈值 → 两条都发，熔断判定用实测值；
3. `COLUMN_DATA_SCAN ~1 row`（真实 `count(*)`）→ 判不可用 → 走第二段，
   **不得**因 `~1 row` 放行；
4. `EMPTY_RESULT`（无行数行）→ 走第二段；
5. 无 `~` 前缀的裸 `N rows` 计划 → 判不可用（防把实测当估算）；
6. 估算 `~0 rows` → 判不可用；
7. 未知算子名（如伪造的 `FANCY_SCAN`）→ 判不可用（fail-closed 回归）。

告警与缓存：

8. 两段都不可计量 → `measurable=False`、**不写缓存**、告警一次；
9. **告警额度不被第一段污染**：先跑一条 `EMPTY_RESULT`（第一段不可用），再跑一条
   真·不可计量查询，后者仍必须发出 WARNING（变异：若第一段也调 `_warn_...`，
   本用例必挂）；
10. 缓存里只出现实测值：连续两次第一段放行同一 SQL，第二次仍走第一段
    （不因缓存变成实测）。

语义与开关：

11. `scan_rows_estimated` 三分：跳过分支 `True`、第二段 `False`、
    `SCAN_PRECHECK_TWO_STAGE=0` 时恒 `False` 且调用序列与今天逐点一致；
12. 采样命中（`AUDIT_RATE=1.0`）→ 强制第二段、按实测熔断、日志含 `est/actual` 比值。

**变异验证（必须有，否则用例是空转）**

- 判定式去掉安全系数（`est < max_scan_rows`）→ 用例 1/2 必须失败；
- 把估算值写入缓存 → 用例 10 必须失败；
- 把 `COLUMN_DATA_SCAN` 加入白名单 → 用例 3 必须失败；
- 第一段解析失败也调 `_warn_unmeasurable_once` → 用例 9 必须失败。

**回归**

- `tests/exec/test_exec.py` 既有扫描行 / 熔断 / 超时用例全绿（`test_scan_rows_within_budget`
  断言 `scan_rows == 200000` 这条要确认仍走第二段）；
- **`tests/audit/test_logging_coverage.py` 两条用例需改写**（`test_scan_cap_logs_warning_when_plan_unmeasurable`、
  `test_scan_cap_unmeasurable_warns_once_per_backend`）：它们 monkeypatch 的是
  `duckdb_adapter.parse_scan_rows`，两段式后 `SELECT count(*) FROM big` 会先撞上
  `COLUMN_DATA_SCAN` 分支；改写为 monkeypatch 第二段入口，并保留"不可计量必须
  告警"的原始语义（不是删用例）；
- 四后端 parity 不回归（CH/Hive 走 `getattr` 为 `None` 的旧路径，必须零差异）；
- 全量 `pytest -q` 基线不下降。

**性能（同一台机器、warm、35 条 golden 合计；口径见 §11 实测分解）**

| 指标 | 目标 | 实测 |
|---|---|---|
| 预检段开销（相对"仅护栏"基线） | 降幅 ≥ 35% | 117.6ms → 70.9ms（**-39.7%**） |
| `execute_sql` 端到端合计 | 降幅 ≥ 12% | 301.5ms → 254.8ms（**-15.5%**） |
| 第二段触发次数 | golden 35 条 **0 次**（采样命中除外） | 0 次（35/35 走第一段） |

> 「0 次」只对 golden 成立，不能当全局硬指标：按 D3，`count(*)` 必然触发第二段；
> 按 D4，`SCAN_PRECHECK_AUDIT_RATE=0.01` 时约 1% 的查询会被采样强制落到第二段
> （预期行为，不是回退）。把它写成全局指标会诱导实现者去用 `~1 row` 凑数，
> 正好踩 §0.5 的坑。
>
> v2 原写的「≤200ms / ≤1.4×」是拿"裸执行"当分母算的，**口径错了**：`execute_sql`
> 里 sqlglot 只读解析 + 执行前审计本身就占 184ms（35 条），预检只占其中一段。
> 按"仅护栏"基线分解后才是可归因的数字（§11）。

**安全系数校准（脚本已落地）**：`scripts/scan_precheck_calibration.py`

```bash
python scripts/scan_precheck_calibration.py               # golden + 合成族，出偏差/跳过率/建议 SF
python scripts/scan_precheck_calibration.py --verbose     # 逐条明细
python scripts/scan_precheck_calibration.py --json logs/sf.json
```

输入 = golden 全量 DSL（28 单轮 + 7 turn）+ **合成过滤族**（语义目录里每个数值字段
各造 `gt MIN-1` / `ne MIN-1` 两条全命中过滤，共 38 条——这是 §0.4 抓出 5× 低估的
来源，少了它 SF 必然定小）。输出「低估条数 / 最大低估 / 最大高估 / 中位比值 /
耗时合计 / SF × 阈值跳过率矩阵 / 建议 SF」。

当前实测（2026-10-27，本机）：golden 35 条 低估 6、最大低估 3.21×、最大高估 750.75×；
合成 38 条 低估 30、最大低估 **5.0×**；合计建议 SF = **32**（`5.0 × 4 → 20 → 取 2 的幂`），
默认阈值下跳过 73/73。结果回写 §0.2-§0.4。

## 7. 配置项（`.env.example` 与 `docs/guides/configuration.md` 同步）

| 变量 | 默认 | 含义 |
|---|---|---|
| `SCAN_PRECHECK_TWO_STAGE` | `1` | 两段式开关；`0` 回退旧行为（D7） |
| `SCAN_PRECHECK_SAFETY_FACTOR` | `32` | 估算放大倍数，放行条件 `est × SF < MAX_SCAN_ROWS`（D2） |
| `SCAN_PRECHECK_AUDIT_RATE` | `0.01` | 跳过路径强制复核的采样比例；`0` = 关闭（D4） |

`configuration.md` 的「资源与缓存」行需补 3 项，并附 §0.3 的「阈值 × SF × 跳过率」
对照表——这个表是运维调参的唯一依据，不放文档里等于让人瞎猜。

## 8. 风险与遗留

| 风险 | 处置 |
|---|---|
| 安全系数随数据分布漂移而失效 | §6 校准脚本（含合成过滤族）+ D4 运行期采样复核 + 系数可配置无需改码 |
| 估算低估超出 SF 导致漏熔断 | 漏熔断的后果是查询变慢而非错误（超时与结果行数硬线仍在），属可接受降级；日志 `scan_rows_est` 便于事后追溯 |
| **超时未启用时漏熔断无兜底** | 生产三处调用均同时传 `statement_timeout_ms` 与 `max_scan_rows`（`core/retrieval/tools.py:94`、`core/retrieval/exploration.py:168`、`tools/builtins/_query_core.py:155`），但协议不强制。实现时加断言：`max_scan_rows is not None and statement_timeout_ms is None` → `logger.warning`（不抛错，避免破坏既有测试与自定义调用方） |
| 第一段 `EXPLAIN` 本身失败（语法/权限） | 与现状同路径：归一为引擎错误，走自愈重试 |
| 未知算子导致跳过率下降（性能回退） | fail-closed 是刻意选择（D3 约束 3）；表现是多一次第二段，监控上看"第二段触发率"即可发现 |
| 审计 `scan_rows` 语义混杂 | D6 的独立小项补齐前，用 `scan_rows_estimated` 在日志与结果对象区分；日志 key 禁止复用 |

**遗留（本设计不覆盖，需另排）**：mysql/pg 两段式（D5）、审计
`scan_rows_source` 列（D6）、SSE 线程上限与审计锁拆分（审计 §9.3 其余项）。

## 9. v1 评审意见修订对照

对应 [评审报告](../reviews/2026-10-27-design-review-scan-precheck-two-stage.md)：

| 评审项 | 处置 | 落点 |
|---|---|---|
| P0-1 估算解析语法与退化算子未定义 | 新增 `usable` 四条件 + 算子白/黑名单表 + 三条实现约束（`~` 前缀、告警额度、fail-closed） | §0.5 / D3 |
| P0-2 D6 与「签名不变」矛盾 | 改「新增可选方法 `explain_scan_estimate`」，`explain_scan_rows` 不动；§2 补 `base.py` 与 `tests/audit/test_logging_coverage.py` | D6 / §2 |
| P0-3 SF=8 样本偏窄 | SF 默认 32；新增 §0.4「5× 下界」实测；校准集加合成过滤族；§0.3 补 SF×阈值矩阵 | D2 / §0.3-0.4 / §6 |
| P1-1 跳过路径永不校准 | 新增 `SCAN_PRECHECK_AUDIT_RATE` 采样复核（强制第二段 + 偏差告警） | D4 |
| P1-2 `scan_rows` 语义漂移下游 | 日志 key 强制区分（`scanned` vs `scan_rows_est`）+ 用例 11 锁定三分语义 | D6 / §6 |
| P1-3「第二段 0 次」当硬指标 | 限定为 golden 35 条，`count(*)`/`EMPTY_RESULT` 例外写明 | §6 |
| P1-4 漏熔断兜底缺超时约束 | §8 新增一行，实现时 `WARNING` 提示非法组合 | §8 |
| P2 目标 ≈1×、口径未注明、单条噪声、配置漏登记、D7 缺验收 | 目标改 ≈1.3×；§0.0 写清 35=28+7；§0.1 加噪声警告；§7 加 `configuration.md`；用例 11 覆盖回退 | §0.0-0.1 / §1 / §7 / §6 |

## 10. v2 实施期新发现（校准脚本落地时抓到的，已回写 D3）

评审没看出来、写代码才撞出来的问题，记在这里避免下一期重犯：

**DuckDB 计划文本是二维 box 布局，逐算子归属解析会算低。** join 的两个子算子并排
画在同一批行上（`│ SEQ_SCAN ││ SEQ_SCAN │`），按行归属算子名的解析器会把这类计划
判成"不可用"（35 条 golden 里 16 条），或退化成只采信某一盒的估算——`Q10` 因此被
算成 est 34 / actual 2,024（低估 59.5×），**方向正好是"该熔断却放行"**。

处置：D3 改为布局无关的两步判定（扫描算子存在性 + 全文本取最大），安全性由
"各算子估算最大值 ≥ 任一扫描算子估算值"的单调性保证，代价只是估算偏高时多走一次
第二段。解析器已下沉 `exec/engine/plan_text.py`，6 条锁定用例在
`tests/exec/test_plan_text_estimate.py`。

## 11. 实施期实测与偏差（二十六期收口，2026-10-27）

**性能分解**（35 条 golden，warm，本机 `analytics_sandbox.duckdb`）：

| 口径 | 耗时 |
|---|---|
| 仅护栏（`max_scan_rows=None`，含 sqlglot 解析 + 执行前审计 + 执行） | 183.9ms |
| 旧行为（ANALYZE 预检） | 301.5ms → 预检段开销 **117.6ms** |
| 两段式（EXPLAIN 预检） | 254.8ms → 预检段开销 **70.9ms** |
| 预检段降幅 / 端到端降幅 | **-39.7%** / **-15.5%** |

结论：省下来的确实是预检那一段，但 `execute_sql` 的固定成本（sqlglot 只读解析 +
执行前审计，35 条约 184ms）远大于预检本身，所以端到端降幅只有 15.5%——§1 目标里
"端到端 ≈2.2× → ≈1.3×"的说法只在"预检+执行"口径成立，**在 `execute_sql` 全链路
口径下是 1.64× → 1.39×**。§6 的性能目标已按可归因口径改写。

**跳过率**：35/35 走第一段，第二段 0 次。注意 `SCAN_PRECHECK_AUDIT_RATE=0.01` 下
约 1% 的查询会被采样强制落到第二段，属预期（实测两次运行里出现过 1 次）。

**解析器下沉的连带修复**：`tests/exec/` 是名为 `exec` 的包，用例里
`sys.path.insert(0, parents[1])` 会把 `tests/` 顶到 `sys.path` 最前，导致
`import exec.guards` 解析到 `tests/exec/`——迁文件时已删掉该 hack，后续新增用例
不要再加。
