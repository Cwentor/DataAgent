# 任务记录｜MySQL 数据库支持（二十一期）

> 配套文档：设计方案 `../designs/2026-10-25-mysql-support-design.md` ｜ 实施计划
> `../plans/2026-10-25-mysql-support-plan.md`（步骤级勾选项在计划内，本文件只跟踪任务级状态）。
> 分支：`feat/mysql-support`。

## 任务总览

| # | 任务 | 依赖 | 验收口径 | 状态 | 提交 |
|---|------|------|---------|------|------|
| T1 | 执行引擎抽象层：异常迁移 + `EngineAdapter` 协议 + DuckDB 逻辑下沉 + watchdog 通用化 | — | 全量 pytest + eval oracle 8/8 零新失败（DuckDB 行为不变） | ✅ 完成 | d0d8afe |
| T2 | 编译方言层：`DialectRenderer` 协议 + DuckDB renderer + `settings.DB_BACKEND/SQL_DIALECT` | T1 | 编译产物逐字节不变（快照/golden 全绿） | ✅ 完成 | 0fdf6e7 |
| T3 | MySQL 执行面：PyMySQL adapter + `normalize_cell` 类型规范化 + 会话加固 + 配置/依赖 | T1 | `tests/engine/` 新增纯逻辑单测全绿；全量回归零新失败 | ✅ 完成 | 4b94f11 |
| T4 | MySQL 扫描行预检：EXPLAIN ANALYZE `actual rows` 解析（样例文本锁定，不依赖服务） | T3 | 解析单测通过（max actual rows 口径 + 不可计量路径） | ✅ 完成 | 754b467 |
| T5 | 只读白名单方言化：sqlglot `read=` 参数化 + MySQL 语句/函数增量黑名单 + 锁语句拒绝 | T1 | MySQL 危险语句 10 例全拒；duckdb 既有渗透用例不破 | ✅ 完成 | 1899e13 |
| T6 | MySQL 方言渲染器：反引号 / date_trunc 等价式 / WITH RECURSIVE spine / 反斜杠双保险 | T2 | 渲染单测 + 编译产物 sqlglot mysql 可解析；duckdb 输出不变 | ✅ 完成 | 43ae434 |
| T7 | 安全面方言化：`build_secure_projection` 纯函数重构 + MySQL 内联派生表 + security 包去引擎依赖 | T1, T2 | 视图等价既有测试绿 + 派生表重写用例绿；security 不再 import duckdb | ✅ 完成 | dbee26c |
| T8 | mock 数仓 MySQL 初始化：`schema_mysql.py` 类型映射与索引 + `init_mysql` + build 灌数分发 + web 端点 | T3 | 两方言 DDL 表/列对拍单测绿；duckdb 建仓路径回归绿 | ✅ 完成 | 45221e0 |
| T9 | catalog_loader MySQL 物理面：类型映射扩展 + table_schema 过滤 + 按后端连接 | T1 | 类型映射参数化用例 + schema 过滤用例绿 | ✅ 完成 | 979db6e |
| T10 | 双后端一致性测试 + `eval --backend` 透传 + README/AGENTS/.env.example 收尾 | T1-T9 | 三件套 + eval oracle 全绿；有 MySQL 环境时一致性用例全过 | ✅ 完成（服务级验证待 MySQL 环境） | 73daa69 |

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
- **设计方案获用户批准**；补充批准注意点：项目须随 MySQL 版本更新
  一同演进（不可避免代价），已固化至 spec §8.1 版本演进跟随策略与
  §9 风险表首条。
- 开始执行 T1（执行引擎抽象层）。
- T1 完成：引擎抽象层（exec/errors + exec/engine 五模块），1028 绿 +
  oracle 全绿。计划外适配两处：test_logging_coverage 的解析桩随执行
  路径迁至 duckdb_adapter；test_exec 的 _run_with_timeout 桩迁至
  watchdog；settings.DB_BACKEND 自 T2 提前至 T1（get_adapter 依赖）。
  提交 d0d8afe。
- T2 完成：编译方言层（compiler/dialect + compiler/errors），duckdb
  产物逐字节不变；_filter_sql/_dimension_expr 的裸拼限定列顺带统一走
  renderer（duckdb 渲染等价裸拼、mysql 侧正确反引号化）。提交 0fdf6e7。
- T3 完成：MySQL adapter（加固四要素/3024 超时归一/normalize_cell/
  insert_dataframe NaN->None）+ MYSQL_* 配置 + pymysql>=1.1.1,<2。
  提交 4b94f11。
- T4 完成：EXPLAIN ANALYZE actual rows 解析（cost 估算不混入；样例
  文本锁定）。提交 754b467。
- T5 完成：白名单方言化（MySQL 语句增量 21 项 + 函数增量 + exp.Lock
  锁语句 AST 拒绝——FOR UPDATE 与 LOCK IN SHARE MODE 双覆盖）。
  提交 1899e13。
- T6 完成：MySQL 渲染器（WEEKDAY 周一/CAST AS DATETIME/反斜杠翻倍/
  WITH RECURSIVE spine）；端到端编译用例 4 例（含方言切换还原断言）。
  提交 43ae434。累计 1069 passed + oracle 31/31。
