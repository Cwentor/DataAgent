# 评审报告｜深度审计：代码深度与生产健壮性（2026-09）

> 审计日期：会话内实时审计 · 审计方式：逐文件通读 + 安全防线实证渗透测试 + 全量测试验证
> 审计范围：agent/ compiler/ exec/ semantic/ security/ auth/ present/ web/ audit/ eval/ mock/（非测试 Python ~5893 行）

---

## 1. 总体定性结论

| 维度 | 结论 |
|---|---|
| **项目实质评估** | **具备雏形的精简生产级（Lean Production）** —— 不是 Toy Prototype，但远未 Production Ready |
| **总代码密度评分** | **74 / 100** |

**评分构成说明**（核心逻辑行占比，扣除注释/空壳/__init__ 转发后）：
- **逻辑密度 +**：compiler 694 行、exec/guards.py 286 行、agent/heuristic.py 436 行几乎全是真实实现，无空壳目录、无死代码、无 mock 占位函数。每个关键机制（超时看门狗、扫描行熔断、DSL 层 RLS、自愈重写循环、审计双写）都是可运行且有测试覆盖的真实代码。
- **数据面 −**：语义目录（17 字段）、权限策略（3 个）、口径文档（7 条）、用户（3 个）全部硬编码在 Python 里，规模停留在演示级；单方言（仅 DuckDB）；无连接池/并发闸；固定口令盐。

**一句话定性**：*"机制面真实、数据面玩具"* —— 架构决策（Spec-Driven、DSL 契约、守卫前移、自愈闭环）是工业级的，但语义层/权限层/口径层全部写死在代码常量中，且执行层缺并发治理。

---

## 2. 核心模块深浅度对照表

| 模块路径 | 当前实现方式（具体实现） | 成熟度评级 | 致命缺失点 |
|---|---|---|---|
| compiler/sql_compiler.py (694 行) | 受控 DSL → SQL 确定性编译器：_resolve_window/_shift_window 时间代数（L77-153）、_quote_ident+_literal 双保险防注入（L64-204）、comparison 双窗口 CTE（L352）、Top-N ROW_NUMBER（L457）、fill_gaps generate_series spine（L547）、窗口函数（L279） | **基础可用**（结构性安全，非 AST 编译器） | ① 时间过滤硬编码 f.order_time（L234、L398）：无法对 refund_time/register_time 做时间窗口；② 单方言：date_trunc/generate_series/UNNEST 纯 DuckDB 语法，无方言抽象；③ 无 sqlglot/sqlparse AST，无语法回读校验 |
| semantic/ (catalog.py 72 行 + dsl_schema.py 271 行) | 结构化指标建模：聚合/比率/窗口 discriminated union（dsl_schema L123-177）、extra="forbid" 严格契约、受限枚举；catalog 静态字段→表映射 | **基础可用**（契约严格，目录静态） | ① catalog.py L25-47 纯硬编码字典：DuckDB 里明明有 _field_metadata 信息模式表（mock/metadata.py L9），语义层却完全独立不读它——新增表/字段必须改 Python 代码；② 无衍生公式/多周期滑动窗口建模（仅 ratio/window 两种模板） |
| security/ + auth/ | RLS 在 **DSL 层强制注入**而非 SQL AST 层：apply_policy 把 row_filters 追加进 dsl.filters（guard.py L76-78）→ 编译时必然进 WHERE；纵深四层：guard（事后）+ scope（Prompt 前移，scope.py L60）+ gateway（服务端映射 principal，gateway.py L70-147）+ 自愈后重查权限（web/service.py L160）；手写 HS256 JWT 校验 exp/iss/aud/nbf（tokens.py L77-115） | **基础可用**（架构正确，内容硬编码） | ① 策略注册表只有 3 个写死主体（policy.py L38-56），RLS 仅支持"字段 IN 值列表"一种谓词形态（L34）；② 固定全局盐 b"futurebi-salt-v1"（identity.py L26）：同密码同哈希，可离线预计算；③ 见 §3 实锤 P0-1：只读校验可被表函数绕过 |
| exec/guards.py (286 行) | 真实三重护栏：① 线程看门狗 + conn.interrupt() 超时取消（L182-222，DuckDB 无原生 timeout 的正确绕行）；② EXPLAIN ANALYZE 预检 + 解析算子行数做预防式扫描熔断（L249-269 / parse_scan_rows L164）；③ 返回行数 LIMIT 硬上限（L279-283）；异常体系可区分 timeout/scan/limit 供上层自愈 | **基础可用偏上**（本仓最扎实模块） | ① 无连接池：web/service.py L257-266 每次请求新建 duckdb.connect()、用完即关，无复用；② 无全局并发信号量：多个病态查询可同时跑 EXPLAIN ANALYZE+正式执行；③ EXPLAIN ANALYZE 会真实执行一次查询，病态 SQL 被执行两次；④ 只读校验为黑名单（见 P0-1） |
| agent/（pipeline/router/agent/heuristic/clarify/slotfill/rag） | **非线性链路**：意图三分类+澄清反问（router.py L49-93）→ NL→受控 DSL JSON→Pydantic 严格校验→编译→受控执行；**自愈闭环真实**：_execute_with_self_heal（web/service.py L120-165）捕获编译/执行错误→rewrite_dsl 喂回 LLM 重写→重新 apply_policy→重试（SQL_SELF_HEAL_MAX_RETRIES）；LLM 故障自动降级启发式（pipeline.py L43-51）；澄清槽位回填（slotfill.py L87-104） | **基础可用**（自愈/路由/降级全部真实） | ① 无状态图/ReAct 编排，是函数式线性 + 单层自愈循环，无 agent 级状态机；② RAG 是 bigram 重叠打分（rag.py L23-45），非向量化召回，且语料仅 7 条硬编码（glossary.py L33-90）；③ 启发式是关键词规则，超出即拒绝——诚实的边界，非缺陷 |

