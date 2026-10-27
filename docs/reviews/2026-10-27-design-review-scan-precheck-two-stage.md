# 评审报告｜方案评审：扫描行预检两段式（duckdb 先行）（2026-10）

> 评审日期：2026-10-27 · 评审方式：静态方案审读 + 本机实测复算（duckdb 1.5.6 / `analytics_sandbox.duckdb`）· 评审范围：[designs/2026-10-27-scan-precheck-two-stage-design.md](../designs/2026-10-27-scan-precheck-two-stage-design.md) 全篇（§0 实测纪要、D1-D7、§5 不做清单、§6 验收、§8 风险）

## 0. 结论

**方向正确，但不能按现稿直接进实施。** 两段式是 DuckDB 1.5 下唯一可行的降本路径（没有"只看计划又拿实测"的第三条路），D1（估算不做熔断值）、D4（估算不进缓存）、D7（可回退开关）三条红线都踩对了。

问题集中在**"估算解析"这一层的契约缺失**：方案把"第一段能不能拿到可信估算"当成一个已解决的问题，而实测表明 DuckDB 的计划里存在三种"有数字但不可信"的形态，方案只覆盖了一种。按现稿实现，大概率会写出一个"看着有单测、上线后熔断静默失效"的版本——正是本方案自己反复引用的那个失败模式。

**P0 三条必须修订后再立计划**（§1）；P1 四条建议同批修订（§2）；P2 为文档与验收口径问题（§3）。

## 1. P0（阻断实施）

### P0-1 估算解析的语法与"退化算子"规则未定义 —— 这是本次改动唯一的真实风险点

方案 §2 只写「`parse_scan_rows`：新增估算解析，旧函数语义不动」，D3 的判据是「无任何行数行 → 必须走第二段」。实测三种形态，方案只覆盖第 3 种：

| 实测形态 | 触发查询 | `EXPLAIN` 文本 | 现有正则 `(\d[\d,]*)\s+rows` | 风险 |
|---|---|---|---|---|
| **元数据快路径** | `SELECT count(*) FROM fact_display`（46.7 万行） | 单算子 `COLUMN_DATA_SCAN` + **`~1 row`** | 不匹配（单数 `row`）→ 判"不可计量" | 若新解析器支持单数 `row`，则 est=1 → `1×8 < 1000 万` → **直接放行**，全表 count 从此永不熔断，且日志里是一句理直气壮的"估算 1 行" |
| **空结果折叠** | `SELECT * FROM order_info WHERE order_status='1001'` | 单算子 `EMPTY_RESULT`，**无任何行数行** | 不匹配 | 按 D3 会正确落到第二段；但方案没写这一类，实现者容易"省一次 ANALYZE"把它当 0 行放行 |
| **普通扫描** | 其余 golden | `SEQ_SCAN` + `~3,755 rows` | 匹配 | 正常 |

**"支持单数 `row`" 与 "`count(*)` 不许放行" 是两个互相冲突的要求，方案没有裁决。** 这不是笔误级别的问题：它决定 `count(*)` 类问法（问数场景高频形态，golden M1/M2/M3 都有）的熔断到底还在不在。

必须补的规则（建议直接写进 D3）：

1. 估算解析器只认**带 `~` 前缀**的行数行（`~N row` / `~N rows` 都接受），且必须同时返回"该行属于哪个算子"；
2. **只有真实扫描算子**（`SEQ_SCAN` / `INDEX_SCAN` / `PARQUET_SCAN` 等）上的 `~N rows` 才算估算值；
3. `COLUMN_DATA_SCAN` / `EMPTY_RESULT` / 仅含 `PROJECTION`+`UNGROUPED_AGGREGATE` 的退化计划，**一律归入"不可作为跳过判据"**（走第二段），并在代码注释里点名 `count(*)` 元数据快路径，避免后人"优化"掉；
4. 第一段解析失败**不得**调用 `_warn_unmeasurable_once`（`exec/guards.py:571` 的去重集合按后端只记一次）。否则一个普通的 `EMPTY_RESULT` 查询就会把"可计量"的告警额度消耗掉，等真的出现 duckdb 2.0 计划格式变更时，告警已经哑了——这正好是 `2026-10-24-repair-ci-duckdb2-scan-cap-bypass` 修的那个洞。

