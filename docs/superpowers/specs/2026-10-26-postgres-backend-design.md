# 设计方案｜PostgreSQL 执行后端支持（二十二期，2026-10）

> 状态：设计定稿，待用户审阅。实施计划与任务跟踪待用户确认后另立
> `docs/plans/` 三件套（对齐 `2026-10-25-mysql-support-{spec,plan,tasks}.md` 先例）。
> 分支：`feat/postgres-backend`（自 master 41ca5cc 切出）。

## 1. 背景与目标

二十一期（41ca5cc）已落地 `DB_BACKEND=duckdb|mysql` 双后端：执行引擎抽象层
`exec/engine/`（EngineAdapter 协议）+ 编译方言层 `compiler/dialect/`
（DialectRenderer 协议）承载引擎差异，护栏框架引擎无关。本期在其上新增
PostgreSQL 作为第三个执行后端，**全量对齐二十一期交付模式**：

1. `DB_BACKEND=duckdb|mysql|postgres` 三值切换，默认 `duckdb` 零破坏；
2. 编译器按后端产出 PostgreSQL 方言 SQL（确定性、无注入不变）；
3. 执行三护栏（statement_timeout / 扫描行熔断 / LIMIT 硬上限）+ 执行前
   审计门在 PG 上等价生效；
4. 探索层（L3）sec_* 安全面在 PG 上以 **TEMP 视图**形式等价实现（与
   DuckDB 同路，非 MySQL 的内联派生表）；
5. mock Gmall 数仓一键初始化到 PG（确定性三原则不变：种子 42、
   AS_OF_DATE=2025-12-31、行数与 DuckDB 版一致）；
6. 双后端一致性回归 + CI `postgres-parity` 门禁 job（CI 首绿即 PG 链路
   首次真实验证——本地开发期 parity 仅依赖 CI，已与用户确认）。

**非目标（本期不做）**：

- 不做 PostgreSQL 12 及以下兼容（基线 >= 13，见 §8）；
- 不做运行期多后端混查（一次进程一个后端，由配置决定）；
- 不做 golden 评测集的 PG 方言重写（golden 锚定 DuckDB 方言不变，PG
  正确性由 parity 测试承担）；
- 不做审计存储与 SQLite KV 状态外置迁移；
- 不动 `semantic/catalog.py`、DSL 契约与护栏框架结构。

## 2. 现状耦合点清单（调研事实）

2026-10-26 在 master（41ca5cc）逐点调研结论，即全部改造面：

