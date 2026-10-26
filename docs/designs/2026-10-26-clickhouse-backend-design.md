# 设计方案｜ClickHouse 执行后端支持（二十三期，2026-10）

> 状态：设计定稿，规格审阅已通过（2026-10-26）。实施计划见
> `../plans/2026-10-26-clickhouse-backend-plan.md`，任务记录见
> `../tasks/2026-10-26-clickhouse-backend-tasks.md`（对齐二十二期先例与
> 五类目录命名规范，见 `docs/README.md`）。
> 分支：`feat/clickhouse-hive-backend`（自 master 92911b6 切出）。

## 1. 背景与目标

二十二期（92911b6）已落地 `DB_BACKEND=duckdb|mysql|postgres` 三后端：执行引擎
抽象层 `exec/engine/`（EngineAdapter 协议，注册表化）+ 编译方言层
`compiler/dialect/`（DialectRenderer 协议九原语，注册表化）承载引擎差异，
护栏框架与连接池引擎无关。本期在其上新增 **ClickHouse** 作为第四个执行后端，
全量对齐二十一/二十二期交付模式：

1. `DB_BACKEND=duckdb|mysql|postgres|clickhouse` 四值切换，默认 `duckdb` 零破坏；
2. 编译器按后端产出 ClickHouse 方言 SQL（确定性、无注入不变）；
3. 执行三护栏（超时 / 扫描行熔断 / LIMIT 硬上限）+ 执行前审计门在
   ClickHouse 上等价生效（承载方式有差异，见 D3-D5）；
4. 探索层（L3）sec_* 安全面在 ClickHouse 上走**内联派生表路线**（与 MySQL
   同路——ClickHouse 无 TEMP VIEW，且 readonly=1 下无 DDL 权限）；
5. mock Gmall 数仓一键初始化到 ClickHouse（确定性三原则不变：种子 42、
   AS_OF_DATE=2025-12-31、行数与 DuckDB 版一致）；
6. 双后端一致性回归 `tests/test_clickhouse_backend_parity.py` + CI
   `clickhouse-parity` 门禁 job（CI 首绿即 ClickHouse 链路首次真实验证——
   本地开发期 parity 仅依赖 CI，沿二十二期与用户确认的模式）；
7. `eval --backend clickhouse` 连通性冒烟接线。

**交付结构（用户已确认）**：两期两分支两 PR。本期 = ClickHouse（二十三期，
当前分支）；Hive = 二十四期（届时另开分支，其结构性差异清单见 §12 预研纪要，
不混入本期契约）。

**非目标（本期不做）**：

- 不做 ClickHouse 23.8 以下兼容（基线 >= 23.8 LTS，见 §9）；
- 不做运行期多后端混查（一次进程一个后端，由配置决定）；
- 不做 golden 评测集的 ClickHouse 方言重写（golden 锚定 DuckDB 方言不变，
  正确性由 parity 测试承担）；
- 不做 Hive（二十四期单独立项）；
- 不动 `semantic/catalog.py`、DSL 契约、护栏框架结构与 `semantic.json`。

## 2. 现状耦合点清单（调研事实）

2026-10-26 在 master（92911b6）逐点调研结论，即全部改造面：

