# 设计方案｜物理元数据面下沉 EngineAdapter（二十四期，2026-10）

> 状态：设计定稿，待规格审阅。实施计划见
> `../plans/2026-10-26-physical-columns-adapter-plan.md`，任务记录见
> `../tasks/2026-10-26-physical-columns-adapter-tasks.md`（对齐二十二/二十三期
> 先例与五类目录命名规范，见 `docs/README.md`）。
> 分支：`feat/physical-columns-adapter`（自 master 9c460fd 切出）。

## 1. 背景与目标

### 1.1 缺口的准确表述

二十三期（9c460fd）合入后，`DB_BACKEND` 已有四值（`duckdb|mysql|postgres|
clickhouse`），**执行面**与**编译方言面**都已完全后端化：`exec/engine/` 的
`EngineAdapter` 协议 + 注册表、`compiler/dialect/` 的 `DialectRenderer` 协议
+ 注册表。但**物理元数据面**（语义目录从哪里读"库里有哪些表、哪些列、什么
类型"）仍停在二十一期前的形态：

```python
# semantic/catalog_loader.py:484-493（现状）
elif settings.DB_BACKEND == "mysql":          # ← 只特判 mysql
    own_conn = MySqlAdapter().connect_readonly()
    physical = _physical_columns(own_conn, schema=settings.MYSQL_DATABASE)
elif settings.DB_PATH.is_file():              # ← postgres/clickhouse 落到这里
    own_conn = duckdb.connect(str(settings.DB_PATH), read_only=True)
    physical = _physical_columns(own_conn)    # ← 读的是本地 DuckDB 文件
```

`web/service.py:650-673` 的 `ensure_db()` 同样只特判 `mysql`：postgres /
clickhouse 后端下，若 `DB_PATH` 文件不存在，它会去跑 `mock.init_duckdb` 建
一个**本地 DuckDB 文件**，再从这个文件读物理列——而后续查询打的是 PG/CH。

即：**目录的物理列来源（本地 DuckDB 文件）与实际执行库（PG/CH）是两个不同的
库**，且二者之间没有任何一致性校验。

### 1.2 后果分级

| 场景 | 表现 | 级别 |
|------|------|------|
| PG/CH 库与 DuckDB 建仓同构（当前 CI / mock 现状） | 物理列恰好一致，链路"看起来正常" | **静默通过**（最危险） |
| 运维在 PG/CH 侧加列 / 改列类型（不经 DuckDB） | 目录仍是旧形态：新列不可查（逻辑字段缺失）、`dtype` 过时导致字面量转义错（`str` 与 `int` 分支不同） | 静默错误 |
| PG/CH 侧改名 / 删列 | 编译器产出引用不存在列的 SQL，运行时才报错 | 显式失败（但错误归因困难） |
| `DB_PATH` 缺失 + PG/CH 后端 | `ensure_db` 建出一个无意义的本地 DuckDB 文件，目录来自它 | 静默错误 + 副作用 |
| 维度成员词汇表（`DIMENSION_MEMBERS`） | 从本地 DuckDB 读 distinct 值，与 PG/CH 实际数据可能不同 → NL 理解层的值映射错 | 静默错误 |

### 1.3 为什么至今未暴露（四条独立原因）

1. **默认后端是 `duckdb`**：本地开发、CI `test` job（1241 用例）全部走
   DuckDB 分支，PG/CH 分支没有测试覆盖。
2. **parity 测试绕过目录层**：`tests/test_{dual,pg,clickhouse}_backend_parity.py`
   直接 `compile_dsl()` + `execute_sql()`，**不经过 `ensure_db` /
   `build_catalog`**，因此物理列来源是否正确不在其覆盖范围内。
3. **CI 不启动 Web 服务**：`mysql-parity` / `postgres-parity` /
   `clickhouse-parity` 三个 job 只建仓 + 跑 parity pytest，`ensure_db()` 这条
   服务启动路径在 PG/CH 上**从未被执行过**。
