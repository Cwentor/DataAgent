# 设计方案｜Hive 执行后端支持（二十五期，2026-10）

> - **状态**：设计定稿，规格经计划审阅通过（2026-10-27）。
> - **实施计划**：[../plans/2026-10-27-hive-backend-plan.md](../plans/2026-10-27-hive-backend-plan.md)
> - **任务跟踪**：[../tasks/2026-10-27-hive-backend-tasks.md](../tasks/2026-10-27-hive-backend-tasks.md)
> - **分支**：`feat/hive-backend`（自 master `a006c39` 切出）。

## 1. 背景与目标

二十一期起执行引擎抽象层（`exec/engine/`）与编译方言层（`compiler/dialect/`）
双轨承载引擎差异，已落地 duckdb / mysql / postgres / clickhouse 四后端；二十四期
把物理元数据面下沉为 `EngineAdapter.physical_columns()`。本期新增第 5 后端
**Hive（HiveServer2 Thrift 协议）**，`DB_BACKEND` 扩为五值
（`duckdb|mysql|postgres|clickhouse|hive`，默认 duckdb 零破坏）。

二十三期 spec §12（Hive 预研纪要）预写了五个结构性差异：无递归 CTE /
EXPLAIN 无 actual rows / Thrift 取消封装弱 / INSERT VALUES 慢 / 无会话级只读。
本期决策全部以**计划期本地容器实测**（`apache/hive:4.0.0` 单容器 HiveServer2 +
内嵌 Derby，见 §0 实测纪要）为依据裁决。

**交付目标（7 条）**：

1. `DB_BACKEND=hive`：`HiveAdapter`（exec/engine）+ `HiveRenderer`（compiler/dialect）
   双轨接入，注册表各登记一行，消费方零改动；
2. 方言层十原语 Hive 实现（D6），既有四 renderer 编译产物逐字节不变；
3. 三护栏等价：只读防线 / 资源预算 / 方言增量黑名单（D3/D4）；
4. 探索层 sec_* 安全面走内联派生表（同 MySQL/ClickHouse 路线，D8）；
5. 建仓链路 `python -m mock.init_hive`：DDL 转译单一来源 + CSV staging 灌数
   （D9/D10），行数与 `init_duckdb` 一致；
6. parity 测试（`HIVE_TEST_*` 未配置自动跳过）+ CI `hive-parity` 门禁（D13）；
7. `eval --backend hive` 连通性冒烟（golden 锚定 DuckDB 不动）。

**非目标**：Kerberos / Ranger / Knox 接入；HDFS 集群部署；Tez/MapReduce 调优；
ACID 事务表；分区表建仓（Gmall DDL 单一来源无分区）；`fill_gaps` 之外窗口语义
扩展；web 前端后端展示。

## 0. 计划期实测纪要（apache/hive:4.0.0 单容器，2026-10-27）

| # | 实测项 | 结果 |
|---|--------|------|
| M1 | `date_trunc` | **不存在**（Spark SQL 函数，Hive 无）；`trunc(ts,'MM'/'Q'/'YEAR')` 三档实测通过且接受 timestamp 输入 |
| M2 | 周对齐 | `pmod(datediff(ts,'1970-01-05'),7)` 实测周日=6（周一锚定成立） |
| M3 | spine 行生成 | `WITH spine AS (SELECT idx FROM (SELECT 1) one LATERAL VIEW posexplode(split(space(n),' ')) x AS idx, val)` 实测产出 0..n（§12 预研"无 generate_series/递归 CTE"成立，但存在无 UDF 承载） |
| M4 | 灌数通道 | CSV + `LOAD DATA LOCAL INPATH`（**服务端本地路径语义**）实测通过；null 标记须为 SQL 字面量 `'\\N'`（双反斜杠——Hive 字面量解析吃单反斜杠，`'\N'` 落库为 `null.format='N'`，实测坑） |
| M5 | 元数据面 | `DESCRIBE <table>` 输出 `col_name/data_type/comment` 三列，解析面清晰 |
| M6 | 除法 | DECIMAL 除法自带高精度（1/3 → 0.3333…19 位）；`NULLIF` 可用 |
| M7 | HAVING 别名 | 实测支持（同 MySQL/ClickHouse 路线） |
| M8 | 表形态 | Hive 4 managed 表默认 `TRANSLATED_TO_EXTERNAL` + `external.table.purge=TRUE`（DROP 清数据，幂等重建无残留）；warehouse 走 `file:/opt/hive/data/warehouse`（本地 FS，无需 HDFS） |
| M9 | 驱动安装 | PyHive 0.7.0 + thrift 0.25.0 在 Windows **纯 Python 安装成功**（无 sasl 编译） |
| M10 | HTTP transport | 容器 `hive.server2.transport.mode=http` 配置面确认；PyHive 连通实测见 tasks 执行日志 |

