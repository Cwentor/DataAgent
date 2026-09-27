# 意图路由收敛与诚实兜底（十八期一期）实施计划

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。

**Goal:** 把编排兜底从"什么问题都猜 GMV"改造为"意图准入制 + 诚实拒答"，实现"理解优先、兜底有界、报告可溯源"。

**Architecture:** 新增确定性意图分类器（`core/orchestrator/intent.py`，L1 词表硬匹配）作为兜底准入与 L3 错位守卫的唯一判定依据；`_scalar_dsl` 锚点化替换固定 `sum(gmv)`；UNKNOWN 意图沿 `blocked_reason` 字段短路直达诚实拒答报告（复用 `no_data_reason` 的短路模式，两引擎同构）；LLM 报告加最小版 Grounding 数值溯源标注。

**Tech Stack:** Python 3.12 + pydantic（AgentState 契约）、pytest、现有 DuckDB/编译器/编排图（零新依赖）。

**Spec:** `docs/superpowers/specs/2026-09-27-intent-routing-honest-fallback-design.md`（执行者必须同时读规格；本文与规格冲突时以规格为准）

## Global Constraints

- 新增逻辑字段必须登记在 `semantic/catalog.py` 的 `COLUMNS` 白名单（本次零新增字段，词表只引用既有字段）；
- LLM 仅产出契约 JSON（DSL），严禁裸 SQL；DSL 一律 `extra="forbid"`；
- 评测锚点 `AS_OF_DATE = 2024-06-30`、随机种子 42，确定性可复现；
- L3 守卫判定依据 = intent 模块对 `user_query` 的**确定性 L1 硬匹配**，不信任 LLM 回传意图；
- CARDINALITY 守卫只限制聚合与投影目标（metrics），**不限制过滤条件（filters）**；
- 拒答/降级路径严禁产生任何 LLM 调用（纯确定性字符串构造）；
- 提交前 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿；
- 运行环境：`C:/Users/cwt15/.conda/envs/futurebi/python.exe`（本机 conda 环境，等效 dataagent）。

## Review Focus（规格隐含、无既有测试覆盖的输入，各配测试挂到 owning task）

1. "退款率是多少"（比率类词，数仓无对应字段）→ 必须 UNKNOWN 拒答，严禁 sum 任何字段（Task 2/3/10）；
2. "有退款的省份有多少个"（过滤语境含指标词"退款"）→ 必须 CARDINALITY 放行且 WHERE 不受限（Task 2/6）；
3. "GMV 和退款金额各多少"（跨表混合锚）→ 兜底拒答构造（`_scalar_dsl` 返回 None），不得输出口径混乱的单查询（Task 3）；
4. "5月GMV和订单量各多少"（同表多锚）→ 单数据集多列（`order_amount` sum + `order_id` count），多列走预览表渲染路径，计数列不万元化（Task 3）；
5. LLM 报告出现编造的千分位/万元/百分比数值 → Grounding 校验产出"数据溯源提示"小节（Task 8）。

---

### Task 1: AgentState 契约扩展

**Files:**
- Modify: `core/orchestrator/state.py`（`class AgentState`，当前约 155 行起）
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Produces: `AgentState` 新增 4 个字段——`blocked_reason: str | None`、`answered_by: str`（默认 `""`）、`intent_type: str | None`、`intent_anchors: list[str]`。后续所有任务消费。

- [ ] **Step 1: 写失败测试**

在 `tests/test_orchestrator.py` 的 `test_agent_state_forbids_extra_fields` 之后追加：

```python
def test_agent_state_intent_fields_contract():
    """十八期：诚实兜底链路的状态契约（blocked/answered_by/intent）。"""
    state = AgentState(user_query="有多少个省份")
    assert state.blocked_reason is None
    assert state.answered_by == ""
    assert state.intent_type is None
    assert state.intent_anchors == []
    # extra=forbid 不被破坏：未知字段仍拒绝
    import pytest as _pytest
    from pydantic import ValidationError as _VE

    with _pytest.raises(_VE):
        AgentState(user_query="x", rogue_field=1)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py::test_agent_state_intent_fields_contract -v`
Expected: FAIL, `AttributeError`（`blocked_reason` 不存在）

- [ ] **Step 3: 最小实现**

在 `core/orchestrator/state.py` 的 `class AgentState` 内、`# 控制与韧性` 注释区（`phase` 字段之前）插入：

```python
    # 诚实兜底（十八期）：意图不可确定时的拒答原因（区别于 no_data_reason 的
    # 数据缺失语义）；非空时 critic/synthesize 短路直达诚实拒答报告
    blocked_reason: str | None = Field(default=None, description="无法作答的原因（意图不可确定/意图-DSL 错位）")
    # 报告产出方式："llm"（LLM 规划成功）| "heuristic"（兜底接管，报告需降级标注）| "blocked"（拒答）
    answered_by: str = Field(default="", description="规划产出方式（降级可见化标注依据）")
    # LLM Planner 回传的意图（仅诊断可观测；L3 守卫不依赖它，用确定性 L1 重判）
    intent_type: str | None = Field(default=None, description="LLM 回传意图类型（diagnostic/cardinality/metric_scalar/unknown）")
    intent_anchors: list[str] = Field(default_factory=list, description="LLM 回传意图锚定字段")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py -q`
Expected: PASS（全文件，含既有用例）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/state.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): AgentState 新增 blocked_reason/answered_by/intent 契约字段"
```

---

### Task 2: intent.py — 确定性意图分类器

**Files:**
- Create: `core/orchestrator/intent.py`
- Test: `tests/test_intent.py`

**Interfaces:**
- Consumes: `semantic/catalog.py` 的 `COLUMNS`、`DRILLDOWN_DIM_FIELDS`（只读）。
- Produces（Task 3/6 消费，签名逐字固定）:
  - `class IntentType`: 常量 `DIAGNOSTIC="diagnostic"` / `CARDINALITY="cardinality"` / `METRIC_SCALAR="metric_scalar"` / `UNKNOWN="unknown"`
  - `@dataclass(frozen=True) class IntentProfile`: `intent: str`、`anchor_fields: tuple[str, ...]`、`confidence: str`
  - `classify_intent(query: str) -> IntentProfile`
  - `count_dimension_dsl(query: str) -> dict | None`（基数 DSL，无时间过滤）
  - `capability_catalog_lines() -> list[str]`（拒答报告能力清单）
  - `DIMENSION_TERMS: dict[str, str]`（nodes.py 收编后复用）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_intent.py`：

