# 物理元数据面下沉 EngineAdapter 实施计划（二十四期）

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [x]` 勾选跟踪。
> 任务确认清单见 `../tasks/2026-10-26-physical-columns-adapter-tasks.md`。

**Goal:** 把语义目录的物理元数据读取从"`catalog_loader` 内特判 `mysql` + 回退
本地 DuckDB 文件"改为"`EngineAdapter.physical_columns()` 按当前后端实读"，
使 postgres / clickhouse 后端下的目录与真实执行库一致；同时修掉 MySQL 分支
`?` 占位符在真实 pymysql 上必失败的问题。默认 `duckdb` 行为零变更。

**Architecture:** 三条不变量——① 单一物理源（目录只来自 `DB_BACKEND` 指向的
库，禁止跨后端回退）；② 引擎知识只在 adapter（`catalog_loader` 不出现后端名/
类型名/占位符）；③ 失败要显式（非 duckdb 库不可用即启动失败，不回退内置默认
目录）。类型归一收敛到共享 `exec/engine/type_map.py`；`ensure_db()` 改为按
后端显式分派，仅 duckdb 建仓。

**Tech Stack:** 既有四 adapter（DuckDB `duckdb` / MySQL `pymysql` / PostgreSQL
`psycopg3` / ClickHouse `clickhouse-connect`）；元数据源分别为
`information_schema.columns`（前三个）与 `system.columns`（CH）；占位符方言
分别为无参数 / `%s` / `%s` / `{db:String}`。

**Spec:** [docs/designs/2026-10-26-physical-columns-adapter-design.md](../designs/2026-10-26-physical-columns-adapter-design.md)（决策 D1-D8；执行者需同时读两者）

## Global Constraints

- **默认 duckdb 零变更**：`pytest -q` 1241 用例、golden 31/31、35 条 golden
  `sql` 锚点（`eval_runner` 的 `sql_ok`）全部保持；`build_catalog(conn=...)`
  输出逐字段一致（列名 / dtype / 表归属）。
- **fail-closed**：未知物理类型抛 `ValueError`（指名原始类型），严禁静默降级
  为 `str`；非 duckdb 后端库不可用即抛错，严禁回退 `catalog.py` 内置默认目录。
- **只读**：元数据查询一律走 `adapter.connect_readonly()`（各后端已加固）。
- **参数化**：库名/schema 名一律驱动原生参数绑定，禁止字符串拼接。
- **占位符方言不统一**：duckdb 无参 / pymysql `%s` / psycopg `%s` /
  clickhouse-connect `{db:String}`——元数据查询由各 adapter 私有方法实现，
  **不复用** `run_query`（执行层通道无参数）。
- 所有新增/修改代码的 docstring 与行内注释使用简体中文（专有名词保留英文）；
  测试函数名英文；Git 提交信息遵循 `feat:/fix:/test:/docs:` 前缀 + 简体中文描述。
- 每个任务收尾 `black --check .`、`ruff check .` 必须绿；全量 `pytest -q` 在
  Task 8 收尾统一跑（中间任务跑各自子集）。
- 本地环境为 conda `dataagent`（Python 3.12.14）：`conda run -n dataagent
  --no-capture-output python ...`；本地无 PG/CH 服务，服务级验证由 CI 承担。
- 本地跑全量 pytest 必须用**新的** `--basetemp` 目录（复用旧目录会出现批量
  fixture 伪失败）。
- 任务产出文档遵循五类目录规范（`docs/README.md`）。

## Review Focus

规格隐含、但没有任何任务的测试直接覆盖的输入类别/失效模式（每条已挂到
owning task 的测试）：

1. **类型覆盖清单的完整性**（四后端真实建仓产物的类型全集，而非"猜"）→
   清单由「DuckDB 建仓 + 三后端 DDL 转译产物」枚举得出，逐项进表驱动用例；
   漏项会被 fail-closed 打成启动失败（可接受），但**降级为 str 不可接受** →
   Task 1。
2. **跨后端目录一致性**（同源 `mock/gmall` 数据下，四后端目录的表/列/dtype
   三元组必须全等）→ Task 8 新增跨后端目录一致性用例（duckdb 与 fake 三后端
   物理列注入对比；真库部分由 CI 冒烟承担）。
3. **`conn=` 注入路径零回归**（`tests/semantic/test_catalog_loader.py` 30+
   处依赖 DuckDB 连接注入）→ 保留 `conn` 参数语义（= duckdb adapter + 注入
   连接），改动后该文件用例必须原样全绿 → Task 2 / Task 6。
4. **`%s` 参数化在真实驱动上生效**（二十一期起该分支只被 fake `_Conn` 覆盖，
   真实 pymysql 下 `?` 必抛 `TypeError`）→ Task 3 加"真实参数风格断言"
   （断言传给 `cursor.execute` 的 SQL 含 `%s` 且不含 `?`），真库验证由 CI
   `mysql-parity` 新增冒烟步骤承担 → Task 3 / Task 8。
5. **非 duckdb 后端不回退默认目录**（库不可用 → 启动失败；`physical` 为空
   dict 也算不可用）→ Task 7 覆盖三分支（连接失败 / 空库 / 未知后端值）→
   Task 7。

---

### Task 1: 类型归一模块 `exec/engine/type_map.py`

**Files:**
- Create: `exec/engine/type_map.py`
- Create: `tests/engine/test_type_map.py`
- Modify: `exec/engine/normalize.py`（如需导出复用的剥壳正则；否则不动）

**Interfaces:**
- Produces: `map_dtype(raw: str) -> str`（返回 `int|float|str|timestamp|bool`，
  未知抛 `ValueError`）；`strip_type_wrappers(raw: str) -> str`（剥
  `Nullable(...)`/`LowCardinality(...)`/括号精度/大小写归一）。
- 消费方：Task 2-5 的四个 `physical_columns()`。

- [x] **Step 1: 枚举四后端真实类型清单**

在 DuckDB 建仓（`python -m mock.init_duckdb`）后取 `information_schema.columns`
的真实 `data_type` 全集；再取 MySQL/PG/CH 三个 `mock/gmall/schema_*.py` 的 DDL
转译产物列类型全集（可用 `sqlglot.transpile` 打印，或读 `schema_*.py` 的快照
断言）。把三份清单原样记入 `tests/engine/test_type_map.py` 的模块 docstring
（作为覆盖度凭证）。

- [x] **Step 2: 实现剥壳与规则表**

`strip_type_wrappers`：循环剥 `Nullable(X)` / `LowCardinality(X)`（可嵌套）、
去掉括号参数（`Decimal(16, 2)` → `Decimal`，注意括号内含空格）、
`strip().upper()`、折叠多余空白。
`map_dtype`：按规则表匹配（先全名精确、再前缀），覆盖：
- 整数族：`INT*`/`BIGINT`/`SMALLINT`/`TINYINT`/`MEDIUMINT`/`HUGEINT`/
  `UINT*`/`INT8..INT256`/`UINT8..UINT256`/`SERIAL`/`BIGSERIAL`
- 浮点族：`DOUBLE`/`REAL`/`FLOAT*`/`NUMERIC`/`DECIMAL`/`MONEY`
- 字符串族：`VARCHAR`/`TEXT`/`CHAR*`/`STRING`/`FIXEDSTRING`/`UUID`/`ENUM*`/
  `LOWCARDINALITY`（剥壳后不应出现）
- 时间族：`TIMESTAMP*`/`DATETIME*`/`DATE`/`DATE32`/`TIMESTAMPTZ`
- 布尔：`BOOLEAN`/`BOOL`
未知 → `ValueError(f"未支持的数据库类型: {raw!r}，请在 exec/engine/type_map.py 登记")`。

- [x] **Step 3: 表驱动用例**

`tests/engine/test_type_map.py`：
- `test_duckdb_types_covered` / `test_mysql_types_covered` /
  `test_postgres_types_covered` / `test_clickhouse_types_covered`（四份清单逐项
  断言映射结果）；
- `test_wrapper_stripping`（`Nullable(Decimal(16, 2))` → `float`、
  `LowCardinality(Nullable(String))` → `str`、`DateTime64(3)` → `timestamp`、
  `timestamp without time zone` → `timestamp`、`character varying` → `str`）；
- `test_unknown_type_fails_closed`（`Array(String)` / `Map(String, String)` /
  `geometry` → `ValueError` 且文案含原始类型）。

- [x] **Step 4: 质量门**

`black --check .` / `ruff check .` / `pytest tests/engine/test_type_map.py -q`。

---

### Task 2: 协议扩展 + DuckDbAdapter.physical_columns（零变更锚）

**Files:**
- Modify: `exec/engine/base.py`（协议 +1 方法）
- Modify: `exec/engine/duckdb_adapter.py`
- Modify: `tests/engine/test_duckdb_adapter.py`（若存在）或 Create

**Interfaces:**
- Produces: `EngineAdapter.physical_columns(conn=None)`；
  `DuckDbAdapter.physical_columns(conn=None) -> dict[str, dict[str, str]]`。
- 消费方：Task 6 的 `catalog_loader`。

- [x] **Step 1: 协议新增方法（含中文 docstring）**

`base.py` 在 `insert_dataframe` 之后追加 `physical_columns`，docstring 说明
三条契约：`conn=None` 自建只读连接并负责关闭；返回值 dtype 已归一；未知类型
fail-closed。同步更新模块 docstring 的方法计数。

- [x] **Step 2: DuckDbAdapter 实现**

```python
def physical_columns(self, conn=None) -> dict[str, dict[str, str]]:
    # 单库无需 schema 过滤；ORDER BY 保证顺序确定
    sql = ("SELECT table_name, column_name, data_type "
           "FROM information_schema.columns ORDER BY table_name, ordinal_position")
    own = None
    if conn is None:
        own = self.connect_readonly()
        conn = own
    try:
        _, rows = self.run_query(conn, sql)
    finally:
        if own is not None:
            own.close()
    out: dict[str, dict[str, str]] = {}
    for table, column, dtype in rows:
        if str(table).startswith("_"):
            continue                      # 元数据表跳过（保持现状语义）
        out.setdefault(str(table), {})[str(column)] = map_dtype(str(dtype))
    return out
