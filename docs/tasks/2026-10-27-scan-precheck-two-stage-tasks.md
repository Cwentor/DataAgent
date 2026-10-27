# 任务记录｜扫描行预检两段式（duckdb 先行）

> 配套文档：设计方案 `../designs/2026-10-27-scan-precheck-two-stage-design.md`（v3 已实施）｜
> 实施计划 `../plans/2026-10-27-scan-precheck-two-stage-plan.md`（步骤级勾选项在计划内，
> 本文件只跟踪任务级状态）｜评审报告 `../reviews/2026-10-27-design-review-scan-precheck-two-stage.md`。
> 分支：`feat/five-backend-docker-matrix`（自 master 切出，实际沿用审计收口分支；
> 原计划的新分支 `feat/scan-precheck-two-stage` 未单独创建）。

## 任务总览

| # | 任务 | 依赖 | 验收口径 | 状态 | 提交 |
|---|------|------|---------|------|------|
| T1 | 解析器下沉 `plan_text.parse_estimated_scan_rows`（+ 脚本改 import + 用例迁 `tests/exec/`） | — | 6 条解析器用例绿（含并排 box 布局、`COLUMN_DATA_SCAN`、`EMPTY_RESULT`、裸 `N rows`）；校准脚本仍能出与设计 §0.2 一致的数字 | ✅ 完成 | `af0e214` |
| T2 | 协议 `ScanPrecheck` + `DuckDbAdapter.explain_scan_estimate` | T1 | 只发 `EXPLAIN`（spy 断言）；`count(*)` 判不可用；`explain_scan_rows` 签名与语义零变更 | ✅ 完成 | `761d0e2` |
| T3 | 三项配置（`SCAN_PRECHECK_TWO_STAGE` / `_SAFETY_FACTOR=32` / `_AUDIT_RATE=0.01`） | — | `settings` / `.env.example` / `configuration.md` 三处同步；默认值用例绿 | ✅ 完成 | `aa366b7` |
| T4 | `guards` 两段式判定核心（usable / SF / 缓存 / 告警额度） | T1, T2, T3 | 用例 1-10 绿；`tests/exec` 既有熔断/超时用例零破 | ✅ 完成 | `ed3b185`（与 T5 合并） |
| T5 | 采样复核 + 日志口径 + 开关回退 | T4 | 用例 11-12 绿；`SCAN_PRECHECK_TWO_STAGE=0` 下调用序列与今天逐点一致 | ✅ 完成 | `ed3b185` |
| T6 | 既有用例改写 + 变异验证（4 条） | T4, T5 | `tests/audit/test_logging_coverage.py` 两条改写后仍守「不可计量必须告警」；4 条变异各自必挂指定用例 | ✅ 完成 | `1826fb9` |
| T7 | 性能实测 + 校准复跑 + 文档收口 | T1-T6 | 预检段降幅 ≥35%、端到端降幅 ≥12%、第二段 0 次；全量 pytest 基线不降 | ✅ 完成 | 本次 docs 提交 |

依赖关系：T1 是地基（解析器契约）；T3 独立可并行；T2 依赖 T1；T4 汇聚 T1+T2+T3；
T5 依赖 T4；T6 依赖 T4+T5；T7 收口。

**T4/T5 合并提交的原因**：两者改的是同一个函数体——`_precheck_scan_rows` 里
「采样骰子」与「估算放行判定」交织在同一控制流上（采样命中要绕过放行分支，
且必须在实测后回填漂移日志），没有干净的切分点。强行拆分会拆出不可独立运行的
中间态，故合并为一条并在此留痕。

## 服务级验证清单

本期**不新增**服务级验证：改动只涉及 duckdb 预检路径，四后端 parity 的期望是
**零差异**（CH/Hive/MySQL/PG 未实现 `explain_scan_estimate`，`getattr` 探测为
`None` 即恒走第二段）。

- [x] `tests/exec/test_guards_clickhouse.py` / `test_guards_postgres.py`（fake 连接）
      绿——它们实际验证"未实现新方法的 adapter 恒走旧路径"（含在 1414 passed 内）
- [x] CI 四个 parity job 无回归：`mysql-parity` / `postgres-parity` /
      `clickhouse-parity` / `hive-parity` 全 success（PR #21 第二轮，head `d8e2a1e`）
- [x] 全量 `pytest -q` 本地绿：**1414 passed / 28 skipped**；CI `test` job 同绿