- T7 完成：投影纯函数 build_secure_projection（renderer 渲染；视图 DDL 与
  派生表单一实现）；install/harden 迁 DuckDbAdapter，security 包零引擎依赖；
  exploration 按方言产出（mysql 内联派生表，越权/未登记口径一致）。
  提交 dbee26c。计划偏差：视图列引用由 `f."col"` 变 renderer 裸拼
  `f.col`（DuckDB 语义等价，既有测试为行为断言不受影响）。
- T8 完成：DDL 由 sqlglot 运行时转译（duckdb→mysql，TIMESTAMP→DATETIME/
  DECIMAL 保真/VARCHAR→TEXT）替代计划中的手写 schema_mysql——对拍测试
  锁定表/列一致且免 29 张表手工漂移风险；二级索引覆盖事实表时间轴；
  build_gmall_tables 引擎化（DDL/灌数/行数统计按 adapter 分发）。
  提交 45221e0。
- T9 完成：类型映射扩展（MySQL 族；发现既有表已含 DATETIME 键去重）、
  table_schema 过滤（MySQL 实例级 information_schema 防污染）、_fetch_all
  双连接兼容、维度成员查询标识符方言化、build_catalog 按后端连接。
  提交 979db6e。
- T10 完成：tests/test_dual_backend_parity.py（golden 全量 + 周边界/5 年
  递归/comparison 配对/池复用加固专项；数值容差 rel=1e-6、行序无关比对）；
  eval --backend mysql 诚实冒烟（golden 锚定 DuckDB 方言不硬跑，较计划的
  agent 管道注入收缩——双模式都以 golden SQL 执行为锚，方言错配必炸，
  完整回归由一致性测试承担）；README/AGENTS/.env.example 收尾。
  提交 73daa69。终态：1098 passed + 7 skipped（一致性 5 例待 MySQL 环境）
  + oracle 31/31。
- **全部 T1-T10 完成**。剩余服务级验证清单见上方（需本地 MySQL 8.0.18+）。

**CI mysql-parity 门禁落地与六轮迭代（2026-10-25，PR #16）**：
新增 `.github/workflows/ci.yml` mysql-parity job（MySQL 8.0 容器 + init_mysql
建仓 + 一致性测试），首跑起连续暴露六处"本地无 MySQL 环境从未真正执行"的
缺陷，逐轮修复（该 job 的价值即时兑现——全部属 T8/T10 的服务级验证缺口）：

1. `sqlglot Create.this 是 Schema（.name 恒空）`：表名提取依赖 .this.name
   产出 `DROP TABLE IF EXISTS ```（1103）→ 改版本无关正则（1bdfb39）；
   对拍测试同型假绿（两侧塌缩成空名仍过）一并修正；
2. init_mysql 双重建表（自身 create + build_gmall_tables 再 create → 1050）
   → 建表全权交 build，清场 DROP 补元数据表（61f07dd）；
3. pymysql Connection 无 executemany（仅 Cursor 有）→ _insert_seed 分执行
   通道（0ab7f21）；
4. 无列名 DataFrame（元组行构造，columns=0..n）被拼进列子句 → Unknown
   column '0'（1054）→ insert_dataframe 位置对应模式（b462cb5）；
5. NaT 直插 DATETIME 列报 1292 → 空值统一 pd.isna 清洗（418fc7e）；
6. 一致性测试自身两处：_compile_dsl 漏 QueryDSL.model_validate（dict 直传
   compile_sql）+ pool 用例误用运行时 MYSQL_* 默认账号（1045 拒绝）→
   补契约校验 + 统一 _test_dsn()。
7. 跨引擎类型/口径归一（第七至十二轮，数据已真实回流后暴露）：
   Decimal(datetime) 类型面（duck DECIMAL 聚合返回 Decimal 为既有行为，
   不动 adapter 以免打穿 golden 哈希，比对层归一）+ DATE vs TIMESTAMP
   归一（spec D5 语义等价）+ golden 多轮 turns[] 嵌套遍历 + 数值容差
   校准（rel=1e-5 大值 / abs=1e-6 小值——MySQL DECIMAL 除法按
   div_precision_increment=4 六位小数舍入的绝对量级差）；
8. **既有审计门真缺陷**（非 MySQL 引入，eval 裸执行从未暴露）：无维度
   comparison 的 FROM cur, prev（两标量聚合 CTE，恒单行）被笛卡尔检查
   误拒 → _is_scalar_aggregate 解包 Alias(Sum)（原判定对带别名聚合恒
   False）+ 无 ON Join 精确放行（sqlglot 30.x 逗号 FROM 归一化形态；
   WITH 键 with_/根 From 键 from_ 版本兼容；CTE 体内 FROM 排除）；
   普通表逗号 FROM / CROSS JOIN 仍拒（防回归用例锁定）。

**终态（2026-10-25）**：PR #16 CI 四 job 全绿（lint / test / mysql-parity /
nightly-skip），mysql-parity = MySQL 8.0 容器建仓 + 35 条 golden DSL 双后端
一致性 + Review Focus 全专项通过；本地 1101 passed + 7 skipped。教训固化：
black/ruff 检查禁用裸管道（tail 吃退出码，三次掩盖实锤，一律 pipefail）。

<!-- 后续格式：
### YYYY-MM-DD
- T<n> 完成：<一句话>；提交 <hash>；测试 <数量> 绿 / 回归差异 <无|明细>。
- 偏离计划：<无|改动点与原因（须同步回写 plan/spec）>。
-->
