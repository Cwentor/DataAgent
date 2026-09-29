# LLM 失联分层降级兜底 实施计划

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。

**Goal:** LLM 不可用时三级降级——强匹配模板（保留）→ 弱解析交互确认（新增：唯一候选走计划审批卡、多候选走选项式澄清）→ NOT_EXIST 拒答（保留并增强文案）；配套 429 退避重试、降级水印与事件埋点。

**Architecture:** 二次判定放在 planner 兜底分支内（`_heuristic_plan` 返回 None 后），`classify_intent`/意图评测零改动；弱解析复用 `_scalar_dsl` 的聚合映射与口径适配，DSL 组装产出 `[query, synthesize]` 两步计划（结构上禁 analyze）；双通道复用既有 plan_review interrupt 与 clarify 挂起（均在 `_plan_gate`），前端零改动；429 重试放 `providers.chat_text` 门面统一生效。

**Tech Stack:** Python 3.12 / pydantic v2（AgentState、QueryDSL 契约）/ LangGraph interrupt / pytest + monkeypatch。

**Spec:** `docs/superpowers/specs/2026-09-29-degraded-fallback-design.md`（执行者需同时读两者）。

## Global Constraints

- `classify_intent`/`IntentType`/`eval/intent_golden.json` 评测契约零改动（意图评测必须全绿）；
- 降级模式严禁猜测筛选条件：条件仅来自显式时间解析、用户确认内容、枚举值精确命中三类；
- 降级计划结构强制 `[query(带 DSL), synthesize]` 两步，严禁 analyze 步；
- 所有新增/修改 Python 代码的 docstring 与行内注释使用简体中文（专有名词保留英文）；
- `black --check .`、`ruff check .`、`python -m pytest -q` 提交前全绿（测试环境 conda futurebi）；
- 工作区 `web/static/js/stream-ui.js`、`web/static/style.css` 未提交改动属另一事项，**严禁 git add 这两个文件**；
- 工作分支：`feat/degraded-fallback`（已存在，勿新建）。

## Review Focus

1. 二轮澄清仍歧义 → 必须 NOT_EXIST 拒答，严禁澄清无限循环（Task 2+4 测试踩中）；
2. 枚举未命中的筛选取值（如数仓没有"西藏"）→ 留白澄清，严禁自动猜测条件（Task 3 测试踩中）；
3. 跨表混合锚（GMV+退款金额）→ NOT_EXIST，严禁硬造单查询 DSL（Task 3 测试踩中）；
4. 429 连续超限（重试耗尽）→ 正常进入降级分流，不无限重试不崩溃（Task 1 测试踩中）；
5. 纯指标锚 UNKNOWN（如"GMV 趋势"）→ 无维度时产出标量 DSL（等价 METRIC_SCALAR 直答口径，不视为猜条件）（Task 3 测试踩中）。

---

### Task 1: providers 网关 429 退避重试

**Files:**
- Modify: `providers/__init__.py:62-89`（`chat_text` 门面）
- Test: `tests/test_providers.py`（文件末尾追加）

**Interfaces:**
- Consumes: `RateLimitError`（providers 包已导出）、`settings.LLM_MAX_RETRIES`（死配置接线）。
- Produces: `chat_text` 对 `RateLimitError` 自动退避重试 `min(LLM_MAX_RETRIES, 2)` 次（短退避 1s/3s），其他异常不重试。Task 4-6 依赖规划层调用因此获得 429 容错。

- [ ] **Step 1: 写失败测试**

`tests/test_providers.py` 末尾追加（`json` 已 import；`settings` 按需 `from config import settings`）：

```python
# --------------------------------------------------------------------------- #
# 429 限流退避重试（2026-09 分层降级兜底：LLM_MAX_RETRIES 死配置接线）
# --------------------------------------------------------------------------- #
def test_chat_facade_retries_on_rate_limit(monkeypatch):
    """第一次 429、第二次成功 => 门面自动重试并返回结果。"""
    from config import settings
    from providers import RateLimitError, chat_text

    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr("providers.time.sleep", lambda _s: None)
    calls: list[int] = []

    class _Flaky:
        def chat_text(self, messages, *, model=None, json_mode=True, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise RateLimitError("配额超限或请求过于频繁（HTTP 429）")
            return "ok"

    assert chat_text(_Flaky(), [{"role": "user", "content": "x"}]) == "ok"
    assert len(calls) == 2


def test_chat_facade_rate_limit_exhausted_raises(monkeypatch):
    """重试耗尽仍 429 => 原样抛出（调用方走各自兜底）。"""
    from config import settings
    from providers import RateLimitError, chat_text

    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr("providers.time.sleep", lambda _s: None)
    calls: list[int] = []

    class _Always429:
        def chat_text(self, messages, *, model=None, json_mode=True, timeout=None):
            calls.append(1)
            raise RateLimitError("429")

    with pytest.raises(RateLimitError):
        chat_text(_Always429(), [{"role": "user", "content": "x"}])
    assert len(calls) == 2  # 首调 + 1 次重试


def test_chat_facade_no_retry_on_other_errors(monkeypatch):
    """非 429 异常（如 ProviderError）不重试，直接抛出。"""
    from config import settings
    from providers import ProviderError, chat_text

    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr("providers.time.sleep", lambda _s: None)
    calls: list[int] = []

    class _Broken:
        def chat_text(self, messages, *, model=None, json_mode=True, timeout=None):
            calls.append(1)
            raise ProviderError("网络请求失败")

    with pytest.raises(ProviderError):
        chat_text(_Broken(), [{"role": "user", "content": "x"}])
    assert len(calls) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_providers.py -k "facade" -v`
