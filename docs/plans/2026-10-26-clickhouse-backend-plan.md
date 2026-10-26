# ClickHouse 执行后端支持 实施计划（二十三期）

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。
> 任务确认清单见 `../tasks/2026-10-26-clickhouse-backend-tasks.md`。

**Goal:** 在二十二期三后端架构上新增 ClickHouse 执行后端（`DB_BACKEND=clickhouse`），全量对齐 MySQL/PostgreSQL 交付模式：引擎 adapter + 编译方言 + CH 建仓 + parity 回归 + CI 门禁 + 评测冒烟。

**Architecture:** 双轨接入（`exec/engine/clickhouse_adapter.py` 执行引擎 + `compiler/dialect/clickhouse.py` 方言渲染器），两处注册表各登记一行；扫描/超时预算由**账号 profile 承载**（readonly=1），加固为**验证式**（查 `system.settings` fail-closed 校验）；探索层复用内联派生表路线；护栏框架与连接池零改动。

**Tech Stack:** clickhouse-connect 0.7+（HTTP 协议，驱动 API 已实测：`get_client`/`query`/`insert_df`/`command`，**无 cancel 方法**，错误码嵌于报错文本）、sqlglot（DDL 转译 + 方言解析）、既有 exec.guards 护栏框架。

**Spec:** [docs/designs/2026-10-26-clickhouse-backend-design.md](../designs/2026-10-26-clickhouse-backend-design.md)（决策 D1-D13；本计划从规格出发论证，执行者需同时读两者）

**计划期实测裁决更新（spec 预留验证点闭环，记入 tasks 执行日志）：**
- spec D5 预留的"cancel 实现期验证"：实测 clickhouse-connect 0.7+ 的 `Client`/`HttpClient` 均无 `cancel`/`kill` 方法 → **cancel() 落定为空操作**，服务端 `max_execution_time` 终止查询（与 MySQL/PG 先例同口径）；
- spec D3/D4 的错误码归一机制：驱动异常**无结构化 code 属性**（HTTP 层 `raise OperationalError/DatabaseError(err_str)`，错误码嵌在文本 `Received ClickHouse exception, code: N`）→ 经文本正则 `\bcode:\s*(\d+)` 提取归一（159 超时 / 131 扫描超限）；
- spec D7 spine 的 dateAdd 细化：改用 **addDays/addMonths/addYears 函数族**（比 `dateAdd(unit, value, date)` 的 unit 关键字签名更稳定），机制（numbers(N) + 纯 Python 桶数）不变。

## Global Constraints

- golden 评测锚定 DuckDB 方言；**duckdb / mysql / postgres 三个 renderer 的任何输出逐字节不变**（golden 锚点保护）。
- LLM 产出的 SQL 永不直接执行；一切执行只发生在治理管道内（只读 + RLS + 资源上限）。
- 确定性三原则：种子 42、`AS_OF_DATE=2025-12-31`、单 rng 顺序消费；多后端逐行同数据。
- 依赖 pin：`clickhouse-connect>=0.7,<1.0`（写入 requirements.txt 并附选型理由注释）；兼容基线 ClickHouse >= 23.8；CI 锚定 `clickhouse/clickhouse-server:24.8`。
- fail-closed：验证式加固失配即抛 `SecurityError`，严禁弱配置连接进管道；扫描预检诚实返回 `(0, False)` 由护栏框架 warning 留痕，严禁静默当成 0 行放行。
- 所有新增/修改代码的 docstring 与行内注释使用简体中文（专有名词保留英文）；测试函数名英文；Git 提交信息遵循 `feat:/fix:/docs:/test:` 前缀 + 简体中文描述。
- 每个任务收尾 `black --check .`、`ruff check .` 必须绿（全量 `pytest -q` 在 Task 10 收尾统一跑，中间任务跑各自子集）。
- 本地开发环境为 conda `dataagent`（Python 3.12）；执行命令一律 `conda run -n dataagent --no-capture-output python ...`（conda run 不支持多行 `-c`，多行探针写临时脚本文件执行）。
- 本地 parity 自动跳过（`CLICKHOUSE_TEST_HOST` 未配置），由 CI `clickhouse-parity` job 承担真实验证（沿二十二期与用户确认的模式）。
- 时区确定性：CI 容器 `TZ=UTC`；部署要求服务器时区 UTC（spec D13；`session_timezone` 为可选双保险，验证结果记入 tasks 执行日志）。
- 任务产出文档遵循五类目录规范（`docs/README.md`）：本计划在 `docs/plans/`，任务记录在 `docs/tasks/`。

## Review Focus

规格隐含、但没有任何任务的测试直接覆盖的输入类别/失效模式，前五条（每条已挂到 owning task 的测试）：

1. **错误码从报错文本提取的脆弱性**：驱动无结构化 code 属性，文本格式随版本演进漂移或缺失 → 提取失败必须退化为普通 `SqlExecutionError`（不得误归一为超时/熔断）→ Task 3 单测锁定"无 code 文本 → None → 普通错误"路径；服务级由 CI parity 兜底。
2. **profile 与应用预算漂移**（部署覆盖 users.d / 版本默认值变化）→ harden 必须对三类失配全 fail-closed（设置缺失 / readonly<1 / 预算不一致），错误消息指名失配项 → Task 3 矩阵单测；CI users.d 与 `config/settings.py` 双侧同步（Task 9）。
3. **探索 SQL 携带 `SETTINGS` 子句篡改预算**（用户文本直达执行面的唯一路径）→ 方言增量黑名单必须拒绝 `SETTINGS`（readonly=1 服务端亦拒绝，黑名单为纵深防御）→ Task 4 单测。
4. **无列名 DataFrame / NaN/NaT 打穿 insert_df**（`_write_rows` 元组行构造，columns 为 0..n 整数；MySQL/PG 先例 CI 实锤坑）→ 位置列名经 `DESCRIBE TABLE` 补齐 + 空值统一清洗为 None → Task 3 单测 + Task 6 建仓兜底。
5. **spine 桶数月历算术边界**（月末/闰年/负向窗口/未知 INTERVAL 形态）→ 未知形态与负向窗口显式 `CompileError`，严禁静默拼接非法 SQL → Task 2 单测锁定（含 `INTERVAL 1 WEEK` 归一为 7 天步长）。

---

### Task 1: 配置与依赖基座（CLICKHOUSE_* 配置组 + clickhouse-connect）

**Files:**
- Modify: `config/settings.py:50`（POSTGRES_DATABASE 行之后插入）
- Modify: `requirements.txt`（文件末尾追加）
- Modify: `.env.example`（POSTGRES 组之后插入）

**Interfaces:**
- Produces: `settings.CLICKHOUSE_HOST/PORT/USER/PASSWORD/DATABASE`（Task 3 adapter 缺省 DSN、Task 6 建仓消费）；clickhouse-connect 可导入（Task 3/6 消费）。

- [x] **Step 1: settings.py 增加 CLICKHOUSE_* 配置组**

在 `POSTGRES_DATABASE` 行（settings.py:50）之后插入：

```python
# ClickHouse 执行后端连接配置（二十三期 spec §6，DB_BACKEND=clickhouse 时生效）
# 运行时查询用 readonly profile 账号（部署要求 readonly=1 + max_rows_to_read /
# max_execution_time 与 MAX_SCAN_ROWS / QUERY_TIMEOUT_MS 一致，harden 验证式
# fail-closed 校验）；建仓（python -m mock.init_clickhouse）复用本组配置但要求
# 账号具备 CREATE/INSERT/DROP 权限（两账号分离见 spec §9）
CLICKHOUSE_HOST: str = os.getenv("CLICKHOUSE_HOST", "127.0.0.1")
CLICKHOUSE_PORT: int = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER: str = os.getenv("CLICKHOUSE_USER", "dataagent_ro")
CLICKHOUSE_PASSWORD: str = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE: str = os.getenv("CLICKHOUSE_DATABASE", "gmall")
```

- [x] **Step 2: .env.example 增量**

在 POSTGRES 组之后插入：

```bash
# ---------- ClickHouse 执行后端（二十三期，DB_BACKEND=clickhouse 时生效） ----------
# 运行时账号须为 readonly profile（readonly=1 + 预算两项，见 docs/designs/
# 2026-10-26-clickhouse-backend-design.md §9）；建仓复用本组但要求可写账号
CLICKHOUSE_HOST=127.0.0.1
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=dataagent_ro
CLICKHOUSE_PASSWORD=
CLICKHOUSE_DATABASE=gmall
```

- [x] **Step 3: requirements.txt 追加依赖（文件末尾）**

```text
# ClickHouse 执行后端（二十三期）：官方主推 HTTP 客户端（选型对比 spec §4 S1——
# 否决 clickhouse-driver：维护缓慢 + 灌数/取消成本高；否决间接通道：护栏隔层）。
# 兼容基线 ClickHouse >= 23.8（spec §9 版本演进跟随策略——升级前须跑全量回归与
# tests/test_clickhouse_backend_parity.py）
clickhouse-connect>=0.7,<1.0
```

- [x] **Step 4: 验证**

Run: `conda run -n dataagent --no-capture-output python -c "from config import settings; print(settings.CLICKHOUSE_HOST, settings.CLICKHOUSE_PORT, settings.CLICKHOUSE_DATABASE)"`
Expected: `127.0.0.1 8123 gmall`

Run: `conda run -n dataagent --no-capture-output python -c "import clickhouse_connect; print(clickhouse_connect.__version__.__version__ if hasattr(clickhouse_connect.__version__, '__version__') else 'ok')"`
Expected: 打印版本号（>= 0.7），无 ImportError

- [x] **Step 5: 质量门 + Commit**

Run: `conda run -n dataagent --no-capture-output black --check . && conda run -n dataagent --no-capture-output ruff check .`
Expected: 全绿

```bash
git add config/settings.py requirements.txt .env.example
git commit -m "feat: ClickHouse 后端配置组与 clickhouse-connect 依赖基座（二十三期 T1）"
```

---

### Task 2: 方言层 ClickHouseRenderer（九原语 + numbers spine）+ 方言注册表

**Files:**
- Create: `compiler/dialect/clickhouse.py`
- Modify: `compiler/dialect/__init__.py:38-42`（`_RENDERER_FACTORIES` 登记一行）
- Test: `tests/compiler/test_dialect.py`（追加 ClickHouse 用例）

