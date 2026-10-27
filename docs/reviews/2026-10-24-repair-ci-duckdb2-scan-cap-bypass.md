# 评审报告｜缺陷修复：CI 扫描行熔断被 DuckDB 2.0 计划渲染变更打穿（2026-10）

> 归档日期：2026-10-24 · 事故窗口：2026-10-22 ～ 2026-10-23 · 影响对象：CI 夜跑 + `exec/` 扫描行护栏
> 修复提交：`85b7241`（PR #14） · 状态：**已闭环**（CI 双绿；夜跑待首次验证）

---

## 一、一句话结论

`requirements.txt` 里 duckdb 只写了下界、没有上界，CI 于 2026-10-22 拉到当天发布的 **duckdb 2.0.0**；2.0 把聚合查询的执行计划折叠成单算子，`EXPLAIN ANALYZE` 不再渲染每个算子处理的行数，导致**扫描行数熔断（`MaxRowsScannedExceeded`）在 2.0 上彻底空转**。失败的那个测试只是最先崩掉的一根线，真正的风险是生产护栏静默失效。

---

## 二、现象

### 2.1 CI 表现

2026-10-22、10-23 两次定时夜跑（`schedule`）连续失败，`lint` 通过、`test` 与 `nightly` 失败，累计 `1 failed, 1015 passed, 2 skipped`。

```
FAILED tests/audit/test_logging_coverage.py::test_scan_cap_logs_error
E   Failed: DID NOT RAISE MaxRowsScannedExceeded
```

| 运行 ID | 触发方式 | 日期 | 结论 |
| --- | --- | --- | --- |
| `39755085923` | schedule | 2026-10-22 | failure（同一根因） |
| `39931104576` | schedule | 2026-10-23 | failure（同一根因） |
| `39948104247` | pull_request | 2026-10-23 | success（修复后） |
| `39948729126` | push → master | 2026-10-23 | success（修复后） |

### 2.2 关键证据：CI 实际安装的版本

```
# 失败时（run 39931104576）
Collecting duckdb>=0.10
Downloading duckdb-2.0.0-cp312-abi3-manylinux_2_27_x86_64.whl (24.4 MB)

# 修复后（run 39948729126）
Downloading duckdb-1.5.6
```

同一份 `requirements.txt`，两次安装出两个主版本 —— 这是"无上界依赖 + 每次全新环境安装"的必然结果。

### 2.3 本地为什么没复现

本机 `duckdb 1.5.6`（历史手工安装，未随 CI 升级），护栏计量正常，用例一直绿。**本地绿 ≠ 生产绿**，这个差异此前未被任何检查覆盖。

---

## 三、根因

### 3.1 护栏的计量方式本身是"文本解析"

`exec/guards.py` 的扫描行熔断实现是"先计量后放行"：

1. 执行 `EXPLAIN ANALYZE <sql>`（DuckDB 无原生扫描行上限，只能借分析执行拿真实行数）；
2. 用正则从计划文本里抠所有 `N rows`，取最大值作为扫描行数：

```python
_ROWS_LINE_RE = re.compile(r"(\d[\d,]*)\s+rows", re.MULTILINE)

def parse_scan_rows(plan_text: str) -> int:
    counts = [...]
    return max(counts) if counts else 0   # ← 解析失败 = 0 = 永不熔断
```

3. `scanned > max_scan_rows` 才抛 `MaxRowsScannedExceeded`。

**这条链路把"资源治理"的正确性绑在了 DuckDB 计划渲染格式上**，而计划格式属于引擎内部实现细节，没有任何契约保证。

### 3.2 2.0 动了渲染格式

DuckDB 2.0.0 起，`count/sum` 等聚合走元数据快路径，执行计划被折叠为单算子，`EXPLAIN ANALYZE` **只输出 Summary 块，不再渲染任何算子行数**。

于是：

```
计划文本无 "N rows"  →  parse_scan_rows 返回 0  →  0 > limit 恒为假  →  不熔断
```

`parse_scan_rows` 的 `return max(counts) if counts else 0` 把**"测不到"**与**"真·零扫描"**混成了同一个值 `0`。这不是异常，是静默降级 —— 最坏的一类失效：护栏看起来还在，实际已经不管事。

### 3.3 三层放大器

| 放大器 | 说明 |
| --- | --- |
| 依赖无上界 | `duckdb>=0.10` 允许 pip 自动跨主版本升级，发布当天即生效，无人审阅 |
| 计量依赖文本 | 护栏正确性耦合引擎内部渲染格式，上游一变即失效 |
| 失效方式静默 | 解析失败返回 0 而非报错，既不熔断也不告警，只能靠测试兜住 |

---

## 四、修复

### 4.1 止血：锁定主版本上界

`requirements.txt` / `pyproject.toml`：

```diff
-duckdb>=0.10
+# duckdb 主版本上界锁定（CI 事故 2026-10-22）……
+duckdb>=1.5.6,<2.0
```

升级 2.x 必须先改造护栏计量方式并跑全量回归，不得由 pip 解析器自动完成。

### 4.2 加固：不可计量时显式留痕

`exec/guards.py` 新增可计量性探针，把"测不到"从"零扫描"里拆出来：

```python
def plan_rows_measurable(plan_text: str) -> bool:
    return _ROWS_LINE_RE.search(plan_text) is not None
```