| # | 位置 | 事实 |
|---|------|------|
| C1 | `exec/engine/__init__.py:32-36` | adapter 注册表已 dict 化（二十二期）：新增后端 = 登记一行惰性工厂，报错文案由 keys 动态生成 |
| C2 | `compiler/dialect/__init__.py:38-42` | renderer 注册表同构（dict + 惰性 import），`resolve_dialect` 显式 `SQL_DIALECT` 优先、缺省跟 `DB_BACKEND` |
| C3 | `exec/guards.py:141-187` | `_DIALECT_EXTRA_KEYWORDS` / `_DIALECT_EXTRA_FUNC_RE` 现有 mysql/postgres 两键；通用黑名单已含 `SET/RESET/COPY/LOAD` 等（CH 增量须去重） |
| C4 | `exec/guards.py:209-215` | 方言经 `_resolve_dialect` 交 sqlglot `read=dialect` 解析；`clickhouse` 是 sqlglot 原生方言名，AST 只读结构校验零新增 |
| C5 | `core/retrieval/exploration.py:79` | 改写期 `if dialect == "mysql":` 走内联派生表（sec_* 安全区内联化）；ClickHouse 需纳入该路线（见 D8） |
| C6 | `core/retrieval/exploration.py:158-160` | 视图安装走 `getattr(adapter, "install_secure_views", None)` 方法存在性分发（二十二期 D10）：CH adapter 不实现该方法即自动跳过安装 |
| C7 | `security/views.py:121` | `build_secure_projection` 纯函数（投影/RLS/禁列）引擎无关，直接复用 |
| C8 | `mock/gmall/build.py` | `:345-352` `build_gmall_tables` 按 `adapter.name` 三分支选建表函数；`:44-51` `_write_rows` 非 duckdb 统一走 `adapter.insert_dataframe`；`:70-87` `_insert_seed` 走 `conn.cursor()` + `executemany`——**clickhouse-connect 无 cursor 协议，种子灌数需改道**（见 D10） |
| C9 | `mock/gmall/schema_postgres.py` | DDL 运行时转译先例：`sqlglot.transpile(read="duckdb", write=...)` + 表名正则提取 + 建表函数；`schema_clickhouse` 同构 + MergeTree 后处理（见 D9） |
| C10 | `tests/parity_utils.py` | 双后端一致性共享工具（二十二期提取，比对口径勿改）：数值 rel=1e-5/abs=1e-6、行序无关、datetime 取日期面 |
| C11 | `.github/workflows/ci.yml:91-127` | `postgres-parity` job 先例：service 容器 + init + parity pytest + 既有 job 零破坏红线；CH 需增量 users.d 配置步骤（见 D12） |
| C12 | `config/settings.py:29-50` | `DB_BACKEND`/`SQL_DIALECT` + `MYSQL_*`/`POSTGRES_*` 五项配置组与权限注释模式；`CLICKHOUSE_*` 同构插入 |
| C13 | `requirements.txt` | 驱动精确 pin（带上界）+ 选型理由注释模式 |
| C14 | `eval/eval_runner.py:466-475` | `--backend` choices + `_mysql_smoke`/`_postgres_smoke` 冒烟函数先例 |
| C15 | `mock/metadata.py` | `FIELD_METADATA`/`TABLE_METADATA` 元数据双表（`_field_metadata`/`_table_metadata`）随建仓落库，CH 同构 |
| C16 | `exec/guards.py:475-485` | `explain_scan_rows` 返回 `(0, False)` 不可计量时由护栏框架 warning 留痕（框架级分支已内建），CH 的"无 EXPLAIN ANALYZE"有现成承接点 |

## 3. 总体架构

与二十一/二十二期同构的双轨接入，消费方零改动：

```
DB_BACKEND=clickhouse
  ├─ exec/engine/clickhouse_adapter.py   ClickHouseAdapter（clickhouse-connect, HTTP）
  │    connect_readonly / run_query / cancel / explain_scan_rows
  │    / engine_error / harden / insert_dataframe
  │    （不实现 install_secure_views——方法存在性分发自动落内联派生表路线）
  └─ compiler/dialect/clickhouse.py      ClickHouseRenderer（独立实现）
       quote / literal / date_trunc / date_add / having / spine / WITH / escape
```

- **注册表各加一行**（C1/C2 已 dict 化，零结构性改造）：`_ADAPTER_FACTORIES`
  与 `_RENDERER_FACTORIES` 各登记 `clickhouse` 惰性工厂。
- **不变项**：golden 锚定 DuckDB（`duckdb/mysql/postgres` 三个 renderer 输出
  逐字节不变）；护栏框架（审计门 / 看门狗 / 扫描预检框架 / LIMIT 硬上限）
  引擎无关零改动；连接池经 `get_adapter` 消费零改动；`semantic.json` 与语义
  目录零改动。
- **备选方案与否决理由**（设计期已与用户确认）：
  - 否决 sqlglot 执行前全转译（编译器只产 DuckDB SQL、执行时 transpile）：
    转译结果不可控（转义/spine/字面量语义漂移），与 golden 逐字节锚定红线
    冲突；九原语最小差异化面已被两期验证。
  - 否决 per-query SETTINGS 注入 + readonly=2：readonly=2 允许客户端改
    settings，探索层 SQL（用户文本直达执行面的唯一路径）可携带
    `SETTINGS max_rows_to_read=0` 子句架空预算；profile 承载 + readonly=1
    更强且零新增抽象。

## 4. 技术栈选型对比（选型矩阵与否决理由）

> 本节是 §5 关键决策（D1-D13）的选型依据矩阵：每个决策点列出全部实质候选
> （≥3）、多维对比与否决理由。**既定要求（用户 2026-10-26 确认）**：本项目
> 后续各期设计文档（含二十四期 Hive）必须沿用本节格式输出选型对比表，
> 作为规格的固定章节。

### S1 驱动选型（对应 D2）

| 候选 | 协议/形态 | 维护与生态 | 取消语义 | 灌数通道 | 结论 |
|------|----------|-----------|---------|---------|------|
| **clickhouse-connect（选定）** | HTTP(S) 8123，官方主推客户端 | 官方重心所在，活跃 | `client.cancel()` 尽力 + 服务端自限 | `insert_df` 走 Arrow 批量 | ✅ wheel 全平台免编译；运维穿透性好；低查询频次下握手开销可忽略 |
| clickhouse-driver | 原生 TCP 9000 | 维护缓慢（官方重心已转移） | 需自验 | executemany 手写循环 | ❌ 灌数与取消两处成本高，无对冲优势 |
| clickhouse-cpp（Python 绑定） | 原生 C++ | Python 绑定非官方一等公民，成熟度低 | 自验 | 手写 | ❌ 成熟度不足以进生产契约 |
| 间接通道（DuckDB httpfs / MySQL 线协议） | 不新增驱动 | — | 无原生取消 | 不可控 | ❌ 方言/加固/护栏隔层，与 adapter 模式不匹配 |