4. **`?` 占位符问题被 fake 连接掩盖**：`_physical_columns` 的 schema 过滤分支
   （`catalog_loader.py:180-181`）用 `?` 占位符，而 `pymysql` / `psycopg` 的
   参数风格都是 `%s`（`execute(sql, args)` 内部走 `query % escaped_args`，
   SQL 里没有 `%s` 却给了 args → `TypeError: not all arguments converted
   during string formatting`）。单测 `tests/semantic/test_catalog_loader.py:507`
   用自造 `_Conn`（有 `execute` 方法）验证，恰好绕过了真实驱动的参数化语义。

> 结论：这条链路上**不止"PG/CH 未接"一个洞**，MySQL 分支的真实参数化也从未
> 在真库上跑通过。本期一并收口。

### 1.4 目标与非目标

**目标**：

1. 物理列读取**下沉到 `EngineAdapter`**：新增协议方法
   `physical_columns(conn=None) -> dict[str, dict[str, str]]`，四后端各自实现
   自己的元数据源（DuckDB `information_schema`、MySQL `information_schema` +
   库过滤、PG `information_schema` + schema 过滤、CH `system.columns`）；
2. 物理类型 → 目录 `dtype` 的映射**收敛到共享模块** `exec/engine/type_map.py`，
   四后端类型清单显式枚举、未知类型 fail-closed（沿用现状 `ValueError` 语义）；
3. `catalog_loader` 与具体驱动解耦：不再 `import duckdb` 做元数据、不再持有
   `_DTYPE_MAP`、不再拼 `?` 占位符；维度成员词汇表改经 `adapter.run_query`；
4. `ensure_db()` 按后端显式分派：**只有 `duckdb` 后端建仓**，其余三后端建仓
   是显式运维动作（`python -m mock.init_{mysql,postgres,clickhouse}`），服务
   启动只刷新目录/策略，库不可用即快速失败；
5. 默认 `duckdb` 行为**零变更**：`build_catalog(conn=...)` 的测试路径输出
   逐字段一致（列名 / dtype / 表归属全等），golden 与 1241 用例全绿。

**非目标（本期不做）**：

- 不改 `config/semantic.json` 覆写契约、DSL 契约、`semantic/catalog.py` 的
  模块级容器结构；
- 不做运行期多后端混查；不做跨库联邦元数据；
- 不引入元数据缓存（每次 `refresh_catalog` 实读一次，见 D7 的失败语义）；
- 不重构 `mock/gmall/schema_*.py` 的建仓链路（建仓与读元数据是两条路径）；
- 不动 `security/policy_loader.py`（已确认无后端耦合，纯配置读取）。

## 2. 现状耦合点清单（调研事实）

2026-10-26 在 master（9c460fd）逐点调研，即本期全部改造面：