## 2. 现状耦合点清单（调研事实）

| # | 位置 | 事实 |
|---|------|------|
| C1 | `exec/engine/__init__.py:32-37` | `_ADAPTER_FACTORIES` 惰性工厂注册表，未装驱动环境零影响 |
| C2 | `compiler/dialect/__init__.py:38-43` | `_RENDERER_FACTORIES` 同构注册表；`resolve_dialect()` SQL_DIALECT 显式优先、缺省跟随 DB_BACKEND |
| C3 | `config/settings.py:30/33/38-66` | DB_BACKEND 四值 + 各后端 DSN 五件套（无运行时校验，非法值由 `get_adapter` ValueError 兜底） |
| C4 | `exec/engine/type_map.py:34-109` | `_EXACT` 已收录 Hive 风格类型名（STRING/TINYINT/BIGINT/DECIMAL/TIMESTAMP/DATE 等）；未知类型 fail-closed |
| C5 | `exec/guards.py:141-241` | 方言增量黑名单三处 per-dialect 分支（关键词/函数族/Lock） |
| C6 | `core/retrieval/exploration.py:79-87` | `dialect in ("mysql","clickhouse")` → 内联派生表，否则 TEMP 视图 |
| C7 | `mock/gmall/build.py:356-369` | 建表按 adapter.name 四分支，**else 兜底 schema_mysql——hive 不加显式分支会被静默喂 MySQL DDL（非 fail-closed，必修）** |
| C8 | `eval/eval_runner.py:387-514` | `--backend` choices + 三 smoke 同构（连通 + 只读加固 + 三护栏整链路） |
| C9 | `semantic/catalog_loader.py:419-452` | 只经 adapter.physical_columns 读元数据；非 duckdb 读不到 fail-closed 不回退。**禁止新增 if backend 分支（AGENTS.md 铁律）** |
| C10 | `web/service.py:665-671` | ensure_db 仅 duckdb 启动期建仓，else 分支语义自然覆盖 hive |
| C11 | `tests/parity_utils.py` | parity 容差口径（rel=1e-5/abs=1e-6、行序无关）注释标明勿改，直接复用 |
| C12 | `tests/test_catalog_backend_parity.py` | `_BACKEND_RAW_TYPES` 需加 hive 条目（纯本地测试） |
| C13 | `requirements.txt` | 驱动统一声明 + 注释段（选型理由/否决项/上界 pin），不进 optional-dependencies |
| C14 | `.github/workflows/ci.yml` | 单文件 6 job；CH 期实锤 services 在 checkout 前启动挂不了仓库文件 → parity 用 step 内 docker run |

## 3. 总体架构

双轨接入，与 21-23 期同构：

```
语义目录/Planner ──> DSL(JSON) ──> sql_compiler（零 if-backend）
                                      │ get_renderer("hive")
                                      ▼
                          compiler/dialect/hive.py  HiveRenderer（十原语）
                                      │ SQL（Hive 方言）
                                      ▼
exec/guards.execute_sql（护栏框架引擎无关：白名单/审计门/扫描预检/超时/LIMIT）
                                      │ EngineAdapter 协议
                                      ▼
                          exec/engine/hive_adapter.py  HiveAdapter
                            （PyHive HTTP transport 连接 / 验证式加固 /
                              DESCRIBE 元数据 / CSV staging 灌数）
```

- 注册表各加一行：`_ADAPTER_FACTORIES["hive"]`、`_RENDERER_FACTORIES["hive"]`（惰性工厂隔离未装驱动环境）；
- **不变项**：golden 锚定 DuckDB；`tests/parity_utils.py` 容差口径；既有四
  renderer 与四 adapter 零改动；catalog_loader 不加分支。

