# 十九期 M1 实施计划：维度枚举直答（根治"把全部品牌名列举给我"拒答）

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。
> 执行环境：conda 环境 `dataagent`（等效 `futurebi`）；工作目录仓库根。

**Goal:** 「把全部品牌名列举给我」类维度取值枚举问法在全链路（含无 LLM 兜底模式）确定性直答；拒答建议按成因分流，能力边界类拒答不再误导用户检查 LLM 网关连通性。

**Architecture:** DSL 契约放开 `metrics` 可空（纯维度投影形态，契约层校验器约束形态边界）→ 编译器新增 `SELECT DISTINCT` 投影路径 → 意图层新增 `ENUMERATION` 意图（问法词表硬匹配 + 确定性 DSL 构造器）→ 编排层启发式分支接入 + LLM 在场时的预路由（现行 LLM 规划契约 metrics 必填，表达不了投影）+ L3 错位守卫补枚举规则 → 拒答建议按 LLM 调用失败标记三分流。

**Tech Stack:** pydantic v2（`model_validator`）、DuckDB、既有 LangGraph 编排（不改图结构、不加节点）。

**Spec:** `docs/superpowers/specs/2026-10-02-capability-boundary-expansion-design.md`（本计划实现其 §3.1 纯投影部分、§3.2、§3.8、§4 案例 A、§8 M1；HAVING 与表达式指标属 M2，不在本计划）。

## Global Constraints

- 评测锚点 `AS_OF_DATE = 2024-06-30`、随机种子 42，评测确定性不可破坏；
- DSL 模型一律 `extra="forbid"`（`semantic/dsl_schema.py` 全部模型已如此，本计划不得放松）；
- 意图分类的**字段词表**以 `semantic/catalog.py` 的 `FieldMeta.aliases` 为单一事实源，严禁在 intent.py 维护字段别名字面词表；**问法触发词**（"列举/多少个/为什么"类）是问法词表，允许在 intent.py 字面维护（既有 `_DIAGNOSTIC_TERMS`/`_COUNT_QUESTION_TERMS` 同类）；
- 严禁绕过 DSL 直接生成 SQL——本计划全部 SQL 由 `compiler/sql_compiler.py` 产出；
- 新增逻辑字段必须登记 `semantic/catalog.py` 的 `COLUMNS` 白名单（本计划不新增字段）；
- 提交前 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿；
- 新增/修改 Python 注释与 docstring 用简体中文（专有名词保留英文）；代码实体、测试函数名英文；
- Git commit 遵循仓库惯例：`type(scope): 中文描述`。

## Review Focus

规格隐含但容易被漏测的输入类别（每条已挂到拥有该代码的任务）：

1. 含指标的"全部"问句（"全部品牌的GMV是多少"）→ 不得误判枚举，须走指标直答（Task 3）；
2. 基数量词与枚举词共存（"有多少个品牌"）→ 基数优先返回计数，不返回清单（Task 3）；
3. 枚举 + 过滤线索（"列出有退款的品牌"）→ `enumeration_dsl` 返回 None，兜底拒答 / LLM 在场时放行 LLM 规划，不得用全量清单冒充过滤语义（Task 3）；
4. 多维枚举（"列出所有省份和品牌"）→ M1 诚实拒答并精确说明原因（Task 3 / Task 6）；
5. 投影无界输出 → `limit` 硬上限生效，默认 100 行（Task 2）；
6. LLM 失败标记不得泄漏进非 blocked 终态——plan 产出时 scratchpad 被整体替换（`nodes.py:622`），标记仅在 blocked 路径存续（Task 5）。

---

### Task 1: DSL 契约——`metrics` 可空与纯投影形态校验

**Files:**
- Modify: `semantic/dsl_schema.py:318`（`QueryDSL.metrics` 字段）与 `semantic/dsl_schema.py:330-341`（校验器区）
- Test: `tests/test_compiler.py`（文件末尾追加）

**Interfaces:**
- Consumes: 既有 `QueryDSL` / `TimeFilter` / `Comparison`（同文件 44-149 行）
- Produces: `QueryDSL` 支持 `metrics=[]` 且 `dimensions` 非空的合法形态（后续所有任务依赖）；非法投影形态在契约层抛 `ValidationError`

- [ ] **Step 1: 写失败测试**

在 `tests/test_compiler.py` 顶部 import 区补 `ValidationError`：

```python
from pydantic import ValidationError
```

文件末尾追加：