| # | 位置 | 事实 |
|---|------|------|
| C1 | `web/service.py:650-673` | `ensure_db()` 只特判 `mysql`：mysql 走"只刷目录/策略不建仓"；其余（含 postgres/clickhouse）落到 `if not DB_PATH.exists(): init_duckdb()` + `refresh_catalog(db_path=DB_PATH)` |
| C2 | `semantic/catalog_loader.py:170-189` | `_physical_columns(conn, schema=None)` 硬编码 `information_schema.columns` + `?` 占位符；PG/CH 的元数据源与之不同（CH 无 `information_schema`，需 `system.columns`） |
| C3 | `semantic/catalog_loader.py:142-151` | `_fetch_all` 双兼容（`hasattr(conn, "execute")` 走直用，否则走 `cursor()`）；**clickhouse-connect 的 `Client` 两者皆无**（只有 `query()`），故 CH 必走第三条通道 |
| C4 | `semantic/catalog_loader.py:75-107` | `_DTYPE_MAP` 43 项：DuckDB 类型族 + MySQL 类型族 + 一个 `DATE32`；**缺 PG（`numeric`/`character varying`/`timestamp without time zone`/`boolean`/`bigint` 大小写）与 CH（`UInt64`/`String`/`Nullable(...)`/`LowCardinality(...)`/`DateTime64(3)`/`Decimal(16, 2)` 带空格）** |
| C5 | `semantic/catalog_loader.py:161-167` | `_map_dtype` 对未知类型**直接 raise**（fail-closed），故 C4 的缺口在 PG/CH 上表现为"读元数据即崩"，而非静默 |
| C6 | `semantic/catalog_loader.py:192-213` | `_load_dimension_members(conn, ...)` 走 `_fetch_all` + `_quote()`（方言渲染器），同样是 DuckDB 连接假设 |
| C7 | `semantic/catalog_loader.py:464-510` | `build_catalog(db_path, conn, overlay_path)` 三参数签名：`conn` 为测试注入（内存 DuckDB），`db_path` 为运维 CLI（`python -m semantic.catalog_loader --db`） |
| C8 | `semantic/catalog_loader.py:513-565` | `refresh_catalog()` 把构建结果原地写回 `semantic.catalog` 模块级容器（消费方动态读取，刷新即时生效）——本期不改该机制 |
| C9 | `exec/engine/base.py:16-55` | `EngineAdapter` 协议现 7 方法（`connect_readonly`/`run_query`/`cancel`/`explain_scan_rows`/`engine_error`/`harden`/`insert_dataframe`）+ `name`/`dialect` 两属性；**新增 `physical_columns` 为第 8 方法** |
| C10 | 四个 adapter 的 `run_query` | 签名统一 `(conn, sql) -> (columns, rows)`：`duckdb_adapter.py:33`、`mysql_adapter.py:98`、`postgres_adapter.py:109`、`clickhouse_adapter.py:171`——**但均不接受参数绑定**（占位符方言各不相同，见 D3） |
| C11 | `config/settings.py:26-62` | `DB_PATH` + `MYSQL_*`/`POSTGRES_*`/`CLICKHOUSE_*` 三组连接配置；**无 `POSTGRES_SCHEMA`**（PG 元数据过滤需要，见 D4） |
| C12 | `tests/semantic/test_catalog_loader.py:497-551` | 现有物理面测试：`_map_dtype` 表驱动（MySQL 类型）、`_physical_columns(schema=...)` 过滤、`_physical_columns` duckdb 兼容——全部基于 fake `_Conn`，本期需随下沉迁移 |
| C13 | `.github/workflows/ci.yml:79-86, 122-129, 201-209` | 三个 parity job 的 pytest 只跑 `tests/test_*_backend_parity.py`；**无任何步骤验证 `ensure_db()` / `refresh_catalog()`** |

## 3. 总体架构

```
                       ┌──────────────────────────────────────────┐
                       │  semantic/catalog_loader.py（消费方）      │
                       │  后端无关：不 import 任何驱动、无类型映射表  │
                       │  ① adapter.physical_columns(conn)         │
                       │  ② adapter.run_query(conn, member_sql)    │
                       └───────────────┬──────────────────────────┘
                                       │ EngineAdapter 协议（第 8 方法）
        ┌──────────────────┬───────────┴────────────┬──────────────────┐
        ▼                  ▼                        ▼                  ▼
  DuckDbAdapter       MySqlAdapter            PostgresAdapter    ClickHouseAdapter
  information_schema  information_schema      information_schema  system.columns
  （无 schema 过滤）    WHERE table_schema=%s   WHERE table_schema=%s  WHERE database=%s
        │                  │                        │                  │
        └──────────────────┴───────────┬────────────┴──────────────────┘
                                       ▼
                        exec/engine/type_map.py（共享）
                        原生类型 → 项目 dtype（int/float/str/timestamp/bool）
                        剥包装：Nullable(...)/LowCardinality(...)/括号精度/大小写
                        未知类型 → ValueError（fail-closed）
```

三条不变量：