Expected: 前两个 FAIL（`RateLimitError` 直接抛出、无重试），第三个 PASS（现状本就不重试）

- [ ] **Step 3: 最小实现**

`providers/__init__.py`：确认模块头部已 `import time`（若无则补）；`RateLimitError` 已在本模块导入（`__all__` 已含，核对 import 语句）；将 `chat_text` 的分发体抽为模块级 `_chat_dispatch` 并在 `chat_text` 加重试循环：

```python
def _chat_dispatch(
    client: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None,
    json_mode: bool,
    timeout: int | None,
) -> str:
    """单次分发（不重试）：三形态透明分发的原有逻辑。"""
    if hasattr(client, "chat_text"):
        # 适配器（BaseAdapter）与分发代理（DispatchingAdapter）均为 chat_text 形态；
        # 鸭子类型分发使测试桩等纯 chat_text 形态同样获得 timeout 透传
        return client.chat_text(messages, model=model, json_mode=json_mode, timeout=timeout)
    return client.chat(messages)


def chat_text(
    client: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    json_mode: bool = True,
    timeout: int | None = None,
) -> str:
    """统一对话入口，三形态透明分发（429 限流自动退避重试）：

    - Model Provider 适配器（``BaseAdapter``）走 UnifiedChatRequest 统一接口；
    - 请求感知分发代理（``DispatchingAdapter``）按请求上下文转发真实适配器；
    - 旧形态 client（测试桩 / 既有 OpenAICompatClient）走 ``chat(messages) -> str``。

    timeout：本次调用读超时覆盖（秒），None 时适配器回退网关默认
    （settings.PROVIDER_TIMEOUT）；报告综合等长文生成调用传更大预算。

    429 限流（RateLimitError）按 LLM_MAX_RETRIES（封顶 2，短退避 1s/3s）
    自动重试——共享中转的间歇性限流不应直接打穿为降级；其他异常不重试。
    """
    from config import settings

    attempts = max(0, min(int(settings.LLM_MAX_RETRIES), 2))
    delays = (1.0, 3.0)
    for attempt in range(attempts + 1):
        try:
            return _chat_dispatch(
                client, messages, model=model, json_mode=json_mode, timeout=timeout
            )
        except RateLimitError:
            if attempt >= attempts:
                raise
            time.sleep(delays[min(attempt, len(delays) - 1)])
    raise RateLimitError("重试循环异常退出（不可达防御分支）")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_providers.py -v`
Expected: 全部 PASS（既有测试零回归——正常路径首次即返回）

- [ ] **Step 5: Commit**

```bash
git add providers/__init__.py tests/test_providers.py
git commit -m "feat(providers): chat_text 门面对 429 限流自动退避重试，接线 LLM_MAX_RETRIES"
```

---

### Task 2: 澄清轮次计数（state 字段 + plan_gate 递增）

**Files:**
- Modify: `core/orchestrator/state.py:178-182`（AgentState 澄清字段区）
- Modify: `core/orchestrator/langgraph_engine.py:137-150`（`_plan_gate` clarify 分支）
- Test: `tests/test_orchestrator.py`（文件末尾追加）

**Interfaces:**
- Consumes: 既有 `_plan_gate` clarify 挂起/resume 结构。
- Produces: `AgentState.clarification_rounds: int`（默认 0）——Task 4 的"二轮澄清超限拒答"消费。

- [ ] **Step 1: 写失败测试**

`tests/test_orchestrator.py` 末尾追加：

```python
# --------------------------------------------------------------------------- #
# 分层降级兜底：澄清轮次计数（二轮上限的状态载体）
# --------------------------------------------------------------------------- #
def test_clarification_rounds_field_defaults_zero():
    """澄清轮次计数字段存在且默认 0。"""
    from core.orchestrator.state import AgentState

    state = AgentState(
        session_id="cr", turn_id="t1", trace_id="tr1", user_query="x"
    )
    assert state.clarification_rounds == 0
    bumped = state.apply(clarification_rounds=state.clarification_rounds + 1)
    assert bumped.clarification_rounds == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_orchestrator.py::test_clarification_rounds_field_defaults_zero -v`
