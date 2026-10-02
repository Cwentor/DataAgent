# 十九期 M2 实施计划：DSL 扩容收尾（HAVING / 表达式指标 / 多维枚举 / 审计门适配）

> 本计划沿用 M1 执行模式（本会话逐任务 TDD）；步骤用 `- [ ]` 勾选跟踪。
> 执行环境：conda `futurebi`；工作目录仓库根。

**Goal:** DSL 契约补齐 M2 扩容——聚合后过滤（HAVING）、白名单函数表达式指标、多维枚举放开——全部经确定性编译器落地并通过审计门；新契约 golden 用例与互斥组合矩阵入 CI。

**Architecture:** Schema 层新增 `having: list[Filter]`（字段限本 DSL 指标别名）与 `ExpressionMetric`（结构化 AST dict：op/ref/lit 三形态，ref 仅限同 DSL 聚合指标别名——单层引用彻底消除环依赖）；编译器指标渲染改两遍（先聚合后表达式代入），HAVING 以输出别名列出；多维枚举在意图层放开 `len(dims) >= 1`；审计门/质检/守卫按新形态适配并测试证明。

**Tech Stack:** pydantic v2 递归模型、DuckDB、sqlglot（审计门既有依赖）。

**Spec:** `docs/superpowers/specs/2026-10-02-capability-boundary-expansion-design.md` §3.1（HAVING/表达式指标）、§3.2（多维枚举 M2 放开）、§8 M2 验收。

## Global Constraints

- 同 M1 计划 Global Constraints 全部适用（评测锚点、extra="forbid"、简体中文注释、提交前三绿、SQL 只由编译器产出）；
- 表达式指标**禁止字符串表达式**：以结构化 dict 描述，op 白名单硬编码（`ExprOp`），ref 仅指向同 DSL 内 `kind="aggregate"` 指标别名（单层引用，无环）；
- HAVING 字段仅限本 DSL 指标别名（非列名、非维度名）；语义上要求有分组维度。

## Review Focus

1. 表达式引用未声明/非聚合/自身别名 → 契约层拒绝（M2-T2）；
2. HAVING 字段不在指标别名集合 / 无分组维度 / 纯投影带 HAVING → 契约层拒绝（M2-T1）；
3. 表达式含 div 时除零 → SQL 层 NULLIF 防护产出 NULL（与既有比率口径对齐），质检 NULL 率断言可见（M2-T2）；
4. 多维枚举兜底直答字段顺序与提取顺序一致、order_by 首字段（M2-T3）；
5. 新形态编译产物过执行前审计门零 REJECTED（M2-T3）；
6. 表达式指标不做负值断言（派生语义可正可负，与 ratio 同口径豁免）（M2-T2）。

---

### Task M2-T1: HAVING 契约与编译

**Files:**
- Modify: `semantic/dsl_schema.py`（QueryDSL 增 `having` + 校验）、`compiler/sql_compiler.py`（HAVING 子句 + 三分支防御）
- Test: `tests/test_compiler.py`、互斥矩阵在 T4 汇总

**Interfaces:**
- Produces: `QueryDSL.having: list[Filter]`（默认空）；编译器对带分组 DSL 产出 `HAVING "alias" op literal`

- [ ] Step 1 失败测试：合法 having（gm v>0 按品类分组）编译含 `HAVING` 且执行有行；having 字段非指标别名 → ValidationError；无 dimensions 带 having → ValidationError；纯投影带 having → ValidationError
- [ ] Step 2 跑测试确认失败
- [ ] Step 3 实现：
  - Schema：`having: list[Filter] = Field(default_factory=list)`；`_check_having` 校验器（有 having ⇒ metrics 非空、dimensions 非空、无 WindowMetric、字段 ⊆ 指标别名、无 top_n/fill_gaps/comparison——并入 `_check_projection_shape` 同层新增独立校验器）
  - 编译器：普通路径 GROUP BY 之后 `HAVING "alias" op literal`（`_having_sql`，dtype 取 float）；comparison/top_n/fill_gaps 三分支防御 CompileError
- [ ] Step 4 通过 + Commit `feat(dsl): HAVING 聚合后过滤契约与编译（十九期 M2）`

### Task M2-T2: 表达式指标

