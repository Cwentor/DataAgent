# 任务记录｜MySQL 数据库支持（二十一期）

> 配套文档：设计方案 `2026-10-25-mysql-support-spec.md` ｜ 实施计划
> `2026-10-25-mysql-support-plan.md`（步骤级勾选项在计划内，本文件只跟踪任务级状态）。
> 分支：`feat/mysql-support`。

## 任务总览

| # | 任务 | 依赖 | 验收口径 | 状态 | 提交 |
|---|------|------|---------|------|------|
| T1 | 执行引擎抽象层：异常迁移 + `EngineAdapter` 协议 + DuckDB 逻辑下沉 + watchdog 通用化 | — | 全量 pytest + eval oracle 8/8 零新失败（DuckDB 行为不变） | ☐ 未开始 | |
| T2 | 编译方言层：`DialectRenderer` 协议 + DuckDB renderer + `settings.DB_BACKEND/SQL_DIALECT` | T1 | 编译产物逐字节不变（快照/golden 全绿） | ☐ 未开始 | |
| T3 | MySQL 执行面：PyMySQL adapter + `normalize_cell` 类型规范化 + 会话加固 + 配置/依赖 | T1 | `tests/engine/` 新增纯逻辑单测全绿；全量回归零新失败 | ☐ 未开始 | |
| T4 | MySQL 扫描行预检：EXPLAIN ANALYZE `actual rows` 解析（样例文本锁定，不依赖服务） | T3 | 解析单测通过（max actual rows 口径 + 不可计量路径） | ☐ 未开始 | |
| T5 | 只读白名单方言化：sqlglot `read=` 参数化 + MySQL 语句/函数增量黑名单 + FOR UPDATE 锁语句拒绝 | T1 | MySQL 危险语句 9 例全拒；duckdb 既有渗透用例不破 | ☐ 未开始 | |
| T6 | MySQL 方言渲染器：反引号 / date_trunc 等价式 / WITH RECURSIVE spine / 反斜杠双保险 | T2 | 渲染单测 + 编译产物 sqlglot mysql 可解析；duckdb 输出不变 | ☐ 未开始 | |
| T7 | 安全面方言化：`build_secure_projection` 纯函数重构 + MySQL 内联派生表 + security 包去引擎依赖 | T1, T2 | 视图等价既有测试绿 + 派生表重写用例绿；security 不再 import duckdb | ☐ 未开始 | |
| T8 | mock 数仓 MySQL 初始化：`schema_mysql.py` 类型映射与索引 + `init_mysql` + build 灌数分发 + web 端点 | T3 | 两方言 DDL 表/列对拍单测绿；duckdb 建仓路径回归绿 | ☐ 未开始 | |
| T9 | catalog_loader MySQL 物理面：类型映射扩展 + table_schema 过滤 + 按后端连接 | T1 | 类型映射参数化用例 + schema 过滤用例绿 | ☐ 未开始 | |
| T10 | 双后端一致性测试 + `eval --backend` 透传 + README/AGENTS/.env.example 收尾 | T1-T9 | 三件套 + eval oracle 全绿；有 MySQL 环境时一致性用例全过 | ☐ 未开始 | |

依赖关系：T1、T2 为地基（先后顺序固定）；T3→T4、T3→T8 为硬依赖；
T5/T6/T7/T9 依赖仅 T1/T2，可并行推进；T10 收口依赖全部。

## 服务级验证环境清单（T10 手工执行前准备）

- [ ] 本地 MySQL 8.0.18+ 实例（`docker run -d -e MYSQL_ROOT_PASSWORD=... -p 3306:3306 mysql:8.0`）
- [ ] `.env` 或环境变量配置 `MYSQL_*` 指向该实例（建仓用可写账号）
- [ ] `conda run -n dataagent --no-capture-output python -m mock.init_mysql`（幂等建仓）
- [ ] 行数对照：init_mysql 输出的每表行数与 `python -m mock.init_duckdb` 版一致
- [ ] 只读账号验证：`GRANT SELECT ON gmall.* TO dataagent_ro@'%'`，以其跑 T10 一致性测试
- [ ] 设置 `MYSQL_TEST_HOST` 等变量后：`python -m pytest tests/test_dual_backend_parity.py -q` 全过

## Review Focus 专项核对（执行 T10 时逐条打勾）

| # | 失效模式 | 覆盖用例 | 结果 |
|---|---------|---------|------|
| 1 | MySQL 反斜杠注入面（`\` 逃逸字面量） | T3 加固语句断言 + T6 转义翻倍单测 | ☐ |
| 2 | 周一口径跨周边界分桶漂移 | T10 test_week_boundary_parity | ☐ |
| 3 | 递归 CTE 深度打穿（5 年 day 窗口） | T3 cte_max_recursion_depth 断言 + T10 test_fill_gaps_five_years_parity | ☐ |
| 4 | Decimal/float 数值漂移 | T3 normalize_cell 单测 + T10 rel=1e-6 断言 | ☐ |
| 5 | 连接池复用后会话加固失效 | T10 test_pool_reuse_session_still_readonly | ☐ |
| 6 | EXPLAIN 计划不可计量被静默放行 | T4 无 actual 段文本用例 | ☐ |

## 执行日志（按日期追加）

### 2026-10-25

- 分支 `feat/mysql-support` 创建，基线 master@fc6b52b。
- 完成全仓耦合点调研（spec §2 C1-C18）、设计定稿（spec §4 D1-D15）、
  实施计划（T1-T10）与本任务记录落盘。
- 未开始任何代码任务——计划待用户审阅后按 T1 顺序执行。

<!-- 后续格式：
### YYYY-MM-DD
- T<n> 完成：<一句话>；提交 <hash>；测试 <数量> 绿 / 回归差异 <无|明细>。
- 偏离计划：<无|改动点与原因（须同步回写 plan/spec）>。
-->
