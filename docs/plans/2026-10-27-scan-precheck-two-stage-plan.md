# 扫描行预检两段式实施计划（duckdb 先行）

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [x]` 勾选跟踪。
> 任务确认清单见 `../tasks/2026-10-27-scan-precheck-two-stage-tasks.md`。

**Goal:** 把 duckdb 的扫描行预检从「`EXPLAIN ANALYZE` 跑一遍 + 真正执行一遍」改成
「先 `EXPLAIN` 拿估算，估算足够小就放行，否则再 `EXPLAIN ANALYZE` 拿实测」，让冷查询
端到端从 ≈2.2× 降到 ≈1.3×，且**不降低熔断有效性**、**口径不静默漂移**。

**Architecture:** 三条不变量——① 估算只做「跳过判据」，绝不做熔断值（最大高估 750×，
当熔断值会误杀合法查询）；② 估算绝不进扫描行缓存（进了等于绕过①）；③ 估算可信度按
**算子类型**判且**布局无关**（`COLUMN_DATA_SCAN ~1 row` 与并排 box 布局是两个已实证
的陷阱，见设计 §0.5 / §0.6）。策略（SF、阈值、采样）留在 `exec/guards.py`，引擎差异
下沉到 adapter 的**可选**方法 `explain_scan_estimate`——CH/Hive/MySQL/PG 不实现即
恒走第二段，行为与今天逐点一致。

**Tech Stack:** 既有 `EngineAdapter` 协议（`exec/engine/base.py`）+ `DuckDbAdapter` +
`exec/engine/plan_text.py` 解析器；DuckDB 1.5.6 的 `EXPLAIN` / `EXPLAIN ANALYZE` 计划
文本；`random` 采样；`config.settings` 三项新配置。

**Spec:** [docs/designs/2026-10-27-scan-precheck-two-stage-design.md](../designs/2026-10-27-scan-precheck-two-stage-design.md)（决策 D1-D7、§0 实测、§6 验收 12 条；执行者需同时读两者）

**前置产出（已完成，实施时直接用）:**
- `scripts/scan_precheck_calibration.py` — D3 参考实现 + 校准命令
- `tests/test_scan_precheck_calibration.py` — 6 条解析器锁定用例（Task 1 已迁入 `tests/exec/test_plan_text_estimate.py`）

## Global Constraints

- **开关置 0 时逐点一致**：`SCAN_PRECHECK_TWO_STAGE=0` 下 `explain_scan_estimate`
  一次都不调，调用序列、缓存写入、告警行为与今天完全相同，`scan_rows_estimated`
  恒 `False`。这是"一键回退"的全部含义，不能打折。
- **估算绝不参与熔断判定**：只有 `scan_rows > max_scan_rows` 这一处比较，且该分支
  的 `scan_rows` 必定来自实测（或缓存里的实测值）。
- **估算绝不写入扫描行缓存**：`cache_scan_rows` 只在第二段（实测）调用。
- **fail-closed**：解析不出可信估算（无白名单扫描算子 / 无 `~` 前缀 / `~0 rows` /
  未知算子）一律走第二段，严禁"拿不到就当 0 行放行"。
- **日志 key 不复用**：跳过分支打 `scan_rows_est`，熔断/第二段打 `scanned`，
  禁止同一个 key 装两种含义（下游趋势对比会被污染）。
- **默认 duckdb 零行为变更**（语义层面）：既有 `tests/exec` / `tests/audit` /
  `tests/engine` 用例除两处必须改写外全绿；四后端 parity 零差异。
- **解析器不许按行归属算子**：`│ SEQ_SCAN ││ SEQ_SCAN │` 去边框后是
  `SEQ_SCANSEQ_SCAN`，逐行匹配必失败；退化成"只采信某一盒"会低估 59.5×
  （设计 §0.6）。只做「扫描算子存在性 + 全文本 `~N row(s)` 取最大」。
- 所有新增/修改代码的 docstring 与行内注释用简体中文（专有名词保留英文）；
  测试函数名英文；Git 提交信息 `feat:/test:/docs:` 前缀 + 简体中文描述。
- 本地环境 conda `dataagent`（`C:\Users\cwt15\.conda\envs\dataagent\python.exe`，
  Python 3.12.14）；跑全量 pytest 必须给**新的** `--basetemp=logs/.pytest_tmp`
  并在跑完删除该目录。
- 每个任务收尾 `black --check .` + `ruff check .` 必须绿；全量 `pytest -q` 在
  Task 7 统一跑（中间任务跑各自子集）。

## Review Focus

规格隐含、但容易被"顺手实现"破坏的失效模式（每条已挂到 owning task 的用例）：

1. **并排 box 布局**（35 条 golden 里 16 条含 join，逐算子归属会把它们全判不可用，
   且 `Q10` 低估 59.5×）→ 解析器必须布局无关，`tests/exec/test_plan_text_estimate.py`
   的 `test_side_by_side_layout_takes_max_not_right_box` 守住 → Task 1。
2. **`COLUMN_DATA_SCAN ~1 row`**（`count(*)` 元数据快路径，报的是"输出 1 行"）→
   有数字但不可信，必须走第二段 → Task 1（解析器）+ Task 4（判定链用例 3）。
3. **告警额度被第一段污染**：`_warn_unmeasurable_once` 按后端只记一次，第一段解析
   失败若也调它，一次普通 `EMPTY_RESULT` 就会把额度消耗掉，真出事时告警已哑 →
   Task 4 用例 9（含变异验证）。
4. **采样命中必须按实测熔断**：不是"看一眼就算了"，否则采样窗口反而是熔断缺口 →
   Task 5 用例 12。
5. **开关回退的逐点一致**：`SCAN_PRECHECK_TWO_STAGE=0` 下 `scan_rows_estimated`
   恒 `False` 且不调新方法 → Task 5 用例 11。

---

### Task 1: 解析器下沉 `plan_text.parse_estimated_scan_rows`

**Files:**
- Modify: `exec/engine/plan_text.py`（新增解析器与算子白名单，旧 `parse_scan_rows`
  一字不动）
- Rename: `tests/test_scan_precheck_calibration.py` → `tests/exec/test_plan_text_estimate.py`
  （`git mv`，**断言一字不改**，只改 import 来源）
- Modify: `scripts/scan_precheck_calibration.py`（删本地实现，改 import）

**Interfaces:**
- Produces: `parse_estimated_scan_rows(plan_text: str) -> tuple[int, str]`
  （`rows=0` 且 `operator=""` 表示不可用）；模块级 `_SCAN_OPERATORS`（白名单 frozenset）。
- 消费方：Task 2 的 `DuckDbAdapter.explain_scan_estimate`。

- [x] **Step 1: 搬实现**

从 `scripts/scan_precheck_calibration.py` 原样搬 `_SCAN_OPERATORS` /
`_SCAN_OPERATOR_RE` / `_ROW_RE` / `_BOX_CHARS` / `_strip_boxes` /
`_parse_estimated_scan_rows` 到 `exec/engine/plan_text.py`，函数改名去掉前导下划线
（对外导出），docstring 保留「为什么不做逐算子归属」那段与「去边框不能删空格」的坑注释。
`__all__` 补 `parse_estimated_scan_rows`。

- [x] **Step 2: 脚本改 import**

`scripts/scan_precheck_calibration.py` 删掉本地实现，改为
`from exec.engine.plan_text import parse_estimated_scan_rows`；调用点同步改名。
模块 docstring 里"与生产代码的关系"一节改为"复用生产解析器，不再各写一份"。

- [x] **Step 3: 用例迁移**

`git mv tests/test_scan_precheck_calibration.py tests/exec/test_plan_text_estimate.py`，
import 改 `from exec.engine.plan_text import parse_estimated_scan_rows`，5 条断言逐字保留；
docstring 里的脚本路径说明同步。新增第 6 条：
`test_plural_and_singular_row_both_match`（`~1 row` 与 `~1 rows` 都能解析，前提是有
白名单扫描算子——单数形态本身不是问题，问题是没有扫描算子时的那 1 行）。

- [x] **Step 4: 质量门**

`black --check .` / `ruff check .` / `pytest tests/exec/test_plan_text_estimate.py
tests/exec/test_exec.py -q` 全绿；`python scripts/scan_precheck_calibration.py --no-synthetic`
仍能出结果（数字与设计 §0.2 一致：35 条、低估 6、最大低估 3.21×、最大高估 750.75×）。

---

### Task 2: 协议 `ScanPrecheck` + `DuckDbAdapter.explain_scan_estimate`

**Files:**
- Modify: `exec/engine/base.py`（新增 `ScanPrecheck` 数据类 + 协议可选方法说明）
- Modify: `exec/engine/duckdb_adapter.py`（实现 `explain_scan_estimate`）
- Modify: `tests/engine/test_duckdb_adapter.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ScanPrecheck:
      rows: int = 0            # 估算（estimated=True）或实测行数
      measurable: bool = False # 是否有可解析的行数
      estimated: bool = False  # True=估算口径，False=实测口径
      operator: str = ""       # 行数来源算子（诊断用）

      @property
      def usable(self) -> bool:
          """能否作为「跳过第二段」的判据（D3）。"""
          return self.estimated and self.measurable and self.rows >= 1 and bool(self.operator)
  ```
- Produces: `DuckDbAdapter.explain_scan_estimate(conn, sql, timeout_ms) -> ScanPrecheck`
  （只发 `EXPLAIN`，走 `run_with_timeout`）。
- 不改：`explain_scan_rows` 签名与语义；CH/Hive/MySQL/PG 一律不实现新方法。

- [x] **Step 1: 数据类**

`base.py` 加 `ScanPrecheck`（`from dataclasses import dataclass`），放在协议之前；
`EngineAdapter` 里加注释块说明「`explain_scan_estimate` 是**可选**能力，实现者返回
`ScanPrecheck(estimated=True)`；未实现的 adapter 由调用方用 `getattr` 探测为 `None`
并恒走第二段」。**不要**把可选方法写进协议必选集——那会强迫 4 个后端做无意义实现。

- [x] **Step 2: duckdb 实现**

```python
def explain_scan_estimate(self, conn, sql, timeout_ms):
    """只做规划（不执行）的扫描行估算；不可用时返回不可用形态。"""
    _, plan_rows = run_with_timeout(self, conn, "EXPLAIN " + sql, timeout_ms)
    plan_text = ...  # 与 explain_scan_rows 相同的取文本逻辑，抽 _plan_text(plan_rows) 复用
    rows, operator = parse_estimated_scan_rows(plan_text)
    return ScanPrecheck(rows=rows, measurable=rows >= 1, estimated=True, operator=operator)