**Interfaces:**
- Consumes: `DialectRenderer` 协议（compiler/dialect/base.py 九原语）；`CompileError`（compiler/errors.py）。
- Produces: `ClickHouseRenderer`（`name="clickhouse"`）、`parse_interval_step(step) -> tuple[int, str]`；`get_renderer()` 在 `settings.SQL_DIALECT="clickhouse"` 或 `DB_BACKEND="clickhouse"` 下返回 `ClickHouseRenderer` 实例（Task 3/6/7 消费）。

- [x] **Step 1: 写失败测试**

在 `tests/compiler/test_dialect.py` 末尾追加（函数名英文、断言精确到字符串）：

```python
class TestClickHouseRenderer:
    """ClickHouse 方言九原语（二十三期 spec D6/D7）+ 桶数计算边界。"""

    def _r(self):
        from compiler.dialect.clickhouse import ClickHouseRenderer

        return ClickHouseRenderer()

    def test_quote_ident_escapes_backtick(self):
        assert self._r().quote_ident("a`b") == "`a``b`"

    def test_quote_column(self):
        assert self._r().quote_column("t", "col") == "`t`.`col`"

    def test_timestamp_literal(self):
        assert (
            self._r().timestamp_literal("2025-06-01 00:00:00")
            == "toDateTime('2025-06-01 00:00:00')"
        )

    def test_date_trunc_four_granularities(self):
        r = self._r()
        assert r.date_trunc("day", "d") == "toStartOfDay(d)"
        assert r.date_trunc("week", "d") == "toMonday(d)"  # 周一锚定（spec D6）
        assert r.date_trunc("month", "d") == "toStartOfMonth(d)"
        assert r.date_trunc("quarter", "d") == "toStartOfQuarter(d)"

    def test_date_trunc_unknown_granularity_rejected(self):
        import pytest

        from compiler.errors import CompileError

        with pytest.raises(CompileError):
            self._r().date_trunc("hour", "d")

    def test_date_add_passthrough(self):
        # 编译器 INTERVAL 1 DAY 形态在 CH 合法，透传拼接（spec D6）
        assert self._r().date_add("d", "INTERVAL 1 DAY") == "d + INTERVAL 1 DAY"

    def test_having_metric_ref_uses_alias(self):
        assert self._r().having_metric_ref("gmv", "sum(x)") == "`gmv`"

    def test_spine_day_1826_buckets(self):
        # 5 年 day 粒度：2021-01-01 ~ 2025-12-31（编译器 end_incl 已回退末桶）
        sql = self._r().spine_cte_sql("ts", "2021-01-01 00:00:00", "2025-12-31 00:00:00", "INTERVAL 1 DAY")
        assert sql == (
            "SELECT addDays(toDateTime('2021-01-01 00:00:00'), number) AS `ts` "
            "FROM numbers(1826)"
        )

    def test_spine_week_step_normalized_to_7_days(self):
        sql = self._r().spine_cte_sql("ts", "2021-01-04 00:00:00", "2021-02-01 00:00:00", "INTERVAL 1 WEEK")
        assert "addDays(" in sql and "number * 7" in sql and "FROM numbers(5)" in sql

    def test_spine_quarter_3month_buckets(self):
        # 2021-01-01 起步长 3 个月至 2021-10-01（末桶起点）：4 个桶
        # （01-01 / 04-01 / 07-01 / 10-01，闭区间含端点）
        sql = self._r().spine_cte_sql("ts", "2021-01-01 00:00:00", "2021-10-01 00:00:00", "INTERVAL 3 MONTH")
        assert "addMonths(toDateTime('2021-01-01 00:00:00'), number * 3)" in sql
        assert "FROM numbers(4)" in sql

    def test_spine_month_end_boundary(self):
        # 月历算术边界：1/31 起步长 1 个月，2 月无 31 日——桶数按锚点日未满计
        # （e.day < s.day 差一个月）：2021-01-31 ~ 2021-03-01 → 2 桶
        sql = self._r().spine_cte_sql("ts", "2021-01-31 00:00:00", "2021-03-01 00:00:00", "INTERVAL 1 MONTH")
        assert "FROM numbers(2)" in sql

    def test_spine_unknown_interval_form_rejected(self):
        import pytest

        from compiler.errors import CompileError

        with pytest.raises(CompileError):
            self._r().spine_cte_sql("ts", "2021-01-01 00:00:00", "2021-02-01 00:00:00", "INTERVAL 1 HOUR")

    def test_spine_negative_window_rejected(self):
        import pytest

        from compiler.errors import CompileError

        with pytest.raises(CompileError):
            self._r().spine_cte_sql("ts", "2021-02-01 00:00:00", "2021-01-01 00:00:00", "INTERVAL 1 DAY")

    def test_with_keyword_non_recursive(self):
        assert self._r().with_keyword() == "WITH"

    def test_escape_string_literal_backslash_family(self):
        # 反斜杠转义系：\ 翻倍 + ' 写作 \'（spec D6，与 MySQL '' 口径不同）
        assert self._r().escape_string_literal("a'b\\c") == "'a\\'b\\\\c'"


def test_clickhouse_registry(monkeypatch):
    """方言注册表：SQL_DIALECT=clickhouse 返回 ClickHouseRenderer（惰性工厂）。"""
    import config.settings as settings
    from compiler.dialect import ClickHouseRenderer, get_renderer

    monkeypatch.setattr(settings, "SQL_DIALECT", "clickhouse")
    assert isinstance(get_renderer(), ClickHouseRenderer)


def test_three_existing_renderers_unchanged_byte_wise():
    """golden 红线：duckdb/mysql/postgres 三 renderer 关键产物逐字节不变（回归锚）。"""
    from compiler.dialect import get_renderer
    import config.settings as settings

    cases = {
        "duckdb": (
            lambda r: r.date_trunc("week", "o.order_time"),
            "date_trunc('week', o.order_time)",
        ),
        "mysql": (
            lambda r: r.date_trunc("week", "o.order_time"),
            "DATE_SUB(DATE(o.order_time), INTERVAL WEEKDAY(DATE(o.order_time)) DAY)",
        ),
        "postgres": (
            lambda r: r.date_add("d", "INTERVAL 1 DAY"),
            "d + INTERVAL '1 day'",
        ),
    }
    old = settings.SQL_DIALECT
    try:
        for name, (fn, expected) in cases.items():
            settings.SQL_DIALECT = name
            assert fn(get_renderer()) == expected, name
    finally:
        settings.SQL_DIALECT = old
```

- [x] **Step 2: 跑测试确认失败**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/compiler/test_dialect.py -q -k ClickHouse`
Expected: FAIL/ERROR（`ModuleNotFoundError: compiler.dialect.clickhouse`）

- [x] **Step 3: 实现 `compiler/dialect/clickhouse.py`**

```python
"""ClickHouse 方言渲染器（二十三期 spec D6/D7）。

口径要点：
- 标识符反引号（内部翻倍）；字符串字面量走反斜杠转义系（\\ 翻倍、' 写作
  \\'——CH 字面量以反斜杠转义为标准语义，与 MySQL 的 '' 口径不同，不使用
  引号翻倍）；
- date_trunc 四档全原生（week 用 toMonday 周一锚定，规避 toStartOfWeek 的
  周起始 mode 歧义，与 DuckDB date_trunc('week') / MySQL WEEKDAY 口径一致）；
- date_add 透传拼接：编译器 INTERVAL 1 DAY 形态在 CH 合法（无需 PG 式转译）；
- spine 用 numbers(N) + addDays/addMonths/addYears 函数族（远古可用、签名稳定；
  拒绝 generateSeries 版本赌注与实验态递归 CTE，spec D7）：N 由渲染器纯
  Python 桶数计算——day/week 按整日差（WEEK 归一 7 天）、month/quarter（MONTH
  形态）与 year（YEAR 形态）手写月历算术（不引入 dateutil），未知步长形态与
  负向窗口显式 CompileError（对齐 pg_interval_literal 先例）。
"""

from __future__ import annotations

import re
from datetime import date, datetime

from compiler.errors import CompileError

# 编译器侧 INTERVAL 字面量形态（与 postgres 转译同源约束）：
# spine 四处步长 + comparison 对齐两类：INTERVAL <n> <UNIT>
_INTERVAL_RE = re.compile(r"INTERVAL\s+(\d+)\s+(DAY|WEEK|MONTH|YEAR)", re.IGNORECASE)

# CH 时间平移函数族（按归一后的单位分发；WEEK 归一为 DAY*7，QUARTER 编译器
# 侧即 MONTH*3 形态）
_UNIT_FUNCS = {"DAY": "addDays", "MONTH": "addMonths", "YEAR": "addYears"}

_FMT = "%Y-%m-%d %H:%M:%S"


def parse_interval_step(step: str) -> tuple[int, str]:
    """编译器 INTERVAL 字面量 -> (步长倍数, 归一单位)。

    'INTERVAL 7 DAY' -> (7, 'DAY')；'INTERVAL 1 WEEK' -> (7, 'DAY')（周步长
    归一天步长）；未知形态显式 CompileError（严禁静默拼接非法 SQL）。
    """
    m = _INTERVAL_RE.fullmatch(step.strip())
    if m is None:
        raise CompileError(f"不支持的 INTERVAL 字面量形态: {step!r}")
    multiple, unit = int(m.group(1)), m.group(2).upper()
    if unit == "WEEK":
        return multiple * 7, "DAY"
    return multiple, unit


def _bucket_count(start: str, end: str, multiple: int, unit: str) -> int:
    """fill_gaps 桶数 N = floor((end - start) / step) + 1。

    end 已由编译器 end_incl 回退到末桶起点，闭区间口径与 DuckDB
    generate_series 一致；start <= end 前提之外（负向窗口）fail-closed 报错。
    month/year 按"锚点日/时未满整步差一个月/年"的月历算术（闰年与月末由
    datetime 比较天然承载）。
    """
    s = datetime.strptime(start, _FMT)
    e = datetime.strptime(end, _FMT)
    if unit == "DAY":
        total = (e - s).days
    elif unit == "MONTH":
        total = (e.year - s.year) * 12 + (e.month - s.month)
        if (e.day, e.hour, e.minute, e.second) < (s.day, s.hour, s.minute, s.second):
            total -= 1
    else:  # YEAR
        total = e.year - s.year
        if (e.month, e.day, e.hour, e.minute, e.second) < (
            s.month,
            s.day,
            s.hour,
            s.minute,
            s.second,
        ):
            total -= 1
    if total < 0:
        raise CompileError(f"spine 窗口负向: start={start!r} end={end!r} unit={unit}")
    return total // multiple + 1


class ClickHouseRenderer:
    """ClickHouse 方言渲染器（DialectRenderer 协议实现）。"""

    name = "clickhouse"

    def quote_ident(self, name: str) -> str:
        return "`" + str(name).replace("`", "``") + "`"

    def quote_column(self, alias: str, column: str) -> str:
        return f"`{alias}`.`{column}`"

    def timestamp_literal(self, value: datetime | date | str) -> str:
        if isinstance(value, datetime):
            s = value.strftime(_FMT)
        else:
            s = str(value)
        return f"toDateTime('{s}')"

    def date_trunc(self, granularity: str, expr: str) -> str:
        mapping = {
            "day": f"toStartOfDay({expr})",
            "week": f"toMonday({expr})",
            "month": f"toStartOfMonth({expr})",
            "quarter": f"toStartOfQuarter({expr})",
        }
        if granularity not in mapping:
            raise CompileError(f"不支持的 granularity: {granularity!r}")
        return mapping[granularity]

    def date_add(self, expr: str, interval: str) -> str:
        # 编译器 INTERVAL 1 DAY 形态在 CH 合法，透传拼接（spec D6）
        return f"{expr} + {interval}"

    def having_metric_ref(self, alias: str, expr: str) -> str:
        # CH 支持别名复用（同 MySQL 路线，spec D6）
        return self.quote_ident(alias)

    def spine_cte_sql(self, alias: str, start: str, end: str, step: str) -> str:
        # numbers(N) + addDays/addMonths/addYears：N 纯 Python 计算（spec D7）；
        # number 为 numbers() 表的 UInt64 列，按步长倍数平移
        multiple, unit = parse_interval_step(step)
        n = _bucket_count(start, end, multiple, unit)
        m = "" if multiple == 1 else f" * {multiple}"
        func = _UNIT_FUNCS[unit]
        return (
            f"SELECT {func}(toDateTime('{start}'), number{m}) AS {self.quote_ident(alias)} "
            f"FROM numbers({n})"
        )

    def with_keyword(self) -> str:
        # spine 非递归（numbers 集合产出），无需 RECURSIVE
        return "WITH"

    def escape_string_literal(self, value: str) -> str:
        # 反斜杠转义系：\\ 翻倍 + ' 写作 \'（CH 标准转义语义，spec D6）
        return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