### S2 扫描行熔断承载（对应 D3）

| 候选 | 时点/强度 | 可篡改性 | 与既有铁律 | 结论 |
|------|----------|---------|-----------|------|
| **profile `max_rows_to_read` 服务端硬熔断（选定）** | 执行中实时计数，超限即杀（错误码 131 归一） | readonly=1 下客户端不可改 | 预算值 `MAX_SCAN_ROWS` 直通 | ✅ 预算语义从"执行前预检"升级为"执行中硬限"；口径为多表读取总和、比既有各算子最大值口径更严（fail-safe 方向） |
| per-query SETTINGS 注入 + readonly=2 | 执行中 | **可被探索 SQL 携带的 SETTINGS 子句架空**（readonly=2 允许客户端改 settings） | — | ❌ 用户文本直达执行面的唯一路径即被攻破 |
| EXPLAIN ESTIMATE 预检 | 执行前（估算值） | — | **"cost 估算不混入"铁律**（二十一/二十二期教训） | ❌ 估算值非 actual，违反既有铁律 |
| 仅 LIMIT 硬上限兜底 + warning 留痕 | 无扫描预算 | — | — | ❌ 全表扫完才截断返回，护栏强度显著弱于其他三后端 |

### S3 只读承载与加固方式（对应 D4）

| 候选 | 强度 | fail-closed 校验 | 结论 |
|------|------|-----------------|------|
| **profile readonly=1 + 验证式 harden（选定）** | 强（服务端拒绝非读请求，客户端不可篡改） | 查 `system.settings` 校验 readonly/预算与应用配置一致，不符抛 `SecurityError` | ✅ 加固从"改"变"验"；顺带捕获 profile 与应用预算的部署漂移 |
| 会话级 SET readonly（MySQL 式） | — | — | ❌ 方案不成立：readonly=1 会话内客户端无权 SET，CH 无 MySQL `SET SESSION TRANSACTION READ ONLY` 等价物 |
| 纯账号授权（无 profile/无验证） | 弱 | 无 | ❌ 无 fail-closed，弱配置连接可进管道 |
| readonly=2 + settings constraints（部署约束） | 中 | 复杂 | ❌ 约束正确性依赖部署面、探索 SQL 仍可试探约束边界，复杂度不值 |

### S4 spine（fill_gaps）实现（对应 D7）

| 候选 | 版本安全 | 语义确定性 | 结论 |
|------|---------|-----------|------|
| **numbers(N) + dateAdd（选定）** | `numbers()` 远古可用 | N 由渲染器纯 Python 计算（day/week 按秒、month/quarter 手写月历算术），未知形态显式 `CompileError` | ✅ 无递归深度问题；5 年 day 粒度 1826 行量级天然承载 |
| generateSeries 表函数 | 较新版本能力（版本赌注） | — | ❌ 兼容基线 >= 23.8 不保证覆盖 |
| 递归 CTE（WITH RECURSIVE） | 实验态（allow_experimental_analyzer 依赖） | — | ❌ 实验特性严禁进生产契约 |
| arrayJoin(range(...)) | 可用 | range 仅整数步长，month/quarter 步长不均匀 | ❌ 月/季步长语义破 |

### S5 探索层 sec_* 安全面路线（对应 D8）

| 候选 | 权限要求 | 治理语义 | 结论 |
|------|---------|---------|------|
| **内联派生表（复用 MySQL 路线）（选定）** | 零 DDL | 与视图版同走 `build_secure_projection`（投影/RLS/禁列同一实现） | ✅ 方法存在性分发自动落位；readonly=1 防线不放宽 |
| TEMPORARY TABLE AS SELECT | 需 readonly=2 + allow_ddl | 物化（Memory 引擎内存占用） | ❌ 放宽只读防线 + 物化成本 |
| 普通视图 + 专用库 | 需建库/建视图权限 | 按 principal 命名与生命周期管理 | ❌ 权限面与运维负担双增 |

### S6 建仓 DDL 通道（对应 D9）

| 候选 | 单一来源 | 维护成本 | 结论 |
|------|---------|---------|------|
| **sqlglot 运行时转译 + MergeTree/Nullable 后处理（选定）** | `schema.py` 单一事实源 | 仅后处理规则需维护 | ✅ 对拍单测锁定；与 schema_mysql/schema_postgres 同构 |
| 手写第二份 CH DDL | ❌ 双份表结构 | 双份维护 | ❌ 违反单一事实源铁律（既有约定明令禁止） |
| 建仓期一次性生成代码入库 | 生成物即第二份 | schema 变更需重新生成 | ❌ 漂移延后而非消除 |

