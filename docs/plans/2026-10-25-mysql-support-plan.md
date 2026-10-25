# MySQL 支持实施计划（二十一期）

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。
> 设计依据：`docs/plans/2026-10-25-mysql-support-spec.md`（下称 spec），
> 执行者需同时读两者。任务状态汇总见 `2026-10-25-mysql-support-tasks.md`。

**Goal:** 新增 MySQL 8.0 作为可选查询执行后端（`DB_BACKEND=duckdb|mysql`），治理管道全量等价生效。

**Architecture:** 执行引擎抽象层（`exec/engine/` Adapter 协议，DuckDB 现有逻辑下沉、MySQL 用 PyMySQL 新实现）+ 编译方言层（`compiler/dialect/` Renderer 协议，SQL 结构组装引擎无关）。消费方（web/tools/retrieval/exploration）零改动。

**Tech Stack:** PyMySQL >= 1.1.1（MIT，纯 Python）；sqlglot（既有，mysql 方言内置）。

**Spec:** `docs/plans/2026-10-25-mysql-support-spec.md`

## Global Constraints

- DuckDB 后端**零行为变更**：编译产物逐字节一致、全部既有测试绿（重构任务 1/2/7 完成后 `python -m pytest -q` 与 `python -m eval.eval_runner` 均不得出现任何新失败）。
- 铁律不破：LLM 产出的 SQL 永不原文执行；一切执行只发生在治理管道内（白名单/审计门/三护栏/探索层重写）。
- 不改 `eval/golden_dataset.json`、`eval/intent_golden.json`、mock 生成器（`mock/gmall/simulate.py`、`business.py`、`logs.py`、`names.py`）。
- 依赖只新增 `pymysql`；不引入 SQLAlchemy / mysql-connector。
- 新增/修改的 docstring 与行内注释用简体中文；Git 提交信息 feat/fix/refactor 等类型词英文 + 中文描述。
- 每个任务提交前 `black --check .`、`ruff check .`、`python -m pytest -q` 三件套全绿。
- 不依赖 MySQL 服务的单测必须在无 MySQL 的机器上照常通过；服务级验证集中在 Task 10（`MYSQL_TEST_HOST` 未设时 skipif 跳过）。

## Review Focus（规格隐含、无明确任务测试覆盖的失效模式，已逐一挂靠）

1. **MySQL 反斜杠注入面**（spec D8）：恶意字符串含 `\'` 使字面量逃逸——挂 Task 6（转义翻倍单测）+ Task 3（harden 含 NO_BACKSLASH_ESCAPES 断言）。
2. **周一口径跨周边界**（spec D5）：周日/周一日期分桶漂移——挂 Task 10 一致性测试专门用例。
3. **递归 CTE 深度打穿**（spec D6）：day 粒度 5 年窗口 > 默认 1000 深度——挂 Task 10 fill_gaps 长窗口用例 + Task 3 cte_max_recursion_depth 加固断言。
4. **Decimal/float 类型漂移**（spec D13）：金额字段两后端数值不一致——挂 Task 3 normalize_cell 单测 + Task 10 相对误差 1e-6 断言。
5. **连接池复用后会话变量污染**（spec §9 风险表）：归还连接被上一查询改写会话状态——挂 Task 10 池复用后 `@@transaction_read_only` 仍为 1 的用例。
6. **EXPLAIN 计划不可计量被静默放行**（spec D3）：解析失败须 warning 留痕——挂 Task 4 无 actual 段文本用例。

---

### Task 1: 执行引擎抽象层——异常迁移 + Adapter 协议 + DuckDB 下沉

**Files:**
- Create: `exec/errors.py`、`exec/engine/__init__.py`、`exec/engine/base.py`、`exec/engine/duckdb_adapter.py`、`exec/engine/plan_text.py`、`exec/engine/watchdog.py`、`tests/engine/__init__.py`（空）、`tests/engine/test_watchdog.py`
- Modify: `exec/guards.py`（异常体系迁移 + re-export；`execute_sql`/`_run_with_timeout` 改经 adapter）、`exec/pool.py`（池构造收 adapter）、`exec/__init__.py`（导出更新）、`tests/exec/test_pool.py`（池构造参数）

**Interfaces:**
- Produces: `exec.engine.get_adapter(backend: str | None = None) -> EngineAdapter`；`exec.engine.watchdog.run_with_timeout(adapter, conn, sql, timeout_ms) -> tuple[list[str], list[list]]`；`DuckDbAdapter(db_path=None)`（`name="duckdb"`、`dialect="duckdb"`、`connect_readonly/run_query/cancel/explain_scan_rows/engine_error/harden/insert_dataframe`）；异常体系从 `exec.errors` 导出、`exec.guards` 原 import 路径保持可用（re-export）。
- Consumes: 现状 `exec/guards.py` 的 `_run_with_timeout`（下沉为 watchdog）、`parse_scan_rows`/`plan_rows_measurable`（迁入 duckdb_adapter）。

- [ ] **Step 1: 迁移异常体系到 exec/errors.py（re-export 兼容）**

新建 `exec/errors.py`，把 `exec/guards.py:56-83` 的 `SqlExecutionError` / `QueryTimeoutError` / `MaxRowsScannedExceeded` / `ResultLimitExceeded` / `UnsafeSqlError` 原样搬入（docstring 不变）；`exec/guards.py` 顶部改为 `from exec.errors import (...)` 并保留模块级名字（全仓 `from exec.guards import SqlExecutionError` 等旧路径零破坏）。

- [ ] **Step 2: 写 watchdog 失败测试**

`tests/engine/test_watchdog.py`：

```python
"""看门狗通用框架测试：超时触发 adapter.cancel 并抛 QueryTimeoutError。"""
import pytest

from exec.engine.watchdog import run_with_timeout
from exec.errors import QueryTimeoutError, SqlExecutionError


class _FakeAdapter:
    name = dialect = "fake"

    def __init__(self, sleep_seconds: float, fail_with: Exception | None = None) -> None:
        self._sleep = sleep_seconds
        self._fail = fail_with
        self.cancelled = False

    def run_query(self, conn, sql):
        import time

        time.sleep(self._sleep)
        if self._fail is not None:
            raise self._fail
        return ["a"], [[1]]

    def cancel(self, conn) -> None:
        self.cancelled = True


def test_run_with_timeout_ok():
    result = run_with_timeout(_FakeAdapter(sleep_seconds=0), object(), "SELECT 1", None)
    assert result == (["a"], [[1]])


def test_run_with_timeout_cancels():
    adapter = _FakeAdapter(sleep_seconds=5.0)
    with pytest.raises(QueryTimeoutError):
        run_with_timeout(adapter, object(), "SELECT 1", 100)
    assert adapter.cancelled is True


def test_run_with_timeout_passthrough_exception():
    with pytest.raises(ValueError, match="boom"):
        run_with_timeout(_FakeAdapter(0, fail_with=ValueError("boom")), object(), "SELECT 1", None)
```

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/engine/test_watchdog.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 base.py + watchdog.py + duckdb_adapter.py**

`exec/engine/base.py`：

```python
"""执行引擎抽象：EngineAdapter 协议（二十一期，spec D1）。

护栏框架（exec.guards.execute_sql）引擎无关；连接创建、执行游标、
超时取消、计划解析、异常归一、连接加固、建仓灌数等引擎差异全部
由各 adapter 实现。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from exec.errors import SqlExecutionError


@runtime_checkable
class EngineAdapter(Protocol):
    """查询执行引擎适配器协议（duck-typing：连接对象不入协议签名）。"""

    name: str  # 引擎名（DB_BACKEND 取值）
    dialect: str  # sqlglot 方言名（白名单解析 / 探索层回写共用）

    def connect_readonly(self) -> Any:
        """创建只读连接（含引擎侧加固；失败即抛，严禁带弱配置返回）。"""
        ...

    def run_query(self, conn: Any, sql: str) -> tuple[list[str], list[list[Any]]]:
        """执行单条 SELECT，返回 (columns, rows)；结果单元格须完成类型规范化。"""
        ...

    def cancel(self, conn: Any) -> None:
        """尽力取消在途查询（超时看门狗回调；引擎无此能力时为空操作）。"""
        ...

    def explain_scan_rows(self, conn: Any, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
        """EXPLAIN ANALYZE 预检扫描行数，返回 (扫描行数, 是否可计量)。"""
        ...

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        """引擎异常归一；非本引擎异常返回 None（调用方原样重抛）。"""
        ...

    def harden(self, conn: Any) -> None:
        """连接级加固（fail-closed：失败抛异常，严禁弱配置连接进管道）。"""
        ...

    def insert_dataframe(self, conn: Any, table: str, df: Any) -> int:
        """DataFrame 批量入库（仅 mock 建仓链路使用），返回插入行数。"""
        ...
```

`exec/engine/watchdog.py`（下沉 `exec/guards.py:373-414` 的线程框架）：

```python
"""通用超时看门狗：worker 线程执行 + 超时回调 adapter.cancel（spec D2）。"""

from __future__ import annotations

import threading
from typing import Any

from audit.logging import get_logger
from exec.errors import QueryTimeoutError

logger = get_logger("exec.watchdog")


def run_with_timeout(adapter, conn, sql: str, timeout_ms: int | None):
    """在工作线程中经 adapter 执行查询；超时回调 cancel 并抛 QueryTimeoutError。"""
    if timeout_ms is None:
        return adapter.run_query(conn, sql)

    outcome: dict[str, Any] = {}

    def _worker() -> None:
        try:
            outcome["result"] = adapter.run_query(conn, sql)
        except BaseException as exc:  # 线程内任意异常均须回传
            outcome["exc"] = exc

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(timeout_ms / 1000.0)

    if worker.is_alive():
        try:
            adapter.cancel(conn)
        except Exception:  # cancel 失败也须继续走超时分支
            pass
        worker.join(2.0)
        logger.error("查询超时被强制取消", extra={"error": f"timeout_ms={timeout_ms}"})
        raise QueryTimeoutError(f"查询超时（>{timeout_ms}ms），已强制取消")

    if "exc" in outcome:
        raise outcome["exc"]
    return outcome["result"]
```