__all__ = ["ClickHouseRenderer", "parse_interval_step"]
```

- [x] **Step 4: 方言注册表登记（`compiler/dialect/__init__.py`）**

`_RENDERER_FACTORIES` 的 `"postgres"` 行之后加：

```python
    "clickhouse": _lazy_factory("compiler.dialect.clickhouse", "ClickHouseRenderer"),
```

同时更新 `get_renderer` 的 docstring 中"取当前方言渲染器（duckdb / mysql / postgres）"为四值枚举。

- [x] **Step 5: 跑测试确认通过**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/compiler/test_dialect.py tests/compiler -q`
Expected: 全 PASS（含既有 duckdb/mysql/postgres 用例——三 renderer 逐字节不变回归）

- [x] **Step 6: 质量门 + Commit**

Run: `conda run -n dataagent --no-capture-output black --check . && conda run -n dataagent --no-capture-output ruff check .`
Expected: 全绿

```bash
git add compiler/dialect/clickhouse.py compiler/dialect/__init__.py tests/compiler/test_dialect.py
git commit -m "feat: ClickHouse 方言渲染器九原语 + numbers spine 桶数计算（二十三期 T2）"
```

---

### Task 3: 引擎层 ClickHouseAdapter（clickhouse-connect）+ 引擎注册表

**Files:**
- Create: `exec/engine/clickhouse_adapter.py`
- Modify: `exec/engine/__init__.py:32-36`（`_ADAPTER_FACTORIES` 登记一行）
- Test: `tests/engine/test_clickhouse_adapter.py`

**Interfaces:**
- Consumes: `EngineAdapter` 协议（exec/engine/base.py）；`normalize_cell`（exec/engine/normalize.py）；`QueryTimeoutError`/`SqlExecutionError`（exec/errors.py）；`settings.CLICKHOUSE_*`（Task 1）。
- Produces: `ClickHouseAdapter`（`name="clickhouse"`、`dialect="clickhouse"`）、`parse_ch_error_code(message) -> int | None`、`HARDEN_REQUIRED_SETTINGS() -> dict[str, int]`、`CH_TIMEOUT_ERROR_CODE=159`、`CH_SCAN_LIMIT_ERROR_CODE=131`；`get_adapter("clickhouse")` 可用（Task 4/5/6/7/8 消费）。adapter **不实现** `install_secure_views`（方法存在性分发自动落内联派生表路线，spec D8）。

- [x] **Step 1: 写失败测试（tests/engine/test_clickhouse_adapter.py）**

```python
"""ClickHouse adapter 纯逻辑单测（二十三期 spec D2-D5）。

驱动已实测（clickhouse-connect 0.7+）：Client 无 cancel 方法（cancel 恒空操作
+ 服务端 max_execution_time 自限）；异常无结构化 code 属性（错误码嵌在报错
文本 "code: N"，正则提取归一）。本文件不依赖真实 CH 服务——harden/归一/灌数
清洗用 Fake client 锁行为；服务级由 CI clickhouse-parity 承担。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from exec.engine.clickhouse_adapter import (
    CH_SCAN_LIMIT_ERROR_CODE,
    CH_TIMEOUT_ERROR_CODE,
    ClickHouseAdapter,
    HARDEN_REQUIRED_SETTINGS,
    parse_ch_error_code,
)


class FakeChError(Exception):
    """伪造 CH 驱动异常基类（模拟 clickhouse_connect.driver.exceptions.Error）。"""


def _err(message: str) -> Exception:
    return FakeChError(message)


class FakeResult:
    def __init__(self, rows, columns):
        self.result_rows = rows
        self.column_names = columns


class FakeClient:
    """按脚本回放 query 结果的假客户端（harden/DESCRIBE 校验用）。"""

    def __init__(self, script: dict[str, FakeResult]):
        self._script = script
        self.inserted: list[tuple[str, pd.DataFrame]] = []

    def query(self, sql: str, *a, **kw):
        return self._script[sql]

    def insert_df(self, table: str, df, **kw):
        self.inserted.append((table, df))
        return len(df)

    def close(self) -> None:  # pragma: no cover - 接口占位
        pass


_HARDEN_SQL = (
    "SELECT name, value FROM system.settings "
    "WHERE name IN ('readonly', 'max_execution_time', 'max_rows_to_read')"
)


def _ok_script(readonly="1", met="30", mrr="10000000") -> dict[str, FakeResult]:
    return {
        _HARDEN_SQL: FakeResult(
            [
                ("readonly", readonly),
                ("max_execution_time", met),
                ("max_rows_to_read", mrr),
            ],
            ["name", "value"],
        ),
        "DESCRIBE TABLE `t1`": FakeResult(
            [("a", ""), ("b", ""), ("c", "")], ["name", "type", "default_type"]
        ),
    }


def test_parse_ch_error_code():
    assert parse_ch_error_code("Received ClickHouse exception, code: 159, message: x") == 159
    assert parse_ch_error_code("no code here") is None


def test_engine_error_timeout_159_normalized(monkeypatch):
    monkeypatch.setattr(
        "exec.engine.clickhouse_adapter._CH_ERROR_BASE", FakeChError
    )
    adapter = ClickHouseAdapter()
    normalized = adapter.engine_error(_err("Received ClickHouse exception, code: 159, message: timeout"))
    from exec.errors import QueryTimeoutError

    assert isinstance(normalized, QueryTimeoutError)


def test_engine_error_scan_limit_131_normalized(monkeypatch):
    monkeypatch.setattr("exec.engine.clickhouse_adapter._CH_ERROR_BASE", FakeChError)
    adapter = ClickHouseAdapter()
    normalized = adapter.engine_error(_err(f"Received ClickHouse exception, code: {CH_SCAN_LIMIT_ERROR_CODE}, message: limit"))
    from exec.errors import SqlExecutionError

    assert isinstance(normalized, SqlExecutionError)
    assert "max_rows_to_read" in str(normalized)


def test_engine_error_without_code_stays_generic(monkeypatch):
    """错误码文本缺失/格式漂移 → 普通错误（Review Focus #1，严禁误归一）。"""
    monkeypatch.setattr("exec.engine.clickhouse_adapter._CH_ERROR_BASE", FakeChError)
    adapter = ClickHouseAdapter()
    normalized = adapter.engine_error(_err("some drift message without code"))
    from exec.errors import SqlExecutionError

    assert type(normalized) is SqlExecutionError


def test_engine_error_non_ch_exception_returns_none(monkeypatch):
    monkeypatch.setattr("exec.engine.clickhouse_adapter._CH_ERROR_BASE", FakeChError)
    assert ClickHouseAdapter().engine_error(ValueError("x")) is None


def test_harden_pass_when_profile_matches_budget():
    from security.errors import SecurityError

    adapter = ClickHouseAdapter()
    adapter.harden(FakeClient(_ok_script()))  # 不抛即通过


@pytest.mark.parametrize(
    "script",
    [
        # 缺设置项
        {
            _HARDEN_SQL: FakeResult([("readonly", "1")], ["name", "value"]),
        },
        # readonly < 1
        _ok_script(readonly="0"),
        # 预算不一致（部署漂移）
        _ok_script(mrr="12345"),
    ],
)
def test_harden_fail_closed_matrix(script):
    from security.errors import SecurityError

    adapter = ClickHouseAdapter()
    with pytest.raises(SecurityError):
        adapter.harden(FakeClient(script))


def test_harden_budget_consistent_with_settings():
    """profile 期望值必须与 settings 预算同源（防单测与部署双向漂移）。"""
    from config import settings

    expected = HARDEN_REQUIRED_SETTINGS()
    assert expected["max_rows_to_read"] == settings.MAX_SCAN_ROWS
    assert expected["max_execution_time"] == settings.QUERY_TIMEOUT_MS // 1000
    assert expected["readonly"] == 1


def test_explain_scan_rows_not_measurable():
    """CH 无 EXPLAIN ANALYZE：诚实返回 (0, False)，warning 留痕由护栏框架承接。"""
    assert ClickHouseAdapter().explain_scan_rows(object(), "SELECT 1", 1000) == (0, False)


def test_cancel_is_noop():
    """驱动实测无 cancel 方法：空操作 + 服务端 max_execution_time 自限（spec D5）。"""
    assert ClickHouseAdapter().cancel(object()) is None


def test_insert_dataframe_unnamed_columns_and_null_cleaning(monkeypatch):
    """无列名 DataFrame 经 DESCRIBE 补物理列名；NaN/NaT/None 统一清洗（Review Focus #4）。"""
    adapter = ClickHouseAdapter()
    client = FakeClient(_ok_script())
    df = pd.DataFrame(
        [(1, None, datetime(2025, 1, 1)), (2, float("nan"), pd.NaT)],
    )
    adapter.insert_dataframe(client, "t1", df)
    table, out = client.inserted[0]
    assert table == "t1"
    assert list(out.columns) == ["a", "b", "c"]  # DESCRIBE 补名（位置对应）
    assert out.isna().sum().sum() == 0  # 全部清洗为 None（对象列）


def test_registry_clickhouse():
    from exec.engine import ClickHouseAdapter, get_adapter

    assert isinstance(get_adapter("clickhouse"), ClickHouseAdapter)


def test_timeout_and_scan_limit_constants():
    assert CH_TIMEOUT_ERROR_CODE == 159
    assert CH_SCAN_LIMIT_ERROR_CODE == 131
```

