# 任务记录｜物理元数据面下沉 EngineAdapter（二十四期）

> 配套文档：设计方案 `../designs/2026-10-26-physical-columns-adapter-design.md` ｜
> 实施计划 `../plans/2026-10-26-physical-columns-adapter-plan.md`（步骤级勾选项在计划内，本文件只跟踪任务级状态）。
> 分支：`feat/physical-columns-adapter`（基线 master@9c460fd）。

## 任务总览

| # | 任务 | 依赖 | 验收口径 | 状态 | 提交 |
|---|------|------|---------|------|------|
| T1 | 类型归一模块 `exec/engine/type_map.py`（剥壳 + 规则表 + 四后端清单） | — | 表驱动用例绿（四后端类型全集逐项映射）；未知类型 fail-closed 且文案含原始类型 | ✅ 完成 | `fd226fc` |
| T2 | 协议扩展 + `DuckDbAdapter.physical_columns`（零变更锚） | T1 | 与旧 `_physical_columns` 逐项对拍相等；`_` 前缀表跳过；既有 engine 用例零破 | ✅ 完成 | `355ec20` |
| T3 | `MySqlAdapter.physical_columns`（`%s` 参数化，修 `?` 历史 bug） | T1, T2 | 用例断言 SQL 含 `%s`、不含 `?`、params 为库名；dtype 映射正确 | ✅ 完成 | `21bc82e` |
| T4 | `PostgresAdapter.physical_columns` + 新增 `POSTGRES_SCHEMA` 配置 | T1, T2 | settings/.env.example 同步；用例断言 `public` 过滤与 PG 类型映射 | ✅ 完成 | `e84a582` |
| T5 | `ClickHouseAdapter.physical_columns`（`system.columns` + `{db:String}`） | T1, T2 | 用例断言 `system.columns` + `parameters={"db": ...}`；自建连接必被关闭 | ✅ 完成 | `6c0c2ad` |
| T6 | `catalog_loader` 改道（删旧物理面实现 + 维度成员走 `run_query` + 失败语义） | T2-T5 | `tests/semantic` 全绿（含迁移后用例）；`tests/compiler` 零破；非 duckdb 空物理列抛错 | ✅ 完成 | `61bdcd4` |
| T7 | `ensure_db()` 按后端显式分派 | T6 | 三分支用例绿（duckdb 建仓 / 非 duckdb 不碰 DB_PATH / 异常透传） | ✅ 完成 | `787d8e3` |
| T8 | 跨后端目录一致性用例 + CI 三步冒烟 + 文档收口 | T1-T7 | 本地全绿（含新 `tests/test_catalog_backend_parity.py`）；CI 三 job 冒烟通过 | ✅ 完成（CI 待远端验证） | 见执行日志 |

依赖关系：T1 为唯一地基；T2 依赖 T1 且定义协议契约；T3/T4/T5 三者互不依赖、
可并行（各自 adapter 独立）；T6 汇聚 T2-T5；T7 依赖 T6；T8 收口。

## 服务级验证清单（CI 三 parity job 承担，本地不执行）

> 本期首次把"目录构建"纳入服务级验证——此前三个 parity job 只跑
> `tests/test_*_backend_parity.py`，`ensure_db()` / `refresh_catalog()` 在
> PG/CH 上从未被执行过（设计 §1.3 第 3 条）。

- [ ] `postgres-parity` 新增 `Catalog refresh smoke` 步骤通过（`DB_BACKEND=postgres`
      下 `python -m semantic.catalog_loader` 输出 `fact_table=order_detail`）
- [ ] `clickhouse-parity` 新增同名步骤通过（同时实锤只读账号可读 `system.columns`，设计 §10 R1）
- [ ] `mysql-parity` 新增同名步骤通过（实锤 `%s` 参数化在真实 pymysql 上生效，Review Focus 4）
- [ ] `lint` / `test` / 三 parity job 结果全绿（零破坏红线）
- [ ] 全量 `pytest -q` 在 CI 上绿（新增用例随 `test` job 跑）

## Review Focus 专项核对（T8 收尾时逐条打勾）

| # | 失效模式 | 覆盖用例 | 结果 |
|---|---------|---------|------|
| 1 | 类型覆盖清单不全（凭猜而非建仓产物枚举），漏项被静默降级为 `str` | T1 四份清单表驱动 + `test_unknown_type_fails_closed` | ✅ 46 用例全绿 |
| 2 | 四后端同源数据下目录不一致（表/列/dtype 三元组漂移） | T8 `tests/test_catalog_backend_parity.py` | ✅ 7 用例全绿；变异测试（CH float→str）可捕获 |
| 3 | `conn=` 注入路径回归（`tests/semantic/test_catalog_loader.py` 30+ 处依赖） | T2/T6 迁移后该文件原样全绿 | ✅ 迁移后全绿 |
| 4 | `%s` 参数化在真实驱动上失效（fake `_Conn` 掩盖，二十一期起未暴露） | T3 `test_physical_columns_uses_percent_placeholder` + CI `mysql-parity` 冒烟 | ✅ 单测绿；CI 冒烟待远端首跑 |
| 5 | 非 duckdb 后端库不可用时静默回退内置默认目录 | T7 三分支用例 + T6 `test_non_duckdb_empty_physical_raises` | ✅ 6+1 用例全绿 |