`exec/engine/duckdb_adapter.py`（现有逻辑平移，行为不变）：

```python
"""DuckDB 适配器：现有执行/取消/计划解析逻辑自 exec.guards 下沉。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from config import settings
from exec.errors import SqlExecutionError
from exec.engine.plan_text import parse_scan_rows, plan_rows_measurable
from exec.engine.watchdog import run_with_timeout


class DuckDbAdapter:
    name = "duckdb"
    dialect = "duckdb"

    def __init__(self, db_path: str | Path | None = None) -> None:
        # db_path 可注入：测试 / 建仓链路使用独立文件；缺省读 settings.DB_PATH
        self._db_path = str(db_path) if db_path is not None else str(settings.DB_PATH)

    def connect_readonly(self):
        return duckdb.connect(self._db_path, read_only=True)

    def run_query(self, conn, sql: str):
        cur = conn.execute(sql)
        columns = list(tuple(d[0] for d in cur.description))
        rows = [list(row) for row in cur.fetchall()]
        return columns, rows

    def cancel(self, conn) -> None:
        conn.interrupt()

    def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None):
        plan_sql = "EXPLAIN ANALYZE " + sql
        _, plan_rows = run_with_timeout(self, conn, plan_sql, timeout_ms)
        plan_text = (
            str(plan_rows[0][1])
            if plan_rows and len(plan_rows[0]) > 1
            else (str(plan_rows[0][0]) if plan_rows else "")
        )
        return parse_scan_rows(plan_text), plan_rows_measurable(plan_text)

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        if isinstance(exc, duckdb.Error):
            return SqlExecutionError(f"{type(exc).__name__}: {exc}")
        return None

    def harden(self, conn) -> None:
        # 文件级只读已由 connect(read_only=True) 保证；探索层加固见
        # security 层（Task 7 迁入），此处无引擎级设置
        return None

    def insert_dataframe(self, conn, table: str, df) -> int:
        conn.execute(f"INSERT INTO {table} SELECT * FROM df")
        return int(len(df))
```

注意：`parse_scan_rows` / `plan_rows_measurable` 及 `_ROWS_LINE_RE` 自 `exec/guards.py:300-324` **迁入新模块 `exec/engine/plan_text.py`**（零依赖模块）；`exec/guards.py` 改为 `from exec.engine.plan_text import parse_scan_rows, plan_rows_measurable` re-export——既有测试的旧 import 路径保持可用。**严禁 duckdb_adapter 顶层 import exec.guards**（guards 需要取 get_adapter，会成环）；guards 侧 `get_adapter` 一律函数体内惰性导入（与现状 audit 延迟导入同手法）。

`exec/engine/__init__.py`：

```python
"""执行引擎注册中心：按 DB_BACKEND 提供 adapter（spec D1）。"""

from __future__ import annotations

from config import settings
from exec.engine.base import EngineAdapter
from exec.engine.duckdb_adapter import DuckDbAdapter

__all__ = ["EngineAdapter", "DuckDbAdapter", "get_adapter"]


def get_adapter(backend: str | None = None) -> EngineAdapter:
    """按后端名取 adapter（缺省读 settings.DB_BACKEND；mysql 惰性 import）。"""
    name = backend or settings.DB_BACKEND
    if name == "duckdb":
        return DuckDbAdapter()
    if name == "mysql":
        from exec.engine.mysql_adapter import MySqlAdapter  # 惰性：未装 pymysql 不影响 duckdb

        return MySqlAdapter()
    raise ValueError(f"未知 DB_BACKEND: {name!r}（仅支持 duckdb / mysql）")
```

- [ ] **Step 4: 改造 guards.py / pool.py 消费 adapter（行为等价）**

`exec/guards.py`：
- 删除 `_run_with_timeout`（下沉）；`execute_sql` 中 `_run_with_timeout(conn, ...)` 两处改为 `run_with_timeout(adapter, conn, ...)`；
- `execute_sql` 签名追加可选参数 `adapter: EngineAdapter | None = None`，函数体首行：

```python
    if adapter is None:
        from exec.engine import get_adapter  # 惰性导入：engine 顶层不得反向依赖 guards

        adapter = get_adapter()
```

- Task 5 将为 `assert_read_only_sql` 追加 `dialect` 参数（本任务不动其签名，内部暂按 duckdb 现状）；
- 预检段（原 460-497 行）改为调 `adapter.explain_scan_rows(conn, sql, statement_timeout_ms)`（缓存/熔断/warning 留痕逻辑不动）；
- 两处 `except duckdb.Error as exc: raise SqlExecutionError(...)` 改为通用归一：

```python
    except Exception as exc:
        norm = adapter.engine_error(exc)
        if norm is not None:
            raise norm from exc
        raise
```

（超时预检段与真实执行段各一处；`QueryTimeoutError` 直接重抛分支保留在最前。）

`exec/pool.py`：`ReadOnlyConnectionPool.__init__(self, adapter, max_connections=4)`，`acquire()` 内 `conn = self._adapter.connect_readonly()`；`default_pool()` 改为 `ReadOnlyConnectionPool(get_adapter(), settings.DB_POOL_SIZE)`。更新 `tests/exec/test_pool.py`：原 `ReadOnlyConnectionPool(path, n)` 构造处改为 `ReadOnlyConnectionPool(DuckDbAdapter(path), n)`。

- [ ] **Step 5: 全量回归 + 提交**

Run: `conda run -n dataagent --no-capture-output python -m pytest -q && conda run -n dataagent --no-capture-output python -m eval.eval_runner`
Expected: 全部 PASS（既有数量不减）、oracle 8/8；`black --check .`、`ruff check .` 干净。

```bash
git add exec/ tests/engine/ tests/exec/test_pool.py
git commit -m "refactor(exec): 执行引擎抽象层——Adapter 协议与 DuckDB 逻辑下沉（行为不变）"
```

---

### Task 2: 编译方言层——Renderer 协议 + DuckDB 实现（输出逐字节不变）

**Files:**
- Create: `compiler/dialect/__init__.py`、`compiler/dialect/base.py`、`compiler/dialect/duckdb.py`、`tests/compiler/test_dialect.py`
- Modify: `compiler/sql_compiler.py`（方言原语委托 renderer：`_quote_ident` / `_qualify` / `render_literal` / `_dimension_expr` / `_time_window_sql` / comparison 时间字面量 / fill_gaps spine 与 WITH 前缀）、`config/settings.py`（新增 `DB_BACKEND` 与 `SQL_DIALECT`）

**Interfaces:**
- Produces: `compiler.dialect.get_renderer() -> DialectRenderer`（7 方法：`quote_ident` / `quote_column` / `timestamp_literal` / `date_trunc` / `spine_cte_sql` / `with_keyword` / `escape_string_literal`，见 spec D4）；`settings.DB_BACKEND="duckdb"`、`settings.SQL_DIALECT=""`（缺省跟随 DB_BACKEND）。
- Consumes: 无（首个方言任务）。

- [ ] **Step 1: settings 增加后端与方言配置**

`config/settings.py` 在 `DB_PATH` 定义之后追加：

```python
# 查询执行后端（二十一期）：duckdb（默认，零破坏）| mysql
DB_BACKEND: str = os.getenv("DB_BACKEND", "duckdb")
# SQL 方言（编译器渲染目标）：缺省跟随 DB_BACKEND 推导；可独立覆盖（测试/排查用）
SQL_DIALECT: str = os.getenv("SQL_DIALECT", "")
```

- [ ] **Step 2: 写方言委托失败测试（duckdb 输出锁定）**

`tests/compiler/test_dialect.py`：

```python
"""方言层测试：DuckDB 渲染与重构前逐字节一致（golden 锚点保护）。"""
from datetime import datetime

from compiler.dialect import get_renderer
from compiler.dialect.duckdb import DuckDbRenderer


def test_duckdb_renderer_snippets():
    r = DuckDbRenderer()
    assert r.quote_ident("gmv") == '"gmv"'
    assert r.quote_ident('a"b') == '"a""b"'
    assert r.quote_column("f", "order_id") == "f.order_id"
    assert r.timestamp_literal(datetime(2025, 1, 1, 0, 0, 0)) == "TIMESTAMP '2025-01-01 00:00:00'"
    assert r.timestamp_literal("2025-01-01") == "TIMESTAMP '2025-01-01'"
    assert r.date_trunc("month", "f.order_time") == "date_trunc('month', f.order_time)"
    assert r.escape_string_literal("a'b") == "'a''b'"
    assert r.with_keyword() == "WITH"
    spine = r.spine_cte_sql(
        "day", datetime(2025, 1, 1), datetime(2025, 1, 3), "INTERVAL 1 DAY"
    )
    assert spine == (
        "SELECT UNNEST(generate_series(TIMESTAMP '2025-01-01 00:00:00', "
        "TIMESTAMP '2025-01-03 00:00:00', INTERVAL 1 DAY)) AS \"day\""
    )


def test_get_renderer_default_is_duckdb(monkeypatch):
    import config.settings as settings

    monkeypatch.setattr(settings, "SQL_DIALECT", "")
    monkeypatch.setattr(settings, "DB_BACKEND", "duckdb")
    assert get_renderer().name == "duckdb"
    monkeypatch.setattr(settings, "SQL_DIALECT", "mysql")
    assert get_renderer().name == "mysql"
```

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/compiler/test_dialect.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 base.py / duckdb.py / __init__.py**