1. **单一物理源**：目录的物理列只能来自"当前 `DB_BACKEND` 指向的那个库"，
   禁止跨后端回退（D7）；
2. **引擎知识只在 adapter**：`catalog_loader` 不出现任何后端名、类型名、占位符；
3. **失败要显式**：非 duckdb 后端下库不可达 → 启动即失败（`refresh_catalog`
   抛错），严禁回退内置默认目录后"看起来能跑"。

## 4. 技术栈选型对比（选型矩阵与否决理由）

> 沿用二十三期 §4 确立的固定章节格式：每个决策点列全部实质候选（≥3）、
> 多维对比与否决理由。

### S1 物理列读取归属（对应 D1）

| 候选 | 改动面 | 后端扩展成本 | 与既有架构一致性 | 结论 |
|------|--------|-------------|-----------------|------|
| **下沉 `EngineAdapter.physical_columns()`（选定）** | 协议 +1 方法，四 adapter 各 +1 实现 | 新增后端只实现自己的元数据源 | ✅ 与"引擎差异全部由 adapter 承载"（二十一期 D1）同一原则；编译方言层已是此模式 | 复用既有注册表与惰性导入；`catalog_loader` 彻底与驱动解耦 |
| 在 `catalog_loader` 内扩 `if/elif` 四分支 | 单文件 | 每加后端改中心文件 + 加占位符/类型映射分支 | ❌ 正是二十一期前的耦合形态（本期要收的债） | 中心文件会持续膨胀，且类型映射与后端强耦合 |
| 独立 `metadata/` 模块 + 后端分派 | 新模块 + 注册表 | 需再建一套注册表 | ❌ 与 `exec/engine/` 职责重叠，形成第二套后端分派 | 双注册表是长期维护负担 |
| 统一走 `information_schema`（CH 侧造假视图） | 零代码 | — | ❌ CH 无 `information_schema`，需 DDL 建视图（只读账号无权限） | 方案不成立 |

### S2 dtype 映射归属（对应 D2）

| 候选 | 映射表位置 | 新增类型成本 | fail-closed | 结论 |
|------|-----------|-------------|-------------|------|
| **共享 `exec/engine/type_map.py`，adapter 返回项目 dtype（选定）** | 一处集中 | 加一行规则 + 一条表驱动用例 | ✅ 未知类型抛 `ValueError`（指名原始类型） | 类型知识属引擎层；四后端可复用剥壳逻辑（CH 的 `Nullable(...)` 与其他后端的括号精度同类问题） |
| 各 adapter 内自带映射表 | 分散四处 | 四处重复改 | 各自实现易漂移 | ❌ 四份 `UInt64→int` 类重复，且难以整体审计覆盖度 |
| adapter 返回**原始类型**，`catalog_loader` 映射 | 仍在 catalog_loader | 中心文件继续膨胀 | 保持 | ❌ 与 S1 结论冲突（catalog_loader 会重新认识所有后端类型名） |
| 不做映射，dtype 一律 `str` | — | — | — | ❌ 破坏字面量安全转义（`int`/`float`/`str`/`timestamp` 分支不同），且 golden 锚点失效 |

### S3 元数据查询通道（对应 D3）

| 候选 | 占位符方言 | 注入面 | 改动 | 结论 |
|------|-----------|--------|------|------|
| **各 adapter 私有 `_meta_query(sql, params)`，用驱动原生参数化（选定）** | duckdb `?` / pymysql `%s` / psycopg `%s` / CH `{db:String}` | 参数化绑定，无拼接 | 每个 adapter +8~12 行 | ✅ 元数据查询与执行层 SQL 的占位符语义本就不同，混用即 C4 的 bug；参数化保持 |
| 复用 `adapter.run_query(conn, sql)` | 需扩 `run_query` 支持参数 | 参数随驱动而异 | 协议 +1 可选参数 | ❌ 把"执行层 SQL（编译产物，无参数）"与"元数据查询（有参数）"混成一个签名，四驱动参数风格仍要在 adapter 内分派，收益为零 |
| 拼接字面量（`WHERE table_schema = 'gmall'`） | 无 | 库名来自配置（非用户输入），但仍属拼接 | 最小 | ❌ 配置可能含引号/反引号，拼接需转义；参数化是零成本更优解 |
| 只用 `current_schema()` / 默认库，不传参 | 无 | 无 | 最小 | ⚠️ PG 可用（`current_schema()`），但 MySQL/CH 的"当前库"语义不如显式参数清晰；且 PG 仍需过滤 `pg_catalog` 噪音（见 S4） |