```python
def test_projection_dsl_valid_and_invalid_shapes():
    """十九期 M1：metrics 可空的纯维度投影契约（形态越界契约层即拒）。"""
    # 合法：纯维度投影（无指标）
    dsl = QueryDSL.model_validate({"dimensions": [{"field": "brand"}]})
    assert dsl.metrics == []

    # 非法：指标与维度同时为空（无查询目标）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate({})

    # 非法：投影 + 分组 Top-N（无指标可排序）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "dimensions": [{"field": "province"}],
                "top_n": {
                    "n": 3,
                    "partition_by": ["province"],
                    "order_by": [{"field": "province", "direction": "asc"}],
                },
            }
        )

    # 非法：投影 + 日期补零（无指标可填充）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate({"dimensions": [{"field": "brand"}], "fill_gaps": True})

    # 非法：投影 + 同比/环比（无指标可对比）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "dimensions": [{"field": "brand"}],
                "time_filter": {
                    "range_type": "absolute",
                    "absolute": {"start": "2024-05-01", "end": "2024-06-01"},
                    "comparison": "mom",
                },
            }
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_compiler.py::test_projection_dsl_valid_and_invalid_shapes -v`
Expected: FAIL —— 合法投影用例报 `metrics` `min_length=1` 校验错（`ValidationError` 之外的失败形态：断言 `dsl.metrics == []` 前即抛错）。

- [ ] **Step 3: 最小实现**

`semantic/dsl_schema.py:318` 改为：

```python
    metrics: list[Metric] = Field(default_factory=list)
```

`QueryDSL` 内新增校验器（放在既有 `_check_scalar_ordering` 之前）：

```python
    @model_validator(mode="after")
    def _check_projection_shape(self) -> QueryDSL:
        """纯维度投影（metrics 为空）的形态约束（十九期 M1）。

        投影仅支持"全量 distinct 取值清单"形态：dimensions 必须非空
        （指标与维度同时为空无查询目标）；分组 Top-N / 日期补零依赖指标
        排序或填充，同比/环比按双窗口指标对比编译——投影均无语义，契约层
        直接拒绝，编译器与执行层不再重复裁决。
        """
        if self.metrics:
            return self
        if not self.dimensions:
            raise ValueError("查询必须包含至少一个指标或维度（metrics 与 dimensions 不能同时为空）")
        if self.top_n is not None:
            raise ValueError("维度投影不支持分组 Top-N（无指标可排序）")
        if self.fill_gaps:
            raise ValueError("维度投影不支持日期补零（无指标可填充）")
        if self.time_filter is not None and self.time_filter.comparison != Comparison.NONE:
            raise ValueError("维度投影不支持同比/环比对比（无指标可对比）")
        return self
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_compiler.py -v`
Expected: PASS（新增用例 + 既有编译器用例全绿——既有用例全部带 metrics，不受影响）。

- [ ] **Step 5: Commit**

```bash
git add semantic/dsl_schema.py tests/test_compiler.py
git commit -m "feat(dsl): metrics 放开可空并新增纯维度投影形态校验（十九期 M1）"
```

---

### Task 2: 编译器——`SELECT DISTINCT` 投影路径

**Files:**
- Modify: `compiler/sql_compiler.py:721-811`（`compile_sql` 普通路径）
- Test: `tests/test_compiler.py`（末尾追加）

**Interfaces:**
- Consumes: Task 1 的 `QueryDSL` 投影形态（契约层已保证投影不带 top_n/fill_gaps/comparison）
- Produces: 投影 DSL → `SELECT DISTINCT <dim> AS "<alias>" ... [WHERE ...] ORDER BY ... LIMIT n`（Task 4 的启发式计划依赖此行为）

- [ ] **Step 1: 写失败测试**

`tests/test_compiler.py` 末尾追加（`conn` fixture 来自 `tests/conftest.py`，已初始化 mock 数仓）：