```python
"""意图分类器单测：词表硬匹配（长词优先）、三类意图判定、基数 DSL、能力清单。"""

from core.orchestrator.intent import (
    IntentType,
    capability_catalog_lines,
    classify_intent,
    count_dimension_dsl,
)


def test_metric_anchor_longest_match_first():
    """长词优先：'退款金额'不被子串误吞（Review Focus 1 前置）。"""
    profile = classify_intent("5月退款金额是多少")
    assert profile.intent == IntentType.METRIC_SCALAR
    assert profile.anchor_fields == ("refund_amount",)
    assert profile.confidence == "hard"


def test_diagnostic_intent():
    profile = classify_intent("为什么GMV下滑")
    assert profile.intent == IntentType.DIAGNOSTIC
    assert "order_amount" in profile.anchor_fields


def test_cardinality_intent_and_filters_with_metric_word():
    """量词+单一维度锚 => 基数；过滤语境的'退款'不构成指标锚（Review Focus 2）。"""
    profile = classify_intent("有多少个省份")
    assert profile.intent == IntentType.CARDINALITY
    assert profile.anchor_fields == ("province",)

    with_refund_filter = classify_intent("有退款的省份有多少个")
    assert with_refund_filter.intent == IntentType.CARDINALITY
    # 含过滤线索：兜底拒绝构造 DSL（全量计数会丢失"有退款"过滤语义），留 LLM
    assert count_dimension_dsl("有退款的省份有多少个") is None

    assert count_dimension_dsl("有多少个省份") is not None


def test_count_dsl_shape():
    dsl = count_dimension_dsl("有多少个省份")
    assert dsl == {
        "metrics": [
            {"kind": "aggregate", "field": "province", "agg": "count_distinct", "alias": "province_count"}
        ],
        "dimensions": [],
        "filters": [],
    }
    assert count_dimension_dsl("各省GMV多少") is None  # 无量词模式


def test_unknown_intent_for_rate_and_vague_queries():
    """比率词/空泛问题 => UNKNOWN（数仓无字段，兜底不猜）。"""
    assert classify_intent("退款率是多少").intent == IntentType.UNKNOWN
    assert classify_intent("帮我看看最近情况").intent == IntentType.UNKNOWN
    assert classify_intent("流量表现怎么样").intent == IntentType.UNKNOWN


def test_capability_catalog_lines():
    lines = capability_catalog_lines()
    assert any("province" in line and "省份" in line for line in lines)
    assert any("order_amount" in line for line in lines)
    assert any("shop_name" in line for line in lines)  # 店铺维度不遗漏
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_intent.py -v`
Expected: FAIL, `ModuleNotFoundError: core.orchestrator.intent`

- [ ] **Step 3: 最小实现**

创建 `core/orchestrator/intent.py`：

```python
"""用户问题意图分类器（十八期：理解优先、兜底准入、诚实拒答）。

三级路由的确定性 L1 层：
- 词表硬匹配（**长词优先**遍历并消费已匹配片段，防"退款金额"被"金额"式
  子串误吞）=> confidence="hard"；
- 判定不出的交给 LLM Planner（confidence="llm"）或诚实拒答（UNKNOWN）；
- L3 意图-DSL 错位守卫使用本模块对 user_query 的确定性重判结果，
  不信任 LLM 回传意图（LLM 意图可能出错，据其拦截会误杀合法查询）。
"""

from __future__ import annotations

from dataclasses import dataclass


class IntentType:
    """意图类型常量（str 平铺，兼容 AgentState 契约序列化）。"""

    DIAGNOSTIC = "diagnostic"
    CARDINALITY = "cardinality"
    METRIC_SCALAR = "metric_scalar"
    UNKNOWN = "unknown"


_INTENT_VALUES = (
    IntentType.DIAGNOSTIC,
    IntentType.CARDINALITY,
    IntentType.METRIC_SCALAR,
    IntentType.UNKNOWN,
)


@dataclass(frozen=True)
class IntentProfile:
    """意图画像：类型 + 硬命中语义字段 + 置信分级。"""

    intent: str
    anchor_fields: tuple[str, ...]
    confidence: str  # "hard"（词表命中）| "llm"（LLM 判定）| "none"（无锚点）


# 指标别名词表：业务词 -> 语义目录字段（长词优先匹配）。
# 仅收录事实主表（fact_orders/fact_refunds）既有字段；比率/派生指标
# （退款率/客单价）数仓无现成字段，严禁收录——宁可 UNKNOWN 拒答。
_METRIC_TERMS: tuple[tuple[str, str], ...] = (
    ("退款金额", "refund_amount"),
    ("优惠金额", "discount_amount"),
    ("折扣金额", "discount_amount"),
    ("成交金额", "order_amount"),
    ("订单金额", "order_amount"),
    ("销售额", "order_amount"),
    ("gmv", "order_amount"),
    ("订单量", "order_id"),
    ("订单数", "order_id"),
    ("买家数", "user_id"),
    ("用户数", "user_id"),
)

# 维度别名词表（自 nodes.py _DIMENSION_TERMS 收编，值=语义字段）。
DIMENSION_TERMS: dict[str, str] = {
    "province": "province",
    "省份": "province",
    "省": "province",
    "地区": "province",
    "地域": "province",
    "区域": "province",
    "大区": "province",
    "城市": "province",
    "category": "category",
    "品类": "category",
    "类目": "category",
    "品类结构": "category",
    "brand": "brand",
    "品牌": "brand",
    "店铺": "shop_name",
    "门店": "shop_name",
    "shop_name": "shop_name",
}

_DIAGNOSTIC_TERMS: tuple[str, ...] = (
    "为什么",
    "下滑",
    "下降",
    "下跌",
    "上涨",
    "增长",
    "归因",
    "原因",
)

# 基数问句量词模式（"多少个省份 / 几个品类 / 多少家店铺"）。
_COUNT_QUESTION_TERMS: tuple[str, ...] = (
    "多少个",
    "几个",
    "多少种",
    "几种",
    "多少类",
    "几类",
    "多少家",
    "几家",
)

# 基数问句中的过滤线索词根（"有退款的省份有多少个"）：此类问句的 WHERE
# 口径无法从词表确定性推导，兜底构造全量计数会丢失过滤语义（答非所问的
# 温和形态）——count_dimension_dsl 返回 None 拒答，留给 LLM 规划。
# 注意：这只是"兜底是否构造 DSL"的约束；意图分类仍判 CARDINALITY，
# L3 守卫对 LLM 规划的带 WHERE 基数查询放行（filters 不受限）。
_FILTER_CLUE_TERMS: tuple[str, ...] = ("退款", "优惠", "折扣", "支付", "成功", "失败")


def _extract_anchors(
    query: str, terms: tuple[tuple[str, str], ...] | dict[str, str]
) -> tuple[str, ...]:
    """词表锚点提取：长词优先遍历，命中后消费片段（防子串重复/误吞）。"""
    pairs = sorted(terms.items() if isinstance(terms, dict) else terms, key=lambda p: len(p[0]), reverse=True)
    lowered = query.lower()
    found: list[str] = []
    remaining = lowered
    for term, field in pairs:
        if term in remaining and field not in found:
            found.append(field)
            remaining = remaining.replace(term, " ")
    return tuple(found)


def _is_count_question(query: str) -> bool:
    return any(t in query for t in _COUNT_QUESTION_TERMS)


def classify_intent(query: str) -> IntentProfile:
    """确定性意图分类（L1 硬匹配）。

    判定优先级：诊断（触发词+锚点）> 基数（量词+单维度锚+无指标锚）>
    标量指标（指标硬锚）> UNKNOWN。基数判定以指标锚为排除条件——
    "有多少个品类的GMV"（量词+维度+指标锚）是按维度的指标问题而非基数问题。
    """
    metrics = _extract_anchors(query, _METRIC_TERMS)
    dims = _extract_anchors(query, DIMENSION_TERMS)
    is_diagnostic = any(t in query for t in _DIAGNOSTIC_TERMS)
    if is_diagnostic and (metrics or dims):
        return IntentProfile(IntentType.DIAGNOSTIC, metrics + dims, "hard")
    if _is_count_question(query) and not metrics and len(dims) == 1:
        return IntentProfile(IntentType.CARDINALITY, dims, "hard")
    if metrics and not is_diagnostic:
        return IntentProfile(IntentType.METRIC_SCALAR, metrics, "hard")
    return IntentProfile(IntentType.UNKNOWN, (), "none")


def count_dimension_dsl(query: str) -> dict | None:
    """基数类问题的确定性 count_distinct DSL（元数据探查，无时间过滤）。

    非基数问题返回 None；含过滤线索（"有退款的省份"）时也返回 None——
    兜底无法确定性推导 WHERE 口径，构造全量计数会丢失过滤语义，宁拒答
    不给口径不完整的结果（LLM 在场时由 Planner 规划带过滤的查询）。
    多维度计数（"多少个省份和品类"）不兜底。
    """
    profile = classify_intent(query)
    if profile.intent != IntentType.CARDINALITY:
        return None
    if any(t in query for t in _FILTER_CLUE_TERMS):
        return None
    field = profile.anchor_fields[0]
    return {
        "metrics": [
            {"kind": "aggregate", "field": field, "agg": "count_distinct", "alias": f"{field}_count"}
        ],
        "dimensions": [],
        "filters": [],
    }


def capability_catalog_lines() -> list[str]:
    """拒答报告的能力清单（诚实告知系统边界，含中文 label）。"""
    from semantic.catalog import COLUMNS, DRILLDOWN_DIM_FIELDS

    dim_fields = sorted(set(DRILLDOWN_DIM_FIELDS) | set(DIMENSION_TERMS.values()))
    dims = "、".join(f"{f}（{COLUMNS[f].label or f}）" for f in dim_fields if f in COLUMNS)
    metrics = "、".join(
        f"{name}（{meta.label or name}）" for name, meta in COLUMNS.items() if meta.dtype == "float"
    )
    return [
        f"- 分析维度：{dims}；支持基数探查（如「有多少个省份」）",
        f"- 金额指标：{metrics}",
        "- 计数指标：订单量（order_id）、买家数（user_id）",
    ]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_intent.py -v`
