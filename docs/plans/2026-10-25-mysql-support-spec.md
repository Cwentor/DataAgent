# 设计方案｜MySQL 数据库支持（二十一期，2026-10）

> 状态：设计定稿，待实现。实施计划见 `2026-10-25-mysql-support-plan.md`，
> 任务跟踪见 `2026-10-25-mysql-support-tasks.md`。
> 分支：`feat/mysql-support`。

## 1. 背景与目标

DataAgent 当前执行层只支持 DuckDB（单文件本地数仓）。为接入企业既有
MySQL 数仓，本项目新增 MySQL 8.0 作为可选查询执行后端，保持既有
治理管道（只读白名单 / RLS / 资源熔断 / 审计）**全量、无降级**地适用于
两个后端。

**目标（本期范围内）**：

1. `DB_BACKEND=duckdb|mysql` 配置切换，默认 `duckdb` 零破坏；
2. DSL -> SQL 编译器按后端产出对应方言 SQL（确定性、无注入不变）；
3. 执行层三护栏（statement_timeout / 扫描行熔断 / LIMIT 硬上限）+
   执行前审计门在 MySQL 上等价生效；
4. 探索层（L3）sec_* 安全面在 MySQL 上以**内联子查询**形式等价实现；
5. mock Gmall 数仓可一键初始化到 MySQL（确定性三原则不变：种子 42、
   AS_OF_DATE=2025-12-31、行数/指纹与 DuckDB 版一致）；
6. 双后端一致性测试：同一 DSL 在两后端执行，规范化结果集必须一致。

**非目标（本期不做）**：

- 不做 MySQL 5.7 / MariaDB 兼容（基线 MySQL >= 8.0.18，见 §8）；
- 不做运行期多后端同时混查（一次进程一个后端，由配置决定）；
- 不做 golden 评测集的 MySQL 方言重写（golden 锚定 DuckDB 方言不变，
  MySQL 正确性由双后端一致性测试承担，见 §7）；
- 审计存储 `logs/audit.duckdb` 与 SQLite KV 状态外置不迁移（与查询
  引擎解耦的内部实现，保持不动）；
- 不做 MySQL 侧的 Parquet 导出链路改动（导出读的是执行结果 DataFrame，
  经类型规范化层后与后端无关）。

## 2. 现状耦合点清单（调研事实）

以下为 2026-10-25 在 master（fc6b52b）上的逐点调研结论，是全部改造面：

| # | 位置 | 耦合事实 |
|---|------|---------|
| C1 | `exec/pool.py:48` | `duckdb.connect(db_path, read_only=True)`；池类型签名 `duckdb.DuckDBPyConnection` |
| C2 | `exec/guards.py:373-414` | 看门狗用 `conn.execute(sql)` + 超时 `conn.interrupt()`（DuckDB 特有） |
| C3 | `exec/guards.py:303-313` | 扫描行预检依赖 DuckDB `EXPLAIN ANALYZE` 计划文本的 `N rows` 渲染 |
| C4 | `exec/guards.py:223,468,503` | sqlglot 以 `read="duckdb"` 解析；异常捕获 `duckdb.Error` |
| C5 | `exec/guards.py:92-151` | 语句/函数黑名单针对 DuckDB 表函数（read_* 等）；MySQL 需增量（`SLEEP`/`BENCHMARK`/`LOAD_FILE`/`USE`/`KILL` 等） |
| C6 | `compiler/sql_compiler.py:62-69` | 标识符一律双引号包裹（MySQL 默认 sql_mode 下双引号是字符串） |
| C7 | `compiler/sql_compiler.py:382` | 时间维度 `date_trunc('g', col)`——MySQL 无 `date_trunc` 函数 |
| C8 | `compiler/sql_compiler.py:236,271` | `TIMESTAMP '...'` 字面量（MySQL TIMESTAMP 有时区转换语义，与 DATETIME 列比较有坑） |
| C9 | `compiler/sql_compiler.py:543-552` | comparison 对齐用 `date_add(expr, INTERVAL n MONTH)`（MySQL 同名函数语义兼容，需验证转译） |
| C10 | `compiler/sql_compiler.py:784-788` | fill_gaps spine 用 `generate_series(...)` + `UNNEST(...)`（DuckDB 表函数，MySQL 不存在） |
| C11 | `security/views.py:41,196-207` | 会话级安全视图用 `CREATE OR REPLACE TEMP VIEW`——**MySQL 不支持临时视图** |
| C12 | `security/views.py:210-232` | `harden_connection` 设 `enable_external_access` / `lock_configuration`（DuckDB 专属设置） |
| C13 | `semantic/catalog_loader.py:132-156` | 物理面读 DuckDB `information_schema.columns`；`_map_dtype` 只登记 DuckDB 类型名 |
| C14 | `mock/gmall/build.py:45` | 灌数据用 `INSERT INTO t SELECT * FROM df`（DuckDB pandas scan）；`mock/gmall/schema.py` 为 DuckDB DDL |
| C15 | `core/retrieval/exploration.py:56,78,95` | 探索层 sqlglot 以 `dialect="duckdb"` 解析与回写 |
| C16 | `web/service.py:654` | 初始化端点直调 `mock.init_duckdb.main` |
| C17 | `eval/eval_runner.py:158` | golden 比对 SQL 文本（DuckDB 方言）与结果快照 |
| C18 | `tests/conftest.py:19-21` | session 级内存 DuckDB fixture 灌 mock 数仓 |