`execute_sql` 中的接法：

```python
scanned = parse_scan_rows(plan_text)
if plan_rows_measurable(plan_text):
    cache_scan_rows(sql, scanned)
else:
    # 不可计量：记 warning 留痕，且不写缓存
    # 避免把"测不到"固化成"扫了 0 行"的假事实
    logger.warning("扫描行数预检不可计量：执行计划未包含算子行数，本轮跳过扫描行熔断", ...)
```

两个设计要点：**不缓存**（否则一次误判会长期污染同一 SQL 的预检结果）、**不静默**（warning 带"请核验 duckdb 版本"的处置指引）。

### 4.3 回归：把漂移变成显式失败

| 新增用例 | 作用 |
| --- | --- |
| `test_scan_rows_measured_on_real_plan` | 对本机实时计划断言 `scan_rows > 0`，版本/格式漂移时**直接报出根因**，而不是表现为"熔断未触发"的间接症状 |
| `test_plan_rows_measurable_distinguishes_zero_scan` | 探针单测：`"0 rows"` → 可计量；Summary-only → 不可计量 |
| `test_scan_cap_logs_warning_when_plan_unmeasurable` | 不可计量场景必须有 warning 可采集，禁止无声放行 |

---

## 五、验证

| 项 | 结果 |
| --- | --- |
| `black --check .` | 215 files unchanged |
| `ruff check .` | All checks passed |
| 本地全量 pytest（duckdb 1.5.6） | `1017 passed, 2 skipped` + 2 个 Windows 既有环境失败（见九） |
| 目标用例 `test_scan_cap_logs_error` | 通过 |
| PR CI（`39948104247`） | lint ✓ 35s / test ✓ 2m52s |
| 合并后 master CI（`39948729126`） | lint ✓ 35s / test ✓ 2m54s（pytest + golden ×2 + intent eval 全过） |
| CI 实装版本 | `duckdb-1.5.6`（上界生效的铁证） |

---

## 六、时间线

| 时间 | 事件 |
| --- | --- |
| 2026-10-21 及之前 | 夜跑持续 green（duckdb 1.x） |
| 2026-10-22 | duckdb 2.0.0 发布；当日夜跑首次 failure |
| 2026-10-22 17:51 | 定位根因并提交修复（`64ae32a`，本地分支） |
| 2026-10-23 09:20 | 夜跑再次 failure（修复未推送，master 无变化） |
| 2026-10-23 19:43 | 推送分支 + 开 PR #14；PR CI 全绿 |
| 2026-10-23 19:48 | PR 合并为 `85b7241`；master push CI 全绿 |
| 2026-10-24 | 本地复验 + 归档本报告 |

> **教训**：修复写完却没推送，等于没修。10-23 那次 failure 完全是流程问题，不是技术问题。

---

## 七、暴露的工程问题

1. **依赖治理**：核心依赖必须锁主版本上界。DuckDB 是本项目的执行引擎，其主版本变更等同于架构级变更，不能交给 pip 自动决策。
2. **护栏设计**：资源治理的正确性不应建立在引擎的文本输出格式上。中期方向是改用引擎可编程接口（profiling / 计量 API）或按算子统计独立累计，而非正则抠 `N rows`。
3. **失败模式**：`return 0` 这类"解析失败给默认值"的写法在护栏代码里是反模式，必须 fail-loud 或至少留痕。
4. **本地/CI 版本漂移**：本地手工环境长期落后于 CI 新装环境，是本次定位耗时的主因。建议本地也走 `pip install -r` 对齐。
5. **告警缺失**：CI 挂了 2 天才被人看见。仓库无 CI 失败通知，建议加邮件/IM 告警。

---

## 八、遗留项

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 夜跑（`schedule`）验证 | **待观察** | 修复后尚未触发过夜跑；`nightly` job 独有的 `boundary_eval` + RLS 对抗矩阵只在 schedule 跑，需下一次夜跑确认 |
| Windows 并发 SQLite 用例 | **已修**（同日追加） | `test_audit_store_db_multi_writer_threads`、`test_concurrent_workers_no_lost_write` 曾因 Windows 并发首次创建文件的竞争稳定失败（`readonly database` / `PermissionError(13)`），Linux CI 不触发。已定位并修复，见提交 `cf1bf9a` 与专项报告 [2026-10-24-repair-win32-sqlite-create-race.md](2026-10-24-repair-win32-sqlite-create-race.md)：并发 `connect`/`open` 未写完的文件被 SQLite 判为只读（状态持久，必须重连），已在 kvstore 初始化与审计锁文件打开路径加"重建连接 + 有界退避"重试 |
| 护栏计量方式改造 | **未做** | 解除 `duckdb<2.0` 上界的前置条件：先让扫描行计量不依赖计划文本渲染 |

---

## 九、复现与验证命令

```bash
# 复现 2.0 失效（会临时升级 duckdb，勿在共享环境执行）
pip install "duckdb==2.0.0"
python -m pytest -q tests/audit/test_logging_coverage.py::test_scan_cap_logs_error
# → Failed: DID NOT RAISE MaxRowsScannedExceeded

# 恢复并验证修复
pip install -r requirements-dev.txt
python -m pytest -q tests/exec/test_exec.py tests/audit/test_logging_coverage.py
```