### S7 建仓灌数通道（对应 D10）

| 候选 | 吞吐 | 复杂度 | 结论 |
|------|------|--------|------|
| **client.insert_df（Arrow 批量）（选定）** | 高（列式批量） | 低 | ✅ 空值清洗后语义与 MySQL/PG 通道对齐 |
| cursor().executemany | 低 | — | ❌ **协议事实否决**：clickhouse-connect 无 cursor 协议（C8 实锤缺口） |
| CSV 落盘 + INSERT FROM FILE | 高 | 高（临时文件编排与清理） | ❌ 本数仓规模不值；二十四期 Hive 因协议限制重新评估（§12） |

### S8 时区确定性承载（对应 D13）

| 候选 | 侵入面 | 结论 |
|------|--------|------|
| **容器/部署 TZ=UTC（选定主承载）** | 零（不进编译产物） | ✅ 部署清单固化（§9） |
| session_timezone setting | 连接参数级 | ✅ 双保险；readonly=1 下可用性实现期验证（列入 Review Focus） |
| 编译产物显式 toTimeZone 包裹 | 侵入全部时间表达式 | ❌ 编译产物与 golden 复杂化，语义风险大 |

## 5. 关键决策（D1-D13）

> 选型依据矩阵见 §4（S1-S8 与 D2/D3/D4/D7/D8/D9/D10/D13 一一对应）；本节
> 记录决策本身与不可比对的工程裁决（D1 交付结构、D5 超时取消、D6 九原语、
> D11/D12 配置与 CI）。

### D1 交付结构：两期两分支两 PR

ClickHouse 二十三期（当前分支）；Hive 二十四期另开分支。Hive 存在四处
结构性差异（无递归 CTE、无 EXPLAIN actual rows、HiveServer2 取消封装弱、
建仓灌数性能），独立立项决策，规格与回归边界不与本期混淆（§12）。

### D2 驱动选型：clickhouse-connect（HTTP 协议）

- **选定理由**：官方主推客户端；per-query SETTINGS 与查询参数传递干净；
  `insert_df` 走 Arrow 批量通道（建仓灌数快）；二进制 wheel 全平台免编译；
  HTTP(S) 运维穿透性好。查询频次低的场景下 HTTP 握手开销可忽略。
- **否决 clickhouse-driver**（原生 TCP 9000）：维护活跃度低于
  clickhouse-connect（官方重心已转移）；批量灌数需手写 executemany 循环；
  取消语义需自验。
- **依赖 pin**：`clickhouse-connect>=0.7,<1.0`（写入 requirements.txt，附
  选型理由注释；精确上界防 1.0 大版本 API 漂移）。
- **否决间接通道**（DuckDB httpfs / MySQL 线协议）：方言下沉不可控，与
  adapter 模式不匹配。

### D3 扫描行熔断承载：账号 profile 服务端硬熔断

- ClickHouse **无 EXPLAIN ANALYZE**（PostgreSQL 语义的计划执行器），二十一/
  二十二期"执行前预检 actual rows"口径不可复制。承载方式改为：运行时账号
  profile 设置 `max_rows_to_read = MAX_SCAN_ROWS`——服务端执行中实时计数、
  超限即杀查询（错误码 131 LIMIT_EXCEEDED 归一），预算语义从"执行前预检"
  升级为"执行中硬限"。
- `explain_scan_rows` 诚实返回 `(0, False)`（warning 留痕，C16 框架分支
  承接），**严禁**用 `EXPLAIN ESTIMATE` 估算值冒充（"cost 估算不混入"
  二十一/二十二期铁律）。
- **口径差异（写明，非缺陷）**：`max_rows_to_read` 计多表读取**总和**，既有
  三后端口径是各算子实际行数**最大值**——同预算值下 ClickHouse 更严
  （fail-safe 方向），parity 专项不含大扫描用例即无感知。
- **否决 per-query SETTINGS 注入**（D2 备选 C 详述）：readonly=1 下注入
  会被服务端拒绝，readonly=2 下注入会被探索 SQL 的 SETTINGS 子句架空。

### D4 只读三防线（fail-closed）

与二十一/二十二期同构，承载方式按 ClickHouse 语义调整：

1. **运行时账号 profile `readonly=1`**（部署要求 §9）：只允许读请求，
   不可改 settings、不可 DDL——预算值随 profile 下发，客户端无法篡改；