- [x] **Step 2: 跑测试确认失败**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/engine/test_clickhouse_adapter.py -q`
Expected: FAIL/ERROR（`ModuleNotFoundError: exec.engine.clickhouse_adapter`）

- [x] **Step 3: 实现 `exec/engine/clickhouse_adapter.py`**

```python
"""ClickHouse 适配器（二十三期 spec D2-D5）：clickhouse-connect HTTP 只读执行面。

超时/扫描预算（spec D3-D5）：由账号 profile 承载（readonly=1 + max_execution_time
+ max_rows_to_read），客户端无法在 readonly=1 会话内改 settings——加固从"改"变
"验"（harden 查 system.settings 与应用预算比对，fail-closed）。

驱动实测（clickhouse-connect 0.7+，计划期 2026-10-26）：
- Client/HttpClient 均无 cancel/kill 方法 → cancel() 空操作，服务端
  max_execution_time 终止查询（与 MySQL/PG 先例同口径，spec D5 退化路径落定）；
- 异常无结构化 code 属性（HTTP 层 raise OperationalError/DatabaseError(err_str)，
  错误码嵌于文本 "code: N"）→ 文本正则提取归一（159 超时 / 131 扫描超限）；
- get_client 的 connect_timeout / send_receive_timeout 经 kwargs 转发 HTTP 客户端
  （客户端读超时 > 服务端超时 +5s：服务端先超时、连接不报废，对齐 MySQL 口径）。

扫描预检（spec D3）：CH 无 EXPLAIN ANALYZE，explain_scan_rows 诚实返回
(0, False)（warning 留痕由护栏框架承接）；真实预算由 profile max_rows_to_read
服务端硬熔断承担——口径为多表读取总和，比既有三后端的各算子最大值更严（fail-safe）。
"""

from __future__ import annotations

import re
from typing import Any

from config import settings
from exec.engine.normalize import normalize_cell
from exec.errors import QueryTimeoutError, SqlExecutionError

# ClickHouse 服务端错误码（异常文本 "code: N" 提取；版本演进由 CI parity 兜底）
CH_TIMEOUT_ERROR_CODE = 159  # TIMEOUT_EXCEEDED（max_execution_time 触发）
CH_SCAN_LIMIT_ERROR_CODE = 131  # LIMIT_EXCEEDED 族（max_rows_to_read 触发）

# 驱动异常基类（模块级变量供测试注入假基类；惰性取真基类见 engine_error）
_CH_ERROR_BASE: type | None = None

_ERROR_CODE_RE = re.compile(r"\bcode:\s*(\d+)")


def _ch_error_base() -> type:
    global _CH_ERROR_BASE
    if _CH_ERROR_BASE is None:
        from clickhouse_connect.driver.exceptions import Error

        _CH_ERROR_BASE = Error
    return _CH_ERROR_BASE


def parse_ch_error_code(message: str) -> int | None:
    """从驱动报错文本提取 CH 错误码（无结构化属性的实测约束）；缺失返回 None。"""
    m = _ERROR_CODE_RE.search(message)
    return int(m.group(1)) if m else None


def HARDEN_REQUIRED_SETTINGS() -> dict[str, int]:
    """验证式加固的期望设置（与应用预算同源；Task 3 单测锁定同源性）。"""
    return {
        "readonly": 1,
        "max_execution_time": settings.QUERY_TIMEOUT_MS // 1000,
        "max_rows_to_read": settings.MAX_SCAN_ROWS,
    }


class ClickHouseAdapter:
    """ClickHouse 查询执行适配器（EngineAdapter 协议实现）。"""

    name = "clickhouse"
    dialect = "clickhouse"

    def __init__(self, dsn: dict[str, Any] | None = None) -> None:
        # dsn 可注入（parity 测试指向独立实例）；缺省读 settings.CLICKHOUSE_*
        self._dsn = dsn or {
            "host": settings.CLICKHOUSE_HOST,
            "port": settings.CLICKHOUSE_PORT,
            "user": settings.CLICKHOUSE_USER,
            "password": settings.CLICKHOUSE_PASSWORD,
            "database": settings.CLICKHOUSE_DATABASE,
        }

    def _new_client(self):
        import clickhouse_connect

        # connect_timeout / send_receive_timeout 经 get_client kwargs 转发 HTTP
        # 客户端（实测 0.7+ 签名含 **kwargs 转发通道）
        return clickhouse_connect.get_client(
            host=self._dsn["host"],
            port=self._dsn["port"],
            username=self._dsn["user"],
            password=self._dsn["password"],
            database=self._dsn["database"],
            connect_timeout=5,
            send_receive_timeout=settings.QUERY_TIMEOUT_MS / 1000.0 + 5.0,
        )

    def connect_readonly(self):
        client = self._new_client()
        try:
            self.harden(client)
        except Exception:
            client.close()
            raise
        return client

    def run_query(self, conn, sql: str) -> tuple[list[str], list[list[Any]]]:
        result = conn.query(sql)
        columns = list(result.column_names)
        rows = [[normalize_cell(v) for v in row] for row in result.result_rows]
        return columns, rows

    def cancel(self, conn) -> None:
        # 驱动实测无 cancel 方法（spec D5 退化路径落定）：看门狗回调空操作，
        # 服务端 max_execution_time 终止查询
        return None

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        if not isinstance(exc, _ch_error_base()):
            return None
        code = parse_ch_error_code(str(exc))
        if code == CH_TIMEOUT_ERROR_CODE:
            return QueryTimeoutError(f"查询超时（ClickHouse max_execution_time），已强制取消: {exc}")
        if code == CH_SCAN_LIMIT_ERROR_CODE:
            return SqlExecutionError(f"扫描行数超限（ClickHouse max_rows_to_read 熔断）: {exc}")
        # 文本格式漂移/其他错误：普通执行错误（喂回自愈，严禁误归一）
        return SqlExecutionError(f"{type(exc).__name__}: {exc}")

    def harden(self, conn) -> None:
        """验证式加固（spec D4，fail-closed）：readonly=1 会话内无法 SET，只能验。

        三类失配全拒绝并指名：设置缺失 / readonly<1 / 预算与应用配置不一致
        （后者同时捕获 profile 与 settings 的部署漂移）。
        """
        from security.errors import SecurityError

        try:
            result = conn.query(
                "SELECT name, value FROM system.settings "
                "WHERE name IN ('readonly', 'max_execution_time', 'max_rows_to_read')"
            )
            actual = {name: value for name, value in result.result_rows}
            expected = HARDEN_REQUIRED_SETTINGS()
            missing = sorted(set(expected) - set(actual))
            if missing:
                raise SecurityError(f"ClickHouse 加固校验失败，缺设置项: {missing}")
            for name, want in expected.items():
                got = int(float(actual[name]))
                if name == "readonly":
                    if got < want:
                        raise SecurityError(
                            f"ClickHouse readonly={got} < 1（账号 profile 未启用只读）"
                        )
                elif got != want:
                    raise SecurityError(
                        f"ClickHouse {name}={got} 与应用预算不一致（期望 {want}）——"
                        "部署 profile 漂移，须与 users.d 同步"
                    )
        except SecurityError:
            raise
        except Exception as exc:
            raise SecurityError(f"ClickHouse 连接加固失败: {exc}") from exc

    def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
        """CH 无 EXPLAIN ANALYZE：诚实不可计量（spec D3）；真实预算 profile 承载。"""
        return 0, False

    def insert_dataframe(self, conn, table: str, df) -> int:
        """DataFrame 批量入库（仅 mock 建仓链路），insert_df 走 Arrow 批量通道。

        无列名 DataFrame（_write_rows 元组行构造，columns 为 0..n 整数）经
        DESCRIBE TABLE 取物理列名按位置补齐（与 MySQL/PG 位置模式同语义）；
        空值统一清洗为 None（Nullable 列语义，NaN/NaT/None 全覆盖——MySQL/PG
        先例实锤 pandas 空值直插会报类型错）。
        """
        import pandas as pd

        data = df.copy()
        if all(str(c).isdigit() for c in data.columns):
            desc = conn.query(f"DESCRIBE TABLE {self.quote_ident(table)}")
            data.columns = [row[0] for row in desc.result_rows]
        data = data.astype(object).where(pd.notna(data), None)
        conn.insert_df(table, data)
        return len(data)

    def quote_ident(self, name: str) -> str:
        # adapter 内部 DESCRIBE 等元数据语句的标识符包裹（与方言渲染器同口径）
        return "`" + str(name).replace("`", "``") + "`"


__all__ = [
    "CH_SCAN_LIMIT_ERROR_CODE",
    "CH_TIMEOUT_ERROR_CODE",
    "ClickHouseAdapter",
    "HARDEN_REQUIRED_SETTINGS",
    "parse_ch_error_code",
]
```

- [x] **Step 4: 引擎注册表登记（`exec/engine/__init__.py`）**

`_ADAPTER_FACTORIES` 的 `"postgres"` 行之后加：

```python
    "clickhouse": _lazy_factory("exec.engine.clickhouse_adapter", "ClickHouseAdapter"),
```

- [x] **Step 5: 跑测试确认通过**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/engine -q`
Expected: 全 PASS（既有 mysql/postgres adapter 用例零变化）

- [x] **Step 6: 质量门 + Commit**

Run: `conda run -n dataagent --no-capture-output black --check . && conda run -n dataagent --no-capture-output ruff check .`
Expected: 全绿