**备选方案与否决**：① 经 Spark Thrift Server（兼容 HiveServer2 协议）接入
——引入 Spark 依赖重、且方言仍是 HiveQL，无增益；② 方言直接用 sqlglot
"presto"（Hive 兼容面更接近现代 SQL）——元数据/DDL 面仍须按 Hive 实报形态
处理，且 SQL_DIALECT 与 sqlglot 方言名一一对应的既有约定被打破，否决。

## 4. 技术栈选型对比（选型矩阵与否决理由）

> 既定要求（用户 2026-10-26 确认，二十三期 spec §4 固化）：每个决策点列出全部
> 实质候选（≥3）、多维对比与否决理由。本节为规格固定章节。

### S1 驱动选型（D2）

| 候选 | 协议形态 | 维护与生态 | Windows 安装 | 结论 |
|---|---|---|---|---|
| PyHive + thrift（HTTP transport） | HiveServer2 Thrift over HTTP | dropbox/PyHive 事实标准（0.7.0/2022 维护缓慢，精确 pin） | ✅ 纯 Python（M9 实测，无 sasl） | ✅ **选定** |
| PyHive[hive]（SASL binary） | Thrift + SASL | 同上 | ❌ `sasl` 无官方 wheel（C 编译），requirements 无法三平台统一，违反"本机全量测试"前提 | ❌ |
| impyla | Thrift（NOSASL/SASL/http） | Cloudera 维护 | ❌ 同样依赖 thrift_sasl/sasl 链路 | ❌ |
| pyhive-fix 等 fork | 同 PyHive | 非官方维护面 | ✅ | ❌ 供应链纪律否决 |
| DuckDB httpfs/hive 扩展 | 间接通道 | 实验态 | — | ❌ 违反执行引擎直连铁律 |

### S2 扫描行熔断承载（D3）

| 候选 | 时点强度 | 与既有铁律 | 结论 |
|---|---|---|---|
| EXPLAIN 成本估算预检 | 预检 | ❌ "cost 估算不混入"铁律 | ❌ |
| hive.mapred.mode=strict | 仅禁全表扫描笛卡尔，非行数熔断 | 不能冒充行数熔断 | ❌（黑名单外不计入预算面） |
| 诚实不可计量 + 兜底 | 后置 | ✅ 同 CH 先例（spec D3） | ✅ **选定**：`explain_scan_rows` 返回 `(0, False)`，warning 留痕；兜底 = LIMIT 硬上限 + 客户端超时 + 部署侧配额 |

### S3 只读承载与加固方式（D4）

| 候选 | 强度 | fail-closed 校验 | 结论 |
|---|---|---|---|
| 运行时账号仅 SELECT（SQL 标准授权）+ 方言黑名单 + 验证式 harden | 账号层 + 语句层 + 连接层三防线 | ✅ harden 验证会话配置面 | ✅ **选定**（无会话级只读，harden 验证面比 CH 更窄，见 D4 明示清单） |
| Ranger 精细授权 | 最强 | — | ❌ 引入 Ranger 服务，CI/部署重量不可接受 |
| HDFS 存储式授权 | 进程层 | 难 CI 化 | ❌ 单容器 file:// 仓库无 HDFS 权限面 |

### S4 spine（fill_gaps）实现（D7）

| 候选 | 版本安全 | 语义确定性 | 结论 |
|---|---|---|---|
| `posexplode(split(space(n),' '))` CTE（M3 实测） | Hive 0.13+ UDTF，稳定 | ✅ N 纯 Python 桶数计算（同 CH 口径） | ✅ **选定** |
| 方言层 CompileError 诚实降级 | 最稳 | 砍 fill_gaps 能力 | 备用兜底（M3 实测通过，不启用） |
| 数仓日历/数字辅助表 | 改 schema 单一来源 | — | ❌ 动全部后端数仓与 golden |

### S5 探索层 sec_* 安全面路线（D8）

| 候选 | 权限要求 | 治理语义 | 结论 |
|---|---|---|---|
| 内联派生表（表名重写到 sec_*） | 零 DDL | 与 MySQL/CH 同一 `build_secure_projection` | ✅ **选定** |
| TEMPORARY TABLE | 需建表权限（只读账号无） | 会话级 | ❌ |
| 普通视图 + 专用库 | 需建库/建视图权限 | — | ❌ |