## Review Focus 专项核对（T7 收尾时逐条打勾）

- [x] **并排 box 布局**（16/35 条 golden 含 join，逐算子归属会低估 59.5×）→
      `test_side_by_side_layout_takes_max_not_right_box` 绿
- [x] **`COLUMN_DATA_SCAN ~1 row`**（`count(*)` 元数据快路径）→ 解析器判不可用 +
      判定链用例 `test_real_count_star_does_not_short_circuit` 绿
- [x] **告警额度不被第一段污染** → `test_first_stage_failure_does_not_consume_warning_quota`
      绿（且变异：第一段也调 `_warn_...` 时必挂）
- [x] **采样命中按实测熔断**（不是"看一眼就算了"）→ `test_sample_hit_forces_exact_and_fuses` 绿
- [x] **开关回退逐点一致** → `test_switch_off_restores_legacy_sequence` 绿
      （`scan_rows_estimated` 恒 `False`、`explain_scan_estimate` 零调用）

## 性能验收记录（T7 回填）

同一台机器、warm、35 条 golden 合计，两轮实测：

| 指标 | 目标 | 实测（开关 0 / 旧行为） | 实测（开关 1 / 两段式） |
|---|---|---|---|
| 35 条 golden 预检+执行合计 | 预检段降幅 ≥35% | 301.5ms（预检开销 117.6ms） | **254.8ms（预检开销 70.9ms，降幅 39.7%）** |
| 端到端倍数（vs 裸执行 183.9ms） | 降幅 ≥12% | 1.64× | **1.39×（降幅 15.5%）** |
| 第二段触发次数（golden 35 条） | 0 次 | 35 次（全部） | **0 次** |
| `count(*)` / `EMPTY_RESULT` 类 | 恒触发第二段（预期） | — | 恒触发（估算不可用，按设计回退） |

> 口径说明：设计 §1 原写的「端到端 ≈2.2× → ≈1.3×」只在"预检+执行"口径成立；
> 在 `execute_sql` 全链路口径下是 **1.64× → 1.39×**（裸执行基准 183.9ms）。
> 目标值已按可归因口径改写为「预检段降幅 / 端到端降幅」，见设计 §6、§11。

## 执行日志

### T1 解析器下沉（`af0e214`）

改了什么：`plan_text.parse_estimated_scan_rows` 新增，返回 `(行数, 算子名)`；
`scripts/scan_precheck_calibration.py` 的本地解析器改为 import 复用；
用例迁到 `tests/exec/test_plan_text_estimate.py`（6 条）。

**踩坑 1（设计评审没看出来，写代码才暴露）**：最初按「逐算子归属」实现——先定位
扫描算子、再取它自己那一行的行数。在并排 box 布局上直接崩：35 条 golden 里 16 条
（全部含 join）被判"不可用"，`Q10` 只采信右盒 `~34 rows`（base_province），丢掉
左盒 `~3,755 rows`（order_detail）→ 低估 59.5×，方向是**该熔断却放行**。
改为布局无关的两步判定：扫描算子只做**存在性**判定（`\b(SEQ_SCAN|INDEX_SCAN|...)\b`），
行数取**全文本最大值**，不做归属。安全性由单调性保证——取最大值只会高估不会低估。

**踩坑 2**：`_BOX_CHARS` 里最初含空格，去边框时把 `~3,003 rows` 的空格也删掉，
变成 `~3,003rows`，正则 `\s+` 不匹配 → 估算恒为 0。修复：从字符集移除空格，
并在代码里写注释锁住这条。

**踩坑 3**：`tests/exec/` 下写 `sys.path.insert(0, parents[1])` 会遮蔽 `exec` 包
（解析到 `tests/exec/`），报 `ModuleNotFoundError: No module named 'exec.guards'`。
删掉该 hack，直接 `from exec.engine.plan_text import ...`。

**踩坑 4**：计划里写的是 `git mv tests/test_scan_precheck_calibration.py ...`，
但该校验脚本与用例是设计阶段新建、**尚未纳入版本控制**的，`git mv` 报
`fatal: not under version control`。改用普通 `mv`，由后续 `git add` 收录。

### T2 协议与 duckdb 实现（`761d0e2`）