### S4 PostgreSQL schema 过滤（对应 D4）

| 候选 | 过滤方式 | 多 schema 支持 | 结论 |
|------|---------|---------------|------|
| **新增 `POSTGRES_SCHEMA`（默认 `public`）+ `WHERE table_schema = %s`（选定）** | 显式配置 | 换 schema 改环境变量 | ✅ 与 `MYSQL_DATABASE`/`CLICKHOUSE_DATABASE` 同构（库/模式级隔离都走配置）；建仓链路写的就是 `public` |
| `WHERE table_schema = current_schema()` | 隐式 | 跟随 `search_path` | ⚠️ 可行但隐式：`search_path` 被连接参数/角色设置改变时目录会静默换源 |
| 不过滤（全 schema） | 无 | — | ❌ `information_schema.columns` 含 `pg_catalog` 等系统 schema，会污染目录（与二十一期 MySQL 实例级污染同型） |
| 过滤 `table_schema NOT IN ('pg_catalog','information_schema')` | 排除式 | 多 schema 混合 | ❌ 多 schema 同名表会冲突（`table` 为键，无 schema 维度） |

### S5 `ensure_db()` 分派（对应 D5）

| 候选 | duckdb | 非 duckdb | 结论 |
|------|--------|-----------|------|
| **按 `DB_BACKEND` 显式分派：仅 duckdb 建仓（选定）** | 文件缺失 → `init_duckdb` | 只 `refresh_catalog()` + `refresh_policies()`，库不可用即抛 | ✅ 建仓是显式运维动作（二十一/二十二/二十三期既定口径）；启动路径零副作用 |
| 现状（只特判 mysql） | 同上 | PG/CH 落到 DuckDB 分支（会建本地文件） | ❌ 本期要修的 bug 本体 |
| 给 adapter 加 `ensure_warehouse()` 钩子 | 钩子内建仓 | 钩子内空操作 | ⚠️ 方向正确但需四 adapter 各加一个方法，而"建仓"目前是 `mock/` 的职责（`mock.init_*`），下沉到 adapter 会打乱职责边界 |
| 启动时一律不建仓（含 duckdb） | 需人工 `init_duckdb` | 同 | ❌ 破坏"clone 即跑"的开发体验（现有测试与 CI 依赖） |

### S6 维度成员词汇表读取（对应 D6）

| 候选 | 通道 | 后端覆盖 | 结论 |
|------|------|---------|------|
| **`adapter.run_query(conn, sql)`（选定）** | 执行层统一通道（无参数） | 四后端已实现 | ✅ SQL 内无参数（表/列名来自目录白名单，经方言渲染器引号包裹），与 `run_query` 签名天然匹配 |
| 保留 `_fetch_all` + duckdb 直连 | 驱动直连 | 仅 duckdb | ❌ 正是本期要收的耦合 |
| 走 `execute_sql` 全护栏链路 | 护栏 + 审计 | 四后端 | ⚠️ 元数据/词汇表查询会触发扫描预算与审计门（`unbounded_output` warning 噪音），且启动期无连接池上下文；收益（防注入）已由白名单保证 |
| 不加载，固定用 `semantic.json` 的 seed | 无查询 | — | ❌ 词汇表就不再数据驱动（丢失"新增维度值无需改配置"能力） |

## 5. 关键决策（D1-D8）