2. **验证式加固 `harden()`**：ClickHouse 下客户端无法在 readonly=1 会话内
   SET（加固从"改"变"验"）——连接建立后查询 `system.settings` 校验
   `readonly >= 1` 且 `max_rows_to_read`/`max_execution_time` 与
   `settings.MAX_SCAN_ROWS`/`QUERY_TIMEOUT_MS` 一致，任何不符抛
   `SecurityError`（严禁弱配置连接进管道；同时捕获 profile 与应用预算的
   部署漂移）；
3. **方言增量黑名单**（C3，见 §5-D6 后的增量清单）：`SYSTEM`（SYSTEM
   SHUTDOWN/RELOAD 等）、`KILL`、`TRUNCATE`、`OPTIMIZE`、`SETTINGS`、
   `INTO OUTFILE` 关键词 + 表函数 `url/file/s3/azure/hdfs/jdbc/odbc/mysql/
   postgresql/mongodb/remote/remoteSecure/cluster/clusterAllReplicas/input/
   generateRandom` + 函数 `sleep/sleepEachRow`（与通用黑名单去重后落盘，
   纯逻辑单测锁定基线；最终清单以实现期与通用黑名单对拍为准）。

### D5 超时与取消

- **超时**：profile `max_execution_time = QUERY_TIMEOUT_MS/1000`（服务端
  杀查询，错误码 159 / TIMEOUT_EXCEEDED 归一 `QueryTimeoutError`）+
  clickhouse-connect 客户端查询超时留余量（对齐 MySQL read_timeout+5s
  口径：客户端后超时，连接不因读超时报废）。
- **取消**：watchdog 回调尽力调 `client.cancel()`（实现期验证语义；若
  clickhouse-connect 版本行为不符则退化为空操作，与服务端自限兜底同
  MySQL/PG 先例——服务端 max_execution_time 终止查询）。

### D6 方言十原语口径（`compiler/dialect/clickhouse.py`）

| 原语 | ClickHouse 口径 |
|------|------|
| `quote_ident` | 反引号包裹 + 内部反引号翻倍（转义语义实现期单测锁定） |
| `quote_column` | `` `alias`.`column` `` |
| `timestamp_literal` | `toDateTime('...')`（datetime 完整格式化；date/str 原样字符串化，与既有 renderer 分支口径一致） |
| `date_trunc` | 全原生四档：day→`toStartOfDay`、week→`toMonday`（周一锚定，与 DuckDB `date_trunc('week')` 口径一致，规避 `toStartOfWeek` 的周起始 mode 歧义）、month→`toStartOfMonth`、quarter→`toStartOfQuarter` |
| `date_add` | `{expr} + {INTERVAL 字面量}` 透传拼接——编译器 `INTERVAL 1 DAY` 形态在 CH 合法（无需 PG 式转译） |
| `divide` | `toFloat64(num) / NULLIF(toFloat64(den), 0)`——Decimal 除法 `S = S1` 截断问题（见 D14，CI 首轮实锤后补入） |
| `having_metric_ref` | 返回别名引用（CH 支持别名复用，同 MySQL 路线） |
| `spine_cte_sql` | `SELECT dateAdd(unit, number, toDateTime(start)) AS {alias} FROM numbers(N)`（见 D7） |
| `with_keyword` | `WITH`（非递归） |
| `escape_string_literal` | 反斜杠转义系：`\` 翻倍 + `'` 写作 `\'`（CH 字符串字面量以反斜杠转义为标准语义，与 MySQL `''` 口径不同——CH 侧不使用引号翻倍） |

golden 红线：`duckdb/mysql/postgres` 三 renderer 输出逐字节不变（既有
dialect 单测 + golden 双保险锁定）。`divide` 原语在锚点三方言上返回
`{num} / NULLIF({den}, 0)`（不加外层括号，外层括号由调用点按原形态保留），
35 条 golden `sql` 锚点（`eval_runner` 的 `sql_ok`）逐字节复核 0 漂移。

### D7 spine（fill_gaps）实现：numbers(N) + 纯 Python 桶数计算

- **拒绝 `generate_series`**：CH 的 generateSeries 表函数属较新版本能力，
  构成版本赌注；`numbers(N)` 远古可用、语义唯一。
- **拒绝递归 CTE**：CH 递归 CTE 支持（WITH RECURSIVE）仍处实验态（
  allow_experimental_analyzer 依赖），不进生产契约。
- **N 的确定性计算**：renderer 解析 start/end 字面量与 step INTERVAL 形态，
  纯 Python 桶数计算（day/week 按秒；month/quarter 手写月历算术，不引入
  dateutil 依赖），N = 桶数；形态未知显式 `CompileError`（对齐 PG
  `_INTERVAL_RE` 先例，杜绝静默拼接非法 SQL）。
- 上界护栏：N 超过编译器既有窗口上界（5 年 day 粒度 1826 行）时由
  `numbers(N)` 天然承载，无递归深度问题（优于 MySQL 路线）。

### D8 探索层：内联派生表路线（复用 MySQL 实现）