| # | 位置 | 事实 |
|---|------|------|
| C1 | `exec/engine/__init__.py:16-29` | `get_adapter` if-elif 双值，惰性 import 隔离驱动；报错文案硬编码 `仅支持 duckdb / mysql` |
| C2 | `compiler/dialect/__init__.py` | `get_renderer` 同构双值硬编码；`resolve_dialect` 显式 `SQL_DIALECT` 优先、缺省跟 `DB_BACKEND` |
| C3 | `exec/guards.py:141-176` | `_DIALECT_EXTRA_KEYWORDS` / `_DIALECT_EXTRA_FUNC_RE` 仅 mysql 键；`_assert_read_only_structure` MySQL 分支拒绝 `exp.Lock` |
| C4 | `exec/guards.py` | 方言经 `get_adapter().dialect` 解析后交 sqlglot（postgres 是 sqlglot 原生方言名，AST 层零新增） |
| C5 | `core/retrieval/exploration.py:79` | `dialect == "mysql"` 走内联派生表，否则改写为 `sec_` 视图名引用 |
| C6 | `core/retrieval/exploration.py:158-160` | `if adapter.name == "duckdb":` 才调 `adapter.install_secure_views`——PG TEMP 视图需要同等安装钩子 |
| C7 | `security/views.py:121` | `build_secure_projection` 纯函数（投影/RLS/禁列），与引擎无关，直接复用 |
| C8 | `mock/gmall/build.py:321-350` | `build_gmall_tables(conn, seed, adapter=...)` 按 `adapter.name` 二分支选 DDL 创建函数；灌数统一 `adapter.insert_dataframe`；占位符 `?`（duckdb）/ `%s`（mysql） |
| C9 | `mock/init_mysql.py` + `mock/gmall/schema_mysql.py` | DDL 运行时转译先例：`sqlglot.transpile(read="duckdb", write=...)` + 转译后校验 + 二级索引增量 |
| C10 | `tests/test_dual_backend_parity.py` | 跳过条件 `MYSQL_TEST_HOST`；`_norm_cell` / `_assert_rows_equal`（rel=1e-5, abs=1e-6）；5 用例结构 |
| C11 | `.github/workflows/ci.yml:46-86` | `mysql-parity` job：service 容器 + init + parity pytest |
| C12 | `config/settings.py:29-41` | `DB_BACKEND` / `SQL_DIALECT` / `MYSQL_*` 五项配置组与权限注释模式 |
| C13 | `requirements.txt` | 驱动 pin + 选型理由注释模式（duckdb 上界、pymysql 纯 Python 理由） |
| C14 | `eval/eval_runner.py:387-438` | `--backend` choices 与 `_mysql_smoke()` 冒烟先例 |
| C15 | `exec/engine/` 全目录 | 无任何 postgres 预留/TODO；全仓 "postgres" 仅 `guards.py` 函数黑名单前缀 `postgres_`（安全拦截，非预留） |
| C16 | `compiler/sql_compiler.py:551` | comparison 配对硬编码 `date_add(expr, INTERVAL …)`——DuckDB/MySQL 恰好同名兼容，**PG 无 `date_add` 函数**，需方言原语化（见 D17） |

## 3. 总体架构

与二十一期同构的双轨接入，消费方零改动：

```
DB_BACKEND=postgres
  ├─ exec/engine/postgres_adapter.py   PostgresAdapter（psycopg3）
  │    connect_readonly / run_query / cancel / explain_scan_rows
  │    / engine_error / harden / insert_dataframe (+ install_secure_views)
  └─ compiler/dialect/postgres.py      PostgresRenderer（独立实现）
       quote / literal / date_trunc / spine / WITH / escape
```

- **工厂注册表化**（方案 A 的结构性改造，仅限工厂内部）：`get_adapter` /
  `get_renderer` 的 if-elif 改为 dict 注册表 + 惰性 import（保持驱动隔离），
  报错文案由注册表 keys 动态生成——第四后端只需各加一行注册。
- **探索层通用化**：`exploration.py` 的视图安装判断从
  `adapter.name == "duckdb"` 改为 `getattr(adapter, "install_secure_views", None)`
  非空即调；DuckDB 与 PG 实现该方法，MySQL 不实现（内联派生表在改写期已处理）。
- **不变项**：golden 锚定 DuckDB；护栏框架（审计门 / 看门狗 / 扫描缓存 /
  LIMIT 硬上限）引擎无关；`semantic.json` 与语义目录零改动。

## 4. 关键决策（D1-D16）

**D1 驱动选型：`psycopg[binary]>=3.2,<4`（psycopg3）**。理由：生态当前
标准（psycopg2 已进入仅维护模式）；二进制 wheel 全平台覆盖（Windows/Linux
免编译）；核心保留纯 Python 回退实现；管道模式 executemany；按 SQLSTATE
解析异常。pg8000（纯 Python，与 pymysql 先例一致）被否决：维护节奏慢、
性能弱；psycopg2 否决：停止发展。选型已与用户确认。

**D2 引擎名与方言名：`name="postgres"`、`dialect="postgres"`**。postgres
是 sqlglot 原生方言名，护栏 AST 解析（`assert_read_only_sql` /
`assert_guardrails`）与探索层改写零新增；配置组命名 `POSTGRES_*` 对齐
`MYSQL_*` 与官方 docker 镜像 env。