```
把 `explain_scan_rows` 里"从 `plan_rows` 取计划文本"的三行抽成私有
`_plan_text(plan_rows) -> str`，两处共用（避免抄第二份）。

- [x] **Step 3: 用例**

`tests/engine/test_duckdb_adapter.py` 新增：
- `test_explain_scan_estimate_is_planning_only`：真实 DuckDB 内存表，spy
  `conn.execute` 的 SQL 前缀为 `EXPLAIN `（不是 `EXPLAIN ANALYZE`）；
- `test_explain_scan_estimate_unusable_for_count_star`：`SELECT count(*) FROM t`
  → `usable is False`、`operator == ""`；
- `test_explain_scan_estimate_usable_for_scan`：带过滤的 `SELECT *` →
  `usable is True`、`rows >= 1`、`operator == "SEQ_SCAN"`；
- `test_scan_precheck_usable_requires_positive_rows`：`ScanPrecheck(rows=0,
  measurable=True, estimated=True, operator="SEQ_SCAN").usable is False`。

- [x] **Step 4: 质量门**

`pytest tests/engine -q` 全绿；`black --check .` / `ruff check .` 绿。

---

### Task 3: 三项配置

**Files:**
- Modify: `config/settings.py`（`MAX_SCAN_ROWS` 附近）
- Modify: `.env.example`
- Modify: `docs/guides/configuration.md`

**Interfaces:**
- Produces: `settings.SCAN_PRECHECK_TWO_STAGE: int = 1` /
  `settings.SCAN_PRECHECK_SAFETY_FACTOR: int = 32` /
  `settings.SCAN_PRECHECK_AUDIT_RATE: float = 0.01`。
- 消费方：Task 4（前两项）、Task 5（第三项）。

- [x] **Step 1: settings 三项**

```python
# 扫描行预检两段式（design 2026-10-27 D7）：1=开（先 EXPLAIN 估算，够小就跳过实测），
# 0=回退「直接 EXPLAIN ANALYZE」的旧行为
SCAN_PRECHECK_TWO_STAGE: int = int(os.getenv("SCAN_PRECHECK_TWO_STAGE", "1"))
# 估算放大倍数（D2）：放行条件 est * SF < MAX_SCAN_ROWS。校准见
# scripts/scan_precheck_calibration.py（golden 3.21x / 合成过滤族 5.0x）
SCAN_PRECHECK_SAFETY_FACTOR: int = int(os.getenv("SCAN_PRECHECK_SAFETY_FACTOR", "32"))
# 跳过路径的强制复核采样比例（D4）：命中即走第二段并按实测熔断，0=关闭
SCAN_PRECHECK_AUDIT_RATE: float = float(os.getenv("SCAN_PRECHECK_AUDIT_RATE", "0.01"))
```

- [x] **Step 2: `.env.example`**

在 `MAX_SCAN_ROWS` / `SCAN_CACHE_TTL_SECONDS` 同段补三项，每项一行注释说明
「默认 32 的来历」与「置 0 回退」，并指向设计文档路径。

- [x] **Step 3: `configuration.md`**

「资源与缓存」行补三项；新增小节贴设计 §0.3 的「阈值 × SF × 跳过率」矩阵
（35 条 golden 实测：10M 阈值下 SF 4/8/32/64 全部 35/35；100k 阈值下 8→32/35、
32→8/35、64→0/35），并注明调低 SF 的代价。

- [x] **Step 4: 用例**

`tests/exec/test_scan_precheck.py` 新增 `test_settings_defaults`（三项默认值）；
环境变量覆盖由既有 settings 用例模式覆盖，不新增（`settings` 是模块级常量，
进程内改 env 不生效，`monkeypatch.setattr(settings, ...)` 才是测试手段）。

- [x] **Step 5: 质量门**

`black --check .` / `ruff check .`；`python -c "from config import settings; print(
settings.SCAN_PRECHECK_SAFETY_FACTOR)"` 输出 32。