Expected: FAIL（`AgentState` 无 `clarification_rounds` 字段；`extra="forbid"` 下 apply 会报错）

- [ ] **Step 3: 最小实现**

`core/orchestrator/state.py` 在 `clarification_options` 字段（约 179-181 行）之后加：

```python
    clarification_rounds: int = Field(
        default=0, description="降级澄清已发生轮次（二轮仍歧义则拒答，防循环）"
    )
```

`core/orchestrator/langgraph_engine.py` 的 `_plan_gate` clarify 分支（resume 后的 `state.apply(...)`，约 142-150 行）追加轮次递增：

```python
        return state.apply(
            user_query=f"{state.user_query}（用户补充：{str(resume_value).strip()}）",
            human_reply=None,
            clarification=None,
            clarification_rounds=state.clarification_rounds + 1,
            plan_steps=[],
            phase="plan",
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_orchestrator.py tests/test_langgraph_parity.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/state.py core/orchestrator/langgraph_engine.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): 新增澄清轮次计数字段，plan_gate 澄清恢复时递增"
```

---

### Task 3: 弱解析核心（`_degraded_parse` + DSL 组装）

**Files:**
- Modify: `core/orchestrator/nodes.py`（`_heuristic_plan` 之后新增两个模块级函数，约 236 行后；`_SCALAR_FIELD_AGG`/`_SCALAR_ANCHOR_TABLE`/`_scalar_dsl`/`parse_explicit_time_window`/`_dimension_label` 均为同文件既有符号，直接复用）
- Test: `tests/test_degraded_fallback.py`（新建）

**Interfaces:**
- Consumes: `classify_intent`/`IntentProfile`（intent.py，只读）、`_SCALAR_FIELD_AGG`/`_SCALAR_ANCHOR_TABLE`（nodes.py 既有）、`parse_explicit_time_window`（agent.time_utils，nodes.py 已 import）、`_dimension_label`（nodes.py 既有）、`profile_enum_values()`（core/retrieval/profiling）、`PlanStep`（state.py）。
- Produces: `_degraded_parse(query: str, profile: IntentProfile, enum_values: dict[str, list[str]] | None) -> tuple[str, Any]`——返回 `("not_exist", reason)` / `("plan", steps)` / `("clarify", (question, options))` 三形态。Task 4 消费此函数。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_degraded_fallback.py`：

```python
"""分层降级兜底（2026-09）：UNKNOWN 二次判定与弱解析的回归锚点。

- 指标锚存在但模板不可拆 => 弱解析（plan/clarify），不再直接拒答；
- 无指标锚/跨表混合锚 => NOT_EXIST 拒答（宁拒不错）；
- 筛选条件仅来自显式时间/用户确认/枚举值精确命中，严禁猜测。
"""

from __future__ import annotations

from core.orchestrator.intent import classify_intent
from core.orchestrator.nodes import _degraded_parse

# 模拟 profiling 枚举（低基数字段真实取值）
ENUMS = {"province": ["北京", "上海", "海南", "广东"], "category": ["服饰", "数码"]}


def test_compound_query_with_enum_hit_yields_plan():
    """复合问句"海南省的GMV"（指标+维度锚）=> 枚举命中唯一 => 降级计划。"""
    profile = classify_intent("海南省的GMV")
    mode, payload = _degraded_parse("海南省的GMV", profile, ENUMS)
    assert mode == "plan"
    s1, s2 = payload
    assert s1.kind == "query" and s1.dsl is not None
    assert s2.kind == "synthesize" and s2.depends_on == ["s1"]
    dsl = s1.dsl
    assert dsl["metrics"][0]["field"] == "order_amount"
    assert {"field": "province", "operator": "eq", "value": "海南"} in dsl["filters"]
    assert dsl["dimensions"] == []  # 筛选形态，非分组


def test_grouping_form_yields_dimensions():
    """"各省份的GMV" => 分组用法（各+别名）=> dimensions 分组、无筛选。"""
    query = "各省份的GMV"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "plan"
    assert payload[0].dsl["dimensions"] == [{"field": "province"}]
    assert all(f["field"] != "province" for f in payload[0].dsl["filters"])


def test_enum_miss_asks_for_clarification():
    """取值未命中枚举（数仓没有"西藏"）=> clarify 留白确认，严禁猜条件。"""
    query = "西藏的GMV"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "clarify"
    question, options = payload
    assert "AI 规划暂不可用" in question
    assert any("省份" in o for o in options)


def test_multi_enum_hits_asks_for_clarification():
    """多枚举命中（"北京和上海的GMV"）=> 多候选澄清，不擅自多值筛选。"""
    query = "北京和上海的GMV"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "clarify"
    _, options = payload
    assert any("北京" in o for o in options) and any("上海" in o for o in options)