`compiler/dialect/base.py`：与 spec D4 一致的 `Protocol`（7 方法，docstring 中文）；`compiler/dialect/duckdb.py` 按上表测试实现（`timestamp_literal` 接受 `datetime | date | str`：datetime 走 `strftime("%Y-%m-%d %H:%M:%S")`，其余 `str(value)`，渲染 `TIMESTAMP '<s>'`）。

`compiler/dialect/__init__.py`：

```python
"""编译方言注册中心：按 SQL_DIALECT/DB_BACKEND 提供 renderer（spec D4）。"""

from __future__ import annotations

from compiler.dialect.base import DialectRenderer
from compiler.sql_compiler import CompileError


def resolve_dialect() -> str:
    """SQL_DIALECT 显式配置优先，缺省跟随 DB_BACKEND（取值即 sqlglot 方言名）。"""
    from config import settings

    if settings.SQL_DIALECT:
        return settings.SQL_DIALECT
    return settings.DB_BACKEND


def get_renderer() -> DialectRenderer:
    """取当前方言渲染器（每次调用现取——测试 monkeypatch settings 需即时生效）。"""
    name = resolve_dialect()
    if name == "duckdb":
        from compiler.dialect.duckdb import DuckDbRenderer

        return DuckDbRenderer()
    if name == "mysql":
        from compiler.dialect.mysql import MySqlRenderer

        return MySqlRenderer()
    raise CompileError(f"未知 SQL_DIALECT: {name!r}（仅支持 duckdb / mysql）")


__all__ = ["DialectRenderer", "get_renderer", "resolve_dialect"]
```

注意 `compiler/dialect/__init__.py` 顶层 `from compiler.sql_compiler import CompileError` 会与 `sql_compiler` import `compiler.dialect` 形成循环——`resolve_dialect` 内部改惰性 `from compiler.errors import CompileError`；因此本任务同时把 `CompileError` 抽到新建 `compiler/errors.py`，`sql_compiler.py` re-export 保持旧路径（同 Task 1 Step 1 手法）。

- [ ] **Step 4: sql_compiler.py 方言原语委托（调用点零改动手法）**

保持全部函数签名与调用点不变，只改函数体：
- `_quote_ident`（sql_compiler.py:62）→ `return get_renderer().quote_ident(name)`；
- `_qualify`（:196）→ `return get_renderer().quote_column(catalog.ALIASES[meta.table], meta.column)`；
- `render_literal`（:216）str 分支 → `return get_renderer().escape_string_literal(value)`；timestamp 分支 → `return get_renderer().timestamp_literal(value)`；
- `_dimension_expr`（:382）→ `expr = get_renderer().date_trunc(granularity.value, qual)`；
- `_time_window_sql`（:271）与 `_compile_with_comparison._window_block`（:579）的 `f"... TIMESTAMP '{s}' ..."` → `f"{col} >= {r.timestamp_literal(start)} AND {col} < {r.timestamp_literal(end)}"`（comparison 处同构）；
- `_compile_with_fill_gaps`（:784-788）spine 与 :805 的 `"WITH "` → `r.spine_cte_sql(time_alias, start, end_incl, step)` 与 `f"{r.with_keyword()} "`（start/end_incl 直接传 datetime，删去两处 strftime）。

- [ ] **Step 5: 全量回归（duckdb 输出零变化的最强证明）+ 提交**

Run: `conda run -n dataagent --no-capture-output python -m pytest -q && conda run -n dataagent --no-capture-output python -m eval.eval_runner`
Expected: 全部 PASS（编译器快照/golden 无一变红）、oracle 8/8。

```bash
git add compiler/ config/settings.py tests/compiler/test_dialect.py
git commit -m "refactor(compiler): 方言层 Renderer 协议——DuckDB 渲染下沉（输出逐字节不变）"
```

---

### Task 3: MySQL 执行面——PyMySQL adapter + 类型规范化 + 连接加固

**Files:**
- Create: `exec/engine/mysql_adapter.py`、`exec/engine/normalize.py`、`tests/engine/test_mysql_adapter.py`、`tests/engine/test_normalize.py`
- Modify: `requirements.txt`（新增 pymysql）、`config/settings.py`（MYSQL_* 配置组）、`.env.example`

**Interfaces:**
- Consumes: Task 1 的 `EngineAdapter` / `run_with_timeout` / `QueryTimeoutError` / `SqlExecutionError`。
- Produces: `MySqlAdapter(dsn: Mapping | None = None)`（dsn 缺省从 `settings.MYSQL_*` 构造）；`exec.engine.normalize.normalize_cell(value)`（Decimal→float、bytes→None 留痕、其余原样）。

- [ ] **Step 1: 依赖与配置**

`requirements.txt` 追加：

```
# MySQL 执行后端（二十一期）：纯 Python 驱动，MIT 许可
pymysql>=1.1.1,<2
```

`config/settings.py` 追加：

```python
# MySQL 执行后端连接配置（DB_BACKEND=mysql 时生效）——spec D10/§6
MYSQL_HOST: str = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER: str = os.getenv("MYSQL_USER", "dataagent_ro")
MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "gmall")
```

`.env.example` 追加同名条目与只读账号说明（部署要求：`GRANT SELECT ON <db>.*`）。

- [ ] **Step 2: 写失败测试（纯逻辑层，不依赖 MySQL 服务）**

`tests/engine/test_normalize.py`：

```python
"""类型规范化测试（spec D13）：pymysql 返回类型 -> 全链路统一类型。"""
import logging
from decimal import Decimal

from exec.engine.normalize import normalize_cell


def test_decimal_to_float():
    assert normalize_cell(Decimal("123.45")) == 123.45
    assert isinstance(normalize_cell(Decimal("1")), float)


def test_datetime_passthrough():
    import datetime as dt

    value = dt.datetime(2025, 1, 1, 12, 0, 0)
    assert normalize_cell(value) is value


def test_bytes_rejected_as_none(caplog):
    with caplog.at_level(logging.WARNING):
        assert normalize_cell(b"\x00bin") is None
    assert any("二进制" in r.message or "bytes" in r.message for r in caplog.records)


def test_none_and_scalars_passthrough():
    assert normalize_cell(None) is None
    assert normalize_cell(3) == 3
    assert normalize_cell("x") == "x"
```

`tests/engine/test_mysql_adapter.py`（无服务可跑的纯逻辑部分）：

```python
"""MySQL adapter 纯逻辑测试：DSN 构造 / 加固语句 / 异常归一（不连服务）。"""
import pytest

from exec.engine.mysql_adapter import HARDEN_STATEMENTS, MySqlAdapter
from exec.errors import QueryTimeoutError, SqlExecutionError


def test_dsn_defaults_from_settings(monkeypatch):
    import config.settings as settings

    monkeypatch.setattr(settings, "MYSQL_HOST", "db.local")
    monkeypatch.setattr(settings, "MYSQL_PORT", 3307)
    monkeypatch.setattr(settings, "MYSQL_USER", "ro")
    monkeypatch.setattr(settings, "MYSQL_PASSWORD", "pw")
    monkeypatch.setattr(settings, "MYSQL_DATABASE", "gmall")
    dsn = MySqlAdapter()._dsn
    assert dsn == {"host": "db.local", "port": 3307, "user": "ro",
                   "password": "pw", "database": "gmall"}


def test_harden_statements_cover_security_baseline():
    joined = " ".join(HARDEN_STATEMENTS(30000))
    assert "TRANSACTION READ ONLY" in joined
    assert "NO_BACKSLASH_ESCAPES" in joined          # spec D8 反斜杠防线一
    assert "cte_max_recursion_depth" in joined       # spec D6 递归深度
    assert "max_execution_time" in joined            # spec D2 服务端超时


def test_engine_error_timeout_3024():
    import pymysql

    err = pymysql.err.OperationalError(3024, "Query execution was interrupted")
    assert isinstance(MySqlAdapter().engine_error(err), QueryTimeoutError)


def test_engine_error_generic_wrapped():
    import pymysql

    err = pymysql.err.ProgrammingError(1064, "You have an error in your SQL syntax")
    norm = MySqlAdapter().engine_error(err)
    assert isinstance(norm, SqlExecutionError)
    assert "ProgrammingError" in str(norm)


def test_engine_error_foreign_returns_none():
    assert MySqlAdapter().engine_error(ValueError("x")) is None
```

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/engine/test_mysql_adapter.py tests/engine/test_normalize.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 normalize.py 与 mysql_adapter.py**

`exec/engine/normalize.py`：

```python
"""执行结果类型规范化（spec D13）：MySQL/DuckDB 返回类型统一到全链路口径。

- Decimal -> float：质检断言/快照比对/JSON 序列化与 DuckDB float 对齐；
- bytes -> None 并记 warning：治理管道只产出标量/时间值，二进制出现
  即异常路径，拒绝外透但留痕（不静默吞）；
- 其余类型原样返回。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from audit.logging import get_logger

logger = get_logger("exec.normalize")


def normalize_cell(value: Any) -> Any:
    """单个结果单元格规范化（见模块 docstring）。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        logger.warning("结果含二进制值，已置 None 并留痕", extra={"error": "bytes cell"})
        return None
    return value
```