§6 验收第 3 条（"估算不可解析 → 走第二段"）要拆成三条用例：`~1 row` 的 `COLUMN_DATA_SCAN`、`EMPTY_RESULT`、以及"无 `~` 前缀的裸 `N rows`"（后者必须判为不可计量，防止把实测计划误读成估算）。

### P0-2 D6 与「签名不变」自相矛盾，且 §2 漏了协议文件

§2 表格写 `duckdb_adapter.explain_scan_rows`「内部两段式，**签名不变**」，D6 又要求 `ExecutionResult.scan_rows_estimated` 可分辨估算/实测。签名不变（`tuple[int, bool]`，见 `exec/engine/base.py:52`）时，`guards` 根本无从得知这个数字是哪一种——两条要求不能同时成立。

必须二选一并写进方案：

- **A（改协议）**：返回 `ScanPrecheck(rows, measurable, estimated)` 数据类。牵连：`exec/engine/base.py` 协议注释 + 5 个 adapter（duckdb/mysql/postgres/clickhouse/hive）+ 两处 `== (0, False)` 断言（`tests/engine/test_clickhouse_adapter.py:170`、`tests/engine/test_hive_adapter.py:286`）。改动面大但语义干净。
- **B（加方法）**：新增 `adapter.scan_precheck()`，两段式整体下沉到 adapter，`explain_scan_rows` 保留旧语义不动（CH/Hive 无需改）。改动面小，推荐。

无论选哪个，§2 的"改动点表"必须补 `exec/engine/base.py`（协议是真正的契约，现在漏了）和 `tests/audit/test_logging_coverage.py`（两处 `monkeypatch.setattr(duckdb_adapter, "parse_scan_rows", ...)`，第 77/96 行）。后者的用例语义会被改动直接翻转：`SELECT count(*) FROM big` 若走"估算放行"，就不再触发"不可计量"告警，用例必挂。§6 写「既有用例全绿」是掩盖问题，应改成「已知需改写用例清单 + 改写理由」。

### P0-3 SF=8 的校准样本偏窄，实测 5× 低估只需一个"全命中比较过滤"

§0.3 的 3.21× 我复算确认无误（见 §4），但那是 **golden 问法**下的最大低估。DSL 的 `filters` 支持 `> / >= / <>` 等比较算子，DuckDB 对这类过滤统一套 **20% 默认选择率**，与真实命中率无关：

| 实测查询 | 估算 | 实测 | 低估 |
|---|---|---|---|
| `SELECT * FROM order_detail WHERE split_total_amount > 0` | 3,003 | 15,019 | **5.0×** |
| `SELECT * FROM order_detail WHERE split_total_amount > 0 AND sku_num > 0 AND order_price > 0` | 3,003 | 15,019 | **5.0×** |
| `SELECT * FROM fact_action WHERE item <> ''` | 5,941 | 29,709 | **5.0×** |

也就是说，**只要业务上出现一个"该字段几乎全不为 0/非空"的比较过滤，低估立刻吃掉 SF=8 的 5/8，只剩 1.6× 余量**。这不是罕见形态，是 DSL 最普通的过滤。

而 SF 提高到 32/64 在默认阈值下**成本为零**：§0.4 已证 `MAX_SCAN_ROWS=10,000,000` 时 35/35 全跳过，而 golden 的最大估算只有 15,019——`15,019 × 64 = 96 万`，仍远低于 1000 万。既然默认口径下加大 SF 不减少任何收益，把它定在"刚好覆盖 3.21× 再留 2.5 倍"就是**用零成本换来了 1.6× 的安全边际**。