**Files:**
- Modify: `semantic/dsl_schema.py`（`ExprOp`/`ExprArg`/`ExpressionMetric` + Metric 联合 + QueryDSL ref 校验）、`compiler/sql_compiler.py`（两遍渲染 + `_expr_sql`）、`security/guard.py`（`_referenced_fields` 处理 expression）
- Test: `tests/test_compiler.py`、`tests/test_intent.py` 无关；守卫测试在 `tests/test_security.py`（若无则在 test_compiler 内联）

**Interfaces:**
- Produces: `{"kind": "expression", "alias": "...", "expr": {"op": "div", "args": [{"ref": "gmv"}, {"lit": 1000}]}}`；`_expr_sql(node, resolve)`；guard 对 expression 引用收集底层聚合字段

- [ ] Step 1 失败测试：`div(ref(gmv), ref(orders))` 编译为 `COALESCE(...) / NULLIF(...)` 形态且执行数学正确；op 白名单外 → ValidationError；ref 未声明别名 → ValidationError；ref 指向非聚合指标（ratio/expression）→ ValidationError；depth>1 的 op 嵌套（`mul(ref, lit)` 内嵌 `add`）合法且数学正确
- [ ] Step 2 确认失败
- [ ] Step 3 实现：
  - `ExprOp`：add/sub/mul/div/coalesce/round/abs；`ExprArg`：`op/ref/lit/args` 四字段可选，校验"恰好一形态 + op 参数个数（二元/一元/n 元）"；`ExpressionMetric`：kind/alias/expr（expr 必须为 op 形态）；`Metric` 联合追加
  - QueryDSL 校验器 `_check_expression_refs`：每个 expression 指标的 ref 必须指向本 DSL `kind="aggregate"` 指标别名（禁自引/环——单层引用结构性消除）
  - 编译器：普通路径两遍渲染（第一遍聚合/比率别名→SQL 表达式映射，第二遍表达式代入）；`_expr_sql`：div 产出 `(a / NULLIF(b, 0))`，add/sub/mul 括号二元，coalesce n 元，round 1-2 元，abs 一元
  - `security/guard.py` `_referenced_fields`：expression 分支按 ref 回查聚合指标 field
- [ ] Step 4 通过 + Commit `feat(dsl): 白名单函数表达式指标——结构化 AST 契约与编译（十九期 M2）`

### Task M2-T3: 多维枚举放开 + 审计门适配

**Files:**
- Modify: `core/orchestrator/intent.py`（`len(dims) >= 1` + `enumeration_dsl` 多维投影）、`core/orchestrator/nodes.py`（`_heuristic_plan` 多维 goal；L3 守卫不变）
- Test: `tests/test_intent.py`（更新 `test_enumeration_multi_dim_is_unknown` → 多维 ENUMERATION + DSL 形态）、`tests/test_exec_audit.py` 附近新增"新形态过审计门"用例

**Interfaces:**
- Produces: `enumeration_dsl("列出所有省份和品牌")` 返回 `dimensions=[province, brand]`；多维枚举兜底直答

- [ ] Step 1 失败测试（分类 + DSL 形态 + `_heuristic_plan` 两维 + 审计门零 REJECTED：投影/HAVING/表达式三种编译 SQL 各跑 `audit_compiled_sql`）
- [ ] Step 2 确认失败
- [ ] Step 3 实现：分类条件放宽、`enumeration_dsl` 多维（dimensions 按锚点序、order_by 首字段）、`_heuristic_plan` goal 联合标签
- [ ] Step 4 通过 + Commit `feat(intent): 多维枚举直答放开 + 新形态审计门适配证明（十九期 M2）`

### Task M2-T4: golden 用例 + 互斥矩阵 + 全量收口

**Files:**
- Modify: `eval/intent_golden.json`（「列出所有省份和品牌」refusal → list_answer）、`eval/golden_dataset.json`（HAVING 与表达式指标各 1 条，expected SQL 由编译器产出后人工核对）、`tests/test_compiler.py`（互斥组合 CompileError/ValidationError 参数化矩阵）
- Test: 全量 pytest + black + ruff + `python -m eval.eval_runner` + `python -m eval.intent_eval`

- [ ] Step 1 互斥矩阵参数化测试（失败先行）：having×{投影, 标量无维度, top_n, fill_gaps, comparison, window}、expression×{ref 未声明, ref 非聚合, 自引}
- [ ] Step 2 golden 用例落盘并核对 expected SQL
- [ ] Step 3 评测与质量门全绿
- [ ] Step 4 Commit `test(eval): M2 新契约 golden 用例与互斥组合矩阵（十九期 M2）`