```python
def test_projection_compiles_distinct_and_orders(conn):
    """十九期 M1：纯维度投影编译 SELECT DISTINCT（无 GROUP BY），执行返回真实取值。"""
    dsl = QueryDSL.model_validate(
        {
            "dimensions": [{"field": "brand"}],
            "order_by": [{"field": "brand", "direction": "asc"}],
            "limit": 100,
        }
    )
    sql = compile_sql(dsl)
    assert "SELECT DISTINCT" in sql
    assert "GROUP BY" not in sql
    assert "ORDER BY" in sql
    assert "LIMIT 100" in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) > 0
    # dim_product 经 fact_orders JOIN 语义：返回的是订单事实中出现过的品牌
    values = {r[0] for r in rows}
    assert "华为" in values


def test_projection_respects_limit_cap(conn):
    """十九期 M1 Review Focus #5：投影无界输出由 LIMIT 硬上限兜底。"""
    dsl = QueryDSL.model_validate({"dimensions": [{"field": "brand"}], "limit": 3})
    sql = compile_sql(dsl)
    assert "LIMIT 3" in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) <= 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_compiler.py::test_projection_compiles_distinct_and_orders tests/test_compiler.py::test_projection_respects_limit_cap -v`
Expected: FAIL —— 当前普通路径产出 `SELECT brand ... GROUP BY`（无 DISTINCT），第一个断言失败。

- [ ] **Step 3: 最小实现**

`compiler/sql_compiler.py` `compile_sql` 普通路径（第 743 行 `# ---- 普通路径` 注释之后）：

(a) 第 747 行 `selects: list[str] = []` 之前加判定：

```python
    # 纯维度投影（十九期 M1）：metrics 为空且 dimensions 非空（契约层已保证
    # 形态）——编译为 SELECT DISTINCT，跳过 GROUP BY；无界输出由 LIMIT 硬上限兜底
    is_projection = not dsl.metrics
```

(b) 第 779 行 `sql = "SELECT " + ", ".join(selects)` 改为：

```python
    sql = ("SELECT DISTINCT " if is_projection else "SELECT ") + ", ".join(selects)
```

(c) 第 783-784 行 GROUP BY 段改为：

```python
    if dim_exprs and not is_projection:
        sql += "\nGROUP BY " + ", ".join(dim_exprs)
```

防御性收口：在三个前置编译分支（`_compile_with_comparison` / `_compile_with_top_n` / `_compile_with_fill_gaps` 的调用点，即第 728-741 行的三个 `if` 块内、`return` 之前）各加一行：

```python
        if not dsl.metrics:
            raise CompileError("维度投影不支持该查询形态（comparison/top_n/fill_gaps）")
```

（三处分支各加同文案一行；契约层校验器在先，此处为编译器对未校验 DSL 的防御。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_compiler.py -v`
Expected: PASS（全部用例）。

- [ ] **Step 5: Commit**

```bash
git add compiler/sql_compiler.py tests/test_compiler.py
git commit -m "feat(compiler): 纯维度投影编译为 SELECT DISTINCT 并保留 LIMIT 硬上限（十九期 M1）"
```

---

### Task 3: 意图层——`ENUMERATION` 意图 + `enumeration_dsl` + 能力清单更新

**Files:**
- Modify: `core/orchestrator/intent.py`（`IntentType` 16-22 行、`classify_intent` 119-140 行、`count_dimension_dsl` 后新增函数、`capability_catalog_lines` 188-190 行）
- Test: `tests/test_intent.py`（末尾追加）

**Interfaces:**
- Consumes: 既有 `_extract_anchors` / `_build_dimension_terms` / `_build_metric_terms` / `_FILTER_CLUE_TERMS`（同文件）
- Produces: `IntentType.ENUMERATION = "enumeration"`；`classify_intent` 对枚举问法返回 `IntentProfile(IntentType.ENUMERATION, (dim,), "hard")`；`enumeration_dsl(query: str) -> dict | None`（不可确定性构造时 None）——Task 4 消费这两个产出

- [ ] **Step 1: 写失败测试**

`tests/test_intent.py` 末尾追加（import 区补 `enumeration_dsl`）：

```python
def test_enumeration_intent_classification():
    """十九期 M1：枚举意图判定（问法词表 + 单维度锚 + 无指标锚）。"""
    profile = classify_intent("把全部品牌名列举给我")
    assert profile.intent == IntentType.ENUMERATION
    assert profile.anchor_fields == ("brand",)
    assert profile.confidence == "hard"

    assert classify_intent("有哪些品类").intent == IntentType.ENUMERATION


def test_enumeration_priority_rules():
    """十九期 M1 Review Focus #1/#2：指标锚排除、基数优先。"""
    # 含指标锚："全部"不构成枚举，走指标直答
    assert classify_intent("全部品牌的GMV是多少").intent == IntentType.METRIC_SCALAR
    # 基数量词优先于枚举词：问计数不问清单
    assert classify_intent("有多少个品牌").intent == IntentType.CARDINALITY