---

### Task 4: `guards` 两段式判定（核心）

**Files:**
- Modify: `exec/guards.py`（预检分支重写 + `ExecutionResult` 加字段 + 模块 docstring
  第 2 段「扫描行数上限」改写）
- Create: `tests/exec/test_scan_precheck.py`

**Interfaces:**
- Produces: `_precheck_scan_rows(adapter, conn, sql, timeout_ms, max_scan_rows, scope)
  -> tuple[int, bool]`（返回 `(scan_rows, estimated)`），供 `execute_sql` 调用。
- Produces: `ExecutionResult.scan_rows_estimated: bool = False`。
- Consumes: Task 1 解析器、Task 2 `explain_scan_estimate`、Task 3 前两项配置。

- [x] **Step 1: 判定式落地**

```python
def _precheck_scan_rows(adapter, conn, sql, timeout_ms, max_scan_rows, scope):
    """两段式预检：估算够小就跳过第二段，否则拿实测（D1/D2/D3）。"""
    estimate_fn = getattr(adapter, "explain_scan_estimate", None)
    if estimate_fn is None or not settings.SCAN_PRECHECK_TWO_STAGE:
        return _exact_scan_rows(adapter, conn, sql, timeout_ms, scope), False   # 旧路径
    est = estimate_fn(conn, sql, timeout_ms)
    if est.usable and est.rows * settings.SCAN_PRECHECK_SAFETY_FACTOR < max_scan_rows:
        logger.info("扫描行预检按估算放行（未执行）",
                    extra={"scan_rows_est": est.rows, "operator": est.operator})
        return est.rows, True
    return _exact_scan_rows(adapter, conn, sql, timeout_ms, scope), False
```
`_exact_scan_rows` = 现有那段逻辑原样抽出（`explain_scan_rows` → 可测则写缓存，
不可测则 `_warn_unmeasurable_once`），**第一段失败路径绝不碰告警去重集合**。