`exec/engine/mysql_adapter.py`：

```python
"""MySQL 适配器（二十一期，spec D1/D2/D10）：PyMySQL 只读执行面。

超时取消：服务端 max_execution_time 为主（仅对 SELECT 生效，恰与白名单
口径一致），错误码 3024 归一为 QueryTimeoutError；客户端 read_timeout
留 5s 余量保证服务端先超时、连接可复用。扫描预检 Task 4 落地。
"""

from __future__ import annotations

import math
from typing import Any

from config import settings
from exec.engine.normalize import normalize_cell
from exec.errors import QueryTimeoutError, SqlExecutionError

# MySQL ER_QUERY_TIMEOUT（服务端 max_execution_time 触发）
ER_QUERY_TIMEOUT = 3024


def HARDEN_STATEMENTS(timeout_ms: int | None = None) -> tuple[str, ...]:
    """会话加固语句清单（spec D10；顺序执行、fail-closed）。"""
    t = timeout_ms if timeout_ms is not None else settings.QUERY_TIMEOUT_MS
    return (
        "SET SESSION TRANSACTION READ ONLY",
        "SET SESSION sql_mode = CONCAT(@@sql_mode, ',NO_BACKSLASH_ESCAPES')",
        "SET SESSION cte_max_recursion_depth = 100000",
        f"SET SESSION max_execution_time = {int(t)}",
    )


class MySqlAdapter:
    name = "mysql"
    dialect = "mysql"

    def __init__(self, dsn: dict[str, Any] | None = None) -> None:
        # dsn 可注入（一致性测试指向独立测试库）；缺省读 settings.MYSQL_*
        self._dsn = dsn or {
            "host": settings.MYSQL_HOST,
            "port": settings.MYSQL_PORT,
            "user": settings.MYSQL_USER,
            "password": settings.MYSQL_PASSWORD,
            "database": settings.MYSQL_DATABASE,
        }

    def _new_conn(self):
        import pymysql

        return pymysql.connect(
            **self._dsn,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
            read_timeout=settings.QUERY_TIMEOUT_MS / 1000.0 + 5.0,
        )

    def connect_readonly(self):
        conn = self._new_conn()
        try:
            self.harden(conn)
        except Exception:
            conn.close()
            raise
        return conn

    def run_query(self, conn, sql: str):
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in cur.description or []]
            rows = [[normalize_cell(v) for v in row] for row in cur.fetchall()]
        return columns, rows

    def cancel(self, conn) -> None:
        # 服务端 max_execution_time 已自限（spec D2 否决 KILL QUERY 方案）；
        # 看门狗回调为空操作，仅等待 worker 携超时异常返回
        return None

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        import pymysql

        if not isinstance(exc, pymysql.err.Error):
            return None
        if isinstance(exc, pymysql.err.OperationalError) and exc.args and exc.args[0] == ER_QUERY_TIMEOUT:
            return QueryTimeoutError(f"查询超时（MySQL max_execution_time），已强制取消: {exc}")
        return SqlExecutionError(f"{type(exc).__name__}: {exc}")

    def harden(self, conn) -> None:
        from security.errors import SecurityError

        try:
            with conn.cursor() as cur:
                for stmt in HARDEN_STATEMENTS():
                    cur.execute(stmt)
        except SecurityError:
            raise
        except Exception as exc:
            raise SecurityError(f"MySQL 连接加固失败: {exc}") from exc

    def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None):
        raise NotImplementedError("Task 4 落地")  # pragma: no cover - 本任务占位即失败

    def insert_dataframe(self, conn, table: str, df) -> int:
        cols = list(df.columns)
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (
            f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in cols)}) "
            f"VALUES ({placeholders})"
        )
        # NaN -> None：pandas NaN 直插 DECIMAL 列会报错（DuckDB 隐式转 NULL，对齐）
        data = [
            tuple(None if v is None or (isinstance(v, float) and math.isnan(v)) else v for v in row)
            for row in df.itertuples(index=False)
        ]
        with conn.cursor() as cur:
            for i in range(0, len(data), 5000):
                cur.executemany(sql, data[i : i + 5000])
        return len(data)
```

注意 `explain_scan_rows` 暂以 `NotImplementedError` 占位——本任务验收不含扫描预检路径（Task 4 补齐后删除）；这是计划内两任务边界，不是遗留 TODO。

- [ ] **Step 4: 跑测试通过 + 全量回归**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/engine/ -q && conda run -n dataagent --no-capture-output python -m pytest -q`
Expected: 新增测试 PASS；全量无新失败（duckdb 默认后端不触发 mysql 代码路径）。

- [ ] **Step 5: Commit**

```bash
git add exec/engine/ requirements.txt config/settings.py .env.example tests/engine/
git commit -m "feat(exec): MySQL 执行面 adapter——PyMySQL 连接/加固/超时归一/类型规范化"
```

---

### Task 4: MySQL 扫描行预检——EXPLAIN ANALYZE 解析

**Files:**
- Modify: `exec/engine/mysql_adapter.py`（`explain_scan_rows` 落地）
- Test: `tests/engine/test_mysql_adapter.py`（追加解析用例）

**Interfaces:**
- Consumes: Task 1 `run_with_timeout`、Task 3 `MySqlAdapter.engine_error`。
- Produces: `MySqlAdapter.explain_scan_rows(conn, sql, timeout_ms) -> tuple[int, bool]`（口径：各算子 `actual rows` 最大值；无 actual 段返回 `(0, False)` 交上层 warning 留痕）。

- [ ] **Step 1: 写失败测试（样例文本锁定格式，不依赖服务）**

追加到 `tests/engine/test_mysql_adapter.py`：

```python
# MySQL 8.0 EXPLAIN ANALYZE 真实输出样例（8.0.18+，版本敏感见 spec §9）
_MYSQL_PLAN_SAMPLE = """\
-> Table scan on order_detail  (cost=1.05 rows=100) (actual time=0.023..0.025 rows=98 loops=1)
    -> Filter: (f.order_time >= DATE'2025-01-01')  (cost=0.35 rows=40) (actual time=0.010..0.012 rows=40 loops=1)
"""

_MYSQL_PLAN_NO_ACTUAL = "-> Count rows in order_detail  (cost=1.05 rows=100)\n"


def test_parse_mysql_scan_rows_takes_max_actual():
    from exec.engine.mysql_adapter import parse_mysql_scan_rows

    rows, measurable = parse_mysql_scan_rows(_MYSQL_PLAN_SAMPLE)
    assert rows == 98 and measurable is True


def test_parse_mysql_scan_rows_not_measurable_without_actual():
    from exec.engine.mysql_adapter import parse_mysql_scan_rows

    rows, measurable = parse_mysql_scan_rows(_MYSQL_PLAN_NO_ACTUAL)
    assert rows == 0 and measurable is False
```

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/engine/test_mysql_adapter.py -q -k parse`
Expected: FAIL（函数不存在）

- [ ] **Step 2: 实现**

`exec/engine/mysql_adapter.py` 模块级追加并替换占位实现：

```python
import re

# 只认 actual 段（cost 段的 rows= 是估算，绝不能混入实际口径）
_MYSQL_ACTUAL_ROWS_RE = re.compile(r"\(\s*actual\s+time=[^)]*?\brows=(\d+)", re.IGNORECASE)


def parse_mysql_scan_rows(plan_text: str) -> tuple[int, bool]:
    """解析 EXPLAIN ANALYZE 树形文本，返回 (最大 actual rows, 是否可计量)。"""
    counts = [int(m.group(1)) for m in _MYSQL_ACTUAL_ROWS_RE.finditer(plan_text)]
    if not counts:
        return 0, False
    return max(counts), True
```

`explain_scan_rows` 实现：

```python
    def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None):
        from exec.engine.watchdog import run_with_timeout

        _, rows = run_with_timeout(self, conn, "EXPLAIN ANALYZE " + sql, timeout_ms)
        plan_text = str(rows[0][0]) if rows else ""
        return parse_mysql_scan_rows(plan_text)
```

（EXPLAIN 抛出的引擎异常沿 watchdog 原样冒出，由 `execute_sql` 的通用归一捕获——Task 1 已接线，无需重复处理。）

- [ ] **Step 3: 跑通过 + 提交**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/engine/ -q`
Expected: PASS

```bash
git add exec/engine/mysql_adapter.py tests/engine/test_mysql_adapter.py
git commit -m "feat(exec): MySQL EXPLAIN ANALYZE 扫描行预检解析（actual rows 口径）"
```

---

### Task 5: 只读白名单方言化——sqlglot read 参数 + MySQL 增量黑名单

**Files:**
- Modify: `exec/guards.py`（`assert_read_only_sql` / `_assert_read_only_structure` 增加 `dialect` 参数；增量黑名单表）、`exec/audit.py`（`audit_compiled_sql` 解析方言透传）
- Test: `tests/exec/test_exec.py`（追加 MySQL 黑名单用例）

**Interfaces:**
- Consumes: Task 1 `get_adapter().dialect`。
- Produces: `assert_read_only_sql(sql, dialect: str | None = None)`（缺省取当前后端方言）；`audit_compiled_sql(sql, dialect: str | None = None)` 同构。

- [ ] **Step 1: 写失败测试**

追加到 `tests/exec/test_exec.py`：

```python
class TestMysqlReadonlyBlacklist:
    """MySQL 方言增量黑名单（spec D11）：语句族与函数族各拦一路。"""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM order_detail FOR UPDATE",
            "SHOW TABLES",
            "USE information_schema",
            "KILL QUERY 42",
            "PREPARE stmt FROM 'SELECT 1'",
            "SELECT SLEEP(10)",
            "SELECT BENCHMARK(1000000, MD5('x'))",
            "SELECT LOAD_FILE('/etc/passwd')",
            "SELECT GET_LOCK('a', 10)",
        ],
    )
    def test_mysql_dangerous_rejected(self, sql):
        with pytest.raises(UnsafeSqlError):
            assert_read_only_sql(sql, dialect="mysql")

    def test_duckdb_still_accepts_plain_select(self):
        # 方言化不破坏既有口径：普通 SELECT 在两方言都放行
        assert_read_only_sql("SELECT 1 AS x", dialect="mysql")
        assert_read_only_sql("SELECT 1 AS x", dialect="duckdb")
