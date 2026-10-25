# 十九期 M4 实施计划：SQL 提升闸门（sqlglot → DSL，拒升 ≠ 拒答）

> 沿用 M1-M3 执行模式（本会话逐任务 TDD）；步骤用 `- [ ]` 勾选跟踪。
> 前置：HEAD @ a32e507，928 测试绿；sqlglot 为既有依赖（exec/audit.py 在用）。

**Goal:** LLM 产出的 SQL 作为输入表面语法，经 sqlglot 解析后**提升回 DSL 契约**、由确定性编译器重新生成执行——拒升产出精确清单喂回自愈，绝不直接执行 LLM SQL。

**Architecture:** 新 `core/retrieval/sql_lift.py`：解析（duckdb 方言）→ 纯引用 CTE 内联 → FROM/JOIN 对照语义目录受控连接 → SELECT 聚合/投影提取 → WHERE/HAVING 白名单操作符叶子上提 → ORDER BY/LIMIT。保守性优先：解析失败/歧义/白名单外构造一律拒升（结构化清单：子句+构造+原因）。接线：PlanStep 增 `sql` 字段，planner_node 内联自愈（拒升清单注入重规划提示词，≤2 次），仍拒升回落 degraded parse（探索层执行留 M6）。

**Tech Stack:** sqlglot（既有）、pydantic v2、语义目录 JOIN_RULES/COLUMNS。

**Spec:** `docs/superpowers/specs/2026-10-02-capability-boundary-expansion-design.md` §3.3（提升闸门）、§5.2（保守性）、§8 M4 验收。

## Global Constraints

- 同 M1-M3 Global Constraints 全部适用；
- **提升产物必须重新编译**：`lift_sql` 只产 DSL dict，执行一律走 `compile_sql`——LLM SQL 永不直接执行（修订后铁律）；
- 拒升 ≠ 拒答：拒升清单必须结构化（clause/construct/reason）且可喂回 LLM；
- 保守性优先：无法确定性证明等价的构造一律拒升，严禁猜语义放行。

## Review Focus

1. 提升等价性：每条可升 SQL 的"原始执行结果 == 提升后 DSL 重编译执行结果"（真值断言，sorted 行集判等，mock 数仓）（M4-T1）；
2. WHERE 中 OR / 函数包裹列 / 子查询 → 拒升（DSL 契约表达不了，严禁降维改写）（M4-T2）；
3. 无 LIMIT 的 SQL → 提升后应用 DSL 默认上限 100 并记入 notes（语义收窄必须可见）（M4-T1）；
4. COUNT(*)（无字段聚合）→ 拒升（AggregateMetric 必须有 field）（M4-T2）；
5. 纯引用 CTE 内联后表名替换必须彻底（多处引用/嵌套 CTE 引用 CTE → 拒升）（M4-T1）；
6. planner_node 拒升自愈上限 2 次内联重规划，仍拒升回落 degraded parse——严禁无限循环（M4-T3）。

---

### Task M4-T1: 提升闸门核心 + 可升构造矩阵（执行等价断言）

**Files:** Create `core/retrieval/sql_lift.py`；Test Create `tests/test_sql_lift.py`
**Interfaces:**
- Produces: `LiftRejection(clause, construct, reason)`（dataclass，`to_dict()`）；`LiftResult(ok, dsl: dict | None, rejections, notes)`；`lift_sql(sql: str) -> LiftResult`

- [ ] Step 1 失败测试（可升矩阵，每条执行等价断言）：单表聚合（sum/count）、COUNT(DISTINCT)、多维分组 JOIN（dim_product）、WHERE 单条件/IN/BETWEEN、HAVING 别名过滤、ORDER BY 别名/维度、DISTINCT 纯投影（无聚合）、LIMIT、纯引用 CTE 内联
- [ ] Step 2 确认失败
- [ ] Step 3 实现 lift_sql（解析→With 内联→FROM/JOIN→SELECT→WHERE→GROUP BY→HAVING→ORDER BY→LIMIT；物理列经 catalog.COLUMNS 反查逻辑字段；字面量按字段 dtype 转义复用 compiler._literal 思路或独立实现）
- [ ] Step 4 通过 + Commit `feat(retrieval): SQL 提升闸门核心与可升构造执行等价矩阵（十九期 M4）`

### Task M4-T2: 拒升构造矩阵（精确原因）

**Files:** `core/retrieval/sql_lift.py`
**Test:** `tests/test_sql_lift.py`

- [ ] Step 1 失败测试（拒升矩阵，断言 ok=False 且 reason 精确）：解析失败（乱文本）、CTE 含计算、CASE WHEN、WHERE 子查询、WHERE OR、函数包裹列（UPPER/CAST）、COUNT(*)、SELECT 无别名聚合、UNION、窗口函数、JOIN 条件与语义目录规则不符、LIMIT 超契约上限
- [ ] Step 2 确认失败
- [ ] Step 3 实现各拒升分支
- [ ] Step 4 通过 + Commit `test(retrieval): 拒升构造矩阵与精确原因（十九期 M4）`

### Task M4-T3: Planner SQL 双产出发丝 + 内联自愈闭环

**Files:** `core/orchestrator/state.py`（PlanStep.sql）、`core/orchestrator/prompts.py`（SQL 双产出契约段）、`core/orchestrator/nodes.py`（planner_node：sql 步骤提升 + ≤2 次内联自愈 + 回落 degraded）
**Test:** `tests/test_orchestrator.py`

- Produces: `PlanStep.sql: str | None`；`_plan_with_lift(payload, state) -> tuple[steps | None, str | None]`（拒绝时返回拒升清单文本）

- [ ] Step 1 失败测试：LLM payload 步骤带 sql（可升）→ 计划 DSL 为提升产物 + scratchpad 记录 `[planner] sql-lifted`；步骤带不可升 sql → 首轮拒升清单注入重试提示词（记录调用次数）、第二次喂修正 sql → 成功；两次均拒升 → steps=None 回落 degraded parse；dsl 与 sql 同给时 dsl 优先
- [ ] Step 2 确认失败
- [ ] Step 3 实现（PlanStep.sql 字段；prompts 增加"仅当分析形态确定超出 DSL 契约才产 sql，能产 DSL 必须产 DSL；sql 步骤将在闸门提升后重编译执行"；planner_node 内联自愈循环）
- [ ] Step 4 通过 + Commit `feat(orchestrator): Planner SQL 双产出发丝与拒升内联自愈闭环（十九期 M4）`

### Task M4-T4: 全量收口

- [ ] Step 1 全量 pytest / black / ruff / intent_eval / eval_runner（oracle+agent 无 Key 等效环境 27/27）
- [ ] Step 2 Commit `test: M4 全量收口——提升闸门回归与质量门（十九期 M4）`