### S6 建仓 DDL 通道（D9）

| 候选 | 单一来源 | 维护成本 | 结论 |
|---|---|---|---|
| sqlglot 运行时转译（read=duckdb, write=hive）+ 确定性后处理 | ✅ schema.py 唯一来源 | 低 | ✅ **选定** |
| 手写第二份 DDL | ❌ 漂移风险 | 高 | ❌ 单一事实源铁律 |
| 一次性生成代码入库 | ❌ 生成物入库 | — | ❌ 同上 |

### S7 建仓灌数通道（D10）

| 候选 | 吞吐 | 复杂度 | 结论 |
|---|---|---|---|
| CSV staging + `LOAD DATA LOCAL INPATH`（每表一条 LOAD） | 高 | staging 双路径配置 | ✅ **选定**（M4 实测；服务端本地路径语义 → CI 卷挂载同路径） |
| `INSERT INTO ... VALUES` 分批 | ❌ 每条语句一个 job，65 万行不可行（§12 预研 + M 实测口径） | 低 | ❌ 协议事实否决 |
| WebHDFS 上传 + LOAD DATA INPATH | 高 | 需 HDFS 服务 | ❌ 单容器无 HDFS |
| ACID streaming write | — | Hive 4 已移除 Streaming API | ❌ |

### S8 CI 形态（D13）

| 候选 | 时序 | 可控性 | 结论 |
|---|---|---|---|
| step 内 `docker run apache/hive:4.0.0`（SERVICE_NAME=hiveserver2 + 内嵌 Derby） | checkout 后启动，可挂卷 | 自写就绪循环 | ✅ **选定**（照 CH 先例，C14） |
| services 容器 | checkout 前启动，挂不了仓库内配置 | 健康检查弱 | ❌ CH 期实锤 |
| 起 metastore+HS2 双容器 compose | 重 | 慢 | ❌ 单容器已满足 |

### S9 物理元数据源（D11）

| 候选 | 版本面 | 权限要求 | 结论 |
|---|---|---|---|
| 逐表 `DESCRIBE <table>` 解析（M5） | Hive 3/4 通用 | 零额外 | ✅ **选定**（28 表 28 条，量级可接受；解析器单测锁定） |
| INFORMATION_SCHEMA.COLUMNS | Hive 4 才有且依赖 metastore SQL 授权配置 | 额外配置 | ❌ 版本兼容面窄 |
| metastore thrift 直连 | — | 额外通道/依赖 | ❌ 打破单连接面 |

### S10 时区确定性承载（D12）

| 候选 | 侵入面 | 结论 |
|---|---|---|
| 容器/服务器 TZ=UTC | 零侵入 | ✅ **选定**（Hive 无会话时区设置面，同 CH 主承载口径） |
| 会话 timezone 设置 | Hive 无此面 | ❌ 不存在 |
| 编译产物 toTimeZone 包裹 | 方言污染 | ❌ |

## 5. 关键决策（D1-D13）

### D1 后端取值与期次

`DB_BACKEND=hive`；`SQL_DIALECT` 缺省跟随 DB_BACKEND 解析为 sqlglot `hive`
方言。二十五期（二十三期 spec §12 预写"二十四期"为笔误顺延，二十四期实际
为物理元数据面下沉）。

### D2 驱动与传输通道

`pyhive>=0.7,<1` + `thrift>=0.16,<1`，**HTTP transport**（`transport='http'` +
`httpPath='cliservice'`，认证 NONE/LDAP 部署面）：唯一能三平台统一
requirements 的通道（S1）；SASL binary 为部署备选（CI Linux 可用，本机
Windows 无法装 sasl——requirements 统一性优先）。客户端 socket 读超时 =
应用预算 +5s（服务端先超时、连接不报废口径对齐 MySQL；超时后连接报废风险
见 R3）。

### D3 扫描熔断：诚实不可计量

`explain_scan_rows` 常量返回 `(0, False)`（Hive EXPLAIN 无 actual rows），
护栏框架 warning 留痕（每查询一条，与 CH 同口径）；真实预算兜底 = LIMIT
硬上限 + 客户端超时 + 部署侧配额（Tez/MapReduce 行数无 per-session 配额，
部署要求文档明示，不冒充服务端熔断）。

### D4 只读三防线与验证式 harden