### D1 物理列读取下沉 `EngineAdapter.physical_columns()`

协议新增第 8 个方法：

```python
def physical_columns(self, conn: Any | None = None) -> dict[str, dict[str, str]]:
    """读取物理元数据，返回 {表: {列: 目录 dtype}}。

    conn=None 时自建只读连接并负责关闭（服务启动路径）；传入连接时复用
    （测试/已持有连接场景）。dtype 已完成归一（见 type_map），未知类型
    fail-closed 抛 ValueError。
    """
```

- 四实现分别对应 C2/C3 的元数据源差异；表名以下划线开头的元数据表
  （`_field_metadata` 等）在**各实现内**统一跳过（保持现状语义）。
- `db_path` 语义保留：`build_catalog(db_path=...)` 仅对 duckdb 有意义
  （运维 CLI `python -m semantic.catalog_loader --db path`），非 duckdb 后端
  传入 `db_path` 显式报错（避免"传了但没用"的静默）。

### D2 dtype 映射收敛到 `exec/engine/type_map.py`

- 公开函数：`map_dtype(raw: str) -> str`，返回 `int|float|str|timestamp|bool`；
  未知类型抛 `ValueError`（文案含原始类型，便于运维补登记）。
- 归一化两阶段：**剥包装**（`Nullable(X)`/`LowCardinality(X)` 逐层、括号精度
  `Decimal(16, 2)`、大小写、`WITH TIME ZONE` 后缀）→ **查规则表**（前缀/全名）。
- 类型覆盖以**真实建仓产物**为准（不靠猜）：四后端的列类型由
  `mock/gmall/schema*.py` 决定，本期用「在 DuckDB 建仓 + 四后端 DDL 转译产物」
  枚举出完整清单，逐项写进表驱动用例（Review Focus 1）。
- `catalog_loader` 删除 `_DTYPE_MAP` / `_map_dtype`；`tests/semantic/
  test_catalog_loader.py:497-504` 的两条映射用例迁到 `tests/engine/
  test_type_map.py`。

### D3 元数据查询由 adapter 自行参数化（不经 `run_query`）

三种占位符语法并存是事实，不是可以统一掉的差异：

| 后端 | 元数据 SQL 形态 | 参数化写法 |
|------|----------------|-----------|
| duckdb | `SELECT table_name, column_name, data_type FROM information_schema.columns ORDER BY table_name, ordinal_position` | 无参数（单库，无需过滤） |
| mysql | 同上 + `WHERE table_schema = %s` | `%s`（pymysql） |
| postgres | 同上 + `WHERE table_schema = %s` | `%s`（psycopg3） |
| clickhouse | `SELECT table, name, type FROM system.columns WHERE database = {db:String} ORDER BY table, position` | `{db:String}`（clickhouse_connect `parameters=`） |

因此元数据查询在 adapter 内以私有方法实现（`_meta_rows(sql, params)`），
**不复用** `run_query`（后者是执行层编译产物的通道，无参数）。

### D4 新增配置项 `POSTGRES_SCHEMA`（默认 `public`）

- 与 `MYSQL_DATABASE` / `CLICKHOUSE_DATABASE` 同构：库/模式级隔离走配置；
- `.env.example` 与 `config/settings.py` 同步登记；
- 默认值 `public` 与 `mock/gmall/schema_postgres.py` 建仓口径一致。

### D5 `ensure_db()` 按后端显式分派

```python
def ensure_db() -> None:
    backend = settings.DB_BACKEND
    if backend == "duckdb":
        if not settings.DB_PATH.exists():
            from mock.init_duckdb import main as init_db
            init_db()
        refresh_catalog(db_path=settings.DB_PATH)
    else:  # mysql / postgres / clickhouse：建仓是显式运维动作
        refresh_catalog()          # 物理列由 adapter 实读；库不可用即抛
    refresh_policies()
```

- 未知后端值（拼错）由 `get_adapter()` 抛错，不静默退化到 duckdb；
- 非 duckdb 分支**不触碰** `DB_PATH`（不建文件、不读文件）。