Expected: PASS（6 用例）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/intent.py tests/test_intent.py
git commit -m "feat(orchestrator): 确定性意图分类器——词表硬匹配长词优先、三类意图判定、基数DSL与能力清单"
```

---

### Task 3: 兜底准入收敛（_scalar_dsl 锚点化 + _heuristic_plan 准入制 + planner blocked 路径）

**Files:**
- Modify: `core/orchestrator/nodes.py`（`_scalar_dsl` 约 461 行、`_heuristic_plan` 约 233 行、`planner_node` 约 345 行、`_count_dimension_dsl` 约 497 行删除、词表常量约 61-94/484-495 行区域）
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Task 2 的 `classify_intent`/`IntentType`/`count_dimension_dsl`/`DIMENSION_TERMS`；Task 1 的 `blocked_reason`/`answered_by`。
- Produces: `_heuristic_plan(query: str) -> list[PlanStep] | None`（签名变化：可返回 None）；`_scalar_dsl(anchors: tuple[str, ...]) -> dict | None`（签名变化：收锚点）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_orchestrator.py` 追加：

```python
def test_scalar_dsl_by_anchors():
    """标量兜底按锚点取数：金额 sum、计数 count、别名带语义（Review Focus 4）。"""
    from core.orchestrator.nodes import _scalar_dsl

    dsl = _scalar_dsl(("order_amount", "order_id"), "2024年5月GMV和订单量各多少")
    assert dsl is not None
    assert dsl["metrics"] == [
        {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "order_amount"},
        {"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "order_id_count"},
    ]
    assert dsl["filters"] == [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}]
    # 跨表混合锚：兜底拒答（口径混乱风险，LLM 在场时由 Planner 规划）
    assert _scalar_dsl(("order_amount", "refund_amount"), "GMV和退款金额各多少") is None
    # 纯退款单锚：不带 pay_status 过滤（fact_refunds 无该字段语义）
    refund = _scalar_dsl(("refund_amount",), "2024年5月退款金额是多少")
    assert refund is not None and refund["filters"] == []


def test_heuristic_plan_admission():
    """兜底准入制：UNKNOWN 返回 None（诚实拒答），三类可答意图各归其位。"""
    from core.orchestrator import intent as intent_mod
    from core.orchestrator.nodes import _heuristic_plan

    assert _heuristic_plan("有多少个省份") is not None
    assert _heuristic_plan("为什么GMV下滑") is not None
    metric_steps = _heuristic_plan("2024年5月订单量是多少")
    assert metric_steps is not None
    assert metric_steps[0].dsl is not None
    assert metric_steps[0].dsl["metrics"][0]["field"] == "order_id"
    # 退款率：数仓无字段，拒答（Review Focus 1）
    assert _heuristic_plan("退款率是多少") is None
    assert intent_mod.classify_intent("退款率是多少").intent == intent_mod.IntentType.UNKNOWN


def test_run_agent_unknown_blocks_honestly(tmp_path, monkeypatch):
    """UNKNOWN 问题：LLM 失败后兜底拒答，绝不再猜 GMV（核心回归锚点）。"""
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: None)
    trace = run_agent("帮我看看最近情况", session_id="blockedq")
    assert trace.phase == "done"
    assert "无法作答" in trace.report
    assert "万元" not in trace.report  # 绝无猜出的 GMV
    assert "province" in trace.report  # 能力清单引导存在
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py::test_scalar_dsl_by_anchors tests/test_orchestrator.py::test_heuristic_plan_admission tests/test_orchestrator.py::test_run_agent_unknown_blocks_honestly -v`
Expected: FAIL（`_scalar_dsl` 签名不接受 tuple / `_heuristic_plan` 返回列表而非 None）

- [ ] **Step 3: 最小实现**

3a. `nodes.py` 头部 import 区（`from agent.heuristic import region_provinces` 附近）加：

```python
from core.orchestrator.intent import (
    DIMENSION_TERMS as DIMENSION_TERMS,
)
from core.orchestrator.intent import (
    IntentType,
    classify_intent,
    count_dimension_dsl,
)
```

3b. 删除 `nodes.py` 中 `_DIMENSION_TERMS`（约 61-79 行）、`_COUNT_QUESTION_TERMS`（约 484-495 行）、`_count_dimension_dsl`（约 497-528 行）；`_explicit_dimensions`（约 102 行）内 `for term, field in _DIMENSION_TERMS.items():` 改为 `for term, field in DIMENSION_TERMS.items():`。

3c. `_scalar_dsl` 整体替换为：

```python
# 标量兜底的聚合方式映射（金额 sum / 订单数 count / 买家数 count_distinct）。
_SCALAR_FIELD_AGG: dict[str, str] = {
    "order_amount": "sum",
    "discount_amount": "sum",
    "refund_amount": "sum",
    "order_id": "count",
    "user_id": "count_distinct",
}
_SCALAR_ANCHOR_TABLE: dict[str, str] = {
    "order_amount": "fact_orders",
    "discount_amount": "fact_orders",
    "order_id": "fact_orders",
    "user_id": "fact_orders",
    "refund_amount": "fact_refunds",
}


def _scalar_dsl(anchors: tuple[str, ...], query: str) -> dict[str, Any] | None:
    """标量指标问题的确定性 DSL（按锚点取数，多锚多度量）。

    回归锚点（十八期）：此前固定 sum(order_amount)，"问退款、答 GMV"。
    - 聚合方式按字段语义映射；别名带聚合语义（sum 原名、计数带 _count 后缀，
      与 _fmt_scalar_answer 的计数列判定联动）；
    - 过滤口径随锚点主表适配：fact_orders 锚点带 pay_status=SUCCESS；
      纯 refund_amount 锚点不带（该过滤对退款语义无意义）；
    - 跨表混合锚（GMV+退款金额）单查询无法同口径构造 => None（兜底拒答，
      LLM 在场时由 Planner 规划）；
    - 时间窗口：用户显式时间优先（parse_explicit_time_window），
      缺省 2024-05 锚（报告侧说明缺省口径）。
    """
    if not anchors or any(a not in _SCALAR_FIELD_AGG for a in anchors):
        return None
    tables = {_SCALAR_ANCHOR_TABLE[a] for a in anchors}
    if len(tables) > 1:
        return None
    explicit = parse_explicit_time_window(query)
    window = (
        {"start": explicit[0], "end": explicit[1]}
        if explicit
        else {"start": "2024-05-01", "end": "2024-05-15"}
    )
    filters = (
        [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}]
        if tables == {"fact_orders"}
        else []
    )
    return {
        "metrics": [
            {
                "kind": "aggregate",
                "field": field,
                "agg": _SCALAR_FIELD_AGG[field],
                "alias": field if _SCALAR_FIELD_AGG[field] == "sum" else f"{field}_count",
            }
            for field in anchors
        ],
        "filters": filters,
        "time_filter": {"range_type": "absolute", "absolute": dict(window)},
    }
```