```

（`FOR UPDATE`：MySQL 8.0 SELECT 尾部锁子句，sqlglot mysql 解析为 Select 的 lock 属性——语句黑名单拦不住，需 AST 层 `exp.Lock` 节点拒绝，实现时以测试锁定。）

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/exec/test_exec.py -q -k mysql`
Expected: FAIL

- [ ] **Step 2: 实现方言参数与增量表**

`exec/guards.py`：

```python
# 方言增量黑名单（spec D11）：通用黑名单之外按引擎追加
_DIALECT_EXTRA_KEYWORDS: dict[str, set[str]] = {
    "mysql": {
        "USE", "SHOW", "KILL", "PREPARE", "EXECUTE", "DEALLOCATE", "LOAD",
        "START", "XA", "FLUSH", "ANALYZE", "OPTIMIZE", "RENAME", "TRUNCATE",
        "GRANT", "REVOKE", "HANDLER", "DO", "SHUTDOWN", "PURGE", "HELP",
        "OUTFILE", "DUMPFILE",
    },
}
_DIALECT_EXTRA_FUNC_RE = {
    "mysql": re.compile(
        r"\b(?:load_file|sleep|benchmark|get_lock|release_lock|is_used_lock|is_free_lock)\s*\(",
        re.IGNORECASE,
    ),
}
```

- `assert_read_only_sql(sql, dialect=None)`：`dialect = dialect or get_adapter().dialect`；黑名单检查为 `for keyword in _FORBIDDEN_SQL_KEYWORDS | _DIALECT_EXTRA_KEYWORDS.get(dialect, set())`；函数正则为 `_FORBIDDEN_FUNCTION_CALL_RE` 与增量正则各查一遍；
- `_assert_read_only_structure(sql, dialect="duckdb")`：`sqlglot.parse(sql, read=dialect)`；遍历处追加：

```python
        if dialect == "mysql" and isinstance(node, exp.Lock):
            raise UnsafeSqlError("拒绝 SELECT ... FOR UPDATE / LOCK IN SHARE MODE 锁语句")
```

- `exec/audit.py:audit_compiled_sql(sql, dialect=None)`：`sqlglot.parse(sql, read=dialect or get_adapter().dialect)` 与 `_assert_read_only_structure(sql, dialect=...)` 透传；`execute_sql` 调用处同步传 `dialect=adapter.dialect`。

- [ ] **Step 3: 跑通过 + 全量回归 + 提交**

Run: `conda run -n dataagent --no-capture-output python -m pytest -q`
Expected: PASS（duckdb 路径黑名单只增不改，既有渗透测试用例不受影响）。

```bash
git add exec/guards.py exec/audit.py tests/exec/test_exec.py
git commit -m "feat(exec): 只读白名单方言化——sqlglot read 参数与 MySQL 增量黑名单"
```

---

### Task 6: MySQL 方言渲染器——quote/date_trunc/spine/反斜杠转义

**Files:**
- Create: `compiler/dialect/mysql.py`
- Test: `tests/compiler/test_dialect.py`（追加 MySQL 渲染与编译产物用例）

**Interfaces:**
- Consumes: Task 2 `DialectRenderer` 协议与 `get_renderer`。
- Produces: `MySqlRenderer`（spec D5/D6/D7/D8 的全部映射）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/compiler/test_dialect.py`：

```python
class TestMySqlRenderer:
    def test_quotes_are_backticks(self):
        from compiler.dialect.mysql import MySqlRenderer

        r = MySqlRenderer()
        assert r.quote_ident("gmv") == "`gmv`"
        assert r.quote_ident("a`b") == "`a``b`"
        assert r.quote_column("f", "order_id") == "`f`.`order_id`"

    def test_backslash_doubled_in_string_escape(self):
        # spec D8 第二道防线：即使 NO_BACKSLASH_ESCAPES 被部署覆盖，翻倍仍兜底
        from compiler.dialect.mysql import MySqlRenderer

        r = MySqlRenderer()
        assert r.escape_string_literal("a\\b") == "'a\\\\b'"
        assert r.escape_string_literal("a'b") == "'a''b'"

    def test_timestamp_literal_cast_datetime(self):
        from compiler.dialect.mysql import MySqlRenderer

        r = MySqlRenderer()
        assert (
            r.timestamp_literal("2025-01-01 00:00:00")
            == "CAST('2025-01-01 00:00:00' AS DATETIME)"
        )

    def test_date_trunc_mappings(self):
        from compiler.dialect.mysql import MySqlRenderer

        r = MySqlRenderer()
        assert r.date_trunc("day", "f.order_time") == "DATE(f.order_time)"
        assert (
            r.date_trunc("week", "f.order_time")
            == "DATE_SUB(DATE(f.order_time), INTERVAL WEEKDAY(DATE(f.order_time)) DAY)"
        )
        assert (
            r.date_trunc("month", "f.order_time")
            == "DATE_SUB(DATE(f.order_time), INTERVAL DAYOFMONTH(DATE(f.order_time)) - 1 DAY)"
        )
        assert (
            r.date_trunc("quarter", "f.order_time")
            == "MAKEDATE(YEAR(f.order_time), 1) + INTERVAL (QUARTER(f.order_time) - 1) * 3 MONTH"
        )

    def test_spine_is_recursive_cte(self):
        from compiler.dialect.mysql import MySqlRenderer

        r = MySqlRenderer()
        body = r.spine_cte_sql("day", "2025-01-01 00:00:00", "2025-01-03 00:00:00", "INTERVAL 1 DAY")
        assert body.startswith("SELECT CAST('2025-01-01 00:00:00' AS DATETIME) AS `day`")
        assert "UNION ALL" in body and "FROM __spine" in body
        assert r.with_keyword() == "WITH RECURSIVE"


class TestCompileMysqlDialect:
    """端到端：MySQL 方言下编译产物结构断言（sqlglot mysql 可解析即语法合法）。"""

    def test_fill_gaps_compiles_to_recursive_cte(self, monkeypatch):
        import sqlglot

        import config.settings as settings
        from compiler.sql_compiler import compile_sql

        monkeypatch.setattr(settings, "SQL_DIALECT", "mysql")
        dsl = make_fill_gaps_dsl()  # 复用本文件已有 DSL 构造 helper；无则从
        # tests/compiler 现有用例拷贝一个 day 粒度补零最小 DSL
        sql = compile_sql(dsl)
        assert sql.startswith("WITH RECURSIVE __spine")
        assert "generate_series" not in sql and "date_trunc(" not in sql
        sqlglot.parse_one(sql, read="mysql")  # 语法合法性（解析失败即测试失败）

    def test_month_groupby_uses_mysql_expr(self, monkeypatch):
        import config.settings as settings
        from compiler.sql_compiler import compile_sql

        monkeypatch.setattr(settings, "SQL_DIALECT", "mysql")
        dsl = make_month_gmv_dsl()  # 月粒度 GMV 最小 DSL（同上取自现有用例）
        sql = compile_sql(dsl)
        assert "date_trunc(" not in sql
        assert "DAYOFMONTH" in sql
```

（`make_fill_gaps_dsl` / `make_month_gmv_dsl` 两个 helper 从 `tests/compiler/` 现有测试中抽出的最小构造函数直接内联到本文件顶部——不要引用测试文件外部符号。）

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/compiler/test_dialect.py -q`
Expected: FAIL

- [ ] **Step 2: 实现 MySqlRenderer**

```python
"""MySQL 方言渲染器（spec D5/D6/D7/D8）。

口径要点：
- 标识符反引号（不依赖 ANSI_QUOTES，两种 sql_mode 下均正确）；
- date_trunc 用纯日期运算等价式，保持返回 DATE 类型（非 DATE_FORMAT
  字符串，避免 fill_gaps spine 对齐类型不一致）；week 用 WEEKDAY 对齐
  周一（与 DuckDB date_trunc('week') 口径一致，禁用 WEEK()/YEARWEEK）；
- 时间字面量 CAST AS DATETIME（TIMESTAMP 'str' 在 MySQL 有时区转换
  语义，与 DATETIME 列比较有隐式坑）；
- 字符串转义：单引号翻倍之外反斜杠亦翻倍（NO_BACKSLASH_ESCAPES 的
  第二道独立防线）。
"""

from __future__ import annotations

from datetime import date, datetime


class MySqlRenderer:
    name = "mysql"

    def quote_ident(self, name: str) -> str:
        return "`" + str(name).replace("`", "``") + "`"

    def quote_column(self, alias: str, column: str) -> str:
        return f"`{alias}`.`{column}`"

    def timestamp_literal(self, value: datetime | date | str) -> str:
        if isinstance(value, datetime):
            s = value.strftime("%Y-%m-%d %H:%M:%S")
        else:
            s = str(value)
        return f"CAST('{s}' AS DATETIME)"

    def date_trunc(self, granularity: str, expr: str) -> str:
        if granularity == "day":
            return f"DATE({expr})"
        if granularity == "week":
            return f"DATE_SUB(DATE({expr}), INTERVAL WEEKDAY(DATE({expr})) DAY)"
        if granularity == "month":
            return f"DATE_SUB(DATE({expr}), INTERVAL DAYOFMONTH(DATE({expr})) - 1 DAY)"
        if granularity == "quarter":
            return f"MAKEDATE(YEAR({expr}), 1) + INTERVAL (QUARTER({expr}) - 1) * 3 MONTH"
        from compiler.errors import CompileError

        raise CompileError(f"不支持的 granularity: {granularity!r}")

    def spine_cte_sql(self, alias: str, start: str, end: str, step: str) -> str:
        a = self.quote_ident(alias)
        s, e = self.timestamp_literal(start), self.timestamp_literal(end)
        return f"SELECT {s} AS {a} UNION ALL SELECT {a} + {step} FROM __spine WHERE {a} + {step} <= {e}"

    def with_keyword(self) -> str:
        return "WITH RECURSIVE"

    def escape_string_literal(self, value: str) -> str:
        return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"