---

## 3. "薄封装"代码实锤清单（P0 隐患）

以下为逐条实证（渗透测试在 futurebi 环境实际运行验证，非静态推断）：

### 🔴 P0-1：只读 SQL 校验是"剥注释+剥字符串+关键词黑名单"，可被 DuckDB 表函数绕过
- **位置**：exec/guards.py L126-137（assert_read_only_sql）+ 黑名单 L72-81（仅 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/PRAGMA）
- **实锤（渗透测试输出）**：
  - read_csv → ACCEPT（SELECT * FROM read_csv('/etc/passwd') 被放行）
  - read_csv_glob → ACCEPT（SELECT * FROM read_csv('/etc/*') 被放行）
  - comment_delete → REJECT（/*x*/DELETE 正确拦截，注释剥离有效）
  - multi → REJECT（分号拦截有效）
- **危害**：DuckDB 的 read_csv/read_json/read_parquet/read_csv_auto 表函数可读取任意本地文件。当前链路因 SQL 全由编译器生成、攻击面收窄，此防线"侥幸未失效"；但任何未来接入外部 SQL 的路径都会直接穿透。

### 🟠 P0-2：语义目录与数据库元数据"双轨制"——库里有的不用，硬编码一份
- **位置**：semantic/catalog.py L25-47 vs mock/metadata.py L9-38 + mock/init_duckdb.py L152-173（_field_metadata 表已入库）
- **实锤**：项目自己的 mock 数仓里写入了 _field_metadata/_table_metadata 元数据表，但 semantic/catalog.py 的 COLUMNS/JOIN_RULES（L64-71 连接规则直接是裸 SQL 字符串）完全不读它。新增一张表 = 改三处 Python 代码（catalog + metadata + init_duckdb），无运行时 schema 发现。

### 🟠 P0-3：权限系统"架构很正，内容全写死"
- **位置**：security/policy.py L38-56（3 个主体）+ L34-35（RLS 谓词仅 _province_filter 一种工厂）
- **实锤**：RLS 注入机制（guard.py L76-78，DSL 层注入→编译进 WHERE）是工业级思路，但策略来源是代码常量：无策略持久化、无动态加载、无通用谓词模板（tenant_id = :principal 这类表达式做不到）。用户表（identity.py L59-78）与策略表互不关联——改权限要改代码重启。

### 🟡 P0-4：口径文档 RAG = 词典查找 + bigram，无任何向量化
- **位置**：agent/rag.py L23-45、agent/glossary.py L33-90
- **实锤**：retrieve() 的"语义匹配"是别名子串命中（alias in q，L36）+ 字符 bigram 重叠（L40）。语料是 7 条硬编码 GlossaryDoc。**"同义词向量化召回"为零**——但注释诚实标注了"零依赖、确定性、可复现"，属于有意的功能裁剪而非伪装。

### 🟡 P0-5：口令哈希盐固定
- **位置**：auth/identity.py L26（_SALT = b"futurebi-salt-v1"）+ L29-31
- **实锤**：PBKDF2-SHA256 迭代 200k 正确，但全局固定盐：所有用户同密码即同哈希，离线彩虹表/字典攻击可批量预计算。每用户随机盐是 OWASP 基线。