建议：SF 默认改 **32**（保守到 64 也可），§0.3 的校准集**必须加入"全命中比较过滤"一族**（按 `>`/`<>`/`CAST(col AS T)` 各造 3-5 条），而不是只跑 golden。§6「安全系数校准」的输入从"golden 全量 DSL"改成"golden + 合成过滤族"。

## 2. P1（建议同批修订）

### P1-1 跳过路径永远不校准（D4 的副作用，方案未处置）

D4 只缓存实测值，且跳过分支不产生实测——结果是**估算放行的查询，其真实扫描行数在运行期永远不可观测**。§6 的校准脚本是离线一次性动作，等于 SF 的有效性只有"上线那天"的保证。

低成本补法：对跳过路径按比例采样（如 1% 或每 N 次）补一次第二段，比对 `实测/估算` 是否突破 SF，超限记 WARNING（不动缓存语义、不动熔断语义）。没有这个，§8「安全系数随数据分布漂移而失效」的处置只有"人去重新跑脚本"，等于没有自动发现能力。

### P1-2 `scan_rows` 语义漂移的下游影响没有量化

除 D6 已承认的审计表，还有两处：

- `tests/exec/test_exec.py:96` 断言 `result.scan_rows == 200000`（精确实测值）；
- `tools/builtins/_query_core.py` 的结果缓存把 `exec_result.scan_rows` 一起存/还原，同一 SQL 在不同路径下可能写入估算值。

建议：日志字段名必须与 `scan_rows` 区分（如 `scan_rows_est`），并补一条用例断言"跳过分支的 `ExecutionResult.scan_rows_estimated is True` 且日志 key 不同"，把"下游拿错数"变成可测而非靠自觉。

### P1-3 「第二段触发 0 次」不能当验收硬指标

§6 写「第二段触发次数：默认阈值下 0 次（35 条）」。按 P0-1，`count(*)` 类查询**必然**触发第二段（`COLUMN_DATA_SCAN` 无有效估算）。把 0 次写成硬指标，会诱导实现者把 `~1 row` 当估算用去凑指标——正好踩 P0-1 的坑。改成「golden 35 条中第二段触发 0 次；`count(*)` 类恒触发第二段，属预期」，并把该例外写进用例。

### P1-4 「漏熔断兜底靠超时」缺一条显式约束

§8 用「超时与结果行数硬线仍在」论证漏熔断可接受。但 `execute_sql` 的 `max_scan_rows` 与 `statement_timeout_ms` 是两个独立可空参数（`exec/guards.py:604-607`）。生产三处调用确实都同时传了（`core/retrieval/tools.py:93-96`、`core/retrieval/exploration.py:168-171`、`tools/builtins/_query_core.py:155-157`），但协议不强制。方案应把"启用扫描预检但 `statement_timeout_ms is None`"定义为非法组合（assert 或 WARNING），否则 §8 那条论证在自定义调用方上不成立。

## 3. P2（文档与口径）