除上述外，`core/retrieval/tools.py`、`tools/builtins/_query_core.py`、
`web/service.py` 均只消费 `exec.pool.default_pool()` 与
`exec.guards.execute_sql(conn, sql, ...)` 两个入口——**消费方零改动**
即可获得多后端能力，这是本设计选择在 `exec/` 内做抽象层的依据。

## 3. 总体架构：执行引擎抽象层 + 编译方言层

```
                         ┌────────────────────────────────┐
 web / orchestrator /    │ exec.pool.default_pool()       │  按 DB_BACKEND 构造
 tools / exploration ───>│ exec.guards.execute_sql()      │  护栏框架（引擎无关）
                         └───────────┬────────────────────┘
                                     │ EngineAdapter 协议
                     ┌───────────────┴────────────────┐
                     │                                │
          exec/engine/duckdb_adapter.py   exec/engine/mysql_adapter.py
          （现有逻辑下沉，行为不变）        （PyMySQL 新实现）

 compiler/sql_compiler.py ──> compiler/dialect/base.py（DialectRenderer 协议）
                             ├── duckdb.py（现有渲染，行为不变）
                             └── mysql.py（反引号/date_trunc 映射/递归 spine/…）
```

分层原则：

- **护栏框架引擎无关**：只读白名单四层防线、审计门、LIMIT 硬上限、
  扫描缓存、超时看门狗的线程框架保留在 `exec/guards.py`；引擎差异
  （连接、执行、取消、计划解析、错误归一、加固）全部下沉 adapter。
- **编译器结构引擎无关**：DSL -> SQL 的结构组装（FROM/JOIN/GROUP BY/
  窗口/CTE）不动；只有"方言原语"（标识符引号、date_trunc、时间字面量、
  序列 spine、布尔/转义）经 DialectRenderer 渲染。
- **单一事实源不变**：`semantic.json` / `semantic.catalog` 与物理引擎
  完全解耦（逻辑字段映射的物理表/列名两后端一致——MySQL 数仓按同一
  schema 初始化），目录层不做方言分支。

## 4. 关键决策（D1-D15）

### D1 引擎抽象：`exec/engine/` Adapter 协议

新建 `exec/engine/base.py` 定义 `EngineAdapter`（`typing.Protocol`）：

| 成员 | 职责 |
|------|------|
| `name: str` | `"duckdb"` / `"mysql"` |
| `dialect: str` | sqlglot 方言名（白名单解析、探索层回写共用） |
| `connect_readonly() -> Conn` | 建只读连接（DuckDB: `connect(read_only=True)`；MySQL: 只读账号 + 会话加固） |
| `run_query(conn, sql) -> tuple[list[str], list[list[Any]]]` | 单条 SELECT 执行，返回 (columns, rows)（内部自管 cursor） |
| `cancel(conn) -> None` | 超时时尽力取消（DuckDB: `interrupt()`；MySQL: 服务端已自限，空操作） |
| `explain_scan_rows(conn, sql) -> int` | 预检扫描行数（各引擎解析自家计划文本） |
| `engine_error(exc) -> SqlExecutionError \| None` | 引擎异常归一（DuckDB: `duckdb.Error`；MySQL: `pymysql.err.Error`，3024 归 `QueryTimeoutError`） |
| `harden(conn) -> None` | 连接级加固（见 D10） |
| `insert_dataframe(conn, table, df)` | 仅建仓链路使用（见 D12） |