（签名两参：`anchors` + `query`——时间解析需要原文；上方测试已同步传 query。）

3d. `_heuristic_plan` 整体替换为（诊断分支逻辑不变，入口改走 classify）：

```python
def _heuristic_plan(query: str) -> list[PlanStep] | None:
    """确定性兜底规划（十八期准入制）：意图可确定才产计划，否则 None 诚实拒答。

    - DIAGNOSTIC => 诊断式 DAG（总量对比 -> 因子分解 -> 维度下钻 -> 综合）；
    - CARDINALITY => count_distinct 直答（DSL 由 intent 模块构造）；
    - METRIC_SCALAR => 按锚点直答（跨表混合锚等不可构造时 None）；
    - UNKNOWN => None（planner 置 blocked_reason，critic/synthesize 短路拒答）。
    """
    profile = classify_intent(query)
    if profile.intent == IntentType.DIAGNOSTIC:
        return _diagnostic_plan_steps(query)
    if profile.intent == IntentType.CARDINALITY:
        dsl = count_dimension_dsl(query)
        if dsl is None:
            return None
        field = dsl["metrics"][0]["field"]
        return [
            PlanStep(
                id="s1",
                goal=f"统计{_dimension_label(field)}的去重取值个数",
                kind="query",
                dsl=dsl,
            ),
            PlanStep(id="s2", goal="直接报告计数结果", kind="synthesize", depends_on=["s1"]),
        ]
    if profile.intent == IntentType.METRIC_SCALAR:
        dsl = _scalar_dsl(profile.anchor_fields, query)
        if dsl is None:
            return None
        return [
            PlanStep(id="s1", goal="按锚定指标取数作答", kind="query", dsl=dsl),
            PlanStep(id="s2", goal="综合查询结果作答", kind="synthesize", depends_on=["s1"]),
        ]
    return None


def _diagnostic_plan_steps(query: str) -> list[PlanStep]:
    """诊断 DAG（原 _heuristic_plan 诊断分支原样搬移，逻辑不变）。"""
    explicit = _explicit_dimensions(query)
    if explicit:
        dim_goal = f"按用户指定维度（{'/'.join(explicit)}）做信息增益下钻，输出归因矩阵"
    else:
        dim_goal = (
            f"在候选维度池（{'/'.join(_DIAGNOSTIC_DIM_POOL)}）内做信息增益下钻，"
            "择优定位主因维度，输出归因矩阵与入选依据"
        )
    return [
        PlanStep(
            id="s1",
            goal="取基线期与当前期的指标总量与驱动因子（订单量/买家数）明细",
            kind="query",
        ),
        PlanStep(
            id="s2",
            goal="沙箱内做乘法因子分解（GMV = 买家数 × 人均订单数 × 客单价），定位量跌还是价跌",
            kind="analyze",
            depends_on=["s1"],
        ),
        PlanStep(
            id="s3",
            goal=dim_goal,
            kind="analyze",
            depends_on=["s1"],
        ),
        PlanStep(
            id="s4",
            goal="汇总根因结论、量化贡献并给出建议",
            kind="synthesize",
            depends_on=["s2", "s3"],
        ),
    ]
```

3e. `planner_node`（约 345 行）中兜底分支替换：

```python
    if steps is None:
        steps = _heuristic_plan(state.user_query)
        planner_used = "heuristic"
    else:
        planner_used = "llm"
    if steps is None:
        # 兜底准入拒绝：意图不可确定 => 诚实拒答。路由链：plan_steps 为空 =>
        # route_from_plan 进 critique => critic 对 blocked_reason 短路 => 拒答报告。
        # 严禁在此猜测口径产计划（回归锚点：曾固定 sum(gmv) 答非所问）。
        return state.apply(
            blocked_reason="无法从语义目录识别问题意图（未命中任何指标/维度锚点）",
            answered_by="blocked",
            plan_steps=[],
            scratchpad=[*state.scratchpad, "[planner] blocked: intent unknown"],
        )
```

其后的 `if state.plan_edit_instruction:` 清除逻辑与 `events.emit_plan(steps)` / `return state.apply(plan_steps=steps, phase="query", ...)` 保持不变，但 apply 里加 `answered_by=planner_used`：

```python
    events.emit_plan(steps)
    return state.apply(
        plan_steps=steps, phase="query", scratchpad=[f"[planner] {planner_used}"], answered_by=planner_used
    )
```

3f. `_run_query_step`（约 666 行）内兜底 DSL 分支（`step.dsl is None` 的 else）更新调用签名：

```python
        else:
            profile = classify_intent(state.user_query)
            if profile.intent == IntentType.DIAGNOSTIC:
                baseline_dsl, current_dsl = _diagnostic_dsl_pair(state.user_query)
                dsl_variants = [baseline_dsl, current_dsl]
            elif profile.intent == IntentType.METRIC_SCALAR:
                scalar = _scalar_dsl(profile.anchor_fields, state.user_query)
                dsl_variants = [scalar] if scalar is not None else []
            else:
                dsl_variants = []
            if not dsl_variants:
                # 兜底无法构造合规 DSL：不猜口径，置拒答原因跳过执行
                updated = state.apply(
                    blocked_reason="无法为该问题构造确定性查询（锚点不可用或跨表混合）",
                    answered_by="blocked",
                )
                return (
                    updated,
                    ToolRecord(
                        step_id=step.id,
                        tool="execute_dsl_query",
                        ok=False,
                        summary=f"[{step.id}] 兜底准入拒绝：意图锚点不可构造",
                    ),
                )
```

（该分支原 `diagnostic = any(...)` 触发词判断与 `_scalar_dsl(state.user_query)` 旧调用删除；`_heuristic_plan` 的 CARDINALITY 分支已内嵌 DSL，`step.dsl is not None` 路径不受影响。）

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py tests/test_intent.py -q`
Expected: `test_run_agent_unknown_blocks_honestly` 可能仍 FAIL（critic 短路未实现——Task 5 完成）；其余 PASS。记录该预期失败。

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): 兜底准入制——scalar锚点化、heuristic意图准入、UNKNOWN诚实拒答路径"
```

---

### Task 4: clarify_node 退役离线字符规则（单一出口）

**Files:**
- Modify: `core/orchestrator/nodes.py`（`clarify_node` 约 191-231 行）
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: 无新依赖。
- Produces: `clarify_node` 契约简化——`human_reply` 合并与 `history_digest` 放行不变，**其余一律放行 plan**；澄清唯一来源为 Planner LLM 契约。