```

- [x] **Step 3: 零变更锚用例**

用内存 DuckDB 建两张表（含 `DECIMAL(16,2)` / `TIMESTAMP` / `VARCHAR` / `INTEGER`），
断言 `physical_columns()` 输出与旧 `catalog_loader._physical_columns()` 逐项相等
（旧实现此时仍在，可直接对拍）；再断言 `_` 前缀表被跳过。

- [x] **Step 4: 质量门**

`black` / `ruff` / `pytest tests/engine -q`。

---

### Task 3: MySqlAdapter.physical_columns（含 `%s` 占位符修复）

**Files:**
- Modify: `exec/engine/mysql_adapter.py`
- Modify: `tests/engine/test_mysql_adapter.py`

**Interfaces:**
- Produces: `MySqlAdapter.physical_columns(conn=None)`（schema 过滤走
  `settings.MYSQL_DATABASE`）。
- 消费方：Task 6。

- [x] **Step 1: 实现（参数化 `%s`）**

```python
def physical_columns(self, conn=None) -> dict[str, dict[str, str]]:
    sql = ("SELECT table_name, column_name, data_type FROM information_schema.columns "
           "WHERE table_schema = %s ORDER BY table_name, ordinal_position")
    ...
    with conn.cursor() as cur:
        cur.execute(sql, (settings.MYSQL_DATABASE,))
        rows = cur.fetchall()