**D3 超时模型：服务端 `statement_timeout` 单轨 + 连接级双保险**。连接
options 预设 + `harden()` 会话 SET 同时生效（对应 MySQL 的 connect
read_timeout + HARDEN_STATEMENTS 分工）。`cancel()` 为空操作：
`pg_cancel_backend` 需要独立 pid 通道，注入复杂度不值得，理由注释进代码
（与 MySQL KILL QUERY 否决同口径）。

**D4 异常归一：SQLSTATE `57014`（QueryCanceled）→ `QueryTimeoutError`**；
其余 `psycopg.Error` → `SqlExecutionError`（附 pgcode 与原始报错喂自愈）。
对齐 MySQL 3024 归一先例；归一逻辑读 `exc.sqlstate` 而非异常类名，锁定
行为不依赖 C 扩展实现细节。

**D5 扫描熔断：`EXPLAIN ANALYZE` 文本仅认 `(actual time=… rows=N …)` 段**，
estimated `rows=` 不混入；取全部 plan 节点最大值；输出无 actual 段 →
不可计量 → warning 留痕、不缓存不熔断（与现有口径一致）。看门狗复用
`run_with_timeout`（引擎无关）。

**D6 harden 四条 SET（幂等）**：
`default_transaction_read_only = on`（只读）、
`statement_timeout = {QUERY_TIMEOUT_MS}`（超时）、
`standard_conforming_strings = on`（转义口径确定性）、
`lock_timeout = 10000`（单位毫秒，防御性锁等待上限 10s；只读查询也可能等
AccessShare 与 DDL 的 AccessExclusive 竞争）。

**D7 转义：仅单引号翻倍，不翻倍反斜杠**。与 MySQL renderer 的关键差异：
PG 在 `standard_conforming_strings=on`（9.1+ 默认，harden 显式 pin）下
反斜杠即字面量，翻倍反而引入多余字符。MySQL 那套"反斜杠双保险"不适用，
语义等价性由 pin 保证。

**D8 date_trunc / spine 全走 PG 原生**：`date_trunc('day|week|month|quarter', expr)`
四档原生（week 周一锚定，与 MySQL WEEKDAY 对齐口径等价）；spine 用
`SELECT gs FROM generate_series(ts1, ts2, interval) AS gs` 原生集合函数，
**无递归深度问题**（MySQL 递归 CTE + cte_max_recursion_depth 的负担在 PG
不存在）；`with_keyword()` 返回 `WITH`。step 以 INTERVAL 字面量渲染，
实现期以 `sql_compiler.py:784` 现有传参为准对齐。

**D9 renderer 独立完整实现，不继承 DuckDbRenderer**。PG 与 DuckDB 的
quote/literal/date_trunc 原语形态虽相同（DuckDB 语法大量源于 PG），但
与 MySQL renderer 先例一致地独立实现（约 40 行），换取零耦合——DuckDB
renderer 未来改动不波及 PG。

**D10 探索层 TEMP VIEW 路线**：PG 原生支持 `CREATE OR REPLACE TEMP VIEW`，
与 DuckDB 同路。`install_secure_views` 为协议外可选方法（`getattr` 判定），
PostgresAdapter 实现之；投影/RLS/禁列复用同一 `build_secure_projection`。
TEMP 视图随连接池会话残留问题与 DuckDB 同构，不新增处理。

**D11 只读三防线**：只读账号（`GRANT SELECT ON ALL TABLES IN SCHEMA
public`，写入 settings 注释）+ 会话 `default_transaction_read_only=on`
（options + harden 双保险）+ 方言增量黑名单（D12）。

**D12 方言增量黑名单（fail-closed 四层结构不变）**：
- 关键词：`COPY`（`COPY … TO PROGRAM` 可执行宿主命令，PG 侧最危险面）、
  `LISTEN` / `NOTIFY`、`DO`、`LOAD`、`VACUUM` / `REINDEX` / `CLUSTER`；