- [ ] **Step 1: 写失败测试**

改写 `test_clarify_node_passes_followup_with_history`（约 340 行）为：

```python
def test_clarify_node_single_exit(monkeypatch):
    """离线字符规则退役：一律放行（能答兜底答、不能答兜底拒答），澄清归 Planner LLM。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.nodes import clarify_node
    from core.orchestrator.state import AgentState

    followup = AgentState(user_query="那华南呢", history_digest="用户: 2024年5月北京GMV")
    assert clarify_node(followup).phase == "plan"

    # LLM 在场：澄清判定在 Planner（clarification 契约）
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    assert clarify_node(AgentState(user_query="有多少个省份")).phase == "plan"

    # 离线：单一出口放行——兜底准入决定答或拒，不再固定文案澄清
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: None)
    assert clarify_node(AgentState(user_query="有多少个省份")).phase == "plan"
    assert clarify_node(AgentState(user_query="那华南呢")).phase == "plan"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py::test_clarify_node_single_exit -v`
Expected: FAIL（离线分支仍返回 clarify）

- [ ] **Step 3: 最小实现**

`clarify_node` 整体替换为：

```python
def clarify_node(state: AgentState) -> AgentState:
    """歧义检测：HITL 门（需求 §2.A ClarificationNode）。

    十八期单一出口（字符规则退役）：clarify 仅保留结构职责——human_reply
    合并、追问轮放行，其余一律放行规划。是否需要人工澄清由 LLM 在场的
    Planner 裁决（clarification 契约）；离线由兜底准入裁决（能答则答、
    不能答经 blocked_reason 诚实拒答）。旧"过短/无指标词即澄清"规则会把
    "有多少个省份"这类事实型问题误拦（回归锚点），且固定文案无法定向。
    """
    query = state.user_query.strip()
    if state.human_reply:
        merged = f"{query}（用户补充：{state.human_reply.strip()}）"
        return state.apply(user_query=merged, human_reply=None, phase="plan", clarification=None)

    if state.history_digest:
        # 追问轮：有会话历史兜底语境，直接放行给 Planner 消解省略指代
        return state.apply(phase="plan")
    return state.apply(phase="plan")
```

同步更新 `test_run_agent_factoid_bypasses_clarify_gate`（约 100 行）中的注释措辞（断言不变——该用例继续通过）。

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py -q`
Expected: 除 `test_run_agent_unknown_blocks_honestly`（Task 5 后转绿）外全 PASS

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_orchestrator.py
git commit -m "refactor(orchestrator): clarify退役离线字符规则——单一出口，澄清归Planner契约"
```

---

### Task 5: blocked 短路生命周期 + 诚实拒答报告

**Files:**
- Modify: `core/orchestrator/nodes.py`（`critic_node` 约 1515 行、`synthesize_node` 约 1998 行、`_cannot_answer_report` 新增于 `_no_data_report` 约 1960 行之后）
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Task 1 `blocked_reason`；Task 2 `capability_catalog_lines`。
- Produces: `_cannot_answer_report(state) -> str`；critic/synthesize 对 `blocked_reason` 的短路语义（**短路路径零 LLM 调用**——Review Focus 实现约束 3）。

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_run_agent_blocked_report_skips_llm_synthesis(tmp_path, monkeypatch):
    """拒答报告必须纯确定性构造：综合层 LLM 被短路（零调用实锤）。"""
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: None)
    llm_report_calls: list[str] = []

    original_chat = nodes._synthesize_with_llm

    def _spy(state, material):
        llm_report_calls.append("called")
        return original_chat(state, material)

    monkeypatch.setattr(nodes, "_synthesize_with_llm", _spy)
    trace = run_agent("帮我看看最近情况", session_id="blockedshort")
    assert trace.phase == "done"
    assert "无法作答" in trace.report
    assert llm_report_calls == []  # 短路实锤：综合层未被触碰
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py::test_run_agent_blocked_report_skips_llm_synthesis tests/test_orchestrator.py::test_run_agent_unknown_blocks_honestly -v`
Expected: FAIL（critic 走"未获得任何数据集→重规划"空转后降级，报告不含"无法作答"）

- [ ] **Step 3: 最小实现**

3a. `critic_node` 开头（`has_summary = ...` 之前）插入：

```python
    # 诚实拒答短路（十八期，先于一切重规划判定）：意图不可确定/意图-DSL
    # 错位时重规划取不回"理解力"，直接转综合输出拒答说明，严禁空转烧额度
    if state.blocked_reason:
        events.emit_reflection(
            state.blocked_reason,
            "proceed",
            "转入综合节点输出诚实拒答说明",
        )
        return state.apply(phase="synthesize")
```

3b. `_no_data_report` 之后新增：

```python
def _cannot_answer_report(state: AgentState) -> str:
    """意图不可确定的诚实拒答报告（十八期）。

    与 _no_data_report 同哲学：严禁让 LLM 在无理解依据时编造答案。
    说明原因 + 能力清单引导 + 可行动建议；纯确定性字符串构造，零 LLM 调用。
    """
    from core.orchestrator.intent import capability_catalog_lines

    lines: list[str] = [f"## 数据说明：{state.user_query}", ""]
    lines.append(f"**本次无法作答：{state.blocked_reason}。**")
    lines.append(
        "系统仅在能够确定查询口径时作答——宁可拒答，也不猜测口径给出可能错误的结果。"
    )
    lines.append("")
    lines.append("**当前支持查询的能力清单：**")
    lines.extend(capability_catalog_lines())
    lines.append("")
    lines.append(
        "建议：请调整问法（明确指标或维度），或配置 LLM 模型后重试以获得完整语义理解。"
    )
    return "\n".join(lines)
```

3c. `synthesize_node` 开头（R3 降级检查之前）插入：

```python
    # 诚实拒答（十八期）：blocked_reason 优先于一切——直接确定性输出拒答
    # 报告，跳过 LLM 综合层（零 token 消耗、零虚构风险）
    if state.blocked_reason:
        report = _cannot_answer_report(state)
        events.emit_event(
            events.EVENT_ARTIFACT_EMIT,
            {"artifact": {"type": "markdown_report", "title": "数据说明", "content": report}},
        )
        return state.apply(report=report, phase="done")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py tests/test_intent.py -q`
Expected: PASS（`test_run_agent_unknown_blocks_honestly` 与短路用例转绿）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): blocked_reason短路生命周期+诚实拒答报告（能力清单引导，零LLM调用）"
```

---

### Task 6: L3 意图-DSL 错位拦截

**Files:**
- Modify: `core/orchestrator/nodes.py`（`_intent_dsl_mismatch` 新增于 `_scalar_dsl` 之后；`_run_query_step` DSL 循环内约 700-730 行区域；`dsl_query_node` 约 806 行跳过条件）
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Task 2 `classify_intent`（确定性判定，不信任 LLM 回传）。
- Produces: `_intent_dsl_mismatch(query: str, dsl_payload: dict) -> str | None`。

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_intent_dsl_guard_unit():
    """L3 守卫：基数+金额聚合拦截；合法 WHERE 不受限（Review Focus 2）。"""
    from core.orchestrator.nodes import _intent_dsl_mismatch

    bad = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
        "filters": [],
    }
    assert _intent_dsl_mismatch("有多少个省份", bad) is not None

    legal = {
        "metrics": [
            {"kind": "aggregate", "field": "province", "agg": "count_distinct", "alias": "province_count"}
        ],
        "filters": [{"field": "refund_amount", "operator": "gt", "value": 0}],
    }
    assert _intent_dsl_mismatch("有退款的省份有多少个", legal) is None

    metric_bad = {
        "metrics": [{"kind": "aggregate", "field": "refund_amount", "agg": "sum", "alias": "refund_amount"}],
        "filters": [],
    }
    assert _intent_dsl_mismatch("5月订单量是多少", metric_bad) is not None
    assert _intent_dsl_mismatch("帮我看看最近情况", bad) is None  # UNKNOWN 不启用守卫