def test_enumeration_multi_dim_is_unknown():
    """十九期 M1 Review Focus #4：多维枚举 M1 判 UNKNOWN（兜底拒答并说明）。"""
    assert classify_intent("列出所有省份和品牌").intent == IntentType.UNKNOWN


def test_enumeration_dsl_shape_and_filter_clue():
    """枚举 DSL 构造：单维投影无指标；过滤线索拒绝构造（宁拒答不冒充）。"""
    dsl = enumeration_dsl("把全部品牌名列举给我")
    assert dsl is not None
    assert dsl["metrics"] == []
    assert dsl["dimensions"] == [{"field": "brand"}]
    assert dsl["filters"] == []
    assert dsl["order_by"] == [{"field": "brand", "direction": "asc"}]

    # Review Focus #3：过滤线索（"退款"）=> 兜底拒绝构造，留 LLM 规划
    assert enumeration_dsl("列出有退款的品牌") is None


def test_capability_catalog_mentions_enumeration():
    """能力清单如实告知枚举能力（拒答报告与工作台同源）。"""
    text = "\n".join(capability_catalog_lines())
    assert "列出全部品牌" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_intent.py -v`
Expected: FAIL —— `IntentType` 无 `ENUMERATION` 属性（`AttributeError`）。

- [ ] **Step 3: 最小实现**

(a) `core/orchestrator/intent.py:16-22` `IntentType` 增加常量：

```python
class IntentType:
    """意图类型常量（str 平铺，兼容 AgentState 契约序列化）。"""

    DIAGNOSTIC = "diagnostic"
    CARDINALITY = "cardinality"
    ENUMERATION = "enumeration"
    METRIC_SCALAR = "metric_scalar"
    UNKNOWN = "unknown"
```

(b) `_COUNT_QUESTION_TERMS`（第 77-86 行）之后新增问法词表：

```python
# 枚举问句触发词（"把全部品牌名列举给我 / 有哪些品类"）：维度取值清单，
# 十九期 M1 新增直答意图。这是问法词表（非字段别名词表），允许字面维护。
_ENUMERATION_TERMS: tuple[str, ...] = (
    "列举",
    "列出",
    "有哪些",
    "都有什么",
    "都有哪些",
    "全部",
)


def _is_enumeration_question(query: str) -> bool:
    return any(t in query for t in _ENUMERATION_TERMS)
```

(c) `classify_intent`（119-140 行）在基数判定之后、标量指标判定之前插入枚举分支，并更新 docstring 判定优先级行：

```python
    判定优先级：诊断（触发词+锚点）> 基数（量词+单维度锚+无指标锚）>
    枚举（问法词+单维度锚+无指标锚）> 标量指标（指标硬锚）> UNKNOWN。
    基数与枚举均以指标锚为排除条件；量词与枚举词共存时基数优先
    （"有多少个品牌"问计数而非清单）。
```

```python
    if _is_count_question(query) and not metrics and len(dims) == 1:
        return IntentProfile(IntentType.CARDINALITY, dims, "hard")
    if _is_enumeration_question(query) and not metrics and len(dims) == 1:
        return IntentProfile(IntentType.ENUMERATION, dims, "hard")
```

（其余分支不动。）

(d) `count_dimension_dsl`（第 143-168 行）之后新增构造器：

```python
def enumeration_dsl(query: str) -> dict | None:
    """枚举类问题的确定性纯维度投影 DSL（十九期 M1，元数据探查，无时间过滤）。

    非枚举问题返回 None；含过滤线索（"列出有退款的品牌"）时也返回 None——
    兜底无法确定性推导 WHERE 口径，全量清单会冒充过滤语义（宁拒答不给
    口径不完整的结果；LLM 在场时由 Planner 规划带过滤的投影）。
    """
    profile = classify_intent(query)
    if profile.intent != IntentType.ENUMERATION:
        return None
    if any(t in query for t in _FILTER_CLUE_TERMS):
        return None
    field = profile.anchor_fields[0]
    return {
        "metrics": [],
        "dimensions": [{"field": field}],
        "filters": [],
        "order_by": [{"field": field, "direction": "asc"}],
    }
```

(e) `capability_catalog_lines` 第 189 行维度清单行追加枚举提示：

```python
        f"- 分析维度：{dims}；支持基数探查（如「有多少个省份」）"
        "与取值枚举（如「列出全部品牌」）",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_intent.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/intent.py tests/test_intent.py