```bash
git add exec/engine/clickhouse_adapter.py exec/engine/__init__.py tests/engine/test_clickhouse_adapter.py
git commit -m "feat: ClickHouseAdapter 引擎适配器（验证式加固/错误码文本归一/Arrow 灌数）+ 注册表（二十三期 T3）"
```

---

### Task 4: 护栏方言增量黑名单（clickhouse 键）

**Files:**
- Modify: `exec/guards.py:141-206`（`_DIALECT_EXTRA_KEYWORDS` / `_DIALECT_EXTRA_FUNC_RE` 各增 clickhouse 键）
- Test: `tests/exec/test_guards_clickhouse.py`

**Interfaces:**
- Consumes: 既有四层 fail-closed 结构（语句级关键词 `\b<kw>\b` 词边界匹配 + 函数正则 + AST 结构校验）。
- Produces: `assert_read_only_sql(sql, dialect="clickhouse")` 拒绝 SYSTEM/KILL/TRUNCATE/OPTIMIZE/SETTINGS/OUTFILE 与外部源表函数族（Task 7 parity 与生产链路隐式消费）。

- [x] **Step 1: 写失败测试（tests/exec/test_guards_clickhouse.py）**

```python
"""ClickHouse 方言护栏测试（二十三期 spec D4）：增量黑名单与 AST 结构拒绝。

通用黑名单与四层 fail-closed 结构由 tests/exec/test_exec.py 锁定（引擎无关）；
本文件只锁 clickhouse 增量面（SYSTEM/KILL/TRUNCATE/OPTIMIZE/SETTINGS/OUTFILE
+ 外部源表函数族）。
"""

from __future__ import annotations

import pytest

from exec.guards import UnsafeSqlError, assert_read_only_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SYSTEM SHUTDOWN",  # 管理语句族（最危险面）
        "SYSTEM RELOAD CONFIG",
        "KILL QUERY WHERE query_id='x'",
        "TRUNCATE TABLE order_detail",
        "OPTIMIZE TABLE order_detail FINAL",
        "SELECT 1 SETTINGS max_rows_to_read=0",  # 探索 SQL 篡改预算面（Review Focus #3）
        "SELECT * FROM order_detail INTO OUTFILE 'x.csv'",
    ],
)
def test_clickhouse_extra_keywords_rejected(sql):
    with pytest.raises(UnsafeSqlError):
        assert_read_only_sql(sql, dialect="clickhouse")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM url('http://evil')",
        "SELECT * FROM s3('http://evil')",
        "SELECT * FROM hdfs('x')",
        "SELECT * FROM file('/etc/passwd', 'CSV')",
        "SELECT * FROM jdbc('x')",
        "SELECT * FROM odbc('x')",
        "SELECT * FROM mysql('host:3306', 'db', 't')",
        "SELECT * FROM postgresql('host', 'db', 't')",
        "SELECT * FROM remote('host', 'db.t')",
        "SELECT * FROM clusterAllReplicas('c', 'db.t')",
        "SELECT * FROM input('x')",
        "SELECT * FROM generateRandom('x')",
        "SELECT sleep(10)",
        "SELECT sleepEachRow(1, 10)",
    ],
)
def test_clickhouse_extra_functions_rejected(sql):
    with pytest.raises(UnsafeSqlError):
        assert_read_only_sql(sql, dialect="clickhouse")


def test_clickhouse_normal_select_allowed():
    """正常只读查询不误伤（黑名单只增不破）。"""
    assert_read_only_sql("SELECT count() FROM order_detail WHERE dt = '2025-12-31'", dialect="clickhouse")
```

- [x] **Step 2: 跑测试确认失败**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/exec/test_guards_clickhouse.py -q`
Expected: FAIL（clickhouse 键不存在，语句不被拒绝）

- [x] **Step 3: 实现（exec/guards.py 两处登记）**

`_DIALECT_EXTRA_KEYWORDS` 的 `"postgres"` 键之后加：

```python
    # ClickHouse 语句族（二十三期 spec D4）：SYSTEM 管理语句族（SHUTDOWN/
    # RELOAD/DROP CACHE 等，CH 侧最危险面）；KILL 查询与变更；TRUNCATE 清表；
    # OPTIMIZE 合并管理；SETTINGS 子句（SELECT ... SETTINGS 改设置面——
    # readonly=1 下服务端本会拒绝，黑名单为纵深防御，Review Focus #3）；
    # OUTFILE 为 SELECT ... INTO OUTFILE 写服务器文件的目标子句
    "clickhouse": frozenset(
        {
            "SYSTEM",
            "KILL",
            "TRUNCATE",
            "OPTIMIZE",
            "SETTINGS",
            "OUTFILE",
        }
    ),
```

`_DIALECT_EXTRA_FUNC_RE` 的 `"postgres"` 键之后加：

```python
    # ClickHouse 表函数族（二十三期 spec D4）：url/s3/azure/hdfs/jdbc/odbc/file
    # 拉取任意外部数据（数据外泄面）；mysql/postgresql/mongodb/remote/
    # remoteSecure 跨库桥接；cluster/clusterAllReplicas 跨节点放大；
    # input/generateRandom 非受控输入；sleep/sleepEachRow DoS
    "clickhouse": re.compile(
        r"\b(?:"
        r"url|urlCluster"
        r"|file|fileCluster"
        r"|s3|s3Cluster"
        r"|azure|azureCluster"
        r"|hdfs|hdfsCluster"
        r"|jdbc|odbc"
        r"|mysql|postgresql|mongodb"
        r"|remote|remoteSecure"
        r"|cluster|clusterAllReplicas"
        r"|input|generateRandom"
        r"|sleep|sleepEachRow"
        r")\s*\(",
        re.IGNORECASE,
    ),
```

- [x] **Step 4: 跑测试确认通过**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/exec -q`
Expected: 全 PASS（既有护栏用例零变化）

- [x] **Step 5: 质量门 + Commit**

```bash
git add exec/guards.py tests/exec/test_guards_clickhouse.py
git commit -m "feat: 护栏方言增量黑名单 clickhouse 键（SYSTEM/SETTINGS/外部源表函数族）（二十三期 T4）"
```

---

### Task 5: 探索层方言集合扩展（内联派生表路线纳入 clickhouse）

**Files:**
- Modify: `core/retrieval/exploration.py:79`（`if dialect == "mysql":` → 集合成员判断）
- Test: `tests/core/test_exploration.py`（追加 clickhouse 用例，仿既有 mysql 用例 157-181 的 monkeypatch 模式）

**Interfaces:**
- Consumes: `get_renderer().name == "clickhouse"`（Task 2）；`build_secure_projection`（security/views.py，引擎无关）。
- Produces: clickhouse 方言下探索 SQL 的表引用重写为内联派生表（与 MySQL 同一分支体，spec D8）。

- [x] **Step 1: 写失败测试**

在 `tests/core/test_exploration.py` 的 `test_rewrite_sql_to_views_mysql_uses_derived_table` 之后追加（monkeypatch 模式同源，sqlglot read=clickhouse 解析锁定）：

```python
def test_rewrite_sql_to_views_clickhouse_uses_derived_table(monkeypatch):
    """ClickHouse 方言：表引用重写为内联派生表（复用 MySQL 路线，spec D8）。"""
    import config.settings as settings

    monkeypatch.setattr(settings, "SQL_DIALECT", "clickhouse")
    monkeypatch.setattr(settings, "DB_BACKEND", "clickhouse")
    rewritten, rejects = rewrite_sql_to_views(
        "SELECT order_no FROM order_detail", "analyst"
    )
    assert rejects == []
    # 内联派生表：基表名不再出现，sec_* 别名以子查询形态出现
    assert "FROM order_detail" not in rewritten
    assert "sec_order_detail" in rewritten
    import sqlglot

    sqlglot.parse_one(rewritten, read="clickhouse")  # 产物必须是合法 CH SQL
```

（`rewrite_sql_to_views` / `"analyst"` 主体名以该测试文件既有 mysql 用例的实际导入与主体为准——同文件内复制其导入区与主体参数，仅改方言两处 monkeypatch 值与断言中 `read="clickhouse"`。）

- [x] **Step 2: 跑测试确认失败**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/core/test_exploration.py -q -k clickhouse`
Expected: FAIL（clickhouse 方言当前走视图名引用分支，`FROM order_detail` 未被内联替换）

- [x] **Step 3: 实现（exploration.py:79）**

```python
        if dialect in ("mysql", "clickhouse"):
```

并把该分支上方注释更新为：`# MySQL 无 TEMP VIEW、ClickHouse readonly=1 下无 DDL 权限（均无临时视图能力）：基表替换为内联派生表（spec D8/D9）——投影/RLS/禁列物理缺席与视图版单一实现，无 DDL 权限要求`。

- [x] **Step 4: 跑测试确认通过**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/core -q`
Expected: 全 PASS（DuckDB/MySQL 两既有路径行为不变）

- [x] **Step 5: 质量门 + Commit**

```bash
git add core/retrieval/exploration.py tests/core/test_exploration.py
git commit -m "feat: 探索层内联派生表路线纳入 clickhouse 方言（二十三期 T5）"
```

---

### Task 6: 建仓链路（schema_clickhouse 转译 + build 分支 + init_clickhouse）

**Files:**
- Create: `mock/gmall/schema_clickhouse.py`
- Modify: `mock/gmall/build.py:345-352`（建表分支）与 `mock/gmall/build.py:70-87`（`_insert_seed` 改道）
- Create: `mock/init_clickhouse.py`
- Test: `tests/mock/test_schema_clickhouse.py`

**Interfaces:**
- Consumes: `DDL_STATEMENTS`（mock/gmall/schema.py 单一来源）；`ClickHouseAdapter.insert_dataframe`（Task 3）；`build_gmall_tables(conn, seed, adapter=...)`（既有）。
- Produces: `clickhouse_ddl() -> list[str]`、`clickhouse_table_names() -> list[str]`、`create_clickhouse_tables(conn)`；`python -m mock.init_clickhouse` 入口（Task 7 parity 前置、Task 9 CI 消费）。

- [x] **Step 1: 转译形态探针（确定性后处理的输入事实）**

```bash
cat > /tmp/ch_ddl_probe.py <<'EOF'
import sqlglot
from mock.gmall.schema import DDL_STATEMENTS
out = sqlglot.transpile(DDL_STATEMENTS[0], read="duckdb", write="clickhouse")
print(out[0])
EOF
conda run -n dataagent --no-capture-output python /tmp/ch_ddl_probe.py
```

记录首表（base_province）产物的类型 token 形态（如 `Int32` / `String` / `DateTime` / `Decimal(38, 9)`）与是否携带 `PRIMARY KEY` 列约束——Step 2 的类型正则与 PRIMARY KEY 剥离以此为输入事实；若产物形态与本计划假设不符（如类型名不同），以实测形态修正 `_TYPE_RE` 枚举后继续。

- [x] **Step 2: 写失败测试（tests/mock/test_schema_clickhouse.py）**

```python
"""ClickHouse 建仓 DDL 对拍（二十三期 spec D9）：转译 + 后处理确定性锁定。

schema.py 是表/列单一来源：本文件锁定 转译+Nullable 包裹+MergeTree 后处理 的
确定性产物（首表全文快照 + 全表清单/引擎/排序键性质断言），严禁手写第二份表结构。
"""

