# 任务记录｜物理元数据面下沉 EngineAdapter（二十四期）

> 配套文档：设计方案 `../designs/2026-10-26-physical-columns-adapter-design.md` ｜
> 实施计划 `../plans/2026-10-26-physical-columns-adapter-plan.md`（步骤级勾选项在计划内，本文件只跟踪任务级状态）。
> 分支：`feat/physical-columns-adapter`（基线 master@9c460fd）。

## 任务总览

| # | 任务 | 依赖 | 验收口径 | 状态 | 提交 |
|---|------|------|---------|------|------|
| T1 | 类型归一模块 `exec/engine/type_map.py`（剥壳 + 规则表 + 四后端清单） | — | 表驱动用例绿（四后端类型全集逐项映射）；未知类型 fail-closed 且文案含原始类型 | ⬜ 待开工 | — |
| T2 | 协议扩展 + `DuckDbAdapter.physical_columns`（零变更锚） | T1 | 与旧 `_physical_columns` 逐项对拍相等；`_` 前缀表跳过；既有 engine 用例零破 | ⬜ 待开工 | — |
| T3 | `MySqlAdapter.physical_columns`（`%s` 参数化，修 `?` 历史 bug） | T1, T2 | 用例断言 SQL 含 `%s`、不含 `?`、params 为库名；dtype 映射正确 | ⬜ 待开工 | — |
| T4 | `PostgresAdapter.physical_columns` + 新增 `POSTGRES_SCHEMA` 配置 | T1, T2 | settings/.env.example 同步；用例断言 `public` 过滤与 PG 类型映射 | ⬜ 待开工 | — |
| T5 | `ClickHouseAdapter.physical_columns`（`system.columns` + `{db:String}`） | T1, T2 | 用例断言 `system.columns` + `parameters={"db": ...}`；自建连接必被关闭 | ⬜ 待开工 | — |
| T6 | `catalog_loader` 改道（删旧物理面实现 + 维度成员走 `run_query` + 失败语义） | T2-T5 | `tests/semantic` 全绿（含迁移后用例）；`tests/compiler` 零破；非 duckdb 空物理列抛错 | ⬜ 待开工 | — |
| T7 | `ensure_db()` 按后端显式分派 | T6 | 三分支用例绿（duckdb 建仓 / 非 duckdb 不碰 DB_PATH / 异常透传） | ⬜ 待开工 | — |
| T8 | 跨后端目录一致性用例 + CI 三步冒烟 + 文档收口 | T1-T7 | 本地全绿（含新 `tests/test_catalog_backend_parity.py`）；CI 三 job 冒烟通过 | ⬜ 待开工 | — |

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
| 1 | 类型覆盖清单不全（凭猜而非建仓产物枚举），漏项被静默降级为 `str` | T1 四份清单表驱动 + `test_unknown_type_fails_closed` | ⬜ 待核对 |
| 2 | 四后端同源数据下目录不一致（表/列/dtype 三元组漂移） | T8 `tests/test_catalog_backend_parity.py` | ⬜ 待核对 |
| 3 | `conn=` 注入路径回归（`tests/semantic/test_catalog_loader.py` 30+ 处依赖） | T2/T6 迁移后该文件原样全绿 | ⬜ 待核对 |
| 4 | `%s` 参数化在真实驱动上失效（fake `_Conn` 掩盖，二十一期起未暴露） | T3 `test_physical_columns_uses_percent_placeholder` + CI `mysql-parity` 冒烟 | ⬜ 待核对 |
| 5 | 非 duckdb 后端库不可用时静默回退内置默认目录 | T7 三分支用例 + T6 `test_non_duckdb_empty_physical_raises` | ⬜ 待核对 |

## 执行日志（按日期追加）

### 2026-10-26

- 分支 `feat/physical-columns-adapter` 自 master@9c460fd 切出。
- 调研（设计 §2 C1-C13）确认缺口不止"PG/CH 未接"：MySQL 分支的 `?` 占位符在
  真实 pymysql 上必抛 `TypeError`（该分支自二十一期起只被 fake 连接覆盖）。
- 设计方案定稿（D1-D8 + §4 选型矩阵 S1-S6）；实施计划与任务记录落盘。
- 待规格审阅通过后按 T1→T8 逐任务实施（每个任务单独提交，`feat:` 前缀）。