git commit -m "feat(intent): 新增枚举意图与确定性维度投影 DSL 构造器（十九期 M1）"
```

---

### Task 4: 编排接入——启发式分支 + Planner 预路由 + L3 守卫

**Files:**
- Modify: `core/orchestrator/nodes.py:203-238`（`_heuristic_plan`）、`core/orchestrator/nodes.py:492-551`（`planner_node` LLM 块与 planner_used 判定）、`core/orchestrator/nodes.py:761-785`（`_intent_dsl_mismatch`）
- Test: `tests/test_orchestrator.py`（末尾追加）

**Interfaces:**
- Consumes: Task 3 的 `IntentType.ENUMERATION` / `enumeration_dsl`（经 `core.orchestrator.intent` 导入，`nodes.py:35-40` import 区需补 `enumeration_dsl`）
- Produces: `planner_node` 对枚举问法产两步计划（query + synthesize，`answered_by="heuristic"`）；L3 守卫拒绝"枚举意图 + 带指标 DSL"错位组合

- [ ] **Step 1: 写失败测试**

`tests/test_orchestrator.py` 末尾追加（确认文件头部已有 `from core.orchestrator.nodes import ...` 与 `from core.orchestrator.state import AgentState` import，缺则补）：

```python
def test_heuristic_plan_enumeration():
    """十九期 M1：枚举问法的确定性两步计划（投影 DSL + 综合）。"""
    steps = _heuristic_plan("把全部品牌名列举给我")
    assert steps is not None and len(steps) == 2
    s1 = steps[0]
    assert s1.kind == "query" and s1.dsl is not None
    assert s1.dsl["metrics"] == []
    assert s1.dsl["dimensions"] == [{"field": "brand"}]
    assert steps[1].kind == "synthesize"


def test_planner_pre_routes_enumeration_without_llm(monkeypatch):
    """枚举预路由：LLM 在场也不发起调用（现行规划契约表达不了投影）。"""

    def _forbidden_llm(*args, **kwargs):
        raise AssertionError("枚举预路由不得调用 LLM")

    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", _forbidden_llm)
    state = planner_node(AgentState(user_query="把全部品牌名列举给我"))
    assert state.phase == "query"
    assert state.answered_by == "heuristic"
    assert state.plan_steps[0].dsl is not None
    assert state.plan_steps[0].dsl["metrics"] == []


def test_intent_dsl_mismatch_rejects_metrics_on_enumeration():
    """L3 守卫：枚举意图的查询必须是纯维度投影（Review Focus #1 兜底）。"""
    query = "把全部品牌名列举给我"
    ok_payload = {"metrics": [], "dimensions": [{"field": "brand"}], "filters": []}
    assert _intent_dsl_mismatch(query, ok_payload) is None
    bad_payload = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
        "dimensions": [{"field": "brand"}],
        "filters": [],
    }
    mismatch = _intent_dsl_mismatch(query, bad_payload)
    assert mismatch is not None and "投影" in mismatch
```

（注：`test_orchestrator.py` 若未导入 `_orch_nodes`，加 `import core.orchestrator.nodes as _orch_nodes`；`_heuristic_plan` / `planner_node` / `_intent_dsl_mismatch` 从 `core.orchestrator.nodes` 导入。）

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_orchestrator.py::test_heuristic_plan_enumeration tests/test_orchestrator.py::test_planner_pre_routes_enumeration_without_llm tests/test_orchestrator.py::test_intent_dsl_mismatch_rejects_metrics_on_enumeration -v`
Expected: FAIL —— `_heuristic_plan` 对枚举问法返回 None（走不到 ENUMERATION 分支）；预路由用例中 `answered_by != "heuristic"`；守卫用例 `mismatch is None`。

- [ ] **Step 3: 最小实现**

(a) `nodes.py:35-40` import 区补 `enumeration_dsl`：

```python
from core.orchestrator.intent import (
    IntentProfile,
    IntentType,
    classify_intent,
    dimension_terms,
    enumeration_dsl,
)
```

(b) `_heuristic_plan`（203-238 行）在 CARDINALITY 分支后插入 ENUMERATION 分支，并更新 docstring：