from __future__ import annotations

import re

import pytest

from mock.gmall.schema import DDL_STATEMENTS
from mock.gmall.schema_clickhouse import (
    _ORDER_BY_KEYS,
    clickhouse_ddl,
    clickhouse_table_names,
)


def test_table_names_match_source_order():
    assert clickhouse_table_names() == [
        re.search(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", d, re.IGNORECASE).group(1)
        for d in DDL_STATEMENTS
    ]


def test_every_table_has_merge_tree_engine():
    for ddl in clickhouse_ddl():
        assert "ENGINE = MergeTree" in ddl, ddl[:80]


def test_time_axis_tables_have_order_by_key():
    for table, col in _ORDER_BY_KEYS.items():
        ddl = next(d for d in clickhouse_ddl() if f"`{table}`" in d)
        assert f"ORDER BY (`{col}`)" in ddl


def test_non_axis_tables_order_by_tuple():
    names = set(clickhouse_table_names()) - set(_ORDER_BY_KEYS)
    assert names, "应存在非时间主轴表"
    for ddl in clickhouse_ddl():
        first = re.search(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", ddl, re.IGNORECASE).group(1)
        if first not in _ORDER_BY_KEYS:
            assert "ORDER BY tuple()" in ddl


def test_all_column_types_wrapped_nullable():
    """全列 Nullable（spec D9）：DateTime/String/Decimal 等类型 token 均被包裹。"""
    for ddl in clickhouse_ddl():
        body = ddl.split("ENGINE")[0]
        # 裸类型 token（未被 Nullable 包裹）必须不存在
        assert not re.search(r"(?<!Nullable\()\b(DateTime|String|Float64|Int32|Int64)\b(?!\))", body.replace("Nullable(", "")), ddl[:120]


def test_primary_key_constraint_stripped():
    """源 DDL 的 PRIMARY KEY 列约束剥离（CH 主键语义由 ORDER BY 承载，spec D9）。"""
    for ddl in clickhouse_ddl():
        assert "PRIMARY KEY" not in ddl


def test_first_table_snapshot():
    """首表全文快照（确定性锚；转译器升级时此测试显式暴露漂移）。"""
    assert clickhouse_ddl()[0] == (
        "CREATE TABLE `base_province` (`province_id` Nullable(Int32), "
        "`province_name` Nullable(String), `region_id` Nullable(Int32), "
        "`area_code` Nullable(String), `iso_code` Nullable(String), "
        "`iso_3166_2` Nullable(String), `create_time` Nullable(DateTime), "
        "`operate_time` Nullable(DateTime)) "
        "ENGINE = MergeTree ORDER BY tuple()"
    )


@pytest.mark.parametrize("module_name", ["mock.init_clickhouse"])
def test_init_module_importable_without_side_effects(module_name):
    import importlib

    importlib.import_module(module_name)  # 无副作用（main 仅 __main__ 执行）
```

（首表快照的期望文本以 Step 1 探针实测产物 + Step 3 后处理规则为准——先按本文本写入跑红，再按**实际确定性输出**修正快照常量后跑绿，并在提交信息注明"快照锁定"。）

- [x] **Step 3: 实现 `mock/gmall/schema_clickhouse.py`**

```python
"""ClickHouse 版 Gmall 建仓 DDL（二十三期 spec D9）：DuckDB DDL 运行时转译。

转换由 sqlglot（read=duckdb, write=clickhouse）承担 + 三步确定性后处理：
1. 剥离 PRIMARY KEY 列约束——CH 主键语义由 MergeTree ORDER BY 承载（二者
   不一致会报 primary key 前缀错误）；唯一性由确定性生成器保证；
2. 列类型统一包 Nullable(...)（对齐 DuckDB/MySQL/PG 全列可空语义；埋点解析
   链路存在 NULL datetime 值——MySQL CI 实锤先例；本数仓规模开销可忽略）；
3. 追加 ENGINE = MergeTree 子句（sqlglot 不覆盖表引擎）：时间主轴表按
   _ORDER_BY_KEYS 设 ORDER BY (主时间列)——CH 主键序存储 + 稀疏索引对齐
   MySQL 二级索引动机（spec D9）；其余表 ORDER BY tuple()。

表/列与 schema.py 单一来源对齐（tests/mock/test_schema_clickhouse.py 对拍锁定），
严禁在本文件手写第二份表结构（漂移风险）。CH 无 CBO 统计概念，建仓无 ANALYZE
步骤（与 init_postgres 差异点，spec D9）。
"""

from __future__ import annotations

import re

import sqlglot

from mock.gmall.schema import DDL_STATEMENTS

# 表名正则提取（与 schema_mysql 同源口径：正则绝对稳定）
_TABLE_NAME_RE = re.compile(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", re.IGNORECASE)

# 时间主轴表 -> ORDER BY 主时间列（与 MySQL 二级索引同一张表清单，spec D9）
_ORDER_BY_KEYS: dict[str, str] = {
    "order_detail": "order_time",
    "order_info": "create_time",
    "payment_info": "create_time",
    "order_refund_info": "create_time",
    "user_info": "create_time",
    "fact_start": "ts",
    "fact_page_view": "ts",
    "fact_action": "ts",
    "fact_display": "ts",
}

# 转译产物类型 token 全集（探针实测为准；Date 在 DateTime 之前但 \b 保证
# "DateTime" 不被 "Date" 前缀截断——边界回溯由 \\b 承载）
_TYPE_RE = re.compile(
    r"\b(String|DateTime|Date|Float64|Float32|Int8|Int16|Int32|Int64"
    r"|UInt8|UInt16|UInt32|UInt64|Decimal\(\d+,\s*\d+\))\b"
)


def _wrap_nullable(ddl: str) -> str:
    return _TYPE_RE.sub(lambda m: f"Nullable({m.group(1)})", ddl)


def _post_process(ddl: str) -> str:
    name = _TABLE_NAME_RE.search(ddl).group(1)
    body = ddl.strip().rstrip(";")
    body = body.replace("PRIMARY KEY", "")  # 主键语义由 ORDER BY 承载（spec D9）
    body = re.sub(r"\s{2,}", " ", body)  # 剥离约束后的连续空格归一
    body = _wrap_nullable(body)
    key = _ORDER_BY_KEYS.get(name)
    order = f"ORDER BY (`{key}`)" if key else "ORDER BY tuple()"
    return f"{body} ENGINE = MergeTree {order}"


def clickhouse_ddl() -> list[str]:
    """DuckDB DDL -> ClickHouse DDL（sqlglot 转译 + 确定性后处理，幂等）。"""
    return [
        _post_process(out)
        for ddl in DDL_STATEMENTS
        for out in sqlglot.transpile(ddl, read="duckdb", write="clickhouse")
    ]


def clickhouse_table_names() -> list[str]:
    """源 DDL 的表名清单（按声明序；供 DROP 重建与对拍，正则提取版本无关）。"""
    names: list[str] = []
    for ddl in DDL_STATEMENTS:
        m = _TABLE_NAME_RE.search(ddl)
        if m is None:
            raise RuntimeError(f"DDL 表名提取失败: {ddl.strip()[:80]!r}")
        names.append(m.group(1))
    return names


def create_clickhouse_tables(conn) -> None:
    """在给定 ClickHouse client 上按序执行全部 DDL（假定空库）。"""
    for ddl in clickhouse_ddl():
        conn.command(ddl)


__all__ = ["create_clickhouse_tables", "clickhouse_ddl", "clickhouse_table_names"]
```

- [x] **Step 4: build.py 两处分支**

`build_gmall_tables` 建表分支（:345-352）在 `elif adapter.name == "postgres":` 块之后插入：

```python
    elif adapter.name == "clickhouse":
        from mock.gmall.schema_clickhouse import create_clickhouse_tables

        create_clickhouse_tables(conn)
```

`_insert_seed`（:70-87）改为 clickhouse 先行分支（无 cursor 协议，spec D10）：

```python
def _insert_seed(conn: Any, seed_rows: dict[str, list[list]], adapter) -> None:
    # 占位符与执行通道随引擎：duckdb `?` + conn.executemany；pymysql/psycopg
    # `%s` + cursor.executemany；ClickHouse 无 cursor 协议（驱动实测）——
    # 种子行走具名 DataFrame + insert_df Arrow 通道（spec D10，列序 = 物理表列序）
    if adapter.name == "clickhouse":
        import pandas as pd

        for table, rows in seed_rows.items():
            cols = SEED_COLUMNS[table]
            converted = [
                tuple(_convert_seed_value(c, v) for c, v in zip(cols, row, strict=False))
                for row in rows
            ]
            adapter.insert_dataframe(conn, table, pd.DataFrame(converted, columns=cols))
        return
    ph = "?" if adapter.name == "duckdb" else "%s"
    # ……（后续既有代码不变）
```

- [x] **Step 5: 跑测试确认通过**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/mock -q`
Expected: 全 PASS（既有 mysql/postgres schema 用例零变化；首表快照按实测锁定后绿）

- [x] **Step 6: 实现 `mock/init_clickhouse.py`**

```python
"""ClickHouse 版 mock 数仓初始化（二十三期 spec §6/D10）。

用法（在项目根目录执行）：
    python -m mock.init_clickhouse

前置条件：
- .env 配置 CLICKHOUSE_* 指向目标实例且**库已存在**（CREATE DATABASE 属部署
  动作；建仓账号需 CREATE/INSERT/DROP 权限，运行时查询另用 readonly profile
  账号，两账号分离见 spec §9）；
- 数仓 DDL 由 schema_clickhouse 运行时转译（表/列与 DuckDB 版单一来源对齐）。

效果：幂等重建（DROP 全部既有表 -> 建表 -> 同一 build_gmall_tables 灌数（种子
42、AS_OF_DATE=2025-12-31，与 DuckDB 版逐行同源）-> 元数据表 -> 打印行数）。
CH 无 CBO 统计概念，**无 ANALYZE 步骤**（与 init_postgres 差异点，spec D9）。
"""

from __future__ import annotations

import pandas as pd
import clickhouse_connect

from config import settings
from mock.gmall.build import build_gmall_tables
from mock.gmall.schema_clickhouse import clickhouse_table_names
from mock.metadata import FIELD_METADATA, TABLE_METADATA

SEED = 42  # 与 mock.init_duckdb 同源：保证评测可复现


def _writable_client():
    # 建仓需要 CREATE/INSERT/DROP：直连驱动（不经 adapter.connect_readonly——
    # 建仓账号非 readonly profile，验证式加固会正确拒绝）
    return clickhouse_connect.get_client(
        host=settings.CLICKHOUSE_HOST,
        port=settings.CLICKHOUSE_PORT,
        username=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD,
        database=settings.CLICKHOUSE_DATABASE,
        connect_timeout=5,
    )


def _write_metadata(client) -> None:
    client.command(
        "CREATE TABLE _field_metadata "
        "(table_name String, column_name String, comment String) "
        "ENGINE = MergeTree ORDER BY tuple()"
    )
    client.insert_df(
        "_field_metadata",
        pd.DataFrame(
            [(t, c, k) for t, cols in FIELD_METADATA.items() for c, k in cols.items()],
            columns=["table_name", "column_name", "comment"],
        ),
    )
    client.command(
        "CREATE TABLE _table_metadata (table_name String, comment String) "
        "ENGINE = MergeTree ORDER BY tuple()"
    )
    client.insert_df(
        "_table_metadata",
        pd.DataFrame(list(TABLE_METADATA.items()), columns=["table_name", "comment"]),
    )


def main() -> None:
    """幂等重建 ClickHouse 版 Gmall 数仓（结构转译 + 同源数据 + 行数对照打印）。"""
    client = _writable_client()
    try:
        # 幂等清场（含元数据表；建表全权交 create_clickhouse_tables，严禁在此
        # 再显式 create——与 init_mysql 双重建表教训同源）
        for table in (*clickhouse_table_names(), "_field_metadata", "_table_metadata"):
            client.command(f"DROP TABLE IF EXISTS `{table}`")
        # write_applog 默认 True：行为事实表由埋点日志解析入仓（与 DuckDB 版
        # 同链路；日志生成确定性，重复落盘幂等）
        counts = build_gmall_tables(client, seed=SEED)
        _write_metadata(client)
        print("[init_clickhouse] 表创建完成:")
        for t, n in counts.items():
            print(f"  - {t}: {n} 行")
        print(
            f"[init_clickhouse] 目标库: {settings.CLICKHOUSE_DATABASE}@"
            f"{settings.CLICKHOUSE_HOST}:{settings.CLICKHOUSE_PORT}"
        )
        print("[init_clickhouse] 行数应与 python -m mock.init_duckdb 输出一致（同源生成器）")
    finally:
        client.close()


if __name__ == "__main__":
    main()
```

（`build_gmall_tables(client, seed=SEED)` 不传 adapter——缺省按 `settings.DB_BACKEND` 现取；执行时以 `DB_BACKEND=clickhouse conda run -n dataagent python -m mock.init_clickhouse` 调用，或在 CI env 中显式 `DB_BACKEND: clickhouse`。）

- [x] **Step 7: 质量门 + Commit**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/mock tests/engine -q && conda run -n dataagent --no-capture-output black --check . && conda run -n dataagent --no-capture-output ruff check .`
Expected: 全绿

```bash
git add mock/gmall/schema_clickhouse.py mock/gmall/build.py mock/init_clickhouse.py tests/mock/test_schema_clickhouse.py
git commit -m "feat: ClickHouse 建仓链路（DDL 转译+MergeTree/Nullable 后处理+种子改道 insert_df）+ init 入口（二十三期 T6）"
```

---

### Task 7: ClickHouse 双后端一致性测试（parity）

**Files:**
- Create: `tests/test_clickhouse_backend_parity.py`

**Interfaces:**
- Consumes: `tests/parity_utils.py` 共享工具（比对口径勿改）；`ClickHouseAdapter`/`get_adapter`（Task 3）；编译器（Task 2）；建仓（Task 6）。
- Produces: 本地 skip 生效、CI 真实执行的回归面（Task 9 CI job 调用）。

- [x] **Step 1: 实现测试文件**

```python
"""ClickHouse 双后端一致性测试（二十三期 spec §6）：同一 DSL 两后端编译+执行，结果必须一致。

运行条件：设置 CLICKHOUSE_TEST_HOST/PORT/USER/PASSWORD/DATABASE 指向已执行
``python -m mock.init_clickhouse`` 的测试库；**测试账号必须为 readonly profile**
（readonly=1 + max_rows_to_read=MAX_SCAN_ROWS + max_execution_time=
QUERY_TIMEOUT_MS/1000，与 users.d 模板一致——harden 验证式校验 fail-closed）；
未设置 CLICKHOUSE_TEST_HOST 时整文件跳过（本地开发不跑 CH，parity 由 CI
clickhouse-parity job 承担——与二十二期模式一致）。

比对口径与 test_dual_backend_parity.py 同源（tests/parity_utils.py，勿改）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.parity_utils import abs_window_dsl as _abs_window_dsl
from tests.parity_utils import assert_rows_equal as _assert_rows_equal
from tests.parity_utils import compile_dsl as _compile_dsl

pytestmark = pytest.mark.skipif(
    not os.getenv("CLICKHOUSE_TEST_HOST"),
    reason="需要 ClickHouse 测试库（CLICKHOUSE_TEST_* 环境变量，readonly profile 账号）",
)


def _test_dsn() -> dict:
    return {
        "host": os.environ["CLICKHOUSE_TEST_HOST"],
        "port": int(os.getenv("CLICKHOUSE_TEST_PORT", "8123")),
        "user": os.getenv("CLICKHOUSE_TEST_USER", "dataagent_ro"),
        "password": os.getenv("CLICKHOUSE_TEST_PASSWORD", ""),
        "database": os.getenv("CLICKHOUSE_TEST_DATABASE", "gmall"),
    }


@pytest.fixture(scope="module")
def ch_conn():
    from exec.engine.clickhouse_adapter import ClickHouseAdapter

    conn = ClickHouseAdapter(_test_dsn()).connect_readonly()
    yield conn
    conn.close()


def _run_dsl(dsl, duck_conn, ch_conn):
    """同一 DSL 双后端编译执行（走 execute_sql 全护栏链路）。"""
    from exec.engine import get_adapter
    from exec.engine.clickhouse_adapter import ClickHouseAdapter
    from exec.guards import execute_sql

    return {
        "duckdb": execute_sql(
            duck_conn,
            _compile_dsl(dsl, "duckdb"),
            adapter=get_adapter("duckdb"),
            max_result_rows=20000,
        ),
        "clickhouse": execute_sql(
            ch_conn,
            _compile_dsl(dsl, "clickhouse"),
            adapter=ClickHouseAdapter(_test_dsn()),
            max_result_rows=20000,
        ),
    }


def test_golden_dsls_parity(conn, ch_conn):
    """golden_dataset 全量 DSL 双后端一致（主断言）。"""
    golden = json.loads(
        (Path(__file__).parents[1] / "eval" / "golden_dataset.json").read_text(encoding="utf-8")
    )
    dsls = [item["dsl"] for item in golden if "dsl" in item] + [
        turn["dsl"] for item in golden for turn in item.get("turns", []) if "dsl" in turn
    ]
    assert dsls, "golden_dataset 未解析出任何 DSL"
    for dsl in dsls:
        results = _run_dsl(dsl, conn, ch_conn)
        _assert_rows_equal(results["duckdb"].rows, results["clickhouse"].rows)


def test_week_boundary_parity(conn, ch_conn):
    """跨周边界分桶一致（toMonday 周一锚定 vs DuckDB date_trunc('week')）。"""
    results = _run_dsl(_abs_window_dsl("week", "2021-01-01", "2021-01-15"), conn, ch_conn)
    rows = results["duckdb"].rows
    assert rows, "DuckDB 侧应产出两周分桶"
    _assert_rows_equal(rows, results["clickhouse"].rows)


def test_fill_gaps_five_years_parity(conn, ch_conn):
    """day 粒度 5 年窗口（1826 行）——numbers(N) spine 实测口径（spec D7）。"""
    results = _run_dsl(
        _abs_window_dsl("day", "2021-01-01", "2026-01-01", fill_gaps=True), conn, ch_conn
    )
    assert len(results["clickhouse"].rows) == 1826
    assert len(results["duckdb"].rows) == 1826
    _assert_rows_equal(results["duckdb"].rows, results["clickhouse"].rows)


def test_comparison_time_dim_parity(conn, ch_conn):
    """comparison 按位配对（CH 透传 INTERVAL 1 YEAR 加法形态）双后端一致。"""
    dsl = _abs_window_dsl("day", "2025-06-01", "2025-07-01")
    dsl["time_filter"]["comparison"] = "yoy"
    results = _run_dsl(dsl, conn, ch_conn)
    _assert_rows_equal(results["duckdb"].rows, results["clickhouse"].rows)


def test_scan_limit_circuit_breaker(ch_conn):
    """profile max_rows_to_read 服务端硬熔断（spec D3）：numbers(20M) 超预算报错。

    CH 口径为多表读取总和：numbers(20000000) 读 2000 万行 > MAX_SCAN_ROWS（1000 万）
    → 服务端错误码 131 → 文本归一为 SqlExecutionError（含熔断文案）。
    """
    from exec.engine.clickhouse_adapter import ClickHouseAdapter
    from exec.errors import SqlExecutionError
    from exec.guards import execute_sql

    with pytest.raises(SqlExecutionError, match="max_rows_to_read"):
        execute_sql(
            ch_conn,
            "SELECT count() FROM numbers(20000000)",
            adapter=ClickHouseAdapter(_test_dsn()),
            max_result_rows=1,
        )


def test_pool_reuse_session_still_readonly():
    """连接池归还复用后验证式加固不失效（system.settings 只读值仍为 readonly>=1）。"""
    from exec.engine.clickhouse_adapter import ClickHouseAdapter
    from exec.pool import ReadOnlyConnectionPool

    adapter = ClickHouseAdapter(_test_dsn())
    pool = ReadOnlyConnectionPool(adapter, max_connections=1)
    conn = pool.acquire()
    conn2 = None
    try:
        pool.release(conn)
        conn2 = pool.acquire()  # 复用同一连接
        result = conn2.query("SELECT value FROM system.settings WHERE name = 'readonly'")
        assert int(result.result_rows[0][0]) >= 1
    finally:
        if conn2 is not None:
            pool.release(conn2)
        pool.close()
```

- [x] **Step 2: 本地收集 + skip 验证**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/test_clickhouse_backend_parity.py -q`
Expected: `6 skipped`（CLICKHOUSE_TEST_HOST 未配置；收集无错误）

- [x] **Step 3: 全量回归（确认零破坏）**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/engine tests/compiler tests/exec tests/core tests/mock -q`
Expected: 全 PASS

- [x] **Step 4: Commit**

```bash
git add tests/test_clickhouse_backend_parity.py
git commit -m "test: ClickHouse 双后端一致性测试（golden 全量 + 熔断/周边界/补零/池复用专项）（二十三期 T7）"
```

---

### Task 8: eval --backend clickhouse 连通性冒烟

**Files:**
- Modify: `eval/eval_runner.py:463-475`（choices 与 `_clickhouse_smoke`）

**Interfaces:**
- Consumes: `ClickHouseAdapter` + `execute_sql`（Task 3）。
- Produces: `python -m eval.eval_runner --backend clickhouse` 冒烟入口（golden 锚定 DuckDB 不变，spec §1 目标 7）。

- [x] **Step 1: 实现（镜像 `_postgres_smoke`）**

在 `_postgres_smoke` 之后追加：

```python
def _clickhouse_smoke() -> int:
    """ClickHouse 后端连通性冒烟（二十三期 spec §1 目标 7）：加固连接 + 白名单 + 三护栏。

    golden 评测的期望 SQL 与快照锚定 DuckDB 方言，ClickHouse 后端不做全量
    比对（诚实跳过而非硬跑方言错配的 golden）；CH 正确性回归由
    tests/test_clickhouse_backend_parity.py（双后端一致性）承担。
    """
    from exec.engine.clickhouse_adapter import ClickHouseAdapter
    from exec.guards import execute_sql

    adapter = ClickHouseAdapter()
    conn = adapter.connect_readonly()
    try:
        result = execute_sql(
            conn,
            "SELECT COUNT(*) AS n FROM order_detail",
            statement_timeout_ms=settings.QUERY_TIMEOUT_MS,
            max_result_rows=1,
            adapter=adapter,
        )
    finally:
        conn.close()
    print(
        f"[eval][clickhouse-smoke] order_detail 行数 = {result.rows[0][0]}"
        "（连接/加固/白名单/护栏链路通）"
    )
    print(
        "[eval][clickhouse-smoke] golden 锚定 DuckDB 方言，CH 正确性回归见 tests/test_clickhouse_backend_parity.py"
    )
    return 0
```

`--backend` 的 `choices` 改为 `["duckdb", "mysql", "postgres", "clickhouse"]`、help 改为 `"查询执行后端：duckdb（默认）；mysql/postgres/clickhouse 仅做连通性冒烟（golden 锚定 DuckDB 方言）"`；`main()` 在 `if args.backend == "postgres":` 分支后加：

```python
    if args.backend == "clickhouse":
        return _clickhouse_smoke()
```

- [x] **Step 2: 验证（oracle 契约回归不破）**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/eval -q && conda run -n dataagent --no-capture-output python -m eval.eval_runner --skip-snapshot`
Expected: 全 PASS；oracle 31/31 契约绿（未配置 CH 时 `--backend clickhouse` 仅在 CI 验证）

- [x] **Step 3: Commit**

```bash
git add eval/eval_runner.py
git commit -m "feat: eval --backend clickhouse 连通性冒烟接线（二十三期 T8）"
```

---

### Task 9: CI clickhouse-parity 门禁（users.d 入库）+ AGENTS.md 契约小节

**Files:**
- Create: `.github/clickhouse/users.d/dataagent_ro.xml`
- Modify: `.github/workflows/ci.yml`（postgres-parity job 之后追加）
- Modify: `AGENTS.md`（MySQL/PG 契约小节之后）

**Interfaces:**
- Consumes: Task 6 `python -m mock.init_clickhouse`、Task 7 parity 文件。
- Produces: CI 真实执行面（CH 链路首次真实验证）。

- [x] **Step 1: users.d 只读账号配置入库**

`.github/clickhouse/users.d/dataagent_ro.xml`（CI service 容器以 volume 挂载；密码为 CI 演示口径，与 mysql root/root 先例同级别，禁止复用到生产）：

```xml
<!-- ClickHouse 运行时只读账号（二十三期 spec D4/D12）：readonly=1 由 profile
     承载（readonly=1 会话内无法 SET，客户端不可篡改）；max_rows_to_read /
     max_execution_time 须与 config/settings.py 的 MAX_SCAN_ROWS /
     QUERY_TIMEOUT_MS 保持一致（adapter.harden 验证式校验 fail-closed）。
     改预算必须两侧同步。 -->
<clickhouse>
    <profiles>
        <ch_ro_profile>
            <readonly>1</readonly>
            <max_rows_to_read>10000000</max_rows_to_read>
            <max_execution_time>30</max_execution_time>
        </ch_ro_profile>
    </profiles>
    <users>
        <dataagent_ro>
            <password>ch_ro_pwd</password>
            <networks>
                <ip>::/0</ip>
            </networks>
            <profile>ch_ro_profile</profile>
            <grants>
                <query>GRANT SELECT ON gmall.*</query>
            </grants>
        </dataagent_ro>
    </users>
</clickhouse>
```

- [x] **Step 2: CI job（postgres-parity 之后追加）**

```yaml
  # ClickHouse 服务级门禁（二十三期）：与 postgres-parity 同构——golden 全量 DSL
  # 双后端一致性 + 熔断/周边界/补零/池复用专项。readonly 预算由仓库内 users.d
  # 配置承载（CH readonly 由 profile 承载、无法会话级 SET，spec D12）；TZ=UTC
  # 时区确定性（spec D13）。本地不跑 CH（CLICKHOUSE_TEST_* 未配置自动跳过）。
  clickhouse-parity:
    runs-on: ubuntu-latest
    services:
      clickhouse:
        image: clickhouse/clickhouse-server:24.8
        env:
          TZ: UTC
        ports:
          - 8123:8123
        options: >-
          --health-cmd="clickhouse-client --query 'SELECT 1'"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=12
          --volume ${{ github.workspace }}/.github/clickhouse/users.d:/etc/clickhouse-server/users.d
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dev dependencies
        run: pip install -r requirements-dev.txt
      - name: Build ClickHouse warehouse (DDL transpiled + same-source data)
        run: python -m mock.init_clickhouse
        env:
          DB_BACKEND: clickhouse
          CLICKHOUSE_HOST: 127.0.0.1
          CLICKHOUSE_PORT: "8123"
          CLICKHOUSE_USER: default
          CLICKHOUSE_PASSWORD: ""
          CLICKHOUSE_DATABASE: gmall
      - name: Dual-backend parity tests (golden DSLs + Review Focus)
        run: python -m pytest tests/test_clickhouse_backend_parity.py -q
        env:
          DB_BACKEND: duckdb
          CLICKHOUSE_TEST_HOST: 127.0.0.1
          CLICKHOUSE_TEST_PORT: "8123"
          CLICKHOUSE_TEST_USER: dataagent_ro
          CLICKHOUSE_TEST_PASSWORD: ch_ro_pwd
          CLICKHOUSE_TEST_DATABASE: gmall
```

- [x] **Step 3: AGENTS.md 契约小节（MySQL/PG 小节之后）**

```markdown
- ClickHouse 执行后端（二十三期，2026-10）：`DB_BACKEND` 四值
  （duckdb|mysql|postgres|clickhouse，默认 duckdb 零破坏）；驱动
  clickhouse-connect（HTTP，无 cancel 方法——超时取消靠服务端
  max_execution_time）；超时/扫描预算由**账号 profile 承载**（readonly=1 +
  max_execution_time + max_rows_to_read），harden 为**验证式加固**（查
  system.settings 与应用预算 fail-closed 比对）；驱动异常无结构化错误码
  （文本 "code: N" 正则归一：159 超时 / 131 扫描超限）；扫描预检诚实不可计量
  （无 EXPLAIN ANALYZE，warning 留痕）；spine 用 numbers(N) +
  addDays/addMonths/addYears（N 纯 Python 桶数计算，周步长归一 7 天）；探索层
  走内联派生表（无 TEMP VIEW 且 readonly=1 无 DDL 权限）；方言增量黑名单
  SYSTEM/KILL/TRUNCATE/OPTIMIZE/SETTINGS/OUTFILE + 外部源表函数族；建仓
  MergeTree ORDER BY (时间列) + 全列 Nullable、无 ANALYZE 步骤；兼容基线
  >= 23.8、CI 锚定 24.8；golden 评测锚定 DuckDB 方言不随引擎切换。
```

- [x] **Step 4: 验证（workflow 语法与本地 skip）**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/test_clickhouse_backend_parity.py -q`
Expected: 6 skipped（本地不受 CI 变更影响）

- [x] **Step 5: Commit**

```bash
git add .github/clickhouse/users.d/dataagent_ro.xml .github/workflows/ci.yml AGENTS.md
git commit -m "feat: CI clickhouse-parity 门禁（users.d 只读 profile 入库）+ AGENTS.md 契约小节（二十三期 T9）"
```

---

### Task 10: 全量收尾（质量门 + Review Focus 核对 + tasks 状态收口）

**Files:**
- Modify: `docs/tasks/2026-10-26-clickhouse-backend-tasks.md`（状态/执行日志/核对表收口）

- [x] **Step 1: 全量质量门**

Run: `conda run -n dataagent --no-capture-output black --check . && conda run -n dataagent --no-capture-output ruff check . && conda run -n dataagent --no-capture-output python -m pytest -q`
Expected: 全绿（CH parity 6 skipped；1163+ 全过）

- [x] **Step 2: Review Focus 五条逐项核对**（结果登记 tasks 文档核对表）

- [x] **Step 3: tasks 文档收口**（任务总览状态、服务级验证清单勾选 CI 观察项留待推送后）

- [ ] **Step 4: Commit + 推送**

```bash
git add docs/tasks/2026-10-26-clickhouse-backend-tasks.md
git commit -m "docs: 二十三期任务记录收口（Review Focus 核对与执行日志）"
git push -u origin feat/clickhouse-hive-backend
```