```

注意：Task 2 的 `spine_cte_sql` DuckDB 版签名收 datetime（Step 4 说明"直接传 datetime"）；MySQL 版 `start`/`end` 收字符串（由 `timestamp_literal` 包装）——**统一为收 str**：Task 2 落地时编译器先把 `start.strftime(...)` 保留、传字符串进 renderer（与现状一致改动最小）。两 renderer 签名以 str 为准，测试里的调用已是 str。

- [ ] **Step 3: 跑通过 + 全量回归 + 提交**

Run: `conda run -n dataagent --no-capture-output python -m pytest -q`
Expected: PASS（duckdb 输出仍逐字节不变）。

```bash
git add compiler/dialect/mysql.py tests/compiler/test_dialect.py
git commit -m "feat(compiler): MySQL 方言渲染器——date_trunc 等价式/递归 spine/反引号/反斜杠双保险"
```

---

### Task 7: 安全面方言化——投影纯函数重构 + MySQL 内联派生表

**Files:**
- Modify: `security/views.py`（拆 `build_secure_projection` 纯函数；列渲染经 renderer；`install_secure_views`/`harden_connection` 迁出）、`core/retrieval/exploration.py`（方言化 + mysql 派生表重写分支）、`exec/engine/duckdb_adapter.py`（承接 `install_secure_views`/`harden_connection`）
- Test: `tests/security/test_security_views.py`（投影等价 + 派生表用例）、`tests/core/test_exploration.py`（mysql 方言重写用例）

**Interfaces:**
- Consumes: Task 2 `get_renderer()`；Task 1 `get_adapter()`。
- Produces: `security.views.build_secure_projection(table: str, policy, renderer) -> SecureProjection`（`select_list: list[str]`、`from_sql: str`、`where_sql: str`）；`DuckDbAdapter.install_secure_views(conn, principal)`、`DuckDbAdapter.harden(conn)`（自 `security/views.py` 迁入，逻辑不变）；`rewrite_sql_to_views(sql, principal)` 行为扩展为按当前方言产出视图名（duckdb）或内联派生表（mysql）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/security/test_security_views.py`：

```python
def test_build_secure_projection_mysql_dialect():
    """MySQL 方言投影：反引号 + RLS 固化 + 禁列物理缺席（spec D9）。"""
    from compiler.dialect.mysql import MySqlRenderer
    from security.policy import POLICIES
    from security.views import build_secure_projection

    proj = build_secure_projection("order_detail", POLICIES["analyst"], MySqlRenderer())
    body = "SELECT " + ", ".join(proj.select_list) + f" FROM {proj.from_sql}"
    if proj.where_sql:
        body += f" WHERE {proj.where_sql}"
    assert "`f`.`order_time`" in body          # 反引号限定列
    assert '"' not in body.split(" AS `")[0]   # 不再出现双引号标识符
    import sqlglot

    sqlglot.parse_one(body, read="mysql")      # 投影体语法合法
```

追加到 `tests/core/test_exploration.py`：

```python
def test_rewrite_sql_to_views_mysql_uses_derived_table(monkeypatch):
    """MySQL 无 TEMP VIEW：表引用重写为内联派生表（spec D9）。"""
    import config.settings as settings

    monkeypatch.setattr(settings, "SQL_DIALECT", "mysql")
    monkeypatch.setattr(settings, "DB_BACKEND", "mysql")
    from core.retrieval.exploration import rewrite_sql_to_views

    rewritten, notes = rewrite_sql_to_views(
        "SELECT order_id FROM order_detail WHERE order_amount > 100", "analyst"
    )
    assert rewritten is not None
    assert "sec_order_detail" in rewritten          # 别名保留授权命名空间语义
    assert rewritten.count("(SELECT") >= 1          # 派生表而非视图名引用
    import sqlglot

    sqlglot.parse_one(rewritten, read="mysql")
```

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/security/test_security_views.py tests/core/test_exploration.py -q`
Expected: FAIL

- [ ] **Step 2: 重构 views.py（投影逻辑单一实现）**

- 把现 `build_secure_views` 主体（views.py:104-193）中"每张表的投影/join/where 组装"抽为 `build_secure_projection(table, policy, renderer)`：内部 `_render_predicate` 与投影片段的 `f'{alias}."{col}"'` / `AS "{fname}"` 全部改经 `renderer.quote_column(alias, col)` / `renderer.quote_ident(fname)`；
- `build_secure_views(principal)`（duckdb 视图 DDL 语义保留）改为逐表调 `build_secure_projection` + 拼 `CREATE OR REPLACE VIEW sec_x AS SELECT ... FROM ... WHERE ...`（列名渲染用 duckdb renderer——注意 **TEMP 改写**仍在 install 处做）；
- `install_secure_views` / `harden_connection` 原函数体迁至 `exec/engine/duckdb_adapter.py`（方法名同名），`security/views.py` 删除对 `duckdb` 的 import（security 包不再依赖任何引擎模块）；`core/retrieval/exploration.py` 改从 `exec.engine.get_adapter()` 取 `install_secure_views` / `harden`；
- `tests/security/test_security_views.py` 既有用例的 import 相应更新（断言不动——投影逻辑等价性由现有测试锁定）。

- [ ] **Step 3: exploration.py 方言化 + 派生表分支**

`rewrite_sql_to_views`（exploration.py:56 起）：

```python
    r = get_renderer()
    dialect = r.name
    tree = sqlglot.parse_one(sql, dialect=dialect)
    # ...定位 Table 节点（现状逻辑）后：
    if dialect == "mysql":
        # MySQL 无 TEMP VIEW：把基表替换为内联派生表（spec D9）——
        # 投影/RLS/禁列缺席与视图版同一 build_secure_projection 产出
        proj = build_secure_projection(table, policy, r)
        inner = "SELECT " + ", ".join(proj.select_list) + f" FROM {proj.from_sql}"
        if proj.where_sql:
            inner += f" WHERE {proj.where_sql}"
        sub = sqlglot.parse_one(f"({inner})", read=dialect)
        table.replace(exp.Subquery(this=sub, alias=sqlglot.to_identifier(view)))
    else:
        table.set("this", sqlglot.to_identifier(view))
    # 回写：tree.sql(dialect=dialect)（78 行两处与 95 行同步参数化）
```

- [ ] **Step 4: 跑通过 + 全量回归 + 提交**

Run: `conda run -n dataagent --no-capture-output python -m pytest -q && conda run -n dataagent --no-capture-output python -m eval.eval_runner`
Expected: PASS、oracle 8/8（duckdb 探索层行为等价由既有 exploration 测试锁定）。

```bash
git add security/views.py core/retrieval/exploration.py exec/engine/duckdb_adapter.py tests/
git commit -m "feat(security): 安全面方言化——投影纯函数重构 + MySQL 内联派生表探索执行"
```

---

### Task 8: mock 数仓 MySQL 初始化

**Files:**
- Create: `mock/gmall/schema_mysql.py`、`mock/init_mysql.py`、`tests/mock/test_schema_mysql.py`
- Modify: `mock/gmall/build.py`（`build_gmall_tables` 收 adapter 参数、`insert_dataframe` 分发）、`web/service.py:654`（初始化端点按后端分发）

**Interfaces:**
- Consumes: Task 3 `MySqlAdapter.insert_dataframe`、`MySqlAdapter._dsn`（建仓用可写连接需调用方自建 pymysql 连接并传入 adapter 仅供分发——建仓连接**不走** `connect_readonly`）。
- Produces: `python -m mock.init_mysql`（幂等重建 MySQL 版 Gmall 数仓）；`mock.gmall.schema_mysql.MYSQL_DDL: list[str]`；`build_gmall_tables(conn, seed=42, adapter=None)`（adapter 缺省 `get_adapter()`）。

- [ ] **Step 1: 写 schema 对拍失败测试（列名一致性锁死）**

`tests/mock/test_schema_mysql.py`：