- CH 无 TEMP VIEW；readonly=1 下无任何 DDL 权限（TEMPORARY TABLE 也不可建）
  → `ClickHouseAdapter` **不实现** `install_secure_views`（C6 方法存在性
  分发自动跳过安装）。
- 改写期方言判断（C5）从 `dialect == "mysql"` 扩为
  `dialect in ("mysql", "clickhouse")`——sec_* 安全区内联化，投影/RLS 与
  视图版同走 `build_secure_projection`（C7 引擎无关）。
- 治理语义不变：探索 SQL 永不原文执行（表名重写 + 三护栏 + PII 脱敏）。

### D9 建仓 DDL 转译与 MergeTree 后处理（`mock/gmall/schema_clickhouse.py`）

- DDL 单一来源仍是 `schema.py`：sqlglot（read=duckdb, write=clickhouse）
  运行时转译 + 表名正则提取（对拍单测锁定，与 schema_mysql/schema_postgres
  同构）。
- **MergeTree 后处理**（sqlglot 不覆盖表引擎子句）：全部表 ENGINE =
  MergeTree；事实表与元数据表按 MySQL `MYSQL_INDEXES` 同一张表清单设置
  `ORDER BY (主时间列)`（稀疏主键索引对齐 MySQL 二级索引动机：CH 主键序
  存储 + 稀疏索引加速时间主轴过滤），其余表 `ORDER BY tuple()`。
- **Nullable 包裹**：转译后处理把所有列统一包 `Nullable(...)`（对齐
  DuckDB/MySQL/PG 全列可空语义，消除类型↔数据耦合；本数仓规模下 Nullable
  开销可忽略；埋点解析链路存在 NULL datetime 值——MySQL CI 实锤先例）。
- **无 ANALYZE 步骤**：CH 无 CBO 统计概念（PG 特有步骤不适用），写明差异。

### D10 建仓灌数通道（C8 增量改造）

- 批量行（`_write_rows`）与非 duckdb 通道统一：`ClickHouseAdapter.
  insert_dataframe` 走 `client.insert_df`（Arrow 通道），空值统一清洗为
  None（pd.isna 覆盖 None/NaN/pd.NA/NaT，对齐 MySQL/PG 口径）。
- **种子行改道**（`_insert_seed`，C8 实锤缺口）：clickhouse-connect 无
  cursor 协议，`build.py` 按 `adapter.name == "clickhouse"` 分支把种子行走
  `insert_dataframe`（具名 DataFrame，列序 = SEED_COLUMNS）；MySQL/PG
  cursor 通道不动。
- 建仓账号要求 CREATE/INSERT/DROP（复用 `CLICKHOUSE_*` 配置组，部署要求
  与运行时只读账号分离，对齐 PG D11 先例）。

### D11 配置组与版本基线

- `config/settings.py` 增 `CLICKHOUSE_HOST/PORT/USER/PASSWORD/DATABASE`
  五项（PORT 缺省 8123 HTTP；权限注释模式对齐 MYSQL/POSTGRES 组）+
  `.env.example` 同步。
- **兼容基线 >= 23.8（LTS）**；CI 锚定具体镜像 tag（见 D12）。版本演进
  跟随策略沿二十一/二十二期 §8.1：升级先跑全量回归 + parity。
- `DB_BACKEND` 取值扩展为四值；`SQL_DIALECT` 缺省跟随推导零改动
  （`clickhouse` 即 sqlglot 方言名）。

### D12 CI 门禁形态（`clickhouse-parity` job）

- 官方 `clickhouse/clickhouse-server` 镜像，pin 具体 tag（兼容基线之上的
  LTS，如 24.8；实现期定版并写入 CI 注释）。
- **双用户形态**（CH 特有增量，因 readonly 由 profile 承载无法会话级 SET）：
  1. `default` 用户（管理员）：`mock.init_clickhouse` 建仓；
  2. `dataagent_ro` 用户：CI 步骤写入 `users.d` 配置（readonly=1 +
     `max_rows_to_read`/`max_execution_time` 与应用预算一致 + 密码），
     parity 测试经 `CLICKHOUSE_TEST_*` 环境变量以该用户连接。
- 时区确定性：容器 `TZ=UTC`（见 D13）。
- 红线不变：`mysql-parity`/`postgres-parity` 两既有 job 零破坏。

### D13 时区确定性

- 列类型 DateTime 无时区语义，字面量 `toDateTime('...')` 按服务器时区解释
  → CI 容器与部署要求统一 **TZ=UTC**（写入 §9 部署清单）。
- `session_timezone` setting（CH 23.4+）作为会话级双保险：实现期验证其在
  readonly=1 下可经连接参数生效后登记（列入 Review Focus；验证不过则仅
  依赖 TZ=UTC，诚实记录）。

### D14 除法精度：`divide` 原语（CI 首轮 parity 实锤后补入）