### D6 维度成员词汇表改经 `adapter.run_query`

- SQL 内无参数（表/列名来自目录白名单 + 方言渲染器引号包裹），与
  `run_query(conn, sql)` 签名匹配；
- 连接复用：`build_catalog` 内若 adapter 自建了连接，则物理列与词汇表共用
  同一条连接（一次 `connect_readonly()`，避免两次握手）；
- 词汇表加载失败（库不可用）语义见 D7。

### D7 失败语义：非 duckdb 后端**禁止**回退内置默认目录

现状是"物理面不可用 → `physical=None` → 用 `catalog.py` 内置默认目录"，这是
为**离线可运行**（无 `analytics_sandbox.duckdb` 也能跑单测/评测）设计的。
本期保留 duckdb 的该语义，但对非 duckdb 后端改为**快速失败**：

| 后端 | 物理面不可用（连接失败/库空） | 理由 |
|------|------------------------------|------|
| duckdb | 回退内置默认目录（现状不变） | 离线开发/评测/单测需要；DuckDB 文件是本地可重建的 |
| mysql/postgres/clickhouse | `refresh_catalog()` 抛 `SqlExecutionError`（连接失败）或 `ValueError`（库内无表 → 物理列为空） | 执行面已指向真实库，目录回退默认值 = 静默错误；宁可启动失败 |

> "库内无表"判据：`physical_columns()` 返回空 dict 即视为不可用（建仓未执行
> 或账号无元数据读权限），文案指名后端与库名，提示先跑 `mock.init_*`。

### D8 类型覆盖清单与 fail-closed 边界

- 覆盖度以四后端**真实建仓产物**为准，清单随任务清单逐项打勾；
- 未知类型 → `ValueError`（指名原始类型 + 后端），**不静默降级为 `str`**
  （降级会让字面量转义走错分支，是安全面问题）；
- CH 的 `Nullable(...)` 剥壳后若仍未知（如 `Array(String)`、`Map(...)`），
  同样抛错——这些类型当前建仓不产出，出现即说明 schema 演进越界，应显式
  登记而非猜测。

## 6. 安全模型对照

| 防线 | 现状 | 本期 |
|------|------|------|
| 元数据查询的只读性 | 复用 `connect_readonly()`（各后端已加固：DuckDB `read_only=True`、MySQL/PG 只读账号 + 会话只读、CH readonly profile + 验证式 harden） | 不变，且**只走只读连接** |
| 注入面（库名/schema 名） | MySQL 分支用 `?`（实际失效，见 §1.3-4） | 四后端**参数化绑定**，无字符串拼接 |
| 注入面（表名/列名进词汇表 SQL） | 目录白名单 + `renderer.quote_ident` | 不变 |
| 失败可见性 | 非 duckdb 静默回退本地 DuckDB | 非 duckdb 快速失败（D7），错误文案指名后端/库/schema |
| 权限最小化 | 只读账号 | 新增要求：PG 只读账号需能读 `information_schema.columns`；CH 需能读 `system.columns`（**CI 实测验证，见 §10 R1**） |

## 7. 配置设计

| 配置项 | 默认值 | 用途 | 变更 |
|--------|--------|------|------|
| `DB_BACKEND` | `duckdb` | 执行后端（四值） | 不变 |
| `DB_PATH` | `analytics_sandbox.duckdb` | 仅 duckdb 后端使用 | 语义收紧：非 duckdb 不再读 |
| `MYSQL_DATABASE` | `gmall` | MySQL 元数据库过滤 | 不变（改为参数化生效） |
| `POSTGRES_DATABASE` | `gmall` | PG 连接库 | 不变 |
| **`POSTGRES_SCHEMA`** | **`public`** | **PG 元数据 schema 过滤** | **新增** |
| `CLICKHOUSE_DATABASE` | `gmall` | CH 连接库 + `system.columns` 过滤 | 不变 |