### 🟡 P0-6：执行层无连接池、无并发闸
- **位置**：web/service.py L257-266
- **实锤**：每次 run_query 执行 duckdb.connect(settings.DB_PATH, read_only=True)，用完 close()。无复用、无连接上限、无请求排队信号量——多个超大查询可同时各占一个连接 + 各跑一轮 EXPLAIN ANALYZE，单机实例可被打满。exec/guards.py 有"护栏"但没有"闸门"。

---

## 4. 走向工业级的最小改造建议（Top 3）

只改核心文件、不加目录结构，按优先级：

### 🥇 Top 1：compiler/sql_compiler.py —— 引入 sqlglot，从"拼字符串"升级为"AST 生成 + 多方言"
- **做法**：compile_sql 的输出从 str 改为 sqlglot AST（sqlglot.exp.Select），最后 ast.sql(dialect=...) 渲染。语义层保留——DSL 契约不变，只是把 _metric_expr/_filter_sql 等从 f-string 换成 exp.func("SUM", ...) 节点。
- **一举解决三个问题**：
  1. 单方言 → DuckDB/ClickHouse/MySQL/Presto 一键切换（date_trunc、generate_series 等由 sqlglot 方言层处理）；
  2. 时间过滤硬编码（L234/L398 f.order_time）→ 按 TimeFilter 指向的字段取列，消除正确性缺陷；
  3. 只读校验（P0-1）→ 改为 sqlglot.parse 后断言根节点为 Select 且无 read_csv 等文件表函数，彻底替代黑名单。
- **成本**：一个 pip install sqlglot，改动集中在 3-4 个 _*_expr 函数，694 行规模不变。

### 🥈 Top 2：semantic/catalog.py + security/policy.py —— 双轨改数据驱动
- **做法**：
  - catalog 改为启动时从 DuckDB information_schema/_field_metadata 加载 + 一个 YAML 增量覆写（别名/类型/表关系），COLUMNS 变运行时构建而非模块常量；JOIN_RULES 改为受控连接声明（join type + on 字段对），不再裸拼 SQL。
  - policy 改为策略文件（YAML/JSON）加载，RLS 谓词支持参数化模板：{"field": "province", "operator": "in", "param": ":principal.provinces"}，主体策略从 3 个写死常量变为可运维配置。
- **解决**：P0-2、P0-3，并让"新增表/新权限"从改代码变为改配置。

### 🥉 Top 3：exec/guards.py —— 补上"闸门"：连接池 + 并发信号量 + 预检合一
- **做法**：
  - 连接治理：自研固定容量池（N 个只读连接 + queue.Queue 借还，或 DBUtils.PooledDB），替换 web/service.py L257-266 的"每次新建/关闭"；
  - 并发闸：threading.BoundedSemaphore(MAX_CONCURRENT_QUERIES) 包住 _run_with_timeout，超配额即排队（配合既有超时看门狗形成"排队+熔断"双保险）；
  - 预检合并：EXPLAIN ANALYZE 结果同时产出扫描行数与执行计划，若预算不足则直接拒绝，避免病态 SQL 被执行两次（当前 L251-273 预检+执行重复跑）。
- **解决**：P0-6 与 EXPLAIN ANALYZE 双重执行问题。

---

## 附：健康度佐证
- 197 个单测全绿（29.9s，覆盖 compiler/security/exec/auth/agent/router/scope 等全部核心模块）
- eval/golden_dataset.json 19 个用例支持 oracle/agent 双模式评测
- 审计双写（JSONL+DuckDB）带跨进程文件锁（audit/store.py L53-93，msvcrt/fcntl 双平台实现）
- 前端 web/static/ 三件套真实存在（app.js 12.5KB）

**结论：机制是"真金"，规模是"银样"——方向对，欠火候。**

---

## 附 2：审计结论核实 + P0 隐患修复记录（会话内完成）

> 本节由后续会话对报告逐条核实并修复后追加。核实方式：逐文件对照行号通读 +
> 渗透测试实证 + 全量测试验证。**修复后全量 224 个单测全绿（30.5s），black/ruff 全绿。**

### 核实结论（报告引用均属实）