- **事实**：CH 的 Decimal 除法规则是 `divide: S = S1`（结果 scale 取**第一
  操作数**的 scale，多余小数**截断不舍入**，官方 Decimal 文档
  "Operations and Result Type"）。本仓金额列 `DECIMAL(16,2)` →
  `(cur.gmv - prev.gmv) / prev.gmv` 的 S1=2，增长率退化为 2 位小数
  （`0.252180` → `0.25`）；DuckDB 的 Decimal 除法返回 DOUBLE（全精度），
  二者语义不等价——CI `test_comparison_time_dim_parity` 实测
  `数值漂移: 0.25218036741510486 vs 0.25`。
- **裁决**：新增第 10 个方言原语 `divide(num, den)`，把"除零口径 + 精度口径"
  收敛到方言层；CH 侧渲染 `toFloat64(num) / NULLIF(toFloat64(den), 0)` 对齐
  DOUBLE 口径，其余三方言渲染裸形态 `{num} / NULLIF({den}, 0)`。
- **括号归属**：外层括号仍由调用点按原形态保留（表达式指标 `(a / NULLIF(b,0))`、
  比率指标与 comparison 增长率不加外层括号）——保证 35 条 golden `sql` 锚点
  逐字节不变（`sql_ok` 是零数据依赖的契约门）。
- **覆盖范围**：三处除法生成点全部改道（表达式 `div` / 比率指标 /
  comparison 增长率列），任一遗漏都会在 CH 上静默截断。
- **副产物（审计误报）**：`FROM cur, prev` 的 AST 形态随解析器而异——
  ClickHouse 解析器把逗号 FROM 归一化为 `kind='CROSS'`（DuckDB/MySQL/PG 为
  空 kind），`exec/audit.py` 的"标量聚合 CTE 放行"写在"无 ON"分支内 → CH
  下被误判为显式 CROSS JOIN 而 REJECTED（golden Q11/Q12/Q15 中断）。放行
  判定前置到 kind 分支之前；真实表 CROSS JOIN 仍拒（放行条件是"全部引用
  均为标量聚合 CTE"，与 JOIN 写法无关）。

## 6. 安全模型对照（治理管道等价性）

| 防线 | duckdb | mysql | postgres | **clickhouse（本期）** |
|------|--------|-------|----------|----------------------|
| 只读承载 | 只读连接参数 | 只读账号 + `SET SESSION TRANSACTION READ ONLY` | 只读账号 + `default_transaction_read_only` | **账号 profile readonly=1（部署）+ 验证式 harden** |
| 转义确定性 | 原生 | NO_BACKSLASH_ESCAPES + 反斜杠翻倍双保险 | `standard_conforming_strings=on` pin | 反斜杠转义系原生语义 + 翻倍转义器 |
| 扫描预算 | EXPLAIN ANALYZE 预检 | EXPLAIN ANALYZE 预检 | EXPLAIN ANALYZE 预检 | **profile `max_rows_to_read` 服务端硬熔断**（预检诚实不可计量） |
| 超时 | 看门狗 + interrupt | max_execution_time | statement_timeout | **profile `max_execution_time`** + 客户端余量 + cancel 尽力 |
| 探索层 sec_* | TEMP 视图 | 内联派生表 | TEMP 视图 | **内联派生表** |
| 方言增量黑名单 | — | USE/SHOW/KILL/PREPARE/OUTFILE 等 | COPY/DO/LISTEN/NOTIFY + pg_* | SYSTEM/KILL/TRUNCATE/OPTIMIZE/SETTINGS/INTO OUTFILE + 外部源表函数族 |

治理管道语义（LLM SQL 永不直接执行 / 只读 / RLS / 敏感数据保护 / 资源上限）
四后端等价；差异仅在承载机制，全部 fail-closed。

## 7. 配置设计

```bash
# .env 增量（CLICKHOUSE_* 组，运行时查询用 readonly profile 账号；
# 建仓 python -m mock.init_clickhouse 复用本组但要求账号可写，两账号分离见 §9）
CLICKHOUSE_HOST=127.0.0.1
CLICKHOUSE_PORT=8123          # HTTP 协议口
CLICKHOUSE_USER=dataagent_ro
CLICKHOUSE_PASSWORD=
CLICKHOUSE_DATABASE=gmall

# parity 测试指向（未配置自动跳过，对齐 MYSQL_TEST_*/POSTGRES_TEST_* 口径）
CLICKHOUSE_TEST_HOST=...
CLICKHOUSE_TEST_PORT=8123
CLICKHOUSE_TEST_USER=dataagent_ro
CLICKHOUSE_TEST_PASSWORD=...
CLICKHOUSE_TEST_DATABASE=gmall
```

adapter DSN 可注入（parity 测试指向独立实例）；缺省读 settings。

## 8. 确定性与可复现