```python
"""MySQL DDL 与 DuckDB DDL 的表/列对拍：同表同列（类型允许方言差异）。"""
import sqlglot


def _tables(ddls, dialect):
    parsed = {}
    for ddl in ddls:
        tree = sqlglot.parse_one(ddl, read=dialect)
        name = tree.this.name.lower()
        cols = [c.name.lower() for c in tree.find_all(sqlglot.exp.ColumnDefinition)]
        parsed[name] = cols
    return parsed


def test_mysql_schema_matches_duckdb_tables_and_columns():
    from mock.gmall.schema import DDL_STATEMENTS
    from mock.gmall.schema_mysql import MYSQL_DDL

    duck = _tables(DDL_STATEMENTS, "duckdb")
    my = _tables(MYSQL_DDL, "mysql")
    assert set(duck) == set(my), f"表集合不一致: {set(duck) ^ set(my)}"
    for t, cols in duck.items():
        assert cols == my[t], f"表 {t} 列不一致: {cols} vs {my[t]}"
```

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/mock/test_schema_mysql.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 2: 编写 schema_mysql.py**

按 `mock/gmall/schema.py` 的 `DDL_STATEMENTS` **逐表机械转换**（24 张全量，类型映射如下；种子/生成/行为/元数据表全部覆盖）：

| DuckDB | MySQL |
|---|---|
| `INTEGER` | `INT` |
| `BIGINT` | `BIGINT` |
| `DECIMAL(p,s)` | `DECIMAL(p,s)` |
| `TIMESTAMP` | `DATETIME` |
| `VARCHAR`（URL/描述等长文本列） | `TEXT` |
| `VARCHAR`（其余） | `VARCHAR(256)` |

示例（其余同构）：

```python
MYSQL_DDL: list[str] = [
    """
    CREATE TABLE base_province (
        province_id   INT PRIMARY KEY,
        province_name VARCHAR(256),
        region_id     INT,
        area_code     VARCHAR(256),
        iso_code      VARCHAR(256),
        iso_3166_2    VARCHAR(256),
        create_time   DATETIME,
        operate_time  DATETIME
    )
    """,
    # ...与 schema.py 表清单一一对应（含 order_detail 宽表全部列）...
]

# 二级索引（MySQL 无 zoneMap：时间列与常用过滤列必须显式索引，
# 否则 EXPLAIN ANALYZE 预检本身即拖垮建仓验证）
MYSQL_INDEXES: list[str] = [
    "CREATE INDEX idx_order_detail_time ON order_detail (create_time)",
    "CREATE INDEX idx_order_detail_order ON order_detail (order_id)",
    "CREATE INDEX idx_order_detail_user ON order_detail (user_id)",
    # fact_page_view/fact_action/fact_display/fact_start 各自时间列、
    # order_refund_info(refund_time)、user_info(create_time) 同构补齐
]
```

- [ ] **Step 3: init_mysql.py + build.py 分发 + web 端点**

`mock/init_mysql.py`：

```python
"""MySQL 版 mock 数仓初始化（二十一期）。

用法：python -m mock.init_mysql（需 .env 配置可写 MySQL 账号；
查询运行时用只读账号，两账号分离见 spec D10 部署要求）。

幂等：DROP 全部表 -> 建表 -> 同一生成器（种子 42 / AS_OF_DATE 锚点）
灌数 -> 元数据表 -> 打印行数对照。与 DuckDB 版逐行同源（spec §7）。
"""

from __future__ import annotations

import pymysql

from config import settings
from exec.engine.mysql_adapter import MySqlAdapter
from mock.gmall.build import build_gmall_tables
from mock.gmall.schema_mysql import MYSQL_DDL, MYSQL_INDEXES
from mock.metadata import FIELD_METADATA, TABLE_METADATA

SEED = 42


def _writable_conn() -> pymysql.connections.Connection:
    # 建仓需要 CREATE/INSERT 权限：复用 MYSQL_* 配置（要求该账号可写），
    # 但 autocommit + utf8mb4 与运行时口径一致
    return pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DATABASE,
        charset="utf8mb4",
        autocommit=True,
    )


def main() -> None:
    adapter = MySqlAdapter()
    conn = _writable_conn()
    try:
        with conn.cursor() as cur:
            for ddl in MYSQL_DDL:
                table = ddl.split("CREATE TABLE ")[1].split(" ")[0].strip()
                cur.execute(f"DROP TABLE IF EXISTS `{table}`")
            for ddl in MYSQL_DDL:
                cur.execute(ddl)
            for idx in MYSQL_INDEXES:
                cur.execute(idx)
        counts = build_gmall_tables(conn, seed=SEED, adapter=adapter)
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE _field_metadata (table_name VARCHAR(256), "
                "column_name VARCHAR(256), comment VARCHAR(1024))"
            )
            cur.executemany(
                "INSERT INTO _field_metadata VALUES (%s, %s, %s)",
                [(t, c, k) for t, cols in FIELD_METADATA.items() for c, k in cols.items()],
            )
            cur.execute(
                "CREATE TABLE _table_metadata (table_name VARCHAR(256), comment VARCHAR(1024))"
            )
            cur.executemany(
                "INSERT INTO _table_metadata VALUES (%s, %s)", list(TABLE_METADATA.items())
            )
        print("[init_mysql] 表创建完成:")
        for t, n in counts.items():
            print(f"  - {t}: {n} 行")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

`mock/gmall/build.py`：`build_gmall_tables(conn, seed=SEED, adapter=None)`，`adapter = adapter or get_adapter()`；原 `conn.execute(f"INSERT INTO {table} SELECT * FROM df")`（build.py:45）改为 `adapter.insert_dataframe(conn, table, df)`；`conn.executemany(...)`（:72 附近，元数据/小表批量插入）改为按 `adapter.name` 分支——duckdb 用 `?` 占位、mysql 用 `%s` 占位（`placeholders = ", ".join(["?"] * n if adapter.name == "duckdb" else ["%s"] * n)`）。

`web/service.py:654` 附近：

```python
            if settings.DB_BACKEND == "mysql":
                from mock.init_mysql import main as init_db
            else:
                from mock.init_duckdb import main as init_db
```

- [ ] **Step 4: 对拍测试通过 + 全量回归 + 提交**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/mock/ -q && conda run -n dataagent --no-capture-output python -m pytest -q`
Expected: PASS（DuckDB 建仓路径行为不变——conftest 内存库仍走 duckdb adapter 分发）。

```bash
git add mock/ web/service.py tests/mock/
git commit -m "feat(mock): MySQL 版 Gmall 数仓初始化——同源生成器/DDL 类型映射/二级索引"
```

---

### Task 9: catalog_loader MySQL 物理面兼容

**Files:**
- Modify: `semantic/catalog_loader.py`（`_map_dtype` 扩展 MySQL 类型；`_physical_columns` 加 table_schema 过滤；`refresh_catalog` 连接按后端）
- Test: `tests/semantic/test_catalog_loader.py`（类型映射与 schema 过滤用例；若该文件不存在则新建）

**Interfaces:**
- Consumes: Task 1 `get_adapter()`。
- Produces: `_map_dtype(type_name)` 兼容两方言类型名；`refresh_catalog(conn=None, db_path=None)` 在 `DB_BACKEND=mysql` 且 conn 为 None 时自动建 MySQL 只读连接。

- [ ] **Step 1: 写失败测试**

```python
class TestMapDtypeMysql:
    @pytest.mark.parametrize(
        "mysql_type,expected",
        [
            ("int", "int"), ("bigint", "int"), ("smallint", "int"), ("tinyint", "int"),
            ("decimal", "float"), ("double", "float"), ("float", "float"),
            ("datetime", "timestamp"), ("timestamp", "timestamp"), ("date", "timestamp"),
            ("varchar", "str"), ("char", "str"), ("text", "str"), ("longtext", "str"),
        ],
    )
    def test_mysql_types(self, mysql_type, expected):
        from semantic.catalog_loader import _map_dtype

        assert _map_dtype(mysql_type) == expected


def test_physical_columns_filters_schema(monkeypatch):
    """MySQL information_schema 含全部库——必须按当前库过滤（spec §9 隐患）。"""
    from semantic import catalog_loader

    captured = {}

    class _Cur:
        def execute(self, sql):
            captured["sql"] = sql

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cur()

    catalog_loader._physical_columns(_Conn(), schema="gmall")
    assert "table_schema" in captured["sql"] and "gmall" in captured["sql"]
```