def test_run_agent_guard_blocks_llm_misaligned_dsl(tmp_path, monkeypatch):
    """LLM 规划出'基数意图+金额查询'的错位 DSL => 拦截不执行，拒答报告。"""
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(
        nodes,
        "_llm_json",
        lambda llm, system, user: {
            "clarification": None,
            "steps": [
                {"id": "s1", "goal": "取GMV", "kind": "query", "depends_on": [],
                 "dsl": {"metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
                         "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
                         "time_filter": {"range_type": "absolute", "absolute": {"start": "2024-05-01", "end": "2024-05-15"}}}},
                {"id": "s2", "goal": "综合作答", "kind": "synthesize", "depends_on": ["s1"], "dsl": None, "code": None},
            ],
        },
    )
    trace = run_agent("有多少个省份", session_id="guardq")
    assert trace.phase == "done"
    assert "无法作答" in trace.report
    assert "万元" not in trace.report
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py::test_intent_dsl_guard_unit tests/test_orchestrator.py::test_run_agent_guard_blocks_llm_misaligned_dsl -v`
Expected: FAIL（`_intent_dsl_mismatch` 不存在；e2e 报告含万元 GMV）

- [ ] **Step 3: 最小实现**

3a. `_scalar_dsl` 之后新增：

```python
def _intent_dsl_mismatch(query: str, dsl_payload: dict[str, Any]) -> str | None:
    """L3 意图-DSL 一致性守卫（十八期）。

    判定依据 = intent 模块对 user_query 的确定性 L1 硬匹配（不信任 LLM
    回传意图）；confidence != hard 时不启用（无判定依据，交由既有契约
    校验与反思器护栏）。CARDINALITY 只限制聚合与投影目标（metrics 必须
    且只能是锚定维度上的单一 count_distinct），过滤条件不受限制——
    "有退款的省份有多少个"的 refund_amount>0 WHERE 合法。
    """
    profile = classify_intent(query)
    if profile.confidence != "hard":
        return None
    metrics = [m for m in (dsl_payload.get("metrics") or []) if isinstance(m, dict)]
    if profile.intent == IntentType.CARDINALITY:
        if (
            len(metrics) == 1
            and metrics[0].get("agg") == "count_distinct"
            and metrics[0].get("field") in profile.anchor_fields
        ):
            return None
        return "基数类问题的查询目标必须是锚定维度上的 count_distinct 聚合"
    if profile.intent == IntentType.METRIC_SCALAR:
        outside = [str(m.get("field")) for m in metrics if m.get("field") not in profile.anchor_fields]
        if outside:
            return f"指标问题锚定 {list(profile.anchor_fields)}，查询出现锚外聚合字段 {outside}"
        return None
    return None
```

3b. `_run_query_step` 的 `for i, dsl_payload in enumerate(dsl_variants):` 循环体开头（`name = ...` 之前）插入：

```python
        # L3 意图-DSL 错位守卫（十八期）：拦截即不执行，置拒答原因
        mismatch = _intent_dsl_mismatch(state.user_query, dsl_payload)
        if mismatch:
            state = state.apply(blocked_reason=mismatch, answered_by="blocked")
            events.emit_tool_end(
                "futurebi_dsl_query",
                step.id,
                ok=False,
                duration_ms=0.0,
                error=mismatch,
            )
            continue
```

3c. `dsl_query_node` 循环跳过条件（约 810 行 `if updated.no_data_reason:`）扩展：

```python
        if updated.no_data_reason or updated.blocked_reason:
```

（注释同步补充"blocked_reason 同款短路：意图错位后剩余取数无意义"）

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): L3意图-DSL错位守卫——确定性判定、拦截不执行、WHERE不受限"
```

---

### Task 7: 降级可见化（报告顶部标注）

**Files:**
- Modify: `core/orchestrator/nodes.py`（`synthesize_node` LLM 报告分支与确定性兜底分支，约 2065-2110 行区域）
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Task 1 `answered_by`（"heuristic"）。
- Produces: 兜底接管时报告顶部出现显著降级提示；LLM 规划成功时无标注。

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_heuristic_answer_banner_visible(tmp_path, monkeypatch):
    """兜底接管时报告顶部必须显著标注（降级不可静默）。"""
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: None)
    trace = run_agent("有多少个省份", session_id="bannerq")
    assert trace.phase == "done"
    assert "查询答案：8" in trace.report
    assert "离线兜底引擎" in trace.report
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py::test_heuristic_answer_banner_visible -v`
Expected: FAIL（无标注）

- [ ] **Step 3: 最小实现**

`synthesize_node` 中新增辅助函数（置于 `_cannot_answer_report` 之后）：

```python
def _degradation_banner(state: AgentState) -> str:
    """兜底接管的降级标注（十八期：降级不可静默）。"""
    if state.answered_by != "heuristic":
        return ""
    return (
        "> ⚠️ **本次回答由离线兜底引擎生成**（LLM 规划不可用），"
        "口径为确定性规则匹配结果，仅供参考。\n\n"
    )
```

在 `synthesize_node` 的两个出口接入（`llm_report` 非空出口与确定性兜底出口的 `report` 赋值处）：

```python
    if llm_report:
        report = _degradation_banner(state) + llm_report
```

```python
    # ---- 确定性分析师兜底（无 LLM / LLM 输出反契约）---- #
    lines: list[str] = []
    banner = _degradation_banner(state)
    if banner:
        lines.append(banner.rstrip("\n"))
        lines.append("")
    lines.append(f"## 分析报告：{state.user_query}")
    lines.append("")
```

（原 `lines: list[str] = [f"## 分析报告：{state.user_query}", ""]` 两行被上述替换。）

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py tests/test_synthesizer_rebuild.py -q`
Expected: PASS（离线 e2e 报告出现标注；LLM 报告路径单测无标注）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): 兜底接管降级可见化——报告顶部显著标注，杜绝静默降级"
```

---

### Task 8: grounding.py — 报告数值溯源校验（最小版）

**Files:**
- Create: `core/orchestrator/grounding.py`
- Modify: `core/orchestrator/nodes.py`（`_analysis_material` 附近新增 `_allowed_values`；`synthesize_node` LLM 报告出口接入）
- Test: `tests/test_grounding.py`

**Interfaces:**
- Consumes: `AgentState`（datasets/artifacts 只读）。
- Produces: `grounding_review(report: str, allowed: set[float]) -> list[str]`（不可溯源 token 列表）；`collect_allowed_values(state: AgentState) -> set[float]`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_grounding.py`：