1. **账号层**（部署要求）：运行时账号仅 `GRANT SELECT ON DATABASE gmall`（SQL
   标准授权 `hive.security.authorization.enabled` 或存储授权）；建仓账号分离；
2. **语句层**：方言增量黑名单（见 T4：DDL/DML/ADMIN/LOAD/DFS/MSCK/RESET/SET
   等 HiveQL 特有语句族 + `system:...` UDF 族）；
3. **连接层 harden（验证式）**：执行 `SET` 回读会话配置——验证
   `hive.mapred.mode=strict`（若部署配置）与当前用户非匿名可写面；
   **明示验证清单（fail-closed）**：harden 校验 `SET -v` 输出中
   `hive.security.authorization.enabled` 与部署声明一致 + `SELECT current_user()`
   非建仓账号（两账号分离防误配）。验证面窄于 CH（无 per-profile 预算可读），
   属 Hive 平台事实，文档诚实记录。

### D5 超时与取消

PyHive 无 TCancelOperation 封装（§12 预研成立）→ `cancel()` 空操作；超时
承载 = 客户端 socket 读超时（D2 的 +5s 口径）+ 部署侧 HiveServer2 查询
超时配置（`hive.server2.query.timeout`，部署要求建议项）。超时异常归一：
PyHive 抛 `socket.timeout`/`HiveServer2Error` 文本 → `QueryTimeoutError`。

### D6 方言十原语 Hive 口径（M1/M2/M6/M7 实测）

| 原语 | Hive 口径 |
|---|---|
| quote_ident / quote_column | 反引号 + 内部翻倍（同 MySQL/CH） |
| timestamp_literal | `CAST('...' AS TIMESTAMP)`（无 toDateTime 简写） |
| date_trunc | day=`to_date({e})`；week=`date_sub({e}, pmod(datediff({e}, '1970-01-05'), 7))`（周一锚定，M2）；month=`trunc({e}, 'MM')`；quarter=`trunc({e}, 'Q')`（M1 实测，3.x 兼容性见 R5） |
| date_add | day 透传 `date_add({e}, n)`；month/year=`add_months({e}, ±n)`（月末钳制 M 实测）；INTERVAL 字面量转译照 `pg_interval_literal` 先例 fullmatch + CompileError |
| divide | 透传 `{n} / NULLIF({d}, 0)`（DECIMAL 除法自带高精度，M6；与 PG 同路） |
| having_metric_ref | 引用别名（M7 实测，同 MySQL/CH 路线） |
| spine_cte_sql | `SELECT idx AS <alias> FROM (SELECT 1) one LATERAL VIEW posexplode(split(space(<n-1>), ' ')) x AS idx, val`——N 纯 Python 桶数计算复用 CH 的 `_bucket_count` 算术（D7） |
| with_keyword | `WITH`（非递归） |
| escape_string_literal | 反斜杠转义系：`\` 翻倍 + `'` 写作 `\'`（Hive 默认反斜杠转义，同 CH；无会话级 pin 配置面） |

### D7 spine（fill_gaps）

主选 posexplode CTE（S4/M3 实测）；若 CI parity 实测 CTE 与 join 语义边界
不符，回退 CompileError 诚实降级并在文档标注能力差异（能力边界诚实原则）。
负向窗口 / 未知步长 fail-closed 沿用 `parse_interval_step` 先例。

### D8 探索层 sec_*

`exploration.py` 内联派生表集合加 `"hive"`（同 mysql/clickhouse）；无
`install_secure_views` 方法（Hive 无 TEMP VIEW），方法存在性分派自然覆盖。

### D9 建仓 DDL 通道

`mock/gmall/schema_hive.py`：`sqlglot.transpile(ddl, read="duckdb", write="hive")`
+ 确定性后处理：① 剥 `PRIMARY KEY`/索引残留；② 追加
`ROW FORMAT DELIMITED FIELDS TERMINATED BY ',' NULL DEFINED AS '\\N' STORED AS
TEXTFILE`（null 标记双反斜杠 M4 实测）；③ 无 ENGINE/ANALYZE 步。
对拍测试照 `test_schema_clickhouse.py` 模板（首表 DDL 快照 + 性质断言）。
**严禁手写第二份表结构。**

### D10 建仓灌数通道