| 报告条目 | 核实结果 |
|---|---|
| §2 compiler 694 行、exec/guards.py 286 行、agent/heuristic.py 436 行真实实现 | ✅ 属实（行数与实现方式一致） |
| §2 semantic catalog 硬编码、不读 _field_metadata | ✅ 属实（catalog.py 原为常量字典；init_duckdb 确已写元数据表） |
| §2 security 仅 3 主体、RLS 单谓词形态 | ✅ 属实（policy.py 原为 3 个写死主体） |
| P0-1 只读校验"剥注释+黑名单"可被表函数绕过 | ✅ 属实且**修复前可复现**：`parquet_kv_metadata`/`parquet_schema`/`parquet_file_metadata`/`sqlite_query`/`postgres_query` 均 ACCEPT；`"read_csv"('...')` 引号函数名 DuckDB 真实接受 |
| P0-2 双轨制 | ✅ 属实（元数据表已入库但 catalog 不读） |
| P0-3 权限内容全写死 | ✅ 属实 |
| P0-4 RAG 词典+bigram，无向量化 | ✅ 属实（注释诚实标注"零依赖、确定性、可复现"，属有意裁剪） |
| P0-5 口令盐固定 | ✅ 属实（_SALT 固定 b"futurebi-salt-v1"） |
| P0-6 无连接池/并发闸 | ✅ 属实（web/service.py 每次新建/关闭连接） |
| 附 §3 漏洞描述与测试佐证（197 单测、19 golden 用例、审计双写、前端三件套） | ✅ 属实 |

### P0 修复清单（全部落地并测试）

| 编号 | 修复 | 位置 |
|---|---|---|
| 🔴 P0-1 | **只读校验改为 sqlglot AST 结构化校验**（根节点必须 SELECT/UNION；全树拒绝文件/外部源函数家族 read_*、parquet_*、sqlite_*、*_scan/*_query/*_execute/*_glob 等；拒绝引号/字符串字面量表引用与 SELECT INTO），叠加既有正则层作防御纵深。渗透验证 26/26：`parquet_kv_metadata`/`sqlite_query`/引号函数名等新绕过面全部 REJECT，正常 SELECT/WITH/UNION 放行，read_csv 数据外泄被 execute_sql 拦截 | `exec/guards.py`、`requirements.txt`（+sqlglot）、`tests/test_exec.py` |
| 🟠 P0-2 | **语义目录数据驱动**：新增 catalog_loader，从 DuckDB information_schema 物理元数据 + `config/semantic.json` 覆写构建目录；JOIN_RULES 改为受控 JoinRule 声明（join type+字段对）不再裸拼 SQL；compiler/guard 动态读 `catalog.XXX`；服务启动 ensure_db 自动刷新。新增表/字段/连接只改配置 | `semantic/catalog_loader.py`（新）、`semantic/catalog.py`、`compiler/sql_compiler.py`、`security/guard.py`、`web/service.py`、`config/semantic.json`（新）、`tests/test_catalog_loader.py` |
| 🟠 P0-3 | **权限策略数据驱动 + 参数化 RLS**：策略与主体属性表移到 `config/policies.json`；RLS 谓词支持 `{"field","operator","param":"principal.provinces"}` 模板，施加时按主体解析；ensure_db 自动刷新。加主体/省份只改配置 | `security/policy_loader.py`（新）、`security/policy.py`、`security/guard.py`、`config/policies.json`（新）、`tests/test_policy_loader.py` |
| 🟡 P0-4 | **RAG 升级为 TF-IDF 稀疏向量余弦**（字符 bigram 特征 + IDF 加权 + 别名强命中），仍零依赖/确定性/可复现；无精确别名、仅语义相近表述（如"平均每单的金额"→客单价）可正确召回 | `agent/rag.py`、`tests/test_router.py` |
| 🟡 P0-5 | **口令哈希改每用户随机盐**：新格式 `pbkdf2_sha256$iter$salt$hash`（secrets 随机盐），verify 兼容旧固定盐格式；默认用户哈希已重新生成（admin123/analyst123/bob123） | `auth/identity.py`、`auth/users.json`、`tests/test_auth.py` |
| 🟡 P0-6 | **执行层补"闸门"**：只读连接池（固定容量 + queue 借还 + 池满排队）+ 全局 BoundedSemaphore 并发信号量，替换"每次新建/关闭" | `exec/pool.py`（新）、`config/settings.py`、`web/service.py`、`tests/test_pool.py` |

### 验证证据
- 渗透测试：`python tools/_p0_1_verify.py` → 26/26 预期 + `EXFILTRATION BLOCKED (guards layer via execute_sql)`；
- 全量单测：`python -m pytest -q` → **224 passed**（30.5s，较修复前 201 增加 23 个 P0 回归测试）；
- 格式/静态：`black --check .`、`ruff check .` 全绿；
- 端到端：`python -m semantic.catalog_loader` / `security.policy_loader` 正常加载；analyst 查询完整链路自动注入 RLS 过滤并走连接池执行。

> 遗留（不在 P0 列表，属 §2 已知限制）：compiler 时间窗口仍按主事实表 `f.order_time` 硬编码
> （TimeFilter 无字段属性，属 DSL 契约扩展）；RAG 为稀疏向量而非神经网络向量化（保持零依赖与确定性）。
> 如需进一步，可参照报告 Top 1 建议在 DSL 层引入时间字段与 sqlglot 多方言。