- 种子 42、`AS_OF_DATE=2025-12-31`、单 rng 顺序消费不变；`init_clickhouse`
  与 `init_duckdb` 消费同一 `build_gmall_tables` 的同一 DataFrame/rows 序列
  （仅入库通道不同），行数输出一致（init 打印行数对照，对齐 PG 先例）。
- golden 锚定 DuckDB 方言不变；`eval --backend clickhouse` 仅连通性冒烟。
- parity 比对口径复用 `tests/parity_utils.py`（rel=1e-5/abs=1e-6、行序
  无关、datetime 取日期面），勿改。

## 9. 版本基线与部署要求

- **兼容基线**：ClickHouse >= 23.8（LTS）；CI 锚定 >= 基线的具体 LTS tag。
  版本演进跟随策略沿二十一/二十二期 §8.1。
- **部署要求（运行时账号）**：
  1. profile `readonly = 1`；
  2. profile `max_rows_to_read = <MAX_SCAN_ROWS>`、`max_execution_time =
     <QUERY_TIMEOUT_MS/1000>`（与应用预算一致，harden 验证 fail-closed）；
  3. GRANT SELECT ON gmall.*（readonly=1 已阻 DDL/DML，授权为纵深防御）；
  4. 服务器/容器时区 UTC。
- **部署要求（建仓账号）**：CREATE/INSERT/DROP ON gmall.*（与运行时账号
  分离）。

## 10. 风险与开放问题

| # | 风险 | 处置 |
|---|------|------|
| R1 | `client.cancel()` 语义与版本行为不确定 | 实现期验证；不符则 watchdog 回调空操作，服务端 max_execution_time 兜底（D5 已定退化路径） |
| R2 | `session_timezone` 在 readonly=1 下不可用 | 实现期验证（D13）；不过则仅依赖 TZ=UTC，诚实记录 |
| R3 | sqlglot clickhouse 方言对个别 CH 语法（SETTINGS 子句/INTO OUTFILE）解析覆盖不全 | 关键词黑名单走通用正则扫描（非 AST 依赖）；AST 校验解析失败本就 fail-closed（GuardrailRejected） |
| R4 | Nullable 全列包裹后 insert_df 类型推断偏差 | parity 专项含空值用例；灌数通道空值清洗统一 None |
| R5 | HTTP 通道大结果集内存 | LIMIT 硬上限既有护栏承担（返回行数熔断引擎无关） |
| R6 | 反引号标识符内翻倍转义语义 | 实现期单测锁定（D6）；标识符源为 catalog 白名单 + 编译器内部别名，无注入面 |

## 11. 成功标准

1. 本地 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿
   （clickhouse parity 未配置自动跳过）；
2. golden 评测 duckdb oracle 31 例不变绿（零破坏红线）；`duckdb/mysql/
   postgres` 三 renderer 编译产物逐字节不变（dialect 单测锁定）；
3. CI 四 job 全绿：三既有 job 不变 + `clickhouse-parity` 首绿（CH 链路
   首次真实验证）；
4. `python -m mock.init_clickhouse` 建仓成功且行数与 `init_duckdb` 输出
   一致；
5. `eval --backend clickhouse` 冒烟接线 fail-fast 生效；
6. 文档三件套齐全（本 spec + plan + tasks），AGENTS.md 增 ClickHouse 契约
   小节。

## 12. 附：Hive 第二期预研纪要（仅记录差异，不定契约）

> 既定要求（用户 2026-10-26 确认）：二十四期规格必须沿用本期 §4 格式，对
> 驱动、spine 承载、CI 形态等每个决策点输出 ≥3 候选的选型对比表与否决理由。

Hive 相对既有四后端的结构性差异，二十四期规格决策：

1. **fill_gaps spine**：无递归 CTE、无 generate_series 等价物——候选方案：
   数仓内置日历/数字辅助表（改数仓 schema，需评估 golden 影响）vs 方言层
   `CompileError` 诚实降级（fill_gaps 在 Hive 上不可用）；
2. **扫描熔断**：Hive `EXPLAIN` 无 actual rows（CBO 统计估算不可混入）——
   不可计量 + warning 留痕，靠账号配额、LIMIT 硬上限、超时兜底；
3. **取消**：HiveServer2 走 Thrift `TCancelOperation`；PyHive 封装弱，
   驱动候选 PyHive[hive]（事实标准但维护缓慢，需精确 pin）vs impyla；
4. **建仓灌数**：`INSERT ... VALUES` 逐行慢（ACID 限制）——需 LOAD DATA
   文件通道（生成器产物落 CSV/ORC 后加载）；CI 候选 `apache/hive` 官方
   镜像（内嵌 metastore + 本地执行引擎，CI 时长约增数分钟）；
5. **只读承载**：无会话级只读——账号权限（HDFS/Ranger）+ 方言黑名单 +
   护栏，harden 退化为验证式。