- [x] **Step 2: `execute_sql` 接上**

预检分支改为
`scanned, estimated = _precheck_scan_rows(...)`（缓存命中时 `estimated=False`），
熔断比较与异常归一保持原样；`ExecutionResult(..., scan_rows_estimated=estimated)`。
模块 docstring 第 2 段补「两段式：估算只做跳过判据，熔断判定一律用实测」与
「不可计量告警只在第二段发」两句。

- [x] **Step 3: 用例 1-10**

`tests/exec/test_scan_precheck.py`（用 `FakeAdapter` 记录调用序列，不碰真实 DuckDB，
除用例 3 用真实 `count(*)`）：
1. 估算远低于阈值 → 只发 `EXPLAIN`，`EXPLAIN ANALYZE` 零次；
2. 估算接近/超阈值 → 两条都发，熔断用实测；
3. 真实 `count(*)` → 判不可用 → 走第二段，不得因 `~1 row` 放行；
4. `EMPTY_RESULT` 计划 → 走第二段；
5. 裸 `N rows`（无 `~`）→ 判不可用；
6. 估算 `~0 rows` → 判不可用；
7. 未知算子 → 判不可用（fail-closed）；
8. 两段都不可计量 → `measurable=False`、**不写缓存**、告警一次；
9. 告警额度不被第一段污染（先 `EMPTY_RESULT` 再真不可计量，后者仍 WARNING）；
10. 缓存只含实测值（连续两次跳过同一 SQL，第二次仍走第一段）。