```

- [x] **Step 2: 修 `?` 的历史 bug 并留痕**

注释记录：二十一期起该分支用 `?`，而 pymysql 参数风格为 `%s`（`execute` 内部
`query % args`，无 `%s` 占位符却传 args → `TypeError: not all arguments
converted`），该路径此前只被 fake `_Conn` 单测覆盖，从未在真实驱动上执行。

- [x] **Step 3: 用例（含真实参数风格断言）**

- `test_physical_columns_uses_percent_placeholder`：fake cursor 捕获
  `execute` 的 `(sql, params)`，断言 SQL 含 `%s`、**不含** `?`、params 为
  `(settings.MYSQL_DATABASE,)`；
- `test_physical_columns_skips_underscore_tables`；
- `test_physical_columns_dtype_mapping`（`int(11)` / `decimal(16,2)` /
  `varchar(64)` / `datetime`）。

- [x] **Step 4: 质量门**

`black` / `ruff` / `pytest tests/engine/test_mysql_adapter.py -q`。

---

### Task 4: PostgresAdapter.physical_columns + `POSTGRES_SCHEMA` 配置

**Files:**
- Modify: `config/settings.py`（`POSTGRES_DATABASE` 行后插入 `POSTGRES_SCHEMA`）
- Modify: `.env.example`
- Modify: `exec/engine/postgres_adapter.py`
- Modify: `tests/engine/test_postgres_adapter.py`

**Interfaces:**
- Produces: `settings.POSTGRES_SCHEMA`（默认 `public`）；
  `PostgresAdapter.physical_columns(conn=None)`。

- [x] **Step 1: 配置项**

```python
# PG 元数据 schema 过滤（二十四期）：目录物理列按该 schema 读取，与建仓口径一致
POSTGRES_SCHEMA: str = os.getenv("POSTGRES_SCHEMA", "public")
```
`.env.example` 同步登记。

- [x] **Step 2: 实现（`%s` 参数化 + schema 过滤）**

```python
sql = ("SELECT table_name, column_name, data_type FROM information_schema.columns "
       "WHERE table_schema = %s ORDER BY table_name, ordinal_position")