```python
    - DIAGNOSTIC => 诊断式 DAG（总量对比 -> 因子分解 -> 维度下钻 -> 综合）；
    - CARDINALITY => count_distinct 直答（DSL 由 intent 模块构造）；
    - ENUMERATION => 纯维度投影直答（十九期 M1，DSL 由 intent 模块构造）；
    - METRIC_SCALAR => 按锚点直答（跨表混合锚等不可构造时 None）；
    - UNKNOWN => None（planner 置 blocked_reason，critic/synthesize 短路拒答）。
```

```python
    if profile.intent == IntentType.ENUMERATION:
        dsl = enumeration_dsl(query)
        if dsl is None:
            return None
        field = dsl["dimensions"][0]["field"]
        return [
            PlanStep(
                id="s1",
                goal=f"列出{_dimension_label(field)}的全部取值",
                kind="query",
                dsl=dsl,
            ),
            PlanStep(id="s2", goal="直接报告维度取值清单", kind="synthesize", depends_on=["s1"]),
        ]
```

(c) `planner_node`（492 行起）预路由重构——精确替换第 492-551 行为：

```python
    llm = _resolve_llm()
    steps: list[PlanStep] | None = None
    from_llm = False
    # 枚举直答预路由（十九期 M1）：ENUMERATION 为词表硬判定的确定性意图，
    # 而现行 LLM 规划契约（metrics 必填）无法表达维度投影——先走启发式
    # 直答；构造失败（如"列出有退款的品牌"含过滤线索）才放行 LLM 规划
    if (
        llm is not None
        and classify_intent(state.user_query).intent == IntentType.ENUMERATION
    ):
        steps = _heuristic_plan(state.user_query)
    if steps is None and llm is not None:
        # SchemaAgent 动态 profiling（后续项）：低基数字段枚举值注入规划上下文；
        # 探查失败降级为空 dict（不阻断规划主链路），离线/无库环境无副作用
        from core.retrieval.profiling import profile_enum_values

        error_context = "\n".join(state.error_context.errors[-3:]) or None
        # plan_review 审批卡的"修改"指令（M2）：与 error_context 同一注入位——
        # 用户必须针对性修正计划，LLM 不得无视修改诉求重新规划
        if state.plan_edit_instruction:
            user_edit = f"用户修改指令：{state.plan_edit_instruction}"
            error_context = f"{error_context}\n{user_edit}" if error_context else user_edit
        payload = _llm_json(
            llm,
            PLANNER_SYSTEM,
            planner_prompt(
                state.user_query,
                schema_digest(profile_enum_values()),
                error_context=error_context,
                history_context=state.history_digest or None,
            ),
        )
        if payload:
            if payload.get("clarification"):
                clar_raw = payload["clarification"]
                options: list[str] = []
                if isinstance(clar_raw, dict):
                    question = str(clar_raw.get("question") or "").strip()
                    raw_opts = clar_raw.get("options")
                    if isinstance(raw_opts, list):
                        options = [str(o) for o in raw_opts if isinstance(o, str) and o.strip()]
                else:
                    question = str(clar_raw).strip()
                return state.apply(
                    phase="clarify",
                    clarification=question or "请补充分析需求",
                    clarification_options=options,
                )
            steps = _plan_from_llm(payload)
            from_llm = steps is not None
            # intent 回传（十八期）：仅诊断可观测，宽容消费（缺失/非法不阻塞）；
            # L3 守卫不依赖它（用 intent 模块确定性重判）
            intent_payload = payload.get("intent")
            if isinstance(intent_payload, dict) and intent_payload.get("type") in (
                "diagnostic",
                "cardinality",
                "metric_scalar",
                "unknown",
            ):
                state = state.apply(
                    intent_type=str(intent_payload["type"]),
                    intent_anchors=[
                        str(a) for a in (intent_payload.get("anchors") or []) if isinstance(a, str)
                    ],
                )
    if steps is None:
        steps = _heuristic_plan(state.user_query)
    planner_used = "llm" if from_llm else "heuristic"
```

（行为等价性：非枚举问法路径与原实现完全一致——原 `else: planner_used = "llm"` 语义由 `from_llm` 精确承载；LLM 成功产计划时 `from_llm=True`，其余一律 `"heuristic"`。）

(d) `_intent_dsl_mismatch`（761-785 行）在 CARDINALITY 分支后加枚举规则：

```python
    if profile.intent == IntentType.ENUMERATION:
        if len(metrics) == 0:
            return None
        return "枚举类问题的查询必须是纯维度投影（不得携带聚合指标）"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: PASS（新增用例 + 既有编排用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): 枚举问法确定性直答接入——启发式分支/LLM 预路由/L3 守卫（十九期 M1）"
```