- [x] **Step 4: 质量门**

`pytest tests/exec -q` 全绿（含既有 `test_exec.py` 的熔断/超时用例）；
`black --check .` / `ruff check .` 绿。

---

### Task 5: 采样复核 + 日志口径 + 开关回退

**Files:**
- Modify: `exec/guards.py`（采样骰子 + 偏差日志）
- Modify: `tests/exec/test_scan_precheck.py`

**Interfaces:**
- Produces: `_audit_sample_hit() -> bool`（读 `settings.SCAN_PRECHECK_AUDIT_RATE`，
  `rate <= 0` 恒 `False`，否则 `random.random() < rate`）。
- 采样命中时：仍跑第一段拿 `est`（1ms 量级），**强制**走第二段，按实测熔断，
  记 `INFO`（`scan_rows_est` / `scanned` / `ratio`），`actual > est * SF` 时升
  `WARNING`（`scan_precheck_estimate_drift`）。

- [x] **Step 1: 采样接入**

`_precheck_scan_rows` 里 `sample = _audit_sample_hit()`；`est.usable and not sample`
才允许跳过。采样分支在拿到实测后比对：
```python
if sample and est.usable:
    ratio = rows / est.rows if est.rows else 0.0
    log = logger.warning if rows > est.rows * SF else logger.info
    log("扫描预检估算偏差采样", extra={"scan_rows_est": est.rows, "scanned": rows,
                                       "ratio": round(ratio, 2)})
```

- [x] **Step 2: 用例 11-12**

11. `scan_rows_estimated` 三分：跳过 `True` / 第二段 `False` /
    `SCAN_PRECHECK_TWO_STAGE=0` 恒 `False` 且 `explain_scan_estimate` 零调用；
12. 采样命中（`AUDIT_RATE=1.0`）→ 强制第二段、按实测熔断（构造 est 小、actual 超阈值的
    fake adapter，断言抛 `MaxRowsScannedExceeded`）、日志含 `ratio`。

- [x] **Step 3: 质量门**

`pytest tests/exec -q`；`black --check .` / `ruff check .`。

---

### Task 6: 既有用例改写 + 变异验证

**Files:**
- Modify: `tests/audit/test_logging_coverage.py`（两条用例）
- Modify: `tests/exec/test_scan_precheck.py`（补变异验证记录）

- [x] **Step 1: 改写 `test_scan_cap_logs_warning_when_plan_unmeasurable`**

两段式后 `SELECT count(*) FROM big` 会先撞 `COLUMN_DATA_SCAN` 分支（判不可用）→
走第二段；原用例 monkeypatch 的是 `duckdb_adapter.parse_scan_rows`，需改为
monkeypatch **第二段入口**（`duckdb_adapter.parse_scan_rows` 仍然生效，但要确认
第一段没被误判为可用）。**保留原语义**：计划不可计量时必须有 WARNING，不能静默
放行。同法处理 `test_scan_cap_unmeasurable_warns_once_per_backend`。

- [x] **Step 2: 变异验证（4 条，逐条手改代码跑一次，改回）**