## 执行日志（按日期追加）

### 2026-10-26

- 分支 `feat/physical-columns-adapter` 自 master@9c460fd 切出。
- 调研（设计 §2 C1-C13）确认缺口不止"PG/CH 未接"：MySQL 分支的 `?` 占位符在
  真实 pymysql 上必抛 `TypeError`（该分支自二十一期起只被 fake 连接覆盖）。
- 设计方案定稿（D1-D8 + §4 选型矩阵 S1-S6）；实施计划与任务记录落盘。
- 待规格审阅通过后按 T1→T8 逐任务实施（每个任务单独提交，`feat:` 前缀）。

- **T1**（`fd226fc`）：`exec/engine/type_map.py` 落盘。两阶段归一（剥
  `Nullable/LowCardinality` 壳与类型参数 → 精确表查 + `U?INT(8|16|...)` 位宽兜底），
  未知类型 fail-closed。46 条用例覆盖四后端清单 + 剥壳矩阵 + 五类未知形态。
  ruff 的 `F507/UP031`（把 `"..." % ("gmall",)` 判为笔误）改为变量承载后规避。
- **T2**（`355ec20`）：协议新增第 8 方法 `physical_columns`，`physical_from_rows`
  抽为四 adapter 共用（`_` 前缀表跳过 + `map_dtype`）。DuckDB 实现与旧
  `catalog_loader._physical_columns` 逐项对拍相等（迁移等价性锚）。
- **T3**（`21bc82e`）：MySQL 侧改 `%s` 参数化，根因写入 docstring——
  pymysql `execute(sql, args)` 内部执行 `query % escaped_args`，`?` 必抛
  `TypeError: not all arguments converted`；新增 `test_question_mark_placeholder_breaks_percent_formatting`
  作为根因锚。顺带把库名取值从 `settings.MYSQL_DATABASE` 改为 `self._dsn["database"]`，
  使 dsn 注入（parity 测试指向独立库）自洽。
- **T4**（`e84a582`）：PG 侧 `%s` + `WHERE table_schema = %s`，新增
  `POSTGRES_SCHEMA`（默认 `public`）并同步 `.env.example`。
- **T5**（`6c0c2ad`）：CH 侧 `system.columns` + `{db:String}` 原生参数绑定
  （clickhouse-connect 的 `conn.query(sql, parameters=...)`）。
- **T6**（`61bdcd4`）：`catalog_loader` 改道。`build_catalog/refresh_catalog` 新增
  `adapter` 参数；删除 `_DTYPE_MAP`/`_map_dtype`/`_physical_columns`/`_fetch_all`；
  维度成员走 `adapter.run_query`；失败语义 D7 落地（非 duckdb 空物理列抛错、
  `--db` 对非 duckdb 显式拒绝）。dtype 缺省直接取 adapter 归一结果，不再二次映射。
- **T7**（`787d8e3`）：`ensure_db()` 四后端显式分派，仅 duckdb 在启动期建仓；
  新增 `tests/web/test_service_ensure_db.py` 六条用例（含异常透传）。
- **T8**：新增 `tests/test_catalog_backend_parity.py`（7 用例）——以 DuckDB 真实
  建仓为基准，另三后端用"驱动实际报告的原生类型形态"模拟后归一，断言目录三元组
  全等；刻意剥掉 overlay 的 `dtype` 以免"配置自比自"。CI 三个 parity job 各加
  `Catalog refresh smoke`（`python -m semantic.catalog_loader` + `grep
  "^fact_table=order_detail"`），CH 侧刻意用只读账号 `dataagent_ro` 以实锤
  `system.columns` 读权限（§10 R1）。`AGENTS.md` 关键约定新增"物理元数据面"条目。

**质量门（本地，`dataagent` conda 环境 / Python 3.12.14）**：`black --check .`
259 files 通过；`ruff check .` 全绿；`pytest -q` **1303 passed / 18 skipped /
0 failed**；`python -m eval.eval_runner` **31/31 通过**，`sql_ok` 锚点零漂移。

> 排障留痕：中途误用 base 环境（`D:\Env\miniconda`，Python 3.14）跑测试，出现
> 18 条 `ModuleNotFoundError: pandas`（`tests/core/test_sandbox.py` 等）。根因是
> base 目录 ACL 仅 Users:RX，`pip install` 回落到用户 site-packages，而沙箱后端
> `_clean_env` 刻意剥掉 `APPDATA` → 子进程看不到用户 site。切回项目环境
> `C:\Users\<user>\.conda\envs\dataagent`（AGENTS.md §运行环境 指定的环境名
> `dataagent`）后全绿。**执行任何命令前先确认激活 `dataagent`**，不要用 base。