改了什么：`ScanPrecheck` frozen dataclass + `usable` 属性；`explain_scan_estimate`
作为**可选**能力（不进 `EngineAdapter` 必选集，沿用 `install_secure_views` 先例，
避免四后端被迫写空实现）；`_plan_text` 抽公共；用例 +4。

**踩坑 1**：`'_duckdb.DuckDBPyConnection' object attribute 'execute' is read-only`，
无法 monkeypatch 记录 SQL。改写信 `_RecordingConn` 代理类。

**踩坑 2**：内存表的 `count(*)` **不走** `COLUMN_DATA_SCAN` 元数据快路径——
10000~1000000 行的内存表计划都是 `SEQ_SCAN`，`test_real_count_star_does_not_short_circuit`
测不出想测的东西。修复：`big_conn` fixture 改为**落盘 + 重新打开**
（`tmp_path / "precheck.duckdb"`，`read_only=True`）。

### T3 三项配置（`aa366b7`）

改了什么：`settings` / `.env.example` / `configuration.md` 三处同步。
SF 从设计 v1 的 8 提到 32，依据是校准脚本实测的最大低估 5.0×
（DuckDB 对 `>` / `<>` / `CAST` 类比较过滤统一套 20% 默认选择率），
按「最大低估 × 4 向上取 2 的幂」定值。

### T4/T5 判定核心与采样复核（`ed3b185`）

改了什么：`_precheck_scan_rows` 判定核心 + `_exact_scan_rows`（第二段唯一入口）
+ `_two_stage_enabled` / `_audit_sample_hit` / `_log_estimate_drift`；
`ExecutionResult.scan_rows_estimated`；用例 17 条。

三条红线全部落进代码结构而非注释：
1. **估算绝不进熔断值**——放行分支只 `return est.rows`，熔断比较永远用实测或缓存值。
2. **估算绝不进缓存**——`cache_scan_rows` 只在 `_exact_scan_rows` 内被调用。
3. **第一段失败不新增失败模式**——只 `logger.debug` 后回退第二段，让真实错误
   （语法/权限）在 `EXPLAIN ANALYZE` 上按既有路径归一。

告警额度：`_warn_unmeasurable_once` 只在 `_exact_scan_rows` 里被调用，成为
**唯一**入口。理由是第一段拿不到可信估算（`COLUMN_DATA_SCAN ~1 row`、
`EMPTY_RESULT`）是**常态分支**，消耗掉按后端去重的额度会让"计划格式变更"
这条真告警提前哑掉。

### T6 既有用例改写 + 变异验证（`1826fb9`）

**结论：两条既有用例无需改写即仍守不变量。** 它们的 `max_scan_rows=100` 远小于
`est × 32`（20 万 × 32 = 640 万），第一段不会放行，走的正是被桩掉的第二段。
本次只补 docstring：说明为什么仍有效、**未来什么条件下会失效**
（阈值调大到能命中估算放行时需显式关开关或把桩点挪到第一段），
以及指向新的专项用例。

**变异验证四条（逐条手改代码 → 跑用例 → 改回）：**

| # | 变异 | 结果 | 打挂的用例 |
|---|------|------|-----------|
| M1 | 去掉 `× SCAN_PRECHECK_SAFETY_FACTOR`（判据退化为 `est < max_scan_rows`） | ✅ 2 条挂 | `test_estimate_near_threshold_runs_exact_and_fuses`、`test_estimate_far_below_but_exact_over_limit_is_still_fused` |
| M2 | 估算也写进缓存（放行分支后加 `cache_scan_rows`） | ✅ 1 条挂 | `test_cache_never_stores_estimate` |
| M3 | `COLUMN_DATA_SCAN` 加进扫描算子白名单 | ✅ 2 条挂 | `test_column_data_scan_is_unusable`、`test_real_count_star_does_not_short_circuit` |
| M4 | 第一段失败时也调 `_warn_unmeasurable_once` | ✅ 1 条挂 | `test_first_stage_failure_does_not_consume_warning_quota` |

四条变异全部被捕获，说明相关用例不是空转。

### T7 性能实测 + 校准复跑 + 收口

**校准复跑**（`python scripts/scan_precheck_calibration.py`）：
golden 低估 6 / 35、最大低估 3.21×、最大高估 750.75×、合成过滤族最大低估 5.0×、
建议 SF=32、默认阈值跳过 73/73 —— 与设计 §0.2-§0.4 完全一致，无漂移。