- 判定式去掉安全系数（`est.rows < max_scan_rows`）→ 用例 1/2 必挂；
- 把估算写进缓存（跳过分支也调 `cache_scan_rows`）→ 用例 10 必挂；
- 把 `COLUMN_DATA_SCAN` 加进 `_SCAN_OPERATORS` → 用例 3 必挂；
- 第一段解析失败也调 `_warn_unmeasurable_once` → 用例 9 必挂。

把四次结果（哪条挂、报什么）记进 `tasks` 文件执行日志，**不留在代码里**。

- [x] **Step 3: 回归**

`pytest tests/exec tests/audit tests/engine -q` 全绿；四后端 parity 用例在无环境时
skip 属预期（CI 承担），但 `tests/exec/test_guards_clickhouse.py` /
`test_guards_postgres.py`（fake 连接）必须绿——它们验证"未实现新方法的 adapter
恒走第二段"。

- [x] **Step 4: 质量门**

`black --check .` / `ruff check .` / 上述子集全绿。

---

### Task 7: 性能实测 + 校准复跑 + 文档收口

**Files:**
- Modify: `docs/designs/2026-10-27-scan-precheck-two-stage-design.md`（§0 回写实测、
  §2/§6 用例路径改 `tests/exec/test_plan_text_estimate.py`、状态改「已实施」）
- Modify: `docs/README.md` §四 该行（计划/任务列 + 状态）
- Modify: `docs/tasks/2026-10-27-scan-precheck-two-stage-tasks.md`（执行日志）
- Modify: `docs/plans/2026-10-27-scan-precheck-two-stage-plan.md`（勾选）

- [x] **Step 1: 性能实测**

同一台机器、warm、35 条 golden 合计，跑「开关 0」与「开关 1」两轮，记：
预检+执行合计、端到端倍数、第二段触发次数、`count(*)` 类触发情况。目标见设计 §6
（**预检段降幅 ≥35% / 端到端降幅 ≥12% / golden 35 条第二段 0 次**）。

> 目标口径在 T7 实测时修正过：原写的「预检+执行 ≤200ms、端到端 ≤1.4×」把裸执行
> 当分母，既不可归因也与设计 §1 的 2.2×→1.3× 不自洽。改为「预检段降幅 /
> 端到端降幅」两个可归因口径，理由与实测见设计 §11。

- [x] **Step 2: 校准复跑**

`python scripts/scan_precheck_calibration.py`，确认数字仍为 golden 低估 6 / 最大
低估 3.21× / 合成最大低估 5.0× / 建议 SF=32；若漂移，把新数字回写设计 §0.2-§0.4
并说明原因。

- [x] **Step 3: 全量回归**

`pytest -q --basetemp=logs/.pytest_tmp`（基线 1385 passed / 28 skipped + 本期新增
用例数），跑完删除 `logs/.pytest_tmp`。

> **实测记录**：实际用了新目录 `logs/.pytest_tmp2`（旧目录里堆了 336 个前几次
> 子集运行的残留子目录，复用会报 `1412 errors` 的伪失败——本文件 Global
> Constraints 已预警此坑，实测确认）。结果 **1412 passed / 28 skipped**，
> 跑完两个目录均已删除。用例账目：1385 + 6（解析器）+ 17（判定链）+ 4（adapter）。
> 后续 PR CI 首轮抓出 PG 取消缺陷、补 2 条 cancel 单测后为 **1414 passed / 28 skipped**。

- [x] **Step 4: 文档收口**

设计 §2/§6 的用例路径统一；`docs/README.md` §四 该行改为「已实施」并挂 tasks；
tasks 文件回填各任务提交号与执行日志（含变异验证结果、性能数字）。

---

## 任务依赖与提交策略

```
T1 (解析器) ─→ T2 (协议/adapter) ─→ T4 (判定核心) ─→ T5 (采样/开关) ─→ T6 (改写/变异) ─→ T7 (收口)
                      T3 (配置) ────┘
```

- T3 与 T1/T2 无依赖，可并行；T4 需要 T1+T2+T3 全部就位。
- 提交粒度：T1/T2/T3/T4/T5 各一条 `feat: ...（扫描行预检两段式 Tn）`；
  T6 一条 `test: ...`；T7 一条 `docs: ...` 收口（勾选 + 执行日志 + 索引）。
- 分支 `feat/scan-precheck-two-stage` 自 master 切出，PR `--base master`。