def test_dimension_only_anchor_is_not_exist():
    """只识别到维度无指标 => NOT_EXIST（区分文案：识别到维度但缺指标）。"""
    profile = classify_intent("按省份看一下")
    mode, payload = _degraded_parse("按省份看一下", profile, ENUMS)
    assert mode == "not_exist"
    assert "维度" in payload and "指标" in payload


def test_no_anchor_is_not_exist():
    """完全无锚点 => NOT_EXIST（原拒答语义保留）。"""
    profile = classify_intent("随便看看")
    mode, payload = _degraded_parse("随便看看", profile, ENUMS)
    assert mode == "not_exist"
    assert "未命中" in payload


def test_cross_table_anchors_are_not_exist():
    """跨表混合锚（GMV+退款金额）=> NOT_EXIST，严禁硬造单查询 DSL。"""
    query = "海南省的GMV和退款金额"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "not_exist"
    assert "跨表" in payload


def test_metric_only_unknown_gets_scalar_dsl():
    """纯指标锚 UNKNOWN（如"GMV趋势"）=> 无维度标量 DSL（不视为猜条件）。"""
    query = "GMV趋势"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "plan"
    dsl = payload[0].dsl
    assert dsl["metrics"][0]["field"] == "order_amount"
    assert dsl["dimensions"] == []


def test_explicit_time_window_wins_over_default():
    """显式时间解析优先于缺省 2024-05 锚。"""
    query = "海南省的GMV 2024年3月1日到3月31日"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "plan"
    window = payload[0].dsl["time_filter"]["absolute"]
    assert window["start"] == "2024-03-01" and window["end"].startswith("2024-03-31")


def test_default_window_and_pay_status_scope():
    """无显式时间 => 缺省 2024-05 锚；fact_orders 锚带 pay_status 口径。"""
    mode, payload = _degraded_parse("海南省的GMV", classify_intent("海南省的GMV"), ENUMS)
    assert mode == "plan"
    dsl = payload[0].dsl
    assert dsl["time_filter"]["absolute"]["start"] == "2024-05-01"
    assert {"field": "pay_status", "operator": "eq", "value": "SUCCESS"} in dsl["filters"]
```

> 注意：`classify_intent` 对各测试 query 的实际判定以运行结果为准——若某条 query 的锚点与预期不符（如"按省份看一下"的锚点提取），以 intent 模块真实行为修正测试输入措辞（换一个能产生预期锚点的问题），**严禁改动 intent.py**。若"海南省的GMV"被 classify_intent 判为 METRIC_SCALAR 而非 UNKNOWN（不影响本函数——`_degraded_parse` 只吃锚点画像不判 intent 字段），测试依然成立。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_degraded_fallback.py -v`
Expected: FAIL（`_degraded_parse` 未定义 → ImportError/AttributeError）

- [ ] **Step 3: 最小实现**

`core/orchestrator/nodes.py` 在 `_heuristic_plan`（约 236 行结束处）之后新增：