**性能实测**：见上方「性能验收记录」表。预检段降幅 39.7%（目标 ≥35%）、
端到端降幅 15.5%（目标 ≥12%）、golden 35 条第二段触发 0 次（目标 0 次），
三项全部达标。

**全量回归**：**1412 passed / 28 skipped in 272.93s**。

> 第一次跑出 `28 skipped, 1412 errors`，全部是 web 用例的 ERROR（非 FAILED）。
> 原因是复用了 `--basetemp=logs/.pytest_tmp`——该目录里已堆了 336 个前几次子集
> 运行的残留子目录。清空后换 `logs/.pytest_tmp2` 重跑，零失败。
> **这正是计划 Global Constraints 里写明的坑，验证有效。**

用例账目：基线 1385 passed 是在校准脚本落地**之前**测的（当时 6 条解析器用例尚不存在）。
本期 = 6（解析器，从校准用例迁入）+ 17（判定链）+ 4（adapter）= 27，
1385 + 27 = **1412**，完全吻合。

**质量门**：`black --check .` 275 文件无改动、`ruff check .` 全通过、
`scripts/check_docs_links.py` 87 个 Markdown + 273 个代码文件零失效引用。

## 附：CI 首轮抓出的分支既有缺陷（非二十六期，顺带修复）

PR 首轮 CI：`test` / `lint` / `mysql-parity` / `clickhouse-parity` / `hive-parity` 全绿，
**`postgres-parity` 红**，挂在 `test_timeout_discards_connection`：

```
AssertionError: 废弃后容量名额未回收（size=0）
WARNING exec.pool:pool.py:106 废弃后补建连接失败，容量名额已退回（等待者继续排队）
psycopg.OperationalError: ... lost synchronization with server: got message type "S"
```

### 归因：与本期的预检改动无关

- 该用例由分支既有提交 `c903029`（审计 §9.3.2）引入，缺陷在 `d67252b`（超时脏归还）；
- 二十六期只给 **duckdb** 加了 `explain_scan_estimate`，PG 走 `getattr` → None →
  `_exact_scan_rows`，调用序列与改造前逐点一致；
- 本地 Windows 跑该用例 **10/10 通过**，是 **Linux 独有的竞争**。

### 根因：fd 复用竞争

1. 看门狗超时后只 `worker.join(2.0)`，比 `pg_sleep(3)` **短**，worker 仍阻塞在旧 socket 上；
2. PG 的 `adapter.cancel()` 当时是 `return None`（依据"服务端 statement_timeout 自限"
   ——但用例的 `timeout_ms=300` 远小于 `QUERY_TIMEOUT_MS`，服务端超时根本没到）；
3. `pool.discard()` 关掉连接后**立刻** `connect_readonly()` 建新连接；
4. Linux 把刚释放的 fd 号立刻复用给新 socket → 仍阻塞的 worker 抢读新连接的启动报文
   → 新连接 `lost synchronization` → 补建失败 → 容量名额退回 → `pool.size == 0`。

Windows 句柄回收不激进，故本机不复现。**本地全绿不代表 CI 绿。**

### 修复

`PostgresAdapter.cancel()` 改为经 `psycopg.Connection.cancel()` 发 CancelRequest
（psycopg3 线程安全、走独立连接，不是 SQL 级 `pg_cancel_backend`）。取消成功则 worker
先退出，`close()` 的是空闲连接，竞争窗口消失。

**验证**：同一条用例耗时 **3.35s → 0.54s**（`pg_sleep(3)` 在 300ms 超时后立即被取消），
说明取消真的生效而不是碰巧不撞。

另补两条单测锁死行为：`test_cancel_delegates_to_driver_cancel`（必须调 `conn.cancel()`，
防止再退化为 `return None`）、`test_cancel_swallows_driver_error`（取消失败不得阻断
超时分支）。`docs/reviews/2026-10-27-audit-project-issues.md` §2.3 的 PG 行已标注修订，
`tests/parity_utils.py` 补记了这段竞争。

**教训**：`pool.discard()` 的"关闭 + 立刻补建"在 worker 未真正退出时是不安全组合。
凡是 `cancel` 退化为空操作的引擎（MySQL / CH / Hive），理论上都有同一个窗口，只是
各驱动关闭忙连接的行为不同、尚未被抓出。后续若要统一加固，方向是"延迟关闭"
（reaper 线程宽限期后关）而不是继续依赖各驱动的巧合。