```python
"""Grounding 数值溯源校验单测：量纲归一化容差、不可溯源检测（Review Focus 5）。"""

from core.orchestrator.grounding import collect_allowed_values, grounding_review
from core.orchestrator.state import AgentState


def test_report_values_within_tolerance_are_grounded():
    """千分位/万元/百分比/舍入均应溯源命中，不误报。"""
    allowed = {1156943.73, 0.078, 8.0}
    report = (
        "GMV 为 115.69 万元（1,156,943.73 元），环比 -7.8%；"
        "共覆盖 8 个省份，另一指标为 1,156,900。"
    )
    assert grounding_review(report, allowed) == []


def test_fabricated_value_is_flagged():
    """数据中完全不存在且无法折算的数值 => 不可溯源。"""
    allowed = {1156943.73}
    report = "GMV 为 115.69 万元，另据测算转化率高达 42.5%。"
    flagged = grounding_review(report, allowed)
    assert any("42.5" in t for t in flagged)
    assert not any("115.69" in t for t in flagged)


def test_collect_allowed_values_from_state():
    state = AgentState(user_query="x")
    state.datasets["s1"] = {"rows": 1, "columns": ["gmv"], "path": "s1.parquet"}
    # collect_allowed_values 对无 workspace 文件时容忍降级（返回空集不抛错）
    values = collect_allowed_values(state, workspace=None)
    assert isinstance(values, set)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_grounding.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: 最小实现**

创建 `core/orchestrator/grounding.py`：

```python
"""报告数值溯源校验（十八期最小版：只标注、不拦截）。

LLM 综合报告中的数值应能溯源到真实查询数据。归一化容差覆盖常见量纲：
千分位逗号、万/亿折算、百分比、四舍五入（<=0.5% 相对 + 0.01 绝对）——
合法的格式化呈现不得触发溯源报警（Review Focus 5）。拦截重试闭环属
二期（防死循环/超时）。
"""

from __future__ import annotations

import json
import re
from typing import Any

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_UNIT_PATTERN = re.compile(r"\s*(亿元|万元|万|亿|%)")


def _candidates(number: float, unit: str) -> list[float]:
    """数值在不同量纲下的候选原值（含绝对值形态——"下降7.8%"溯源到 0.078）。"""
    cands = [number]
    if unit == "万" or unit == "万元":
        cands.append(number * 10000)
    elif unit == "亿" or unit == "亿元":
        cands.append(number * 1e8)
    elif unit == "%":
        cands.extend([number / 100, number])
    return cands + [abs(c) for c in cands]


def _is_grounded(allowed: set[float], value: float) -> bool:
    return any(abs(value - a) <= max(abs(a) * 0.005, 0.01) for a in allowed)


def grounding_review(report: str, allowed: set[float]) -> list[str]:
    """返回报告中不可溯源的数值 token（原样文本，供报告引用）。"""
    flagged: list[str] = []
    for match in _NUMBER_RE.finditer(report):
        token = match.group(0)
        unit_match = _UNIT_PATTERN.match(report[match.end(): match.end() + 4])
        unit = unit_match.group(1) if unit_match else ""
        try:
            number = float(token.replace(",", ""))
        except ValueError:
            continue
        if not any(_is_grounded(allowed, c) for c in _candidates(number, unit)):
            flagged.append(f"{token}{unit}".strip())
    return flagged


def _collect_numbers(payload: Any, out: set[float]) -> None:
    if isinstance(payload, bool):
        return
    if isinstance(payload, (int, float)):
        out.add(float(payload))
    elif isinstance(payload, dict):
        for v in payload.values():
            _collect_numbers(v, out)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            _collect_numbers(v, out)


def collect_allowed_values(state: Any, workspace: Any = None) -> set[float]:
    """从数据集预览与 summary 产物收集可溯源数值全集（读取失败容忍降级）。"""
    allowed: set[float] = set()
    for artifact in getattr(state, "artifacts", []) or []:
        _collect_numbers(artifact.payload, allowed)
    if workspace is not None:
        try:
            from core.orchestrator.nodes import _preview_rows

            for ref in (getattr(state, "datasets", {}) or {}).values():
                rows = _preview_rows(str(workspace / "inputs" / ref.get("path", "")))
                for row in rows:
                    for cell in row:
                        if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                            allowed.add(float(cell))
        except Exception:
            pass  # 预览不可用时容忍降级：校验退化为 summary 产物范围
    return allowed
```

3b. `nodes.py` 的 `synthesize_node` LLM 报告出口接入：

```python
    if llm_report:
        from core.orchestrator.grounding import collect_allowed_values, grounding_review

        allowed = collect_allowed_values(state, workspace)
        ungrounded = grounding_review(llm_report, allowed)
        if len(ungrounded) > 3:
            logger.warning(
                "LLM 报告存在不可溯源数值，追加溯源提示",
                extra={"error": str(ungrounded[:10])[:400]},
            )
            llm_report = (
                llm_report
                + "\n\n---\n**数据溯源提示**：以下数值未能对应到本次真实查询结果，"
                "请谨慎采信："
                + "、".join(ungrounded[:10])
            )
        report = _degradation_banner(state) + llm_report
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_grounding.py tests/test_orchestrator.py -q`
Expected: PASS（注意 `_collect_numbers` 对 `"8 个省份" 的 8`：若 allowed 无 8.0 会误标——e2e 场景 count 类数值必然在数据集预览内，成立）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/grounding.py tests/test_grounding.py core/orchestrator/nodes.py
git commit -m "feat(orchestrator): Grounding最小版——量纲归一化容差溯源校验，超阈值追加提示（只标注不拦截）"
```

---

### Task 9: Planner intent 回传契约（prompts + planner_node）