（`_physical_columns` 签名增加 `schema: str | None = None`：None 时保持现状行为——DuckDB 全库单 schema 无需过滤。）

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/semantic/ -q -k mysql`
Expected: FAIL

- [ ] **Step 2: 实现**

- `_map_dtype`（catalog_loader.py:132）改为表驱动：按 `base.split("(")[0].strip().upper()` 查映射字典，覆盖 DuckDB（INTEGER/BIGINT/DOUBLE/DECIMAL/TIMESTAMP/VARCHAR/DATE/BOOLEAN）与 MySQL（INT/BIGINT/SMALLINT/TINYINT/MEDIUMINT/DECIMAL/NUMERIC/DOUBLE/FLOAT/DATETIME/TIMESTAMP/DATE/VARCHAR/CHAR/TEXT 系）全集，未命中维持现有 `ValueError`（fail-fast 口径不变）；
- `_physical_columns` 查询追加 `WHERE table_schema = '{schema}'`（schema 由调用方传：mysql 取 `settings.MYSQL_DATABASE`，duckdb 传 None 走现状无过滤路径）；
- `refresh_catalog`（:427-446）conn 为 None 分支：`DB_BACKEND=="mysql"` 时 `own_conn = MySqlAdapter().connect_readonly()`，否则现状 duckdb.connect。

- [ ] **Step 3: 跑通过 + 全量回归 + 提交**

Run: `conda run -n dataagent --no-capture-output python -m pytest -q`
Expected: PASS。

```bash
git add semantic/catalog_loader.py tests/semantic/
git commit -m "feat(semantic): catalog_loader 兼容 MySQL 物理面——类型映射/schema 过滤/按后端连接"
```

---

### Task 10: 双后端一致性测试 + eval 透传 + 文档收尾

**Files:**
- Create: `tests/test_dual_backend_parity.py`
- Modify: `eval/eval_runner.py`（`--backend` 参数：透传执行连接与 sql_ok 跳过）、`README.md`、`AGENTS.md`（常用命令与配置节）、`.env.example`
- Test: 即 `tests/test_dual_backend_parity.py`（自含）

**Interfaces:**
- Consumes: 全部前置任务（adapter/renderer/init_mysql）。
- Produces: 一致性回归入口 `MYSQL_TEST_HOST` 环境变量驱动的测试文件；`python -m eval.eval_runner --backend mysql`（手工排查用）。

- [ ] **Step 1: 写一致性测试（含 Review Focus 专项）**

`tests/test_dual_backend_parity.py`：

```python
"""双后端一致性测试（spec D14）：同一 DSL 两后端编译+执行，结果必须一致。

运行条件：设置 MYSQL_TEST_HOST/PORT/USER/PASSWORD/DATABASE 指向已执行
`python -m mock.init_mysql` 的测试库；未设置时整文件跳过。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("MYSQL_TEST_HOST"), reason="需要本地 MySQL 测试库（MYSQL_TEST_* 环境变量）"
)


def _assert_rows_equal(duck_rows, mysql_rows):
    assert len(duck_rows) == len(mysql_rows), "行数不一致"
    for rd, rm in zip(duck_rows, mysql_rows):
        assert len(rd) == len(rm)
        for vd, vm in zip(rd, rm):
            if vd is None or vm is None:
                assert vd is vm
                continue
            if isinstance(vd, (int, float)) and isinstance(vm, (int, float)):
                assert vm == pytest.approx(vd, rel=1e-6, abs=1e-9)  # Review Focus #4
            else:
                assert vm == vd


@pytest.fixture(scope="module")
def mysql_conn():
    from exec.engine.mysql_adapter import MySqlAdapter

    dsn = {
        "host": os.environ["MYSQL_TEST_HOST"],
        "port": int(os.getenv("MYSQL_TEST_PORT", "3306")),
        "user": os.getenv("MYSQL_TEST_USER", "root"),
        "password": os.getenv("MYSQL_TEST_PASSWORD", ""),
        "database": os.getenv("MYSQL_TEST_DATABASE", "gmall"),
    }
    conn = MySqlAdapter(dsn).connect_readonly()
    yield conn
    conn.close()


@pytest.fixture()
def duck_conn(conn):  # tests/conftest.py 的 session 级内存 DuckDB
    return conn


def _compile(dsl, dialect):
    """按方言编译（临时切换 settings.SQL_DIALECT，用完即还原）。"""
    import config.settings as settings

    old = settings.SQL_DIALECT
    settings.SQL_DIALECT = dialect
    try:
        from compiler.sql_compiler import compile_sql

        return compile_sql(dsl)
    finally:
        settings.SQL_DIALECT = old


def _run(dsl, duck_conn, mysql_conn):
    from exec.engine import get_adapter
    from exec.engine.mysql_adapter import MySqlAdapter
    from exec.guards import execute_sql

    return {
        "duckdb": execute_sql(
            duck_conn, _compile(dsl, "duckdb"), adapter=get_adapter("duckdb"), max_result_rows=1000
        ),
        "mysql": execute_sql(
            mysql_conn, _compile(dsl, "mysql"), adapter=MySqlAdapter(), max_result_rows=1000
        ),
    }

用例（每条独立函数，Review Focus 对应标注）：

```python
def test_golden_dsls_parity(duck_conn, mysql_conn):
    """golden_dataset 全量 DSL 双后端一致（spec D14 主断言）。"""
    golden = json.loads((Path(__file__).parents[1] / "eval" / "golden_dataset.json").read_text("utf-8"))
    for item in golden:
        dsl = item["dsl"]
        results = _run(dsl, duck_conn, mysql_conn)
        _assert_rows_equal(results["duckdb"].rows, results["mysql"].rows)


def test_week_boundary_parity(duck_conn, mysql_conn):
    """Review Focus #2：跨周边界（周日 2021-01-03 / 周一 2021-01-04）分桶一致。"""
    dsl = make_week_dsl("2021-01-01", "2021-01-10")  # 从 golden 用例改造的周粒度 DSL
    results = _run(dsl, duck_conn, mysql_conn)
    _assert_rows_equal(results["duckdb"].rows, results["mysql"].rows)


def test_fill_gaps_five_years_parity(duck_conn, mysql_conn):
    """Review Focus #3：day 粒度 5 年窗口（1826 天 > 默认递归深度 1000）。"""
    dsl = make_fill_gaps_dsl("2021-01-01", "2025-12-31")
    results = _run(dsl, duck_conn, mysql_conn)
    assert len(results["mysql"].rows) >= 1820
    _assert_rows_equal(results["duckdb"].rows, results["mysql"].rows)


def test_pool_reuse_session_still_readonly():
    """Review Focus #5：连接池归还复用后会话加固不失效。"""
    from exec.engine.mysql_adapter import MySqlAdapter
    from exec.pool import ReadOnlyConnectionPool

    adapter = MySqlAdapter()
    pool = ReadOnlyConnectionPool(adapter, max_connections=1)
    conn = pool.acquire()
    conn2 = None
    try:
        pool.release(conn)
        conn2 = pool.acquire()          # 复用同一连接
        with conn2.cursor() as cur:
            cur.execute("SELECT @@transaction_read_only")
            assert cur.fetchone()[0] == 1
    finally:
        if conn2 is not None:
            pool.release(conn2)
```

（`make_week_dsl` / `make_fill_gaps_dsl` 在本文件顶部以 `QueryDSL(...)` 字面构造，参考 `eval/golden_dataset.json` 中相应形态的最小字段集；不要 import 其他测试文件的 helper。）

Run: `MYSQL_TEST_HOST=127.0.0.1 conda run -n dataagent --no-capture-output python -m pytest tests/test_dual_backend_parity.py -q`
Expected: 本地无 MySQL 时 SKIP；有库时 PASS。

- [ ] **Step 2: eval_runner --backend 透传**

`eval/eval_runner.py`：argparse 追加 `--backend`（默认 `duckdb`）；仅 `backend=="duckdb"` 时执行 oracle 模式（golden SQL 是 DuckDB 方言锚点）；`backend=="mysql"` 时 agent 管道按 `settings.DB_BACKEND` 前置注入（入口处 `settings.DB_BACKEND = args.backend`），oracle 段打印 skip 说明退出码 0。`report.sql_ok` 在 mysql 下置 `None` 并在报告标注 `sql_compare=skipped(dialect)`。

- [ ] **Step 3: 文档收尾**

- `README.md`：环境要求补 MySQL >= 8.0.18、`mock.init_mysql` 用法、`DB_BACKEND`/`MYSQL_*` 配置表；
- `AGENTS.md`：常用命令节补 `python -m mock.init_mysql`；目录结构节 exec/ 描述补 `engine/`（执行引擎抽象层：duckdb/mysql adapter）；
- `.env.example`：补全 MySQL 配置组与只读账号部署说明。

- [ ] **Step 4: 全量验收 + 提交**

Run: `conda run -n dataagent --no-capture-output python -m pytest -q && conda run -n dataagent --no-capture-output black --check . && conda run -n dataagent --no-capture-output ruff check . && conda run -n dataagent --no-capture-output python -m eval.eval_runner`
Expected: 三件套 + oracle 8/8 全绿。

```bash
git add tests/test_dual_backend_parity.py eval/eval_runner.py README.md AGENTS.md .env.example
git commit -m "feat(eval): 双后端一致性测试与 --backend 透传，MySQL 支持文档收尾"
```

---

## 红线自查记录（写完计划后执行）

1. **规格覆盖**：spec §2 耦合点 C1-C18 → Task 1（C1/C2/C4 部分）、Task 4（C3）、Task 5（C4/C5）、Task 2+6（C6-C10）、Task 7（C11/C12/C15）、Task 9（C13）、Task 8（C14/C16）、Task 10（C17）、conftest C18 明确不动（duckdb 内存 fixture 继续服务单测）。spec §4 D1-D15 与 §6-§8 配置/确定性/版本基线均有对应任务或约束。无缺口。
2. **占位符扫描**：Task 3 `explain_scan_rows` 的 `NotImplementedError` 是任务边界（Task 4 Step 2 明确删除并给出完整实现），非 TBD；Task 6/10 的 DSL helper 均指明"从 golden_dataset/现有用例抽取字面构造"，无"稍后再补"。通过。
3. **类型一致性**：`get_adapter()`（Task 1 定义，Task 4/5/7/8/9/10 消费）、`get_renderer()`（Task 2 定义，Task 6/7 消费）、`build_secure_projection(table, policy, renderer)`（Task 7 定义并自测）、`HARDEN_STATEMENTS(timeout_ms)`（Task 3 定义并测试）——签名全文唯一口径。通过。
4. **Review Focus 落实**：#1→Task 3/6、#2→Task 10、#3→Task 3/10、#4→Task 3/10、#5→Task 10、#6→Task 4。各有测试踩到。通过。

## 移交执行建议

任务间接口依赖集中在 Task 1（adapter）与 Task 2（renderer）两个地基任务上，其后 Task 3-9 相互独立可并行；建议**本会话逐任务顺序执行**（Task 1/2 的"零行为变更"验收依赖全量回归对照，同会话上下文最省），Task 10 最后收口。