cur.execute(sql, (settings.POSTGRES_SCHEMA,))
```

- [x] **Step 3: 用例**

`test_physical_columns_filters_public_schema`（捕获 SQL 与 params，断言 `%s`
且 params 为 `('public',)`）、`test_physical_columns_pg_types`（
`numeric` / `character varying` / `timestamp without time zone` / `boolean` /
`bigint` → 正确 dtype）。

- [x] **Step 4: 质量门**

`black` / `ruff` / `pytest tests/engine/test_postgres_adapter.py -q`。

---

### Task 5: ClickHouseAdapter.physical_columns（`system.columns`）

**Files:**
- Modify: `exec/engine/clickhouse_adapter.py`
- Modify: `tests/engine/test_clickhouse_adapter.py`

**Interfaces:**
- Produces: `ClickHouseAdapter.physical_columns(conn=None)`（`database` 过滤走
  `settings.CLICKHOUSE_DATABASE`）。

- [x] **Step 1: 实现（`{db:String}` 参数化）**

```python
sql = ("SELECT table, name, type FROM system.columns "
       "WHERE database = {db:String} ORDER BY table, position")
result = conn.query(sql, parameters={"db": settings.CLICKHOUSE_DATABASE})
```

注意：`run_query` 无参数通道（D3），故本方法内直接用 `conn.query`；连接自建时
复用 `connect_readonly()` 并在 `finally` 关闭。

- [x] **Step 2: CH 类型剥壳接线**

`Nullable(Decimal(16, 2))` / `LowCardinality(String)` / `DateTime64(3)` /
`UInt64` / `String` / `Date32` 全部经 `map_dtype` 归一；`Array(...)`/`Map(...)`
抛 `ValueError`。

- [x] **Step 3: 用例**

`test_physical_columns_queries_system_columns`（fake client 断言 SQL 含
`system.columns` 且 `parameters={"db": "gmall"}`）、
`test_physical_columns_ch_types`（六种真实类型）、
`test_physical_columns_closes_own_connection`（自建连接必被关闭）。

- [x] **Step 4: 质量门**

`black` / `ruff` / `pytest tests/engine/test_clickhouse_adapter.py -q`。

---

### Task 6: `catalog_loader` 改道（物理列 + 维度成员 + 删除旧实现）

**Files:**
- Modify: `semantic/catalog_loader.py`（删 `_DTYPE_MAP` / `_map_dtype` /
  `_physical_columns` / `_fetch_all`；改 `build_catalog` / `_load_dimension_members`）
- Modify: `tests/semantic/test_catalog_loader.py`（迁移物理面用例）

**Interfaces:**
- Consumes: `EngineAdapter.physical_columns()` / `run_query()`（Task 2-5）
- Produces: `build_catalog(db_path=None, conn=None, overlay_path=None, adapter=None)`
  —— 新增 `adapter` 参数（缺省按 `settings.DB_BACKEND` 取 `get_adapter()`）。

- [x] **Step 1: 物理列改道**

```python
def build_catalog(db_path=None, conn=None, overlay_path=None, adapter=None):
    if adapter is None:
        from exec.engine import get_adapter
        adapter = get_adapter()
    physical = None
    own_conn = conn
    try:
        if db_path is not None and adapter.name != "duckdb":
            raise ValueError(f"--db 仅对 duckdb 后端有意义（当前 DB_BACKEND={adapter.name}）")
        physical = adapter.physical_columns(own_conn)
        ...