**Files:**
- Modify: `core/orchestrator/prompts.py`（`PLANNER_SYSTEM` 输出契约块，约 19-52 行）
- Modify: `core/orchestrator/nodes.py`（`planner_node` payload 消费区，约 345-392 行）
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Task 1 `intent_type`/`intent_anchors`。
- Produces: LLM 计划契约可选 `intent` 字段（仅诊断可观测；L3 守卫不依赖）。

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_planner_llm_intent_echoed_to_state(monkeypatch):
    """LLM 回传 intent => 落 state（诊断可观测）；缺失时不阻塞规划。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(
        nodes,
        "_llm_json",
        lambda llm, system, user: {
            "intent": {"type": "metric_scalar", "anchors": ["order_amount"]},
            "clarification": None,
            "steps": [
                {"id": "s1", "goal": "取GMV", "kind": "query", "depends_on": [],
                 "dsl": {"metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
                         "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
                         "time_filter": {"range_type": "absolute", "absolute": {"start": "2024-05-01", "end": "2024-05-15"}}}},
                {"id": "s2", "goal": "综合作答", "kind": "synthesize", "depends_on": ["s1"], "dsl": None, "code": None},
            ],
        },
    )
    state = nodes.planner_node(AgentState(user_query="5月GMV是多少"))
    assert state.intent_type == "metric_scalar"
    assert state.intent_anchors == ["order_amount"]
    assert state.phase == "query"

    # 无 intent 字段：宽容不阻塞
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: {"clarification": None, "steps": []})
    state2 = nodes.planner_node(AgentState(user_query="5月GMV是多少"))
    assert state2.intent_type is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py::test_planner_llm_intent_echoed_to_state -v`
Expected: FAIL（intent_type 未落位）

- [ ] **Step 3: 最小实现**

3a. `PLANNER_SYSTEM` 输出契约块中 `"clarification"` 行之后插入：

```
  "intent": {"type": "diagnostic|cardinality|metric_scalar|unknown", "anchors": ["命中的语义字段"]},
```

并在 `# 澄清判定纪律` 小节之后新增：

```
# 意图回传（可选，尽力而为）
- intent 字段是你对问题意图的判断回传，仅用于系统诊断观测，不影响计划合法性；
- type 取值：diagnostic（归因诊断）/ cardinality（维度基数探查，如"多少个省份"）/
  metric_scalar（指标取值，如"5月GMV"）/ unknown（无法归类）；
- anchors 填语义目录中实际命中的字段名（如 ["order_amount"]）。
```

3b. `planner_node` 中 `if payload:` 块内、`steps = _plan_from_llm(payload)` 之后追加：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py tests/test_langgraph_parity.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/prompts.py core/orchestrator/nodes.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): Planner意图回传契约——intent可选字段落state（仅诊断观测）"
```

---

### Task 10: HITL e2e 改写 + intent 评测集 + 全量回归

**Files:**
- Modify: `tests/test_orchestrator.py`（`test_run_agent_hitl_flow` 约 68-95 行）
- Create: `eval/intent_golden.json`
- Create: `eval/intent_eval.py`

**Interfaces:**
- Consumes: Task 3-7 全部产出（端到端验收）。
- Produces: `python -m eval.intent_eval` 可重复执行的"答非所问率"评测（离线确定性）。

- [ ] **Step 1: 改写 HITL e2e**

`test_run_agent_hitl_flow` 整体替换为（澄清来源改为 Planner LLM 契约，恢复链路契约不变）：

```python
def test_run_agent_hitl_flow(tmp_path, monkeypatch):
    """LLM 规划判定歧义 -> 澄清中断 -> 用户答复 -> 完成全流程。

    十八期：离线字符规则退役后，clarify 的唯一来源是 Planner clarification
    契约；本用例以 mock LLM 验证中断-恢复两段式契约不回归。
    """
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    plan_payload = {
        "clarification": None,
        "steps": [
            {"id": "s1", "goal": "取GMV总量", "kind": "query", "depends_on": [],
             "dsl": {"metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
                     "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
                     "time_filter": {"range_type": "absolute", "absolute": {"start": "2024-05-01", "end": "2024-05-15"}}}},
            {"id": "s2", "goal": "综合作答", "kind": "synthesize", "depends_on": ["s1"], "dsl": None, "code": None},
        ],
    }
    responses = iter([{"clarification": "你关注哪个时间段的 GMV？", "steps": []}, plan_payload])
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: next(responses))

    paused = run_agent("GMV呢？", session_id="hitl")
    assert paused.phase == "clarify" and paused.clarification

    final = run_agent(
        "GMV呢？",
        session_id="hitl",
        resume_state=paused.apply(human_reply="2024 年 5 月"),
    )
    assert final.phase == "done"
    assert any(s["ok"] for s in final.steps)
```

- [ ] **Step 2: 创建评测集与执行器**

创建 `eval/intent_golden.json`：

```json
[
  {"question": "有多少个省份", "expect": "count_answer", "expected_value": 8},
  {"question": "2024年5月订单量是多少", "expect": "count_answer", "expected_value": null},
  {"question": "2024年5月GMV是多少", "expect": "metric_wan_answer"},
  {"question": "2024年5月GMV和订单量各多少", "expect": "multi_metric"},
  {"question": "退款率是多少", "expect": "refusal"},
  {"question": "流量表现怎么样", "expect": "refusal"},
  {"question": "帮我看看最近情况", "expect": "refusal"}
]
```

创建 `eval/intent_eval.py`：

```python
"""意图路由评测（十八期）：离线确定性运行，监控"答非所问率"。

对每条 golden 用例断言编排终态行为：
- count_answer  => 报告含"查询答案："且不含金额化（计数直答，杜绝 GMV 万能答案）；
- metric_wan_answer => 报告含"查询答案："且金额万元化（金额类锚点直答）；
- multi_metric  => 单数据集多列呈现：计数列名（order_id_count）进报告表格
  列头，无"无法作答"（多锚不丢锚，Review Focus 4）；
- refusal => 报告含"无法作答"（诚实拒答 + 能力清单）。

用法：
    python -m mock.init_duckdb   # 若数仓文件不存在
    python -m eval.intent_eval
"""

from __future__ import annotations

import json
from pathlib import Path

from config import settings
from core.orchestrator.agent import run_agent

GOLDEN_PATH = Path(__file__).resolve().parent / "intent_golden.json"


def main() -> int:
    if not settings.DB_PATH.exists():
        raise SystemExit(f"未找到数仓文件 {settings.DB_PATH}，请先执行: python -m mock.init_duckdb")
    cases = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []
    for case in cases:
        question = case["question"]
        expect = case["expect"]
        trace = run_agent(question, session_id="intent-eval")
        report = trace.report or ""
        ok = False
        if expect == "count_answer":
            expected_value = case.get("expected_value")
            ok = (
                trace.phase == "done"
                and "查询答案：" in report
                and "万元" not in report
                and (expected_value is None or str(expected_value) in report)
            )
        elif expect == "metric_wan_answer":
            ok = trace.phase == "done" and "查询答案：" in report and "万元" in report
        elif expect == "multi_metric":
            ok = (
                trace.phase == "done"
                and "order_id_count" in report
                and "无法作答" not in report
            )
        elif expect == "refusal":
            ok = trace.phase == "done" and "无法作答" in report and "万元" not in report
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {question} (expect={expect})")
        if not ok:
            failures.append(question)
            print(f"       report head: {report[:200]!r}")
    print(f"\n{len(cases) - len(failures)}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: 跑评测与全量回归**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m eval.intent_eval`
Expected: `7/7 passed`

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m black --check . && C:/Users/cwt15/.conda/envs/futurebi/python.exe -m ruff check . && C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest -q`
Expected: 全绿（黑名单文件若 black 报格式差异，运行 `black <file>` 修复后重跑）

- [ ] **Step 4: Commit**

```bash
git add tests/test_orchestrator.py eval/intent_golden.json eval/intent_eval.py
git commit -m "test(eval): HITL e2e改写为Planner契约链路+意图路由golden评测（答非所问率防回归）"
```

---

## 红线自查记录（编写者已执行，含修正）

1. **规格覆盖**：§3.1→Task 2/9、§3.2→Task 3/4、§3.3→Task 6、§3.4→Task 5、§3.5→Task 7/8、§3.6→Task 10、数据契约→Task 1，无缺口；
2. **占位符**：全文无 TBD/TODO/"适当处理"；初稿中 `_current_query()` 伪引用已修正为 `_scalar_dsl(anchors, query)` 两参签名（时间解析需原文）；
3. **类型一致性**：`_heuristic_plan -> list[PlanStep] | None`、`_scalar_dsl(anchors, query) -> dict | None`、`classify_intent -> IntentProfile`、`count_dimension_dsl(query) -> dict | None`、`grounding_review(report, allowed) -> list[str]`、`collect_allowed_values(state, workspace) -> set[float]` 各任务引用一致；初稿 Task 3 测试漏传 query、Task 8 负值百分比溯源缺口（`_candidates` 补绝对值候选）、pandas 残留——均已修正；
4. **Review Focus 落实**：5 条全部落到 owning task 测试——1（退款率拒答）→Task 2/3/10；2（过滤语境基数问句）→Task 2（意图判定 + 过滤线索拒答 DSL）与 Task 6（守卫对 LLM 的 WHERE 放行）；3（跨表混合锚拒答）→Task 3；4（同表多锚多列呈现）→Task 3（DSL 结构）+ Task 10（golden multi_metric）；5（编造数值溯源提示）→Task 8。

## 移交执行

- 推荐方式：**本会话逐任务**（任务间接口依赖强：Task 2 的签名被 3/5/6 逐字消费、Task 1 字段被全部任务消费，子代理切换的上下文重建成本高于收益；共 10 任务、每任务自带测试周期，出错代价由 TDD 与全量回归兜底）。
- 执行交 dev-executing-plans；测试纪律见 dev-tdd。