`HiveAdapter.insert_dataframe(conn, table, df)`：df 清空值统一 None → 逐列
转字符串（None 写 `\N`）→ 写 CSV 到 **client staging 目录**
（`HIVE_STAGING_CLIENT_DIR`，默认 `<root>/build/hive_staging`）→ 执行
`LOAD DATA LOCAL INPATH '<server_dir>/<table>.csv' INTO TABLE <table>`
（**server staging 目录** = `HIVE_STAGING_SERVER_DIR`，为 hiveserver2 进程
可见路径；CI 中两路径经 docker 卷映射为同一内容）。每表一条 LOAD；分批
不适用（LOAD 本身批量）。

### D11 physical_columns：DESCRIBE 逐表解析

逐表 `DESCRIBE <table>`（dsn database 限定），解析 `col_name/data_type`
两列（剥离 `# Partition Information` 段与空行，遇分区段 fail-closed 报错
——数仓单一来源无分区，出现即漂移）；dtype 交 `physical_from_rows` →
`map_dtype` 归一（未知类型 fail-closed）。表清单来源：`SHOW TABLES`（一次
调用，零额外权限）。

### D12 时区

容器/服务器 TZ=UTC 单承载（S10）；文档部署要求明示。

### D13 CI hive-parity job

`ci.yml` 新增 `hive-parity`（照 clickhouse-parity 同构）：step 内
`docker run -d --name hive-ci -p 10000:10000 -e SERVICE_NAME=hiveserver2
-e TZ=UTC -v <conf>/hive-site.xml:/opt/hive/conf/hive-site.xml:ro -v
<staging>:/opt/dataagent_staging apache/hive:4.0.0`（单容器内嵌 Derby +
HTTP transport conf + staging 卷挂载）→ 自写 beeline 就绪循环（40×10s，
超时 dump docker logs）→ `CREATE DATABASE IF NOT EXISTS gmall`（beeline）→
`python -m mock.init_hive`（HIVE_TEST_* 指向 localhost:10000）→
`python -m pytest tests/test_hive_backend_parity.py -q`。CI 时长预期比 CH
增数分钟（镜像 ~1.8GB + HS2 启动 2-4 分钟，M 实测）。

## 6. 安全模型对照

| 防线 | duckdb | mysql | postgres | clickhouse | **hive（本期）** |
|---|---|---|---|---|---|
| 只读承载 | 只读连接（bool 配置） | 只读账号 + SET SESSION READ ONLY | 只读账号 + default_transaction_read_only | readonly=1 profile | **账号仅 SELECT（SQL 标准授权）** |
| 转义确定性 | 单引号翻倍 | NO_BACKSLASH_ESCAPES + 翻倍 | 单引号翻倍（pin） | 反斜杠系 | **反斜杠系（无会话 pin，方言层固定）** |
| 扫描预算 | EXPLAIN ANALYZE | EXPLAIN ANALYZE | EXPLAIN ANALYZE | profile max_rows_to_read | **诚实不可计量 + LIMIT/超时/部署配额兜底** |
| 超时 | 看门狗 interrupt | max_execution_time | statement_timeout | max_execution_time | **客户端 socket 超时 + 部署 hive.server2.query.timeout** |
| 取消 | conn.interrupt() | KILL QUERY | pg_cancel_backend | 无（服务端自限） | **无（TCancelOperation 未封装，空操作）** |
| sec_* 安全面 | TEMP 视图 | 内联派生表 | TEMP 视图 | 内联派生表 | **内联派生表** |
| 方言黑名单 | 通用 | mysql 族 | pg_* 族 | CH 族 | **HiveQL 族（DDL/DML/LOAD/DFS/MSCK/RESET/SET/ADMIN）** |

## 7. 配置设计

```bash
# .env 增量（HIVE_* 组，运行时查询用只读账号；建仓 python -m mock.init_hive
# 复用本组但要求账号可写，两账号分离见 §9）
HIVE_HOST=127.0.0.1
HIVE_PORT=10000                # HiveServer2 口（HTTP transport）
HIVE_USER=dataagent_ro
HIVE_PASSWORD=
HIVE_DATABASE=gmall
# 建仓 staging（D10）：client 目录为本机生成 CSV 路径；server 目录为
# hiveserver2 进程可见路径（CI/docker 部署中与 client 同卷映射）
HIVE_STAGING_CLIENT_DIR=build/hive_staging
HIVE_STAGING_SERVER_DIR=/opt/dataagent_staging

# parity 测试指向（未配置自动跳过，对齐 *_TEST_* 口径；不进 .env.example）
HIVE_TEST_HOST=...
HIVE_TEST_PORT=10000
HIVE_TEST_USER=dataagent_ro
HIVE_TEST_PASSWORD=...
HIVE_TEST_DATABASE=gmall
HIVE_TEST_STAGING_SERVER_DIR=/opt/dataagent_staging
```