```

- [x] **Step 2: 维度成员改道 `run_query`**

`_load_dimension_members(adapter, conn, columns, member_fields)`：SQL 仍由
`_quote()` + 目录白名单构造（无参数），执行改 `adapter.run_query(conn, sql)`。
连接复用：若 `conn is None` 且 `adapter.physical_columns()` 内部已自建并关闭，
则此处再建一次（避免把连接生命周期跨函数传递；记录该取舍：两次握手换简单性，
启动期一次，可接受）。

- [x] **Step 3: 删除旧实现 + 失败语义（D7）**

- 删 `_DTYPE_MAP` / `_map_dtype` / `_physical_columns` / `_fetch_all`；
- 非 duckdb 后端：`physical` 为空 dict 或 `physical_columns()` 抛错 → 直接向上抛
  （`ValueError`/`SqlExecutionError`），**不**回退内置默认；
- duckdb 后端：保持现状回退（`physical=None` → 内置默认）。

- [x] **Step 4: 迁移测试**

- `test_map_dtype_*` 两条 → 已在 Task 1 覆盖，此处删除；
- `test_physical_columns_filters_schema` / `test_physical_columns_duckdb_keeps_legacy_path`
  → 改为"`build_catalog(conn=...)` 的物理列来自注入连接"（保留原断言意图）；
- 新增 `test_build_catalog_uses_backend_adapter`（monkeypatch
  `exec.engine.get_adapter` 返回 fake adapter，断言其 `physical_columns` 被调用、
  目录 dtype 来自其返回值）；
- 新增 `test_non_duckdb_empty_physical_raises`（fake adapter 返回 `{}` →
  `ValueError`，文案含后端名）；
- 新增 `test_db_path_rejected_for_non_duckdb`。

- [x] **Step 5: 质量门**

`black` / `ruff` / `pytest tests/semantic -q` + `pytest tests/compiler -q`（目录
被编译器消费，防连带）。

---

### Task 7: `ensure_db()` 按后端显式分派

**Files:**
- Modify: `web/service.py:650-673`
- Modify: `tests/web/test_web.py`（或新建 `tests/web/test_service_ensure_db.py`）

**Interfaces:**
- Consumes: `refresh_catalog()`（Task 6 后的新签名，向后兼容）
- Produces: `ensure_db()` 四后端分派语义（D5）。

- [x] **Step 1: 实现**

```python
def ensure_db() -> None:
    backend = settings.DB_BACKEND
    if backend == "duckdb":
        if not settings.DB_PATH.exists():
            from mock.init_duckdb import main as init_db
            init_db()
        refresh_catalog(db_path=settings.DB_PATH)
    else:
        # mysql / postgres / clickhouse：建仓是显式运维动作（python -m mock.init_*）
        refresh_catalog()
    refresh_policies()
```

- [x] **Step 2: 三分支用例**

- `test_ensure_db_duckdb_initializes_when_missing`（monkeypatch `DB_PATH` 指向
  不存在文件 + fake `mock.init_duckdb.main`，断言被调用一次）；
- `test_ensure_db_non_duckdb_never_touches_duckdb_file`（`DB_BACKEND=postgres`
  + `DB_PATH` 不存在 → 断言 `init_db` **未**被调用、`refresh_catalog` 未传
  `db_path`）；
- `test_ensure_db_propagates_backend_failure`（fake `refresh_catalog` 抛
  `SqlExecutionError` → `ensure_db` 原样抛，不吞异常）。

- [x] **Step 3: 质量门**

`black` / `ruff` / `pytest tests/web -q`。

---

### Task 8: 跨后端目录一致性用例 + CI 门禁扩展 + 文档收口

**Files:**
- Create: `tests/test_catalog_backend_parity.py`
- Modify: `.github/workflows/ci.yml`（三个 parity job 各加一步冒烟）
- Modify: `docs/tasks/2026-10-26-physical-columns-adapter-tasks.md`（执行日志）
- Modify: `AGENTS.md`（如契约小节需同步）

**Interfaces:**
- Consumes: 全部前序任务。

- [x] **Step 1: 跨后端目录一致性（Review Focus 2）**

`tests/test_catalog_backend_parity.py`：以 DuckDB 建仓为基准，构造三个 fake
adapter（分别返回 MySQL/PG/CH 风格的**原始类型**，经各自 `map_dtype` 归一），
断言 `build_catalog(adapter=...)` 的 `COLUMNS` 三元组（逻辑字段/表/列/dtype）
与 duckdb 基准**全等**。该用例纯本地可跑（不需真库），锁住"四后端同源数据 →
同目录"这一不变量。

- [x] **Step 2: CI 冒烟（真实库）**

在 `mysql-parity` / `postgres-parity` / `clickhouse-parity` 三个 job 的 parity
pytest 之后各加一步：

```yaml
- name: Catalog refresh smoke (physical columns from real backend)
  run: python -m semantic.catalog_loader
  env:
    DB_BACKEND: <backend>
    ...（对应连接变量）
```

判据：命令成功退出且输出含 `fact_table=order_detail`（CLI 现有输出）。这一步
同时实锤 Review Focus 4（真实驱动参数化）与 §10 R1/R2（只读账号元数据读权限）。

- [x] **Step 3: 全量质量门**

`black --check .`、`ruff check .`、`pytest -q --basetemp=logs/.pytest_tmp-<新目录>`；
`python -m eval.eval_runner`（oracle 31/31 + `sql_ok` 锚点 0 漂移）。

- [x] **Step 4: 文档收口**

tasks 执行日志逐任务记录；`AGENTS.md` 若含"目录构建"契约描述则同步（注明物理
列来源 = 当前后端 adapter）。