```python
# --------------------------------------------------------------------------- #
# 分层降级兜底（2026-09）：UNKNOWN 二次判定与弱解析（设计 §3.1-3.3）
# --------------------------------------------------------------------------- #
# 分组提示词根："{词根}{维度别名}"形态（"各省份/按省份/每个省份"）判分组用法
_GROUP_HINT_TOKENS: tuple[str, ...] = ("各", "按", "每个", "分", "所有")
# 缺省查询窗口（与 _scalar_dsl 同锚；报告侧由 _default_scope_note 明示口径）
_DEGRADE_DEFAULT_WINDOW = {"start": "2024-05-01", "end": "2024-05-15"}


def _wants_grouping(query: str, dim: str) -> bool:
    """判定维度在问题中是否为分组用法（"各省份的 GMV"）而非筛选（"海南省的 GMV"）。"""
    for alias, field in dimension_terms().items():
        if field != dim:
            continue
        if any(f"{token}{alias}" in query for token in _GROUP_HINT_TOKENS):
            return True
    return False


def _degraded_parse(
    query: str, profile: IntentProfile, enum_values: dict[str, list[str]] | None
) -> tuple[str, Any]:
    """UNKNOWN 二次判定（分层降级兜底）。

    输入为 classify_intent 的确定性画像（不信任 LLM 回传意图）。返回 (mode, payload)：
    - ("not_exist", reason)：无指标锚 / 仅维度锚 / 跨表混合锚 => NOT_EXIST 拒答
      （语义不存在，宁拒不错）；
    - ("plan", steps)：唯一候选口径 => 降级计划（query+synthesize 两步结构，
      严禁 analyze——禁多步组合是结构约束而非提示词约束）；
    - ("clarify", (question, options))：筛选值多候选或缺失 => 选项式澄清
      （用户回答经 human_reply 合并后重新解析，唯一化后进 plan_review）。

    筛选条件来源白名单（强制）：显式时间解析 / 用户确认内容 / 枚举值精确命中
    （数据真实存在不算猜）；枚举未命中的取值一律留白交用户确认。
    """
    metrics_anchors = tuple(a for a in profile.anchor_fields if a in _SCALAR_FIELD_AGG)
    if not metrics_anchors:
        dim_only = [a for a in profile.anchor_fields if a in dimension_terms()]
        if dim_only:
            labels = "、".join(_dimension_label(d) for d in dim_only)
            return (
                "not_exist",
                f"识别到维度【{labels}】但未识别到任何可查询指标，无法构造查询",
            )
        return ("not_exist", "无法从语义目录识别问题意图（未命中任何指标/维度锚点）")
    tables = {_SCALAR_ANCHOR_TABLE[a] for a in metrics_anchors}
    if len(tables) > 1:
        return (
            "not_exist",
            "识别到跨表指标锚点（如 GMV 与退款金额），单查询无法同口径构造",
        )
    dim_anchors = tuple(a for a in profile.anchor_fields if a not in metrics_anchors)

    # 维度用法判定：分组提示命中 => 分组；取值唯一命中枚举 => 筛选；
    # 多候选/未命中 => 澄清（严禁猜测筛选条件）
    enums = enum_values or {}
    filters: list[dict[str, Any]] = []
    group_dims: list[str] = []
    pending: list[tuple[str, list[str]]] = []
    for dim in dim_anchors:
        if _wants_grouping(query, dim):
            group_dims.append(dim)
            continue
        hits = [v for v in (enums.get(dim) or []) if v and v in query]
        if len(hits) == 1:
            filters.append({"field": dim, "operator": "eq", "value": hits[0]})
        else:
            pending.append((dim, hits))
    if pending:
        metric_labels = "、".join(_metric_label(m) for m in metrics_anchors)
        question = (
            f"AI 规划暂不可用，已识别指标【{metric_labels}】，但以下维度的"
            "筛选条件无法从问题中唯一确定，请补充或选择："
        )
        options: list[str] = []
        for dim, hits in pending:
            label = _dimension_label(dim)
            if hits:
                options.extend(f"{label}={v}" for v in hits[:4])
            else:
                options.append(f"按{label}分组统计（不筛选具体取值）")
                options.append(f"不限定{label}，查询全部")
        return ("clarify", (question, options[:6]))

    explicit = parse_explicit_time_window(query)
    window = (
        {"start": explicit[0], "end": explicit[1]}
        if explicit
        else dict(_DEGRADE_DEFAULT_WINDOW)
    )
    if tables == {"fact_orders"}:
        filters.append({"field": "pay_status", "operator": "eq", "value": "SUCCESS"})
    dsl: dict[str, Any] = {
        "metrics": [
            {
                "kind": "aggregate",
                "field": field,
                "agg": _SCALAR_FIELD_AGG[field],
                "alias": field if _SCALAR_FIELD_AGG[field] == "sum" else f"{field}_count",
            }
            for field in metrics_anchors
        ],
        "dimensions": [{"field": d} for d in group_dims],
        "filters": filters,
        "time_filter": {"range_type": "absolute", "absolute": window},
    }
    scope_desc = "、".join(_metric_label(m) for m in metrics_anchors)
    if group_dims:
        scope_desc += f"（按{'、'.join(_dimension_label(d) for d in group_dims)}分组）"
    for f in filters:
        if f["field"] != "pay_status":
            scope_desc += f"（{_dimension_label(f['field'])}={f['value']}）"
    steps = [
        PlanStep(id="s1", goal=f"按确认条件查询{scope_desc}", kind="query", dsl=dsl),
        PlanStep(
            id="s2",
            goal="按确认条件汇总查询结果作答（降级模式）",
            kind="synthesize",
            depends_on=["s1"],
        ),
    ]
    return ("plan", steps)
```

> 依赖核对：`IntentProfile` 需从 `core.orchestrator.intent` 导入（nodes.py 头部既有 import 块补充）；`dimension_terms`/`_metric_label`/`PlanStep`/`parse_explicit_time_window` 均为 nodes.py 既有可用符号（核对 `_metric_label` 定义位置在本函数之前或运行时可用——模块级函数互相调用无顺序要求）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_degraded_fallback.py -v`
Expected: 全部 PASS（个别锚点判定与预期不符时按 Step 1 注意事项调整测试措辞）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_degraded_fallback.py
git commit -m "feat(orchestrator): UNKNOWN 二次判定与弱解析 DSL 组装（降级兜底核心）"
```

---

### Task 4: planner 集成（三分流 + answered_by + NOT_EXIST 文案增强）