## 8. 确定性与可复现

- 元数据查询带 `ORDER BY`（表名 + 列序号 `ordinal_position` / `position`），
  目录构建顺序确定（`dict` 保序，但下游 `sorted()` 使用处不依赖）；
- 无随机、无时间依赖；`physical_columns()` 每次实读，不做跨调用缓存（避免
  运维改库后目录 stale——`refresh_catalog` 是唯一刷新入口）；
- 四后端同源数据（`mock/gmall`）→ 目录输出应**完全一致**（表集合、列集合、
  dtype 三元组），由新增的跨后端目录一致性用例锁定（Review Focus 2）。

## 9. 版本基线与部署要求

| 后端 | 基线 | 元数据源稳定性 |
|------|------|---------------|
| duckdb | 锁定主版本上界（`fix/ci-duckdb2-scan-rows` 既定） | `information_schema.columns` 稳定 |
| mysql | 8.0（CI 锚定） | `information_schema.columns` 稳定 |
| postgres | 16（CI 锚定），兼容 12+ | `information_schema.columns` 稳定 |
| clickhouse | >= 23.8 LTS（CI 锚定 24.8） | `system.columns` 列名 `database/table/name/type/position` 长期稳定 |

## 10. 风险与开放问题

| # | 风险/问题 | 处置 |
|---|-----------|------|
| R1 | **只读账号能否读 `system.columns`**：CH 的 `GRANT SELECT ON *.*` 是否覆盖 `system` 库未经验证；若不可读，`physical_columns()` 在 CI 上直接失败 | Task 4 附 CI 实测步骤（用 `dataagent_ro` 账号 `SELECT count() FROM system.columns`）；不可读则回退"部署文档要求显式 `GRANT SELECT ON system.columns`"并在 users.d/CI 授权步骤补一条 |
| R2 | PG 只读账号读 `information_schema.columns` 是否受限 | PG 该视图基于 `pg_catalog`，`PUBLIC` 默认可读；CI `postgres-parity` 增加一次目录刷新冒烟即实锤 |
| R3 | 非 duckdb 后端"库内无表"与"账号无权限"错误文案难区分 | 二者都抛错；文案同时给出后端/库/schema 与"请先执行 `python -m mock.init_*`"提示，运维按提示排查 |
| R4 | 现有 `build_catalog(conn=...)` 的 30+ 处测试依赖 `conn` 为 DuckDB 连接 | 保留 `conn` 参数语义（等价于 duckdb adapter + 注入连接），测试零改动或最小改动（Review Focus 3） |
| R5 | `POSTGRES_SCHEMA` 新增后，历史 `.env` 未设该项 | 默认值兜底，向后兼容 |
| R6 | 类型映射遗漏（如 PG `money`、CH `Enum8`） | fail-closed 抛错而非静默降级；覆盖清单以建仓产物为准（D8） |
| R7 | `_load_dimension_members` 的 SQL 在 CH 上的引号口径（反引号）与 PG（双引号）不同 | 已由 `renderer.quote_ident` 承载（现状即如此），本期只换执行通道 |

## 11. 交付结构与验收

- 单期单分支单 PR：`feat/physical-columns-adapter` → `master`；
- 验收口径：
  1. 默认 duckdb 全链路零变更（1241 用例 + golden 31/31 + 35 条 SQL 锚点 0 漂移）；
  2. `pytest -q` 新增用例全绿（type_map 表驱动、四 adapter 物理列、ensure_db 分派、跨后端目录一致性）；
  3. CI 新增/扩展的门禁步骤全绿：`postgres-parity` 与 `clickhouse-parity` 各增
     一步"真实库目录刷新冒烟"（`DB_BACKEND=postgres|clickhouse` 下跑
     `python -m semantic.catalog_loader` 并断言表/列数与库一致）；
  4. `mysql-parity` 同步增一步（覆盖 `%s` 参数化修复的真实驱动验证）。