`exec/pool.py` 池改为按 `settings.DB_BACKEND` 委托 adapter 建连接；
`ReadOnlyConnectionPool` 的排队/复用框架不变。`execute_sql` 的签名
`execute_sql(conn, sql, *, statement_timeout_ms, max_scan_rows,
max_result_rows)` **不变**（连接对象本身 duck-tpye 满足协议即可），
所有既有消费方（C18 之外的 web/tools/retrieval/exploration）零改动。

### D2 MySQL 超时取消：服务端 `max_execution_time` 为主

MySQL 无 `conn.interrupt()`。采用**服务端超时为主、客户端等待兜底**：

1. 连接初始化 `SET SESSION max_execution_time = <QUERY_TIMEOUT_MS>`
   （MySQL 8.0.1+，仅对 SELECT 生效——恰好我们只允许单条 SELECT）；
2. 超时服务端报错误码 3024（ER_QUERY_TIMEOUT），adapter 归一为
   `QueryTimeoutError`，语义与 DuckDB 的 InterruptException 对齐；
3. 看门狗线程框架保留（防网络挂起）：pymysql 连接参数
   `read_timeout = QUERY_TIMEOUT_MS/1000 + 5`（余量保证服务端先超时，
   连接可复用不报废）；看门狗超时触发时 `cancel(conn)` 为空操作，
   仅等待 worker 返回并归一异常。
4. 否决 `KILL QUERY` 方案：需要第二个管理连接 + PROCESS 权限，且
   竞态复杂（连接复用后 kill 可能误杀下一条查询）；服务端超时已覆盖。

### D3 MySQL 扫描行熔断：`EXPLAIN ANALYZE`（8.0.18+）解析

MySQL 8.0.18+ 的 `EXPLAIN ANALYZE` 同样实际执行查询并输出树形文本，
每算子含 `(actual time=0.10..0.12 rows=1234 loops=1)`。口径与 DuckDB
版对齐：**取所有算子 `actual rows` 的最大值**作为扫描行数预算；解析
失败（无 actual 段）按 DuckDB 版同语义记 warning 留痕（复用
`plan_rows_measurable` 概念，adapter 各自实现）。既有扫描缓存
（`cached_scan_rows`）直接复用（key 是规范化 SQL 文本，引擎无关）。

备选 `EXPLAIN FORMAT=JSON`（估算行数、不实际执行）被否决：口径是估算
而非实际，与 DuckDB 版"先计量后放行"语义不一致，且估算偏差可能放大
（直方图缺失时 MySQL 估算劣化明显）。

### D4 编译器方言层：`compiler/dialect/`

新建 `DialectRenderer` 协议（`compiler/dialect/base.py`），编译器所有
方言原语经 `get_renderer()` 获取（由 `settings.SQL_DIALECT` 决定，缺省
跟随 `DB_BACKEND` 推导，允许独立覆盖以便单测）：

```python
class DialectRenderer(Protocol):
    name: str
    def quote_ident(self, name: str) -> str: ...
    def quote_column(self, alias: str, column: str) -> str: ...   # 限定列
    def timestamp_literal(self, dt: datetime) -> str: ...
    def date_trunc(self, granularity: str, expr: str) -> str: ... # 时间维度截断
    def spine_cte_sql(self, alias: str, start: str, end: str, step: str) -> str: ...
    def with_keyword(self) -> str: ...                            # WITH / WITH RECURSIVE
    def escape_string_literal(self, value: str) -> str: ...
    def bool_literal(self, value: bool) -> str: ...
    def identifier_literal(self, name: str) -> str: ...
```

`sql_compiler.py` 的 `_quote_ident` / `_qualify` / `render_literal` /
`_dimension_expr` / `_time_window_sql` / fill_gaps spine 全部改为委托
renderer。comparison 对齐用的 `date_add(expr, INTERVAL n MONTH)` **无需
方言化**：MySQL 函数名大小写不敏感，`date_add` 即 `DATE_ADD`，且
`INTERVAL n MONTH` 字面量两方言语法一致、语义等价。**输出字符串在
DuckDB 后端必须与现状逐字节一致**（golden 锚点，重构期以全量测试锁定）。

