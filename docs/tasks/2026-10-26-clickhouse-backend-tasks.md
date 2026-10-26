# 任务记录｜ClickHouse 执行后端支持（二十三期）

> 配套文档：设计方案 `../designs/2026-10-26-clickhouse-backend-design.md` ｜
> 实施计划 `../plans/2026-10-26-clickhouse-backend-plan.md`（步骤级勾选项在计划内，本文件只跟踪任务级状态）。
> 分支：`feat/clickhouse-hive-backend`（基线 master@92911b6）。

## 任务总览

| # | 任务 | 依赖 | 验收口径 | 状态 | 提交 |
|---|------|------|---------|------|------|
| T1 | 配置与依赖基座：`CLICKHOUSE_*` 配置组 + clickhouse-connect 依赖 + .env.example | — | settings 字段默认值正确；驱动可导入；black/ruff 绿 | ⬜ 未开始 | — |
| T2 | 方言层：ClickHouseRenderer 九原语 + numbers spine 桶数计算 + 方言注册表 | T1 | 方言单测绿；duckdb/mysql/postgres 编译产物逐字节不变（golden 红线） | ⬜ 未开始 | — |
| T3 | 引擎层：ClickHouseAdapter（验证式加固/错误码文本归一/Arrow 灌数）+ 引擎注册表 | T1 | adapter 纯逻辑单测绿（加固矩阵/归一/灌数清洗/注册）；既有 mysql/postgres adapter 用例不破 | ⬜ 未开始 | — |
| T4 | 护栏方言增量黑名单：SYSTEM/KILL/TRUNCATE/OPTIMIZE/SETTINGS/OUTFILE + 外部源表函数族 | T3 | guards clickhouse 用例绿；既有护栏用例零变化 | ⬜ 未开始 | — |
| T5 | 探索层内联派生表路线纳入 clickhouse（方言集合扩展） | T3 | 探索层用例绿（DuckDB/MySQL 两既有路径行为不变） | ⬜ 未开始 | — |
| T6 | 建仓链路：schema_clickhouse（转译+Nullable/MergeTree 后处理）+ build 分支（种子改道 insert_df）+ init_clickhouse | T3 | DDL 对拍单测绿（首表快照锁定）；`import mock.init_clickhouse` 无副作用 | ⬜ 未开始 | — |
| T7 | CH 双后端一致性测试（golden 全量 + 熔断/周边界/补零/池复用专项） | T2, T3, T6 | 本地收集 + skip 生效（6 skipped）；CI clickhouse-parity 实际执行 | ⬜ 未开始 | — |
| T8 | `eval --backend clickhouse` 连通性冒烟 | T3 | duckdb oracle 契约回归绿（31/31）；冒烟路径 fail-fast 接线 | ⬜ 未开始 | — |
| T9 | CI clickhouse-parity 门禁（users.d 只读 profile 入库）+ AGENTS.md 契约小节 | T1-T8 | 本地 black/ruff/pytest 全绿；推送后 CI 四 job 观察项 | ⬜ 未开始 | — |
| T10 | 全量收尾（质量门 + Review Focus 核对 + 本文件收口） | T1-T9 | 本地全绿；CI 观察项留待推送 | ⬜ 未开始 | — |

依赖关系：T1 为唯一地基；T2/T3 依赖 T1 且二者可并行；T4/T5/T6 依赖 T3；
T7 汇聚 T2/T3/T6；T8 依赖 T3；T9 汇聚全部；T10 收口。

## 服务级验证清单（CI clickhouse-parity job 承担，本地不执行）

- [ ] CI `clickhouse-parity` job 首绿 = CH 链路首次真实验证通过
- [ ] `mock.init_clickhouse` 建仓成功（clickhouse-server:24.8 服务容器 + users.d 挂载）
- [ ] golden 全量 DSL duckdb↔clickhouse 结果集一致（行数对照 + diff 明细）
- [ ] 周边界 / 5 年补零 1826 行（numbers spine）/ comparison yoy 配对 / 池复用只读 四专项通过
- [ ] 扫描熔断专项通过（numbers(20M) 触发 max_rows_to_read=10M 熔断 → 131 归一）
- [ ] `test / mysql-parity / postgres-parity` 三既有 job 结果不变绿（零破坏红线）

## Review Focus 专项核对（T10 收尾时逐条打勾）

| # | 失效模式 | 覆盖用例 | 结果 |
|---|---------|---------|------|
| 1 | 错误码文本提取漂移/缺失被误归一（159/131） | T3 test_engine_error_without_code_stays_generic + CI 服务级 | ⬜ |
| 2 | profile 与应用预算漂移（缺失/readonly<1/预算不一致）未被 fail-closed 拦截 | T3 test_harden_fail_closed_matrix + test_harden_budget_consistent_with_settings | ⬜ |
| 3 | 探索 SQL 携带 SETTINGS 子句篡改预算 | T4 test_clickhouse_extra_keywords_rejected（SETTINGS 用例） | ⬜ |
| 4 | 无列名 DataFrame / NaN/NaT 打穿 insert_df | T3 test_insert_dataframe_unnamed_columns_and_null_cleaning + T6 建仓兜底 | ⬜ |
| 5 | spine 桶数月历算术边界（周归一/月末/未知形态/负向窗口） | T2 spine 五用例（week 归一/month_end/unknown/negative/quarter） | ⬜ |

## 执行日志（按日期追加）

### 2026-10-26

- 分支 `feat/clickhouse-hive-backend` 创建，基线 master@92911b6。
- 全仓耦合点调研（spec §2 C1-C16）、设计定稿（spec §4 D1-D13 + §3 备选方案）、
  八节设计呈现获用户通过；选型对比矩阵 S1-S8 补入 spec §4（既定要求固化为每期
  设计必含）。
- spec 迁入 `docs/designs/`（五类目录规范，3cbef76）；规格审阅通过（2026-10-26）。
- 实施计划定稿（本文件配套 plan）。**计划期驱动实测（clickhouse-connect 0.7+）
  三项裁决更新**（spec 预留验证点闭环，已回写 plan 头部）：
  1. D5 cancel：Client/HttpClient 均无 cancel/kill 方法 → cancel() 落定空操作 +
     服务端 max_execution_time 自限（与 MySQL/PG 先例同口径）；
  2. D3/D4 错误码归一机制：驱动异常无结构化 code 属性（HTTP 层 raise
     OperationalError/DatabaseError(err_str)，错误码嵌于文本 "code: N"）→
     文本正则提取归一（159 超时 / 131 扫描超限），提取失败退化为普通错误；
  3. D7 spine 细化：dateAdd(unit,…) 改用 addDays/addMonths/addYears 函数族
     （签名更稳定），numbers(N) + 纯 Python 桶数机制不变，WEEK 步长归一 7 天。
- 计划与任务记录落盘（新五类目录规范：plan → docs/plans/，tasks → docs/tasks/）。
- 待用户最终确认后开工开发（dev-executing-plans 逐任务执行）。