---

### Task 5: 拒答建议按成因三分流

**Files:**
- Modify: `core/orchestrator/nodes.py:2601-2639`（`_cannot_answer_report`）；`planner_node` LLM 块内（Task 4 (c) 的 `payload = _llm_json(...)` 之后）
- Test: `tests/test_degraded_fallback.py`（末尾追加）

**Interfaces:**
- Consumes: Task 4 重构后的 `planner_node`；`AgentState.scratchpad: list[str]`
- Produces: scratchpad 标记 `"[planner] llm-call-failed"`（planner blocked 路径存续）；`_cannot_answer_report` 建议语三分支

- [ ] **Step 1: 写失败测试**

`tests/test_degraded_fallback.py` 末尾追加（确认已导入 `AgentState` 与 `_cannot_answer_report`；`_cannot_answer_report` 为私有函数，测试经 `from core.orchestrator.nodes import _cannot_answer_report` 导入，文件若已有 nodes 相关 import 则并入）：

```python
def test_refusal_advice_distinguishes_cause():
    """十九期 M1：拒答建议按成因三分流（Review Focus #6）。"""
    from core.orchestrator.nodes import _cannot_answer_report

    # 能力边界类拒答（LLM 健康）：不得建议检查网关连通性
    state_cap = AgentState(user_query="流量表现怎么样", blocked_reason="无法从语义目录识别问题意图")
    report_cap = _cannot_answer_report(state_cap)
    assert "能力边界" in report_cap
    assert "网关连通性" not in report_cap

    # LLM 调用失败标记 => 保留连通性排查指引
    state_llm = AgentState(
        user_query="流量表现怎么样",
        blocked_reason="无法从语义目录识别问题意图",
        scratchpad=["[planner] llm-call-failed"],
    )
    report_llm = _cannot_answer_report(state_llm)
    assert "网关连通性" in report_llm
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_degraded_fallback.py::test_refusal_advice_distinguishes_cause -v`
Expected: FAIL —— 当前实现 LLM 已配置分支固定输出"网关连通性"文案（第一个断言失败；`_resolve_llm` 在测试环境返回 None 时先补 `_resolve_llm` 的 monkeypatch，见 Step 3 后的测试修正说明）。

- [ ] **Step 3: 最小实现**

(a) `planner_node` LLM 块内（Task 4 (c) 的 `payload = _llm_json(...)` 赋值之后、`if payload:` 之前）插入：

```python
        if not payload:
            # LLM 调用/解析失败标记（十九期 M1）：拒答建议按成因分流；
            # 该标记仅在 blocked 路径存续（plan 产出时 scratchpad 被整体替换）
            state = state.apply(scratchpad=[*state.scratchpad, "[planner] llm-call-failed"])
```

(b) `_cannot_answer_report`（2601-2639 行）建议段替换——docstring 补一行"十九期 M1：建议按拒答成因三分流（能力边界 / LLM 失败 / 未配置）"，第 2629-2638 行 `if _resolve_llm() is None: ... else: ...` 整段替换为：

```python
    llm_call_failed = "[planner] llm-call-failed" in state.scratchpad
    if _resolve_llm() is None:
        lines.append(
            "建议：请调整问法（明确指标或维度）。当前未配置 LLM 模型，"
            "语义理解由确定性兜底承担；配置 LLM 后重试可获得完整语义理解。"
        )
    elif llm_call_failed:
        lines.append(
            "建议：请调整问法（明确指标或维度）。LLM 已配置但本次调用失败，"
            "拒答由确定性兜底裁决；若持续出现，请检查 LLM 网关连通性或查看服务日志。"
        )
    else:
        lines.append(
            "建议：请调整问法（明确指标或维度）。本次拒答源于问法超出当前可确定的"
            "查询口径范围（能力边界），与 LLM 服务状态无关。"
        )
```

(c) Step 1 测试的环境适配：测试进程内 `_resolve_llm` 可能因无 Key 返回 None（走"未配置"分支使断言失真）——两个用例各自 monkeypatch：