- 函数正则：`pg_sleep(_for)?`、`pg_advisory_lock` 族、
  `pg_terminate_backend` / `pg_cancel_backend`、`pg_read_file` /
  `pg_ls_dir`、`dblink`、`lo_import` / `lo_export`（实现期先核对既有
  `postgres_` 前缀家族黑名单，只补增量不重复）；
- AST 层：postgres 分支同样拒绝 `exp.Lock`（`LOCK TABLE` 阻塞他人）。

**D13 建仓**：`mock/init_postgres.py` 镜像 `init_mysql.py` 三段式（DROP
全表 → `build_gmall_tables(conn, seed=42, adapter=PostgresAdapter())` →
写 metadata），末尾追加**显式 `ANALYZE`**——PG CBO 依赖统计信息，新灌
数据不收集则 EXPLAIN 计划与扫描熔断口径失真（PG 特有步骤）。
`schema_postgres.py` 逐条 `sqlglot.transpile(read="duckdb", write="postgres")`
+ 逐表存在性校验（失败携带原始 DDL 自诊断）；类型口径 `DECIMAL(p,s)` →
`NUMERIC(p,s)`，其余保真；不强制 MySQL 那套二级索引（parity 断言结果集
不依赖索引）。`build.py` 的引擎分支点改为按 backend 选择 DDL 创建函数
（duckdb `create_tables` / mysql `create_mysql_tables` / postgres
`create_postgres_tables`），占位符差异封装在 adapter 内。

**D14 parity 测试**：提取 `tests/parity_utils.py`（`_norm_cell` /
`_assert_rows_equal` / 编译执行辅助，容差口径 rel=1e-5, abs=1e-6 逐字
不变），`test_dual_backend_parity.py` 改 import 断言行为不变；
`test_pg_backend_parity.py` 5 用例同款（golden 全量 DSL / 跨周边界 /
fill_gaps 1826 行 / comparison yoy / 池复用只读断言——PG 断言
`current_setting('transaction_read_only') == 'on'`）；跳过条件
`POSTGRES_TEST_HOST`。

**D15 CI `postgres-parity` job**：service 容器 `postgres:16`
（`POSTGRES_PASSWORD/DB` + `pg_isready` 健康检查）→ `python -m
mock.init_postgres` → `pytest tests/test_pg_backend_parity.py -q`。
版本锚定 postgres:16（与 mysql:8.0 同策略），升级走二十一期 spec §8.1
式回归。

**D16 评测冒烟**：`eval --backend` choices 增加 `postgres`，`_postgres_smoke()`
对齐 `_mysql_smoke()`（COUNT order_detail，全护栏链路）。golden 评测不动。

**D17 comparison 对齐的方言原语化（date_add）**。调研补遗（计划期实证发现）：
`sql_compiler.py:551` 硬编码 `date_add(expr, INTERVAL …)`——DuckDB/MySQL 恰好
同名兼容，**PostgreSQL 无 `date_add` 函数**。DialectRenderer 协议新增第 8 原语
`date_add(expr, interval)`：DuckDB/MySQL 渲染与现状**逐字节一致**（golden 锚点
不动）；PG 渲染 `{expr} + INTERVAL '…'` 加法等价式。INTERVAL 字面量转译
（`INTERVAL 1 DAY` → `INTERVAL '1 day'`——编译器产出形态为 DuckDB/MySQL 兼容
语法，PG 非法）与 spine 步长共用同一转译点 `pg_interval_literal`，未知形态
显式 `CompileError`。

## 5. 安全模型对照（治理管道等价性）