**Files:**
- Modify: `core/orchestrator/nodes.py:381-401`（`planner_node` 的 `if steps is None:` 兜底块）
- Test: `tests/test_degraded_fallback.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `clarification_rounds`；Task 3 的 `_degraded_parse`。
- Produces: planner 兜底三分流行为——`("plan", steps)` → `answered_by="degraded_confirmed"` 的两步计划（自动过 plan_review 审批门）；`("clarify", ...)` → `phase="clarify"`（`_plan_gate` 挂起，二轮超限拒答）；`("not_exist", reason)` → `blocked_reason` 拒答。Task 5 的水印消费 `degraded_confirmed`。

- [ ] **Step 1: 写失败测试**

`tests/test_degraded_fallback.py` 追加：

```python
# --------------------------------------------------------------------------- #
# planner 集成：三分流与降级可见化
# --------------------------------------------------------------------------- #
class _NoLLM:
    """resolve_default_client 返回 None 的替身。"""


def test_planner_unknown_with_metric_anchor_yields_degraded_plan(monkeypatch, tmp_path):
    """"海南省的GMV"（LLM 不可用）=> 弱解析计划，answered_by=degraded_confirmed。"""
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    state = AgentState(
        session_id="dg", turn_id="t1", trace_id="tr1", user_query="海南省的GMV"
    )
    out = orch.planner_node(state)
    assert out.answered_by == "degraded_confirmed"
    assert [s.kind for s in out.plan_steps] == ["query", "synthesize"]
    assert out.plan_steps[0].dsl is not None
    assert any(
        f["field"] == "province" and f["value"] == "海南" for f in out.plan_steps[0].dsl["filters"]
    )


def test_planner_unknown_without_anchor_blocks(monkeypatch, tmp_path):
    """无锚点 UNKNOWN => 拒答（原语义保留）。"""
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    state = AgentState(
        session_id="dg2", turn_id="t1", trace_id="tr1", user_query="随便看看"
    )
    out = orch.planner_node(state)
    assert out.answered_by == "blocked"
    assert out.blocked_reason


def test_planner_clarify_round_limit_blocks_second_round(monkeypatch, tmp_path):
    """二轮澄清仍歧义 => 拒答（防循环）。"""
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    state = AgentState(
        session_id="dg3",
        turn_id="t1",
        trace_id="tr1",
        user_query="西藏的GMV",
        clarification_rounds=1,  # 已澄清过一轮
    )
    out = orch.planner_node(state)
    assert out.answered_by == "blocked"
    assert "澄清" in out.blocked_reason or "确认" in out.blocked_reason


def test_planner_clarify_first_round_emits_options(monkeypatch, tmp_path):
    """首轮多候选 => clarification + options（交 _plan_gate 挂起）。"""
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    state = AgentState(
        session_id="dg4", turn_id="t1", trace_id="tr1", user_query="西藏的GMV"
    )
    out = orch.planner_node(state)
    assert out.phase == "clarify"
    assert out.clarification
    assert out.clarification_options
    assert out.clarification_rounds == 1
```

> 同 Task 3 注意事项：query 措辞若与 `classify_intent` 真实锚点不符，以真实行为换措辞，严禁改 intent.py。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_degraded_fallback.py -k "planner" -v`
Expected: FAIL（现状 UNKNOWN 一律 `blocked`，无弱解析分流）

- [ ] **Step 3: 最小实现**

`core/orchestrator/nodes.py` 的 `planner_node`，将现有兜底块（`if steps is None:` 起至 `return state.apply(blocked_reason=..., ...)` 止）替换为：

```python
    if steps is None:
        # 兜底准入（十八期）+ 分层降级二次判定（2026-09）：UNKNOWN 拆为
        # 弱解析（字段存在但无法自动拆解 => 交互确认）与 NOT_EXIST（语义
        # 不存在 => 拒答）。严禁猜测口径产计划（回归锚点：曾固定 sum(gmv)
        # 答非所问）。
        from core.retrieval.profiling import profile_enum_values

        mode, payload = _degraded_parse(
            state.user_query, classify_intent(state.user_query), profile_enum_values()
        )
        if mode == "plan":
            events.emit_event(
                "degrade", {"outcome": "parse_hit", "query": state.user_query[:200]}
            )
            events.emit_plan(payload)
            return state.apply(
                plan_steps=payload,
                phase="query",
                scratchpad=[*state.scratchpad, "[planner] degraded-parse"],
                answered_by="degraded_confirmed",
                # 新计划 = 新的执行授权需求：高危确认锚复位
                high_risk_approved=False,
            )
        if mode == "clarify":
            if state.clarification_rounds >= 1:
                # 二轮澄清仍歧义 => 拒答（防循环；plan_edit_instruction 同类经验）
                return state.apply(
                    blocked_reason="经一轮澄清后查询条件仍无法唯一确定，已停止降级解析。"
                    "请调整问法（明确指标与筛选条件），或等待 AI 规划服务恢复后重试。",
                    answered_by="blocked",
                    plan_steps=[],
                    plan_edit_instruction=None,
                    scratchpad=[
                        *state.scratchpad,
                        "[planner] blocked: degraded clarify round-limit",
                    ],
                )
            question, options = payload
            events.emit_event(
                "degrade", {"outcome": "parse_hit", "query": state.user_query[:200]}
            )
            return state.apply(
                phase="clarify",
                clarification=question,
                clarification_options=[str(o) for o in options],
                clarification_rounds=state.clarification_rounds + 1,
                scratchpad=[*state.scratchpad, "[planner] degraded-clarify"],
            )
        return state.apply(
            blocked_reason=str(payload),
            answered_by="blocked",
            plan_steps=[],
            plan_edit_instruction=None,
            scratchpad=[
                *state.scratchpad,
                "[planner] blocked: intent unknown（NOT_EXIST）",
            ],
        )
```