adapter DSN 可注入（parity 指向独立实例）；缺省读 settings。

## 8. 确定性与可复现

- 种子 42、`AS_OF_DATE=2025-12-31`、单 rng 顺序消费不变；`init_hive` 消费
  同一 `build_gmall_tables` 的同一 DataFrame 序列（仅入库通道不同），行数
  输出与 `init_duckdb` 一致（init 打印对照）；
- golden 锚定 DuckDB 方言不变；`eval --backend hive` 仅连通性冒烟；
- parity 比对口径复用 `tests/parity_utils.py` 勿改。

## 9. 版本基线与部署要求

- **兼容基线**：Hive >= 3.1（企业存量面）；CI 锚定 `apache/hive:4.0.0`。
  `trunc(ts,'Q')` 在 3.1 的支持情况为 R5 开放问题（CI 4.0.0 已实测可用）；
  版本演进跟随策略沿 21/22 期 §8.1（升级先跑全量 + 对应 parity）。
- **部署要求（运行时账号）**：1. 仅 `GRANT SELECT`（SQL 标准授权或存储授权）；
  2. 建议配置 `hive.server2.query.timeout` 与 QUERY_TIMEOUT_MS 同源；
  3. 服务器 TZ=UTC；4. hive-site 启用 `hive.server2.transport.mode=http`
  （或部署侧切 SASL binary 并自备三平台驱动方案——requirements 统一性收敛到 HTTP）。
- **部署要求（建仓账号）**：CREATE/INSERT/DROP ON gmall（与运行时账号分离）；
  hiveserver2 进程可写 staging server 目录。

## 10. 风险与开放问题

| # | 风险 | 处置 |
|---|------|------|
| R1 | CI 首跑收敛轮次多（HS2 启动 2-4 分钟 + 镜像拉取 + 配置坑；CH 曾九轮） | 就绪循环超时 dump docker logs；conf 与 staging 卷挂载形态按 M4/M10 实测固化 |
| R2 | posexplode CTE 在复杂 join 语义下行为不符预期 | 兜底 CompileError 诚实降级（D7）；parity 含 fill_gaps 用例裁决 |
| R3 | 客户端 socket 超时后连接报废进池 | 读超时 = 预算 +5s 口径对齐 MySQL；超时异常路径连接不回收（照先例） |
| R4 | PyHive 维护停滞（0.7.0/2022） | 精确上界 pin + fork 预案（S1）；依赖注释段写明升级跟随策略 |
| R5 | `trunc(ts,'Q')` 在 Hive 3.1 的可用性未验证 | 基线声明 + CI 锚定 4.0.0；若企业部署 3.1 报错，方言层换等价式（date_format 算术）为后续小版本项 |
| R6 | DESCRIBE 输出形态跨版本漂移 | 解析器单测锁定 M5 实测形态；漂移 fail-closed 报错不静默 |
| R7 | `\\N` null 标记转义在 Python→Thrift 链路再吃一层 | D10 用 Python 字符串显式 `'\\\\N'` 口径 + init 行数对照兜底；单测锁定 CSV 写出字节 |

## 11. 成功标准

1. 本地 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿
   （hive parity 未配置自动跳过）；
2. golden 评测 duckdb oracle 31 例不变绿（零破坏红线）；既有四 renderer
   编译产物逐字节不变（dialect 单测锁定）；
3. CI 五 job 全绿：四既有 job 不变 + `hive-parity` 首绿（Hive 链路首次
   真实验证）；
4. `python -m mock.init_hive` 建仓成功且行数与 `init_duckdb` 输出一致；
5. `eval --backend hive` 冒烟接线 fail-fast 生效；
6. 文档三件套齐全（本 spec + plan + tasks），AGENTS.md 增 Hive 契约小节。
