# 十九期 M5 实施计划：安全视图层（会话级 principal 视图 + 连接加固 + 沙箱旁路封堵）

> 沿用 M1-M4 执行模式（本会话逐任务 TDD）；步骤用 `- [ ]` 勾选跟踪。
> 前置：HEAD @ 339b26f，954 测试绿。

**Goal:** 按 principal 生成会话级安全视图（禁列物理投影掉 + RLS 行过滤固化在视图定义）；DuckDB 连接级加固封死外部访问；沙箱 `duckdb.connect` 旁路封堵——为 M6 探索层提供"构造即安全"的执行面。

**Architecture:** 新 `security/views.py`：`build_secure_views(principal)` 纯函数（策略 → 视图定义，确定性可单测）+ `install_secure_views(conn, principal)`；`harden_connection(conn)` 连接级锁（enable_external_access/lock_configuration，fail-closed）；沙箱 `FORBIDDEN_CALLS` 增 `connect`（AST 三形态全覆盖：duckdb.connect / 别名 .connect / from duckdb import connect）。DSL 核双保险以"视图定义与 guard RLS 语义一致性"交叉验证测试落地，不改编译器（探索层连接生命周期 M6 接入）。

**Tech Stack:** DuckDB 视图与连接设置、security.policy 策略模型、沙箱 ast_guard。

**Spec:** `docs/superpowers/specs/2026-10-02-capability-boundary-expansion-design.md` §3.4（安全视图层）、§6.1（沙箱旁路封堵）、§8 M5 验收。

## Global Constraints

- 同 M1-M4 Global Constraints 全部适用；
- 视图列集合 = 语义目录该表物理列 − policy.forbidden_columns（禁列**物理不存在**于视图，非查询时拦截）；
- 行过滤使用 `_resolve_row_filter` 解析后的主体属性（与 guard 同一事实源）；
- 连接加固 fail-closed：锁设置失败必须抛 SecurityError，严禁带弱配置继续。

## Review Focus

1. 视图逃逸矩阵：基表名在受管连接上仍可引用（视图层不删除基表权限，隔离靠 M6 探索层只暴露视图命名空间）——因此本任务的硬验收是**经视图查询**时禁列不存在、行过滤无条件生效、越权表无视图（M5-T1）；
2. 视图定义与 guard RLS 语义一致性：同一 principal 下"视图查询结果"必须等于"guard 注入 RLS 后编译查询结果"（双保险交叉验证）（M5-T1）；
3. `harden_connection` 后 ATTACH/COPY TO/read_csv 必须被 DuckDB 拒绝（连接层实测，非仅静态断言）（M5-T2）；
4. 沙箱封堵三形态：`duckdb.connect(...)`、`import duckdb as d; d.connect(...)`、`from duckdb import connect; connect(...)` 全部被静态校验拒绝；合法 `read_parquet` 消费不受影响（M5-T3）；
5. install 需要可写连接（read_only 连接无法 CREATE VIEW）——视图安装的生命周期接线属 M6 探索层连接管理，本任务以内存连接验证语义并如实记录边界（M5-T1）。

---

### Task M5-T1: 安全视图管理器 + 逃逸矩阵 + 语义一致性

**Files:** Create `security/views.py`；Test Create `tests/test_security_views.py`
**Interfaces:**
- Produces: `build_secure_views(principal: str | None) -> dict[str, str]`（视图名 → CREATE VIEW SQL）；`install_secure_views(conn, principal) -> dict[str, str]`；视图名约定 `sec_<物理表>`

- [ ] Step 1 失败测试：restricted 视图无 discount_amount/refund_amount 物理列；视图查询强制广东行；fact_refunds 无视图（不在 allowed_tables）；admin 视图全列无行过滤；**语义一致性**：restricted 下视图聚合结果 == guard 注入 RLS 后编译查询结果
- [ ] Step 2 确认失败
- [ ] Step 3 实现（build：COLUMNS 按表分组取物理列 − forbidden_columns，行过滤 _resolve_row_filter + `_literal` 转义渲染 WHERE；install：CREATE OR REPLACE VIEW）
- [ ] Step 4 通过 + Commit `feat(security): 会话级安全视图管理器——禁列物理投影与 RLS 固化（十九期 M5）`

### Task M5-T2: 连接级加固

**Files:** `security/views.py`（`harden_connection`）
**Test:** `tests/test_security_views.py`

- [ ] Step 1 失败测试：harden 后 ATTACH / COPY TO / read_csv 被 DuckDB 拒绝；重复 harden 幂等
- [ ] Step 2 确认失败
- [ ] Step 3 实现（SET enable_external_access=false + SET lock_configuration=true；设置异常 fail-closed 抛 SecurityError）
- [ ] Step 4 通过 + Commit `feat(security): DuckDB 连接级加固——外部访问封死与配置锁定（十九期 M5）`

### Task M5-T3: 沙箱 duckdb.connect 旁路封堵

**Files:** `core/sandbox/ast_guard.py`（FORBIDDEN_CALLS 增 connect）
**Test:** `tests/` 沙箱守卫测试文件（补三形态用例 + read_parquet 合法性）

- [ ] Step 1 失败测试（三形态 + 合法 read_parquet 不受影响）
- [ ] Step 2 确认失败
- [ ] Step 3 实现（FORBIDDEN_CALLS 增 "connect"；docstring 更新说明旁路封堵）
- [ ] Step 4 通过 + Commit `feat(sandbox): 封堵 duckdb.connect 旁路——取数权只在执行层（十九期 M5）`

### Task M5-T4: 全量收口

- [ ] Step 1 全量 pytest / black / ruff / intent_eval / eval_runner（无 Key 等效环境双模式 27/27）
- [ ] Step 2 Commit `test: M5 全量收口——安全视图层回归与质量门（十九期 M5）`