> 核对：原块中 `plan_edit_instruction=None` 清除语义保留（防 plan_gate edit 路由无限循环）；`classify_intent`/`events`/`PlanStep` 等符号 nodes.py 已可用；`IntentProfile` 类型注解若 Task 3 已加则无需重复。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_degraded_fallback.py tests/test_orchestrator.py tests/test_intent.py tests/test_sse_contract.py -v`
Expected: 全部 PASS（既有 blocked 链路测试若依赖"任意 UNKNOWN 必 blocked"的措辞，按真实行为把该测试的 query 换成无锚点问题——blocked 语义本身不变）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_degraded_fallback.py
git commit -m "feat(orchestrator): planner 兜底三分流集成——弱解析计划/选项澄清/拒答"
```

---

### Task 5: 降级水印与事件埋点

**Files:**
- Modify: `core/orchestrator/nodes.py:2413-2420`（`_degradation_banner`）
- Modify: `core/orchestrator/langgraph_engine.py:153-158`（`_plan_gate` reject 分支补降级拒绝埋点）
- Test: `tests/test_degraded_fallback.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `answered_by="degraded_confirmed"` 与 `"degrade"` 事件（parse_hit 已在 Task 4 发射）。
- Produces: `degraded_confirmed` 报告头部水印；`"degrade"` 事件三形态齐备——`parse_hit`（Task 4，弱解析命中）、`rejected`（本任务，用户拒绝降级计划）、`confirmed`（用户批准后由 `answered_by`/水印承载，不重复发事件）。

- [ ] **Step 1: 写失败测试**

`tests/test_degraded_fallback.py` 追加：

```python
# --------------------------------------------------------------------------- #
# 降级水印：degraded_confirmed 与 heuristic 文案区分
# --------------------------------------------------------------------------- #
def test_degradation_banner_distinguishes_confirmed():
    """degraded_confirmed 水印明示"条件经人工确认"；heuristic 保持原文案。"""
    from core.orchestrator.nodes import _degradation_banner
    from core.orchestrator.state import AgentState

    state = AgentState(
        session_id="wm", turn_id="t1", trace_id="tr1", user_query="x",
        answered_by="degraded_confirmed",
    )
    banner = _degradation_banner(state)
    assert "降级模式" in banner and "人工确认" in banner
    heuristic_state = AgentState(
        session_id="wm2", turn_id="t1", trace_id="tr2", user_query="x",
        answered_by="heuristic",
    )
    assert "离线兜底引擎" in _degradation_banner(heuristic_state)


def test_degraded_plan_reject_emits_event(monkeypatch):
    """降级计划被用户拒绝 => 发射 degrade/rejected 事件（可观测埋点）。"""
    from core.orchestrator import events as orch_events
    from core.orchestrator.langgraph_engine import _plan_gate
    from core.orchestrator.state import AgentState, PlanStep

    seen: list[tuple[str, dict]] = []
    orig = orch_events.emit_event

    def spy(event: str, payload: dict) -> None:
        seen.append((event, payload))
        orig(event, payload)

    monkeypatch.setattr(orch_events, "emit_event", spy)
    state = AgentState(
        session_id="rj", turn_id="t1", trace_id="tr1", user_query="海南省的GMV",
        answered_by="degraded_confirmed",
        plan_steps=[
            PlanStep(id="s1", goal="按确认条件查询GMV", kind="query"),
            PlanStep(id="s2", goal="汇总作答", kind="synthesize", depends_on=["s1"]),
        ],
        phase="query",
    )
    out = _plan_gate(
        state, {"action": "reject", "instruction": None}, trigger="plan_review"
    )
    assert out.phase == "done"
    assert any(e == "degrade" and p.get("outcome") == "rejected" for e, p in seen)