```python
def test_refusal_advice_distinguishes_cause(monkeypatch):
    """十九期 M1：拒答建议按成因三分流（Review Focus #6）。"""
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.nodes import _cannot_answer_report

    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())

    # 能力边界类拒答（LLM 健康）：不得建议检查网关连通性
    state_cap = AgentState(user_query="流量表现怎么样", blocked_reason="无法从语义目录识别问题意图")
    report_cap = _cannot_answer_report(state_cap)
    assert "能力边界" in report_cap
    assert "网关连通性" not in report_cap

    # LLM 调用失败标记 => 保留连通性排查指引
    state_llm = AgentState(
        user_query="流量表现怎么样",
        blocked_reason="无法从语义目录识别问题意图",
        scratchpad=["[planner] llm-call-failed"],
    )
    report_llm = _cannot_answer_report(state_llm)
    assert "网关连通性" in report_llm
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_degraded_fallback.py -v`
Expected: PASS（新增用例 + 既有分层降级用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_degraded_fallback.py
git commit -m "fix(orchestrator): 拒答建议按成因三分流——能力边界类不再误导检查网关（十九期 M1）"
```

---

### Task 6: 意图评测用例 + 全量验证收口

**Files:**
- Modify: `eval/intent_eval.py`（`main` 的 expect 分支区，45-68 行之后加 `list_answer`）
- Modify: `eval/intent_golden.json`（末尾追加 3 条用例）
- Test: 全量回归 + 质量门

**Interfaces:**
- Consumes: Task 1-5 全部产出（端到端兜底链路）
- Produces: 意图评测含枚举用例并全绿（案例 A 验收）

- [ ] **Step 1: 写失败评测用例**

`eval/intent_golden.json` 数组末尾追加：

```json
  ,
  {
    "question": "把全部品牌名列举给我",
    "expect": "list_answer",
    "expected_contains": "华为"
  },
  {
    "question": "有哪些品类",
    "expect": "list_answer",
    "expected_contains": "数码"
  },
  {
    "question": "列出所有省份和品牌",
    "expect": "refusal"
  }
```

（注意 JSON 语法：在现有最后一个用例的 `}` 后补逗号再追加；追加后整体 `json.loads` 校验合法。）

- [ ] **Step 2: 跑评测确认失败**

Run: `python -m mock.init_duckdb && python -m eval.intent_eval`
Expected: FAIL —— `list_answer` 无对应分支，两条枚举用例判 FAIL（当前被拒答）。

- [ ] **Step 3: 最小实现**

`eval/intent_eval.py` 的 `main` expect 分支区（`refusal` 分支之后）追加：

```python
        elif expect == "list_answer":
            # 十九期 M1：维度取值枚举直答——报告含结果小节与指定取值，
            # 不得出现拒答文案（多行清单经表格渲染，非"查询答案："单值形态）
            expected_contains = case.get("expected_contains")
            ok = (
                trace.phase == "done"
                and "查询结果" in report
                and "无法作答" not in report
                and (expected_contains is None or expected_contains in report)
            )
```

- [ ] **Step 4: 跑评测确认通过**

Run: `python -m eval.intent_eval`
Expected: `11/11 passed`（8 条既有 + 3 条新增全绿）。

- [ ] **Step 5: 全量质量门**

```bash
python -m pytest -q          # 既有 875 + 本计划新增约 12 条全绿
black --check .
ruff check .
```

Expected: 三项全绿。任何失败先修复再提交（严禁带红提交）。

- [ ] **Step 6: Commit**

```bash
git add eval/intent_eval.py eval/intent_golden.json
git commit -m "test(eval): 意图评测新增枚举直答与多维枚举拒答用例（十九期 M1）"
```

---

## 红线自查记录（计划完成后执行者可复核）

1. **规格覆盖**：spec §3.1 纯投影 → Task 1/2；§3.2 枚举意图（词表/直答/兜底可答/多维拒答/能力清单）→ Task 3；§3.8 文案分流 → Task 5；§4 案例 A → Task 6 端到端；§8 M1 验收标准全部有对应任务。HAVING/表达式指标明确不在本计划（M2）。
2. **占位符扫描**：无 TBD/TODO；全部代码步骤带完整代码与验证命令。
3. **类型一致性**：`enumeration_dsl` 返回 dict（`metrics/dimensions/filters/order_by` 四键），Task 4 `(b)` 消费 `dsl["dimensions"][0]["field"]`，Task 1 契约校验 `metrics == []` 合法——一致；`IntentType.ENUMERATION` 字符串值 `"enumeration"` 在 Task 3/4 间一致。
4. **Review Focus 落实**：六条全部挂到任务测试（#1/#2/#3/#4 → Task 3，#5 → Task 2，#6 → Task 5）。