| 防线 | DuckDB | MySQL | PostgreSQL |
|------|--------|-------|-----------|
| 只读账号 | 单文件只读打开 | `GRANT SELECT` 运行时账号 | `GRANT SELECT ON ALL TABLES IN SCHEMA public` |
| 会话只读 | `enable_external_access=false` + `lock_configuration` | `SET SESSION TRANSACTION READ ONLY` | `default_transaction_read_only=on`（options + harden） |
| 语句黑名单 | DuckDB 表函数家族 | mysql 增量（USE/SHOW/KILL/…） | postgres 增量（COPY/DO/LISTEN/…，见 D12） |
| AST 只读结构 | sqlglot + `exp.Lock`（mysql） | 同左 | sqlglot postgres 方言 + `exp.Lock` |
| 超时 | watchdog + `conn.interrupt()` | `max_execution_time`（3024 归一） | `statement_timeout`（57014 归一） |
| 扫描熔断 | EXPLAIN ANALYZE `N rows` | actual rows 正则 | actual 段正则（D5） |
| 探索层 sec_* | TEMP 视图 | 内联派生表 | **TEMP 视图**（D10） |
| PII 脱敏导出 | 后端无关 | 后端无关 | 后端无关 |

## 6. 配置设计

```bash
DB_BACKEND=duckdb            # duckdb（默认）| mysql | postgres
SQL_DIALECT=                 # 显式方言覆盖，缺省跟随 DB_BACKEND

POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=dataagent_ro   # 运行时只读账号：GRANT SELECT ON ALL TABLES IN SCHEMA public
POSTGRES_PASSWORD=
POSTGRES_DATABASE=gmall
```

测试库独立配置 `POSTGRES_TEST_HOST/PORT/USER/PASSWORD/DATABASE`（对齐
`MYSQL_TEST_*` 先例，与运行时配置解耦）。依赖：`requirements.txt` 增加
`psycopg[binary]>=3.2,<4`（pin 注释写明 D1 理由）。

## 7. 确定性与可复现

- SEED=42、`AS_OF_DATE=2025-12-31`、单 rng 顺序消费三原则不变；同一生成
  器双通道入库（duckdb / mysql / postgres 三后端逐行同数据）；
- golden 评测锚点不动（DuckDB 方言 + 结果快照）；
- parity 断言行序无关 + 数值 rel/abs 双口径，沿用二十一期容差口径。

## 8. 版本基线与部署要求

- CI 锚定 `postgres:16`；兼容基线 **PostgreSQL >= 13**：
  `standard_conforming_strings` 默认 on（9.1+）、`date_trunc('week')`
  周一锚定、timestamp 版 `generate_series`、`sqlstate` 异常属性
  （psycopg3 全版本支持）均为长期稳定特性；
- 版本演进跟随回归：升级镜像前先跑全量 + parity（对齐二十一期 spec §8.1）；
- 运行时账号权限模型与建仓账号分离（见 D11 与 §6 注释）。

## 9. 风险与开放问题

| # | 风险 | 缓解 |
|---|------|------|
| R1 | 本地无 PG 实例，parity 仅 CI 验证（用户已确认此模式） | CI 断言输出带行数对照与 diff 明细；init 失败携带原始 DDL；正则/异常归一用纯单测与真库解耦 |
| R2 | PG EXPLAIN 文本格式随版本漂移 | 正则只锚 `actual` 段；版本锚定 postgres:16；不可计量时退化不熔断（留痕） |
| R3 | sqlglot duckdb→postgres DDL 转译盲区 | 逐表存在性校验 + 失败自诊断；CI 首跑即暴露，修复成本低 |
| R4 | psycopg3 类型往返（Decimal/timestamp/NaN）与 `normalize_cell` 对齐 | 纯单测锁定归一行为；parity 数值双口径容差兜底 |
| R5 | CI 首跑 parity 失败迭代轮次不可控 | init 与 parity 步骤分离，失败定位面窄；镜像是本地可复现的（docker run 一条命令） |

## 10. 成功标准

1. 本地 `black --check .` / `ruff check .` / `pytest -q` 全绿（PG parity
   本地自动跳过）；
2. CI 三个 job（test / mysql-parity / postgres-parity）全绿；
   **postgres-parity 首绿即 PG 链路首次真实验证通过**；
3. duckdb / mysql 既有链路零破坏（既有 CI job 结果不变绿）；
4. `python -m mock.init_postgres` 行数对照与 DuckDB 版一致。