```

> `_plan_gate` 的签名以 `core/orchestrator/langgraph_engine.py:100` 实际代码为准（`state, payload, trigger` 关键字形态参照同文件 `maybe_interrupt` 调用点）；若为位置参数形态按实际调整调用方式。测试内 monkeypatch `emit_event` 的模块路径以 import 关系为准（`langgraph_engine` 若 `from core.orchestrator import events` 则 patch `core.orchestrator.events.emit_event` 即可覆盖）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_degraded_fallback.py -k "banner or reject" -v`
Expected: 水印测试 FAIL（banner 对新值返回空串）；rejected 事件测试 FAIL（reject 分支无事件发射）

- [ ] **Step 3: 最小实现**

(a) `core/orchestrator/nodes.py` 的 `_degradation_banner` 替换为：

```python
def _degradation_banner(state: AgentState) -> str:
    """兜底接管的降级标注（十八期：降级不可静默；2026-09 分层降级扩展）。"""
    if state.answered_by == "degraded_confirmed":
        return (
            "> ⚠️ **本次报告由降级模式生成**（AI 规划暂不可用）：查询条件为规则推断"
            "并经人工确认，未经 LLM 完整语义理解。\n\n"
        )
    if state.answered_by != "heuristic":
        return ""
    return (
        "> ⚠️ **本次回答由离线兜底引擎生成**（LLM 规划不可用），"
        "口径为确定性规则匹配结果，仅供参考。\n\n"
    )
```

(b) `core/orchestrator/langgraph_engine.py` 的 `_plan_gate` reject 分支（约 153 行）在终止前补事件：

```python
    if resume.get("action") == "reject":
        if state.answered_by == "degraded_confirmed":
            # 降级可观测：用户拒绝降级推断计划（设计 §3.5 degrade_rejected）
            events.emit_event(
                "degrade", {"outcome": "rejected", "query": state.user_query[:200]}
            )
        # 规格 §5.1 ③：终止并如实报告，不产出
        return state.apply(
            phase="done",
            no_data_reason="用户拒绝了分析计划，未执行任何查询",
            report="用户拒绝了分析计划，本次未执行任何查询、未产出任何结论。",
        )
```

> 核对 `langgraph_engine.py` 头部是否已导入 `events`（`from core.orchestrator import events` 或等效）；若无则补。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_degraded_fallback.py tests/test_synthesizer_rebuild.py tests/test_langgraph_parity.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py core/orchestrator/langgraph_engine.py tests/test_degraded_fallback.py
git commit -m "feat(orchestrator): 降级确认水印与拒绝埋点（degrade 事件三形态齐备）"
```

---

### Task 6: 端到端验证与全量回归

**Files:**
- Test: `tests/test_degraded_fallback.py`（追加端到端用例）；无生产代码改动（回归暴露问题归属对应任务文件）

**Interfaces:**
- Consumes: Task 1-5 全部产出。
- Produces: 端到端两场景验证 + 意图评测零回归证明。

- [ ] **Step 1: 端到端失败测试**

`tests/test_degraded_fallback.py` 追加：

```python
# --------------------------------------------------------------------------- #
# 端到端：LLM 不可用全链路（弱解析 → 审批 → 执行 → 水印报告）
# --------------------------------------------------------------------------- #
def test_e2e_degraded_compound_query_with_approval(monkeypatch, tmp_path):
    """"海南省的GMV"（LLM 不可用）→ 弱解析计划 → 模拟批准后 DSL 执行 → 水印报告。

    用 mock 编译-执行验证 DSL 契约合法性；水印随 answered_by 注入报告头。
    """
    import pandas as pd
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    state = orch.planner_node(
        AgentState(
            session_id="e2e", turn_id="t1", trace_id="tr1", user_query="海南省的GMV"
        )
    )
    assert state.answered_by == "degraded_confirmed"
    dsl = state.plan_steps[0].dsl
    # DSL 契约合法性：直接过 semantic 契约校验（编译器入口）
    from semantic.dsl_schema import QueryDSL

    QueryDSL.model_validate(dsl)  # 非法即 ValidationError
    # 审批（模拟 plan_review 批准：plan_reviewed 置位）
    state = state.apply(plan_reviewed=True)
    # 水印在 synthesize 渲染头部（以确定性数据集报告路径验证）
    assert "降级模式" in orch._degradation_banner(state)
```

- [ ] **Step 2: 确认通过（本用例为既有行为锚定，若 RED 按失败点归属修复）**

Run: `python -m pytest tests/test_degraded_fallback.py -v`
Expected: 全部 PASS

- [ ] **Step 3: 意图评测零回归**

Run: `python -m eval.intent_eval`
Expected: 与基线一致（8/8 全绿——`classify_intent` 未被本分支触碰）

- [ ] **Step 4: 全量回归与格式**

Run: `black --check . && ruff check . && python -m pytest -q`
Expected: 全绿；若有格式问题先 `black .`/`ruff check --fix .` 后复查并单独 style 提交。

- [ ] **Step 5: 收尾提交（若 Step 4 有格式修复）**

```bash
git add -u
git commit -m "style: black/ruff 格式化（分层降级兜底收尾）"
```