1. **§0 没有复现脚本**。§6 说"随实现交付"，等于评审阶段的数据不可复核。建议评审通过前就把脚本落到 `scripts/scan_precheck_calibration.py`，§0 贴命令与输出摘要。
2. **「35 条」的口径没写**。`eval/golden_dataset.json` 只有 31 个条目（28 单轮 + 3 个多轮，多轮 2+2+3=7 turn），35 = 28 + 7。后人按"35 条 golden"去跑只会得到 28 条，数字对不上。§0 应注明"28 单轮 + 7 多轮 turn"。
3. **§0.3 的最大高估 750.75× 没标注来源**：我复算确认它来自 `M3-T1`（est 3,003 / act 4），单轮集的最大高估是 234.67×（`Q23`，est 15,019 / act 64）。不写清出处，读者会以为是 golden 单轮里的普遍现象。
4. **§0.1「单查询 EXPLAIN 恒定是 ANALYZE 的 1/2~1/3」不成立**：我单次实测出现 `Q01` 的 EXPLAIN 1.87ms > ANALYZE 1.60ms（首查绑定/计划缓存未热）。**合计口径成立**（我测 explain 40.2ms / analyze 155.0ms / exec 127.2ms，方案是 38.1/171.7/125.6），但单条查询噪声大于差异本身，别拿单条下结论。
5. **§1「冷查询端到端延迟回到 ≈1×」偏乐观**：规划成本仍在，实测 35 条合计是 `40.2 + 127.2 = 167ms` vs 直接执行 127.2ms，即 **≈1.3×**，不是 1×。目标写成"≈1.2~1.3×（规划占比 ~25%）"更诚实，也便于验收。
6. **配置项漏登记**：§7 只提 `.env.example`，`docs/guides/configuration.md` 的"资源与缓存"行也要补 `SCAN_PRECHECK_TWO_STAGE` / `SCAN_PRECHECK_SAFETY_FACTOR`（该指南已登记 `MAX_SCAN_CACHE_SIZE` 等同类项）。
7. **D7 缺一条验收**：`SCAN_PRECHECK_TWO_STAGE=0` 时 `scan_rows_estimated` 必须恒为 `False`、且第二段调用序列与今天逐点一致（否则"一键回退"名不副实）。
8. **§5「用 EXPLAIN 完全取代预检」列为不做是对的**，但理由写"见 D1"不够：真正的原因是 `EXPLAIN` 拿不到实测，一旦估算是唯一依据就无法区分"扫了 1000 万行"和"扫了 10 行"。建议补一句，防止后人把它当"还能再省一次执行"的优化项重提。

## 4. 独立复核结果（本机实测，2026-10-27）

复算方法：`eval/golden_dataset.json` → `QueryDSL.model_validate` → `compile_sql` → 同一只读连接上分别跑 `EXPLAIN` / `EXPLAIN ANALYZE` / 直接执行，解析沿用 `exec/engine/plan_text.parse_scan_rows`。

| 方案 §0 数据 | 方案值 | 我的复算 | 结论 |
|---|---|---|---|
| 可编译 DSL 条数 | 35 | 35（28 单轮 + 7 turn） | 一致（口径需注明） |
| `EXPLAIN` 合计 | 38.1ms | 40.2ms | 一致 |
| `EXPLAIN ANALYZE` 合计 | 171.7ms | 155.0ms | 一致（机器状态差异） |
| 直接执行合计 | 125.6ms | 127.2ms | 一致 |
| 低估条数 | 6 / 35 | 6 / 35（Q02/Q05/Q07/Q13/Q19/Q28） | **完全一致** |
| 最大低估 | 3.21× | 3.21×（3,755 → 12,060） | **完全一致** |
| 最大高估 | 750.75× | 750.75×（M3-T1：3,003 → 4） | **完全一致** |
| 中位比值 est/act | 0.06 | 0.058 | 一致 |
| `count(*)` 两种计划都无有效行数 | 是 | 是（`EXPLAIN` 为 `COLUMN_DATA_SCAN ~1 row`，`ANALYZE` 仅 Summary 块） | 一致，但方案的描述"都没有 rows 行"漏掉了 `~1 row` 这个关键细节（见 P0-1） |

**§0 的数据质量是本方案最大的优点**——35 条全量、双向偏差、跳过率矩阵都给全了，我逐项复算没有发现造假或口径注水。问题不在数据，在于从数据到规则的那一步跳得太快：`§0.3 说低估最大 3.21×` → `D2 定 SF=8`，中间的"这个 3.21× 是 golden 的下界还是真实分布的上界"没有被追问。§1 P0-3 的实测（同样一张表、同样一个字段，换 `>` 就是 5×）就是答案：**它是下界。**

## 5. 修订清单（建议直接作为计划期输入）