### D5 `date_trunc` 的 MySQL 映射（保持 DATE 类型返回）

MySQL 无 `date_trunc`。为保证 GROUP BY / JOIN 配对 / 排序语义与
DuckDB 一致且**返回 DATE 类型**（不用 `DATE_FORMAT` 字符串，避免
fill_gaps spine 对齐时类型不一致），采用纯日期运算等价式：

| granularity | DuckDB | MySQL 等价式 |
|---|---|---|
| day | `date_trunc('day', e)` | `DATE(e)` |
| week | `date_trunc('week', e)` | `DATE_SUB(DATE(e), INTERVAL WEEKDAY(DATE(e)) DAY)` |
| month | `date_trunc('month', e)` | `DATE_SUB(DATE(e), INTERVAL DAYOFMONTH(DATE(e)) - 1 DAY)` |
| quarter | `date_trunc('quarter', e)` | `MAKEDATE(YEAR(e), 1) + INTERVAL (QUARTER(e) - 1) * 3 MONTH` |

**周起始语义对齐**：DuckDB `date_trunc('week')` 周一开头；MySQL
`WEEKDAY()` 返回 0=周一，上表 week 表达式与之口径一致（不得使用
`YEARWEEK`/`WEEK()`，其默认 mode 周日开头）。此口径写入双后端一致性
测试用例（跨周边界日期必须覆盖）。

### D6 fill_gaps spine：MySQL 用 `WITH RECURSIVE`

`generate_series` + `UNNEST` 是 DuckDB 表函数。MySQL 8.0 用递归 CTE：

```sql
__spine AS (
    SELECT CAST('<start>' AS DATETIME) AS <time_alias>
    UNION ALL
    SELECT <time_alias> + INTERVAL 1 DAY FROM __spine
    WHERE <time_alias> + INTERVAL 1 DAY <= CAST('<end_incl>' AS DATETIME)
)
```

**注意**：MySQL 递归 CTE 默认深度上限 `cte_max_recursion_depth=1000`，
day 粒度 5 年数据域（1826 天）会打穿——连接初始化时
`SET SESSION cte_max_recursion_depth = 100000`（D10 加固清单一并执行）。

### D7 标识符引号：MySQL 用反引号

`quote_ident`/`quote_column` 在 MySQL 渲染为 `` `alias`.`col` ``（内部
反引号翻倍转义）。**不依赖** `ANSI_QUOTES` sql_mode（部署环境不受控，
显式反引号在两种 mode 下都正确）。

### D8 字符串/时间字面量：反斜杠双保险 + DATETIME 显式 CAST

- **反斜杠**：MySQL 默认 sql_mode 下 `\` 是转义字符，`'a\b'` 中的 `\b`
  会被吃掉、构造出注入面（值被改写甚至改变字符串边界）。双保险：
  1. MySQL 连接初始化 `SET SESSION sql_mode = CONCAT(@@sql_mode, ',NO_BACKSLASH_ESCAPES')`；
  2. renderer 的 `escape_string_literal` 在 MySQL 方言额外把 `\` 翻倍。
  两道防线独立生效，任一被部署环境覆盖（如 init_command 冲突）另一道
  仍兜底。
- **时间字面量**：MySQL `TIMESTAMP 'str'` 有时区转换语义，与 DATETIME
  列比较有隐式转换坑。MySQL 渲染为 `CAST('2025-01-01 00:00:00' AS DATETIME)`
  显式类型（D14 的 DDL 中时间列统一用 DATETIME，两侧无时区参与）。
- 布尔字面量：MySQL `TRUE`/`FALSE` 即 1/0，直接复用现状渲染。

### D9 探索层安全面：MySQL 无临时视图 -> 内联派生表（derived table）

MySQL **不支持 TEMPORARY VIEW**（也不支持 `CREATE OR REPLACE VIEW`
与 TEMPORARY 组合；MariaDB 才支持）。且持久视图方案需要 CREATE 权限、
污染系统目录、并发会话要靠命名隔离——全部否决。

采用**查询重写内联**：`rewrite_sql_to_views` 把基表引用替换为派生表：

```sql
-- DuckDB（现状）：FROM sec_order_detail
-- MySQL（本期）：FROM (
       SELECT f.`order_id` AS `order_id`, ..., f.`province` AS `province`
       FROM order_detail f JOIN base_province p ON ...   -- 受控连接 + 禁列物理缺席
       WHERE f.`province` = '广东'                        -- RLS 固化
   ) AS sec_order_detail
