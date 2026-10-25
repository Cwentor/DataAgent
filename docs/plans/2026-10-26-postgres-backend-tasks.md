# 任务记录｜PostgreSQL 执行后端支持（二十二期）

> 配套文档：设计方案 `../superpowers/specs/2026-10-26-postgres-backend-design.md` ｜
> 实施计划 `2026-10-26-postgres-backend-plan.md`（步骤级勾选项在计划内，本文件只跟踪任务级状态）。
> 分支：`feat/postgres-backend`（基线 master@41ca5cc）。

## 任务总览

| # | 任务 | 依赖 | 验收口径 | 状态 | 提交 |
|---|------|------|---------|------|------|
| T1 | 配置与依赖基座：`POSTGRES_*` 配置组 + psycopg3 依赖 + .env.example | — | psycopg 可导入；black/ruff 绿 | ✅ 完成 | 47819e4 |
| T2 | 方言层：`date_add` 原语化（D17）+ PostgresRenderer + 方言注册表化 | T1 | 方言单测绿；duckdb/mysql 编译产物逐字节不变（golden 红线） | ✅ 完成 | 4612f3b |
| T3 | 引擎层：PostgresAdapter（psycopg3）+ 引擎注册表化 | T1 | adapter 纯逻辑单测绿（异常归一/计划解析/灌数/注册）；既有 mysql adapter 用例不破 | ✅ 完成 | 89d0728 |
| T4 | 护栏方言增量黑名单：COPY/DO/LISTEN/NOTIFY + pg_* 函数族 + 锁子句 | T3 | guards postgres 用例绿；既有护栏用例零变化 | ✅ 完成 | ce7a0d9 |
| T5 | 探索层视图安装钩子通用化（方法存在性分发，PG TEMP 视图同路） | T3 | 探索层用例绿（DuckDB/MySQL 两既有路径行为不变） | ✅ 完成 | 252e49d |
| T6 | PG 建仓链路：schema_postgres DDL 转译 + build 分支 + init_postgres（含显式 ANALYZE） | T3 | DDL 对拍单测绿；`import mock.init_postgres` 无副作用 | ✅ 完成 | fef3c81 |
| T7 | parity 工具提取至 tests/parity_utils（行为不变重构） | — | mysql parity 文件收集无错（跳过态）；全量回归绿 | ✅ 完成 | 206e53f |
| T8 | PG 双后端一致性测试（golden 全量 + 4 专项） | T2, T3, T6, T7 | 本地收集 + skip 生效；CI postgres-parity 实际执行 | ✅ 完成（服务级验证待 CI） | 1a17ddd |
| T9 | `eval --backend postgres` 连通性冒烟 | T3 | duckdb oracle 契约回归绿（31/31）；postgres 冒烟路径 fail-fast 接线 | ✅ 完成 | 7476504 |
| T10 | CI postgres-parity 门禁（postgres:16）+ AGENTS.md 同步 + 全量收尾 | T1-T9 | 本地 black/ruff/pytest 全绿（1159 passed / 12 skipped）；推送后 CI 三 job 观察项 | ✅ 完成（CI 观察项待推送） | 202a1fa |

依赖关系：T1 为唯一地基；T2/T3 依赖 T1 且二者可并行；T4/T5/T6 依赖 T3；
T7 独立（纯测试重构，可穿插）；T8 汇聚 T2/T3/T6/T7；T9 依赖 T3；T10 收口依赖全部。

## 服务级验证清单（CI postgres-parity job 承担，本地不执行）

- [x] CI `postgres-parity` job 首绿 = PG 链路首次真实验证通过（2026-10-26，PR #17 第二轮）
- [x] `mock.init_postgres` 建仓成功（postgres:16 服务容器，首跑即通——DDL 转译与同源灌数无 R3 风险暴露）
- [x] golden 全量 DSL duckdb↔postgres 结果集一致（行数对照 + diff 明细）
- [x] 周边界 / 5 年补零 1826 行 / comparison yoy 配对 / 池复用只读 四专项通过
- [x] `test / mysql-parity` 两既有 job 结果不变绿（零破坏红线）

## Review Focus 专项核对（T10 收尾时逐条打勾）

| # | 失效模式 | 覆盖用例 | 结果 |
|---|---------|---------|------|
| 1 | statement_timeout 触发未归一（SQLSTATE 57014 裸抛） | T3 test_engine_error_timeout_57014 + CI 服务级 | ✅ |
| 2 | PG EXPLAIN 文本漂移致不可计量被静默放行 | T3 test_parse_pg_scan_rows_not_measurable_without_actual | ✅ |
| 3 | standard_conforming_strings 被关闭致转义口径漂移 | T3 加固基线断言 + T2 反斜杠不翻倍单测 | ✅ |
| 4 | spine/comparison 步长字面量新形态静默拼接非法 SQL | T2 test_spine_rejects_unknown_interval_form | ✅ |
| 5 | 无列名 DataFrame / NaN/NaT 打穿灌数通道 | T3 灌数三用例（位置模式/NaN/NaT） | ✅ |

## 执行日志（按日期追加）

### 2026-10-26

- 分支 `feat/postgres-backend` 创建，基线 master@41ca5cc。
- 全仓耦合点调研（spec §2 C1-C16）、设计定稿（spec §4 D1-D17）、
  设计文档提交（7046427）。
- 实施计划定稿（本文件配套 plan）；D17（date_add 方言原语化）为计划期
  实证发现的规格补遗，已回写 spec。
- T1-T10 全部执行完成（12 提交，47819e4..202a1fa），本地全量
  1159 passed / 12 skipped、black/ruff 全绿、eval oracle 31/31。
- 裁决记录 5 条（台账 logs/exec-ledger-postgres-phase22/progress.md）：
  R1 .env.example 注释三值化 / R2 类尾追加缩进教训 / R3 管道吞 lint 退出码教训 /
  R4 FOR UPDATE 实际由通用关键字层拦截（AST 扩展保留兜底）/
  R5 parity_utils 用包内绝对导入（tests/ 为包）。
- 终审：本会话作者自审（inline 模式无独立评审代理），详见台账 Final 节。

### 2026-10-26（CI 门禁轮）

- PR #17 首轮 CI：lint / test / mysql-parity 全绿（既有链路零破坏；
  `Build PostgreSQL warehouse` 步骤首跑即通——DDL 转译无 R3 风险暴露）；
  postgres-parity 4/5 通过、golden 全量用例失败。
- **D18 规格补遗（CI 实证发现）**：HAVING 聚合后过滤产出 `HAVING "gmv" > …`
  引用输出别名——DuckDB/MySQL 允许、PG 禁止（UndefinedColumn）。新增第 9
  方言原语 `having_metric_ref`：DuckDB/MySQL 返回别名引用（golden 逐字节
  不变），PG 借编译器两遍渲染机制（alias_expr）重放底层聚合表达式。
  spec 已回写；TDD 修复（3 RED → 修 → 方言 59 绿 + 全量 1163 绿）。
- 第二轮 CI：lint / test / mysql-parity / **postgres-parity 全绿**
  （parity 5/5，PG 链路首次真实验证通过）。