| # | 项 | 落点 |
|---|---|---|
| 1 | 定义估算解析语法：必须带 `~`、支持单数 `row`、返回算子类型 | D3 / §3 判定式 |
| 2 | 白名单真实扫描算子；`COLUMN_DATA_SCAN`/`EMPTY_RESULT` 判为不可作为跳过判据 | D3 表格新增两行 |
| 3 | 第一段解析失败不得消耗"不可计量"告警去重额度 | D3 / §6 用例 |
| 4 | 选定 D6 的实现通道（推荐方案 B：新增 `scan_precheck()`），§2 补 `exec/engine/base.py` 与 `tests/audit/test_logging_coverage.py` | D6 / §2 |
| 5 | SF 默认 8 → 32；校准集加入"全命中比较过滤"族 | D2 / §6 |
| 6 | 跳过路径按比例采样复核（可配 0 = 关） | D4 新增一节 |
| 7 | 「第二段 0 次」限定为 golden 35 条，`count(*)` 例外写进用例 | §6 性能表 |
| 8 | 预检启用但超时为 None → assert 或 WARNING | §8 |
| 9 | 校准脚本评审期即落 `scripts/`，§0 补口径（28+7）与出处（750.75× = M3-T1） | §0 / §6 |
| 10 | 性能目标改 ≈1.3×；`guides/configuration.md` 补两项配置；D7 补"回退后 `scan_rows_estimated` 恒 False"用例 | §1 / §7 / §6 |

改完 1-5 即可立计划；6-10 可以进计划的任务清单，不必回头改设计正文。

## 6. 修订确认（方案 v2，2026-10-27 同日）

设计方已按本报告出 v2（`designs/2026-10-27-scan-precheck-two-stage-design.md`），逐项对照见其 §9。复核结论：

| 项 | 处置 | 是否闭合 |
|---|---|---|
| P0-1 退化算子 | 新增 `usable` 四条件（`measurable` / `scan_operator` 白名单 / `rows ≥ 1`）+ `COLUMN_DATA_SCAN`、`EMPTY_RESULT` 黑名单 + `~` 前缀强制 + 未知算子 fail-closed | ✅ 闭合，且补了「第一段解析失败不得消耗告警去重额度」 |
| P0-2 签名矛盾 | 改「新增可选方法 `explain_scan_estimate` + `ScanPrecheck`」，`explain_scan_rows` 一字不动，CH/Hive 零改动；`getattr` 探测沿用 `install_secure_views` 先例 | ✅ 闭合（比我建议的方案 B 更省，无需改 5 个 adapter） |
| P0-3 SF 偏窄 | SF 8 → 32；新增 §0.4「5× 下界」实测；补 SF×阈值跳过率矩阵（含 100k 阈值下 SF=32 掉到 8/35 的代价，诚实披露）；校准集加合成过滤族 | ✅ 闭合 |
| P1-1 跳过路径不校准 | 新增 `SCAN_PRECHECK_AUDIT_RATE`（默认 1%）：命中即强制第二段并按实测熔断，兼作观测与兜底 | ✅ 闭合（比"只看不改判"更好） |
| P1-2 语义漂移 | 日志 key 强制区分 `scanned` / `scan_rows_est` + 用例锁定三分语义 | ✅ 闭合 |
| P1-3 硬指标 | 「第二段 0 次」限定 golden 35 条，`count(*)` 例外入用例 | ✅ 闭合 |
| P1-4 超时约束 | §8 新增一行，实现期 `WARNING`（不抛错，避免破坏既有调用方） | ✅ 闭合 |
| P2 各项 | 目标改 ≈1.3×；§0.0 写清 35 = 28 单轮 + 7 turn；§0.1 加单条噪声警告；`configuration.md` 入清单；D7 回退语义入用例 11 | ✅ 闭合 |

一处需实施期留意：`tests/audit/test_logging_coverage.py` 两条用例是**改写**而非删除——
它们守的是「不可计量必须告警」这个原始语义，两段式后要改成 monkeypatch 第二段入口，
不能因为「第一段撞上 `COLUMN_DATA_SCAN`」就把用例删了。v2 §6 已写明这一点。

**结论：v2 可作为计划期基线，无剩余阻断项。**