```

实现上把 `security/views.py` 的 `build_secure_views` 拆出纯函数
`build_secure_projection(table, policy) -> SecureProjection`
（select 投影 + from/join/where 片段）：DuckDB 版拼 `CREATE TEMP VIEW`
DDL，MySQL 版拼派生表子查询，**同一投影/RLS 逻辑单一实现**，双保险
交叉验证测试（tests/security/test_security_views.py）扩展为两后端同跑。

### D10 MySQL 连接加固清单（harden）

只读与安全不依赖单一手段，分层：

1. **账号层**（部署要求，文档写明）：数仓账号仅授 `SELECT ON <db>.*`，
   无 DDL/DML/PROCESS/SUPER/File 权限；
2. **会话层**（adapter `harden` 逐一执行，失败 fail-closed 抛
   SecurityError）：
   - `SET SESSION TRANSACTION READ ONLY`；
   - `SET SESSION sql_mode = CONCAT(@@sql_mode, ',NO_BACKSLASH_ESCAPES')`；
   - `SET SESSION cte_max_recursion_depth = 100000`（D6）；
   - `SET SESSION max_execution_time = <QUERY_TIMEOUT_MS>`（D2）。
3. **驱动层**：`charset=utf8mb4`、`autocommit=True`（只读会话不留事务）、
   `connect_timeout=5`、`read_timeout=QUERY_TIMEOUT_MS/1000 + 5`（D2）。

DuckDB adapter 的 `harden` 保持现状（enable_external_access +
lock_configuration）。

### D11 只读白名单 MySQL 增量

四层防线框架不动，增量按方言表驱动：

- 语句黑名单 MySQL 追加：`USE`（切库）、`SHOW`、`KILL`、`PREPARE`、
  `EXECUTE`、`DEALLOCATE`、`LOAD`、`START`、`XA`、`FLUSH`、`ANALYZE`、
  `OPTIMIZE`、`RENAME`、`TRUNCATE`、`GRANT`、`REVOKE`、`HANDLER`、
  `DO`、`SHUTDOWN`、`PURGE`、`HELP`；
- 函数黑名单 MySQL 追加：`load_file`（读服务器文件）、`sleep`、
  `benchmark`（DoS）、`get_lock`/`release_lock`/`is_used_lock`（锁滥用）；
- `SELECT ... INTO OUTFILE/DUMPFILE` 由既有 AST `exp.Into` 拦截覆盖
  （sqlglot mysql 方言同样解析为 Into 节点，实现时以测试锁定）；
- sqlglot 解析方言 `read=adapter.dialect`（白名单/审计门/探索层三处统一改）。

### D12 mock 数仓 MySQL 初始化：`python -m mock.init_mysql`

- 新建 `mock/gmall/schema_mysql.py`：与 DuckDB 版同表同列，类型映射
  `INTEGER->INT`、`BIGINT->BIGINT`、`VARCHAR->VARCHAR(n)/TEXT`、
  `TIMESTAMP->DATETIME`（无时区语义、无 2038 问题）、`DECIMAL(p,s)` 原样；
  基表主键 + 事实表时间列/外键列补二级索引（MySQL 无 zoneMap，无索引
  则扫描熔断预检全表计数会拖慢建仓验证）；
- 灌数据：adapter 的 `insert_dataframe`（MySQL 走 pymysql `executemany`
  自动重组 multi-values，每批 5000 行分批；DuckDB 保持 `INSERT INTO t
  SELECT * FROM df` 现状）；生成器（simulate/business/logs）纯 Python
  不动——**同一 rng 顺序消费保证两侧数据逐行一致**；
- 幂等：`DROP DATABASE` / `CREATE DATABASE` / 建表灌数；
- `web/service.py` 初始化端点按后端分发到 `mock.init_duckdb` /
  `mock.init_mysql`。

### D13 结果类型规范化层（防类型漂移打穿质检/序列化）

pymysql 与 duckdb 返回类型不同：`DECIMAL -> decimal.Decimal`（DuckDB
float）、`DATETIME -> datetime`、`TINYINT(1) -> int`（DuckDB bool）、
无 None 差异。新增 `exec/engine/normalize.py::normalize_cell(value)`：
`Decimal -> float`（quantize 到 float 安全位数）、`bytes -> 拒绝`（审计
留痕并置 None，正常链路不应出现）、`date/datetime -> 原样`（序列化层已
处理）。适配在 MySQL adapter `run_query` 返回前统一执行，使
`execute_sql` 之上的全链路（quality 断言 / ParquetRef / web JSON /
快照比对）无需感知后端。质检层负值断言对 `Decimal->float` 后语义不变。

### D14 评测策略：golden 锚定 DuckDB，MySQL 走双后端一致性测试

- `eval_runner` / golden_dataset.json 不改（SQL 文本与快照都是 DuckDB
  方言/结果锚点）；
- 新增 `tests/test_dual_backend_parity.py`：遍历 golden_dataset 的 DSL，
  在 DuckDB 与 MySQL 两个后端各自编译+执行，**规范化结果集必须一致**
  （含 fill_gaps 补零序列、comparison 按位配对、TopN 序号边界等方言
  敏感形态）；无本地 MySQL 时整文件 skipif 标记跳过；
- `eval_runner` 增加 `--backend` 透传（默认 duckdb）仅供手工排查，
  MySQL 后端下 SQL 文本比对自动置 skipped（不产红）。

### D15 依赖与许可：PyMySQL（MIT）

`requirements.txt` 增加 `pymysql>=1.1.1,<2`。选 PyMySQL 而非
mysql-connector-python：纯 Python 零编译依赖、MIT 许可（mysql-connector
为 GPL 系，商用分发有顾虑）、DB-API 接口一致。SQLAlchemy 不引入
（YAGNI：我们只需要执行通道，不需要 ORM/连接池框架）。

## 5. 安全模型对照（治理管道等价性）

| 治理面 | DuckDB（现状） | MySQL（本期） |
|---|---|---|
| 只读 | 文件 read_only 连接 | 只读账号 + `SET SESSION TRANSACTION READ ONLY` + 四层白名单（增量 D11） |
| 注入 | 字面量严格转义 + 标识符双引号 | 反斜杠双保险（D8）+ 反引号（D7）+ 同一套白名单 |
| 资源 | interrupt 看门狗 + EXPLAIN ANALYZE 熔断 + LIMIT 硬上限 | 服务端 max_execution_time（D2）+ EXPLAIN ANALYZE 解析（D3）+ 同一 LIMIT 硬上限 |
| RLS/禁列 | 编译器注入 + sec_* TEMP VIEW 双保险 | 编译器注入（不变）+ sec_* 内联派生表（D9）双保险 |
| 探索层连接 | TEMP VIEW 安装 + DuckDB 加固 | 派生表重写（无 DDL）+ MySQL 会话加固（D10） |
| 审计 | audit.jsonl + audit.duckdb（不变） | 不变（审计存储与查询引擎解耦） |

铁律重申：LLM 产出的 SQL 永不原文执行的架构不变；MySQL 侧探索 SQL
同样先经 sqlglot 重写为派生表形态才进 `execute_sql`。

## 6. 配置设计

`.env` / 环境变量新增（`config/settings.py`）：

```python
DB_BACKEND = "duckdb"            # duckdb | mysql
MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_USER = "dataagent_ro"      # 只读账号（部署要求，见 D10）
MYSQL_PASSWORD = ""              # 环境变量注入，不落 .env 仓库
MYSQL_DATABASE = "gmall"
SQL_DIALECT = ""                 # 缺省跟随 DB_BACKEND 推导；可独立覆盖（测试用）
```

既有 `QUERY_TIMEOUT_MS` / `MAX_SCAN_ROWS` / `MAX_RESULT_ROWS` /
`DB_POOL_SIZE` / `MAX_CONCURRENT_QUERIES` 两后端共用（语义一致）。

## 7. 确定性与可复现

- mock 生成器不动：种子 42、AS_OF_DATE=2025-12-31、单 rng 顺序消费，
  两后端数据逐行一致（由 `insert_dataframe` 消费同一 DataFrame）；
- `mock/init_mysql` 完成后跑行数对照（每表 count 与 DuckDB 版一致），
  对照脚本纳入任务验收；
- eval 锚点（golden_dataset / intent_golden）不动。

## 8. 版本基线与部署要求

- MySQL >= 8.0.18（EXPLAIN ANALYZE 8.0.18 / 递归 CTE 与窗口函数 8.0 /
  max_execution_time 8.0.1）；推荐 8.0 稳定系，8.4 未纳入回归；
- 数仓账号最小权限：`GRANT SELECT ON <db>.* TO <user>`（禁止 File /
  Process / Super）；
- 字符集 utf8mb4（中文标签/维度成员一致）；
- 本地开发：`docker run -e MYSQL_ROOT_PASSWORD=... -p 3306:3306
  mysql:8.0` 即可，一致性与冒烟测试对无 MySQL 环境自动 skip。

### 8.1 版本演进跟随策略（已批准的注意点，2026-10-25）

**项目必须随 MySQL 版本更新一同演进，这是采纳本设计的不可避免代价。**
治理管道对引擎的依赖是深层的（计划文本计量、服务端超时、sql_mode
默认值、优化器行为），MySQL 升级不可能"零成本免费获得"。既有教训：
DuckDB 2.0 计划渲染变更曾打穿扫描行熔断（2026-10-22 CI 事故，
requirements 被迫锁定主版本上界）——MySQL 侧同类风险只多不少。

跟随策略（写入 README 部署要求，随 Task 10 落地）：

1. **声明支持区间**：README 与 requirements 注释声明本期支持
   `mysql 8.0（>= 8.0.18）`；8.4 / 9.x 未纳入回归，不在支持区间；
2. **格式漂移可探测**：`parse_mysql_scan_rows` 以真实输出样例文本的
   单测锁定（Task 4），MySQL 升级后跑测试即可暴露 EXPLAIN ANALYZE
   格式漂移（不可计量路径只 warning 不熔断，不会静默错杀）；
3. **升级准入流程**：跨版本升级（8.0.x 小版本、8.0→8.4 大版本）须先跑
   全量 pytest + 双后端一致性测试（Task 10）+ 手工核验
   `EXPLAIN ANALYZE` 样例，全绿后再变更支持区间声明；
4. **敏感面登记**：对版本敏感的面集中在 EXPLAIN ANALYZE 输出格式、
   `max_execution_time` 语义、`sql_mode`/`cte_max_recursion_depth`
   默认值——升级评审按此清单逐项核对（§9 风险表第 1 条）。

## 9. 风险与开放问题

| 风险 | 影响 | 缓解 |
|---|---|---|
| MySQL 版本演进（EXPLAIN ANALYZE 输出格式 / sql_mode 默认值 / 优化器行为随版本变化） | 扫描熔断失效或误报、加固被部署默认值架空 | **已批准为必然代价**（§8.1）：声明支持区间 + 样例单测锁格式 + 升级准入流程（全量回归 + 双后端一致性）|
| week 口径漂移（MySQL WEEK mode 众多） | 周粒度结果两后端不一致 | D5 明确用 WEEKDAY（周一锚）；双后端一致性测试含跨周边界用例 |
| Decimal 精度：DuckDB float vs MySQL DECIMAL | 快照比对/质检数值漂移 | D13 统一 float 化；mock 金额字段 DECIMAL(16,2) 在 float 化后误差 < 1e-9，一致性测试容差按 1e-6 相对误差 |
| 大表无索引导致 EXPLAIN ANALYZE 预检本身超时 | 预检被自身超时拦截 | 预检与执行共用超时预算（现状语义）；DDL 补索引（D12）降低预检成本 |
| 递归 CTE 深度不足（部署覆盖 cte_max_recursion_depth） | fill_gaps 大窗口报错 | 会话级 SET 优先（D10）；开放问题：若运维用 init_command 覆盖，需在错误归一中把 3636（递归深度）翻译为可自愈报错提示显式窗口 |
| 池归还时会话变量被上一查询改写 | 加固失效 | 只读链路不执行任何 SET；所有会话 SET 仅在连接创建时执行一次 |
