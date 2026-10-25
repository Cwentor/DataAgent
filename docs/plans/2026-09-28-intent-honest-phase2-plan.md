# 十八期二期（收尾 + 防虚构闭环 + 选项式澄清）实施计划

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。

**Goal:** 完成诚实兜底的二期收尾——五条 Minor 修复、别名收编语义目录（单一事实源）、Grounding 定向重试闭环、选项式澄清后端契约、评测挂 CI。

**Architecture:** `FieldMeta.aliases` 成为意图词表唯一来源（`intent.py` 从 `COLUMNS` 动态构建，零字面词表）；Grounding 失败走"定向重试 1 次 → 仍超阈值降级确定性渲染"；选项式澄清经 `clarification_options` 状态字段透传到前端已就绪的 `elHitl` 渲染（前端零改动）。

**Tech Stack:** 现有栈零新依赖（dataclass / LangGraph interrupt / pytest / GitHub Actions）。

**Spec:** `docs/superpowers/specs/2026-09-28-intent-honest-phase2-design.md`（执行者必须同时读规格；冲突以规格为准）

## Global Constraints

- L3 守卫判定依据 = intent 模块对 user_query 的确定性 L1 硬匹配，不信任 LLM 回传意图；
- 拒答/降级路径严禁产生任何 LLM 调用（纯确定性字符串构造）；
- Grounding 重试上限硬编码 1 次，重试后仍超阈值必须降级确定性渲染；
- 选项式澄清零前端改动（前端 `elHitl` 已消费 `item.options` 字符串数组）；
- 评测锚点 `AS_OF_DATE = 2024-06-30`、随机种子 42，确定性可复现；
- 提交前 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿；
- 运行环境：`C:/Users/cwt15/.conda/envs/futurebi/python.exe`。

## Review Focus（规格隐含、无既有测试覆盖的输入，各配测试挂到 owning task）

1. 旧版 `semantic.json` 无 aliases 键时 loader 必须回退内置默认（向后兼容），分类结果零漂移（Task 4）；
2. Grounding 重试时 LLM 再次失败（异常/空文本）=> 直接降级确定性渲染，不得挂起或输出未校正报告（Task 5）；
3. clarification 为对象但缺 `question` 键 => 回退默认问题文本，options 仍保留（Task 6）；
4. options 含空串/非字符串项 => 过滤后为空列表，前端走自由输入兜底而非报错（Task 6）；
5. 别名收编后 `classify_intent` 对一期全部语料判定零漂移（等价性红线，靠复跑一期既有测试覆盖，Task 4 显式跑）。

---

### Task 1: 守卫审计面 + code_exec blocked 短路（Minor M1/M2）

**Files:**
- Modify: `core/orchestrator/nodes.py`（`_run_query_step` DSL 循环 mismatch 分支；`code_exec_node` 开头）
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: 一期 `_intent_dsl_mismatch` / `blocked_reason`（不改签名）。
- Produces: 拦截路径完整审计（tool_start/tool_end 配对、notes 记录、全拦时 `ToolRecord.ok=False`）；`code_exec_node` 对 `blocked_reason` 直接返回。

- [ ] **Step 1: 写失败测试**

在 `tests/test_orchestrator.py` 追加：

```python
def test_guard_intercept_full_audit(tmp_path, monkeypatch):
    """L3 拦截路径审计面完整：tool_start/end 配对、ok=False（终审 M1）。"""
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
                {
                    "id": "s1",
                    "goal": "取GMV",
                    "kind": "query",
                    "depends_on": [],
                    "dsl": {
                        "metrics": [
                            {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                        ],
                        "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
                        "time_filter": {
                            "range_type": "absolute",
                            "absolute": {"start": "2024-05-01", "end": "2024-05-15"},
                        },
                    },
                },
                {"id": "s2", "goal": "综合作答", "kind": "synthesize", "depends_on": ["s1"], "dsl": None, "code": None},
            ],
        },
    )
    events: list[dict] = []
    trace = run_agent("有多少个省份", session_id="guardaudit", on_event=events.append)
    assert trace.phase == "done"
    starts = [
        e
        for e in events
        if e["event"] == "tool_start" and e["payload"]["tool"]["name"] == "futurebi_dsl_query"
    ]
    ends = [
        e
        for e in events
        if e["event"] == "tool_end" and e["payload"]["tool"]["name"] == "futurebi_dsl_query"
    ]
    assert len(starts) == len(ends) == 1  # 配对实锤（此前孤儿 tool_end）
    assert starts[0]["payload"]["tool"]["output"]["dsl"] == ends[0]["payload"]["tool"]["input"]["dsl"]
    assert ends[0]["payload"]["tool"]["error"]
    # 步骤轨迹 ok=False（此前恒 True）
    assert all(not s["ok"] for s in trace.steps if s["tool"] == "execute_dsl_query")


def test_code_exec_skipped_when_blocked(tmp_path, monkeypatch):
    """拒答路径零沙箱执行（终审 M2）：blocked 后 analyze 步骤不跑沙箱。"""
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
                {
                    "id": "s1",
                    "goal": "取GMV",
                    "kind": "query",
                    "depends_on": [],
                    "dsl": {
                        "metrics": [
                            {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                        ],
                        "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
                        "time_filter": {
                            "range_type": "absolute",
                            "absolute": {"start": "2024-05-01", "end": "2024-05-15"},
                        },
                    },
                },
                {
                    "id": "s2",
                    "goal": "沙箱分析",
                    "kind": "analyze",
                    "depends_on": ["s1"],
                    "dsl": None,
                    "code": "save_summary(title='x', metrics={}, table={'columns': [], 'rows': []}, findings=[], extra={})",
                },
            ],
        },
    )
    sandbox_calls: list[str] = []
    original = nodes.run_code

    def _spy(code, workspace, name="code"):
        sandbox_calls.append(name)
        return original(code, workspace, name=name)

    monkeypatch.setattr(nodes, "run_code", _spy)
    trace = run_agent("有多少个省份", session_id="guardskip")
    assert trace.phase == "done"
    assert "无法作答" in trace.report
    assert sandbox_calls == []  # 拒答路径零沙箱执行
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py::test_guard_intercept_full_audit tests/test_orchestrator.py::test_code_exec_skipped_when_blocked -v`
Expected: FAIL（start/end 不配对或 ok 恒 True；sandbox_calls 非空）

- [ ] **Step 3: 最小实现**

3a. `_run_query_step` 的 DSL 循环（mismatch 分支在 `name = ...` 之后重排）替换为：

```python
    guard_blocked: list[str] = []
    for i, dsl_payload in enumerate(dsl_variants):
        name = f"{step.id}_v{i}" if len(dsl_variants) > 1 else step.id
        # L3 意图-DSL 错位守卫（十八期）：拦截即不执行，置拒答原因；
        # 审计面完整（终审 M1）：tool_start/end 配对、notes 记录
        mismatch = _intent_dsl_mismatch(state.user_query, dsl_payload)
        if mismatch:
            state = state.apply(blocked_reason=mismatch, answered_by="blocked")
            guard_blocked.append(name)
            events.emit_tool_start("futurebi_dsl_query", step.id, {"dataset": name, "dsl": dsl_payload})
            events.emit_tool_end(
                "futurebi_dsl_query",
                step.id,
                ok=False,
                duration_ms=0.0,
                error=mismatch,
            )
            notes.append(f"{name} 被意图守卫拦截：{mismatch}")
            continue
```

（原 mismatch 分支删除；循环其余部分不变。）

3b. 循环后的 `all_blocked` 汇总行替换为：

```python
    all_blocked = (bool(blocked_windows) or bool(guard_blocked)) and not produced
    if guard_blocked and not produced:
        windows = "；".join(dict.fromkeys(guard_blocked))
        no_data_reason = f"取数全部被意图守卫拦截：{windows}"
        summary = f"[{step.id}] 取数被意图守卫拦截：{'；'.join(notes)}"
    elif all_blocked:
        windows = "、".join(dict.fromkeys(blocked_windows))
        no_data_reason = f"查询时间范围 {windows} 超出数仓数据覆盖范围，该时段无任何数据"
        summary = f"[{step.id}] 取数被时间域守卫拦截：{no_data_reason}"
    else:
        no_data_reason = None
        summary = f"[{step.id}] 取数完成：{'; '.join(notes)}"
    return (
        state.apply(
            step_outputs={**state.step_outputs, step.id: produced},
            no_data_reason=no_data_reason,
        ),
        ToolRecord(
            step_id=step.id,
            tool="execute_dsl_query",
            ok=not all_blocked,
            summary=summary,
        ),
    )
```

3c. `code_exec_node` 开头（`from config import settings` 之后的现有 `if state.no_data_reason:` 块）替换为：

```python
    if state.no_data_reason or state.blocked_reason:
        # 时间域守卫已拦截：严禁空数据上跑分析产出编造结论；
        # 意图守卫已拦截（终审 M2）：拒答路径零沙箱执行、零产物发射。
        # 两者均直接返回，交由 critic 短路到 synthesize。
        return state
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py -q`
Expected: PASS（含一期 guard e2e）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_orchestrator.py
git commit -m "fix(orchestrator): 守卫拦截审计面完整化+code_exec对blocked短路（二期M1/M2）"
```

---

### Task 2: 拒答报告锚点行 + 能力清单分组（Minor M3/M4）

**Files:**
- Modify: `core/orchestrator/nodes.py`（`_cannot_answer_report`）
- Modify: `core/orchestrator/intent.py`（`capability_catalog_lines`）
- Test: `tests/test_orchestrator.py`、`tests/test_intent.py`

**Interfaces:**
- Consumes: `classify_intent`（现有签名不变）。
- Produces: `capability_catalog_lines()` 返回文本分组变化（unit_price 单列）；`_cannot_answer_report` 增加"已识别锚点"行。

- [ ] **Step 1: 写失败测试**

`tests/test_intent.py` 追加：

```python
def test_capability_catalog_groups_unit_price_separately():
    """unit_price 单价不属于金额指标（终审 M4）。"""
    lines = capability_catalog_lines()
    money_line = next(line for line in lines if line.startswith("- 金额指标"))
    assert "unit_price" not in money_line
    assert any("unit_price" in line for line in lines)  # 仍在清单中（其他分组）
```

`tests/test_orchestrator.py` 追加：

```python
def test_blocked_report_lists_anchor_status(tmp_path, monkeypatch):
    """拒答报告必须写明锚点识别状态（终审 M3，一期规格 §3.4 补齐）。"""
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: None)
    trace = run_agent("帮我看看最近情况", session_id="anchorq")
    assert "未在语义目录中识别到任何指标或维度" in trace.report
    assert "帮我看看最近情况" in trace.report  # 复述原问句
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_intent.py::test_capability_catalog_groups_unit_price_separately tests/test_orchestrator.py::test_blocked_report_lists_anchor_status -v`
Expected: FAIL（unit_price 在金额行；报告无锚点行）

- [ ] **Step 3: 最小实现**

3a. `intent.py` 的 `capability_catalog_lines` 中金额指标推导替换为：

```python
    metrics = "、".join(
        f"{name}（{meta.label or name}）"
        for name, meta in COLUMNS.items()
        if meta.dtype == "float" and name != "unit_price"
    )
    others = "、".join(
        f"{name}（{meta.label or name}）" for name, meta in COLUMNS.items() if name == "unit_price"
    )
```

return 列表追加一行 `f"- 其他：{others}"`（others 非空时才追加）。

3b. `_cannot_answer_report` 在 `lines.append(f"**本次无法作答：{state.blocked_reason}。**")` 之后插入：

```python
    profile = classify_intent(state.user_query)
    if profile.anchor_fields:
        lines.append(
            f"已识别锚点：{'、'.join(profile.anchor_fields)}"
            "（但不足以确定完整查询口径）。"
        )
    else:
        lines.append("未在语义目录中识别到任何指标或维度。")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_intent.py tests/test_orchestrator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/intent.py core/orchestrator/nodes.py tests/test_intent.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): 拒答报告锚点行+能力清单unit_price独立分组（二期M3/M4）"
```

---

### Task 3: parity 链式场景覆盖（Minor M5）

**Files:**
- Modify: `tests/test_langgraph_parity.py`（`test_clarify_interrupt_and_resume_roundtrip`）
- Test: 同文件

**Interfaces:**
- Consumes: `planner_node` monkeypatch 手法（既有 `_patch_two_step_plan` 同款）。
- Produces: clarify-resume → 新计划 → plan_review interrupt → approve 全链断言。

- [ ] **Step 1: 改写测试（预期先失败）**

`test_clarify_interrupt_and_resume_roundtrip` 整体替换为：

```python
def test_clarify_interrupt_and_resume_roundtrip(monkeypatch):
    """clarify 中断 -> resume 恢复 -> 新计划 -> plan_review -> approve 全链。

    终审 M5：恢复 clarify→plan_review 链式覆盖（一期改写时丢失）。
    mock planner：首轮 clarification（挂起），恢复轮返回两步计划（触发
    plan_review 审批门），批准后收敛。
    """
    import core.orchestrator.langgraph_engine as lge_mod
    from core.orchestrator.langgraph_engine import invoke_langgraph, resume_langgraph
    from core.orchestrator.state import PlanStep

    two_step = [
        PlanStep(
            id="p1",
            goal="取上月销售额",
            kind="query",
            dsl={
                "metrics": [
                    {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                ],
                "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
                "time_filter": {
                    "range_type": "absolute",
                    "absolute": {"start": "2024-05-01", "end": "2024-05-15"},
                },
            },
        ),
        PlanStep(id="p2", goal="综合作答", kind="synthesize", depends_on=["p1"]),
    ]
    calls = iter(["clarify", "plan"])

    def _planner(state):
        if next(calls) == "clarify":
            return state.apply(phase="clarify", clarification="你关注哪个指标？")
        return state.apply(
            plan_steps=two_step, phase="query", answered_by="llm"
        )

    monkeypatch.setattr(lge_mod, "planner_node", _planner)

    state = _minimal_state(user_query="什么是销售额？", autonomy_level="L2")
    state2, pending = invoke_langgraph(state, thread_id="u1:s1", observer=None)
    assert pending is not None and pending["kind"] == "clarify"
    assert state2.phase == "clarify"

    resumed, pending2 = resume_langgraph(
        state2,
        {"kind": "clarify", "resume_value": "按 2024-06 口径"},
        thread_id="u1:s1",
        observer=None,
    )
    # 恢复后产出两步计划 => 必然再遇 plan_review 审批门（终审 M5 链式实锤）
    assert pending2 is not None and pending2["kind"] == "plan_review"
    approved, pending3 = resume_langgraph(
        resumed,
        {"kind": "plan_review", "resume_value": {"action": "approve", "instruction": None}},
        thread_id="u1:s1",
        observer=None,
    )
    assert pending3 is None
    assert approved.phase == "done"
```

- [ ] **Step 2: 跑测试**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_langgraph_parity.py::test_clarify_interrupt_and_resume_roundtrip -v`
Expected: PASS（一期已修好挂起与路由，此任务是补断言；若 FAIL 按 dev-debugging 找根因，严禁改断言凑输出）

- [ ] **Step 3: Commit**

```bash
git add tests/test_langgraph_parity.py
git commit -m "test(parity): clarify-resume→plan_review→approve链式场景覆盖（二期M5）"
```

---

### Task 4: 别名收编语义目录（FieldMeta.aliases 单一事实源）

**Files:**
- Modify: `semantic/catalog.py`（`FieldMeta` 加 `aliases`；`COLUMNS` 各字段登记别名）
- Modify: `semantic/catalog_loader.py:192`（FieldMeta 构造支持 aliases 覆写）
- Modify: `core/orchestrator/intent.py`（删 `_METRIC_TERMS`/`DIMENSION_TERMS` 字面词表，改为从 `COLUMNS` 动态构建）
- Modify: `config/semantic.json`（不写 aliases 键——回退内置默认，与 label 同语义；本任务验证回退）
- Test: `tests/test_intent.py`、`tests/test_catalog_loader.py`

**Interfaces:**
- Produces: `FieldMeta.aliases: tuple[str, ...] = ()`；`intent.py` 内部函数 `_build_metric_terms() -> tuple[tuple[str, str], ...]` 与 `_build_dimension_terms() -> dict[str, str]`（后续任务不直接消费，词表消费仅在 intent 内部）。

- [ ] **Step 1: 写失败测试**

`tests/test_intent.py` 追加：

```python
def test_terms_built_from_catalog_aliases():
    """意图词表从语义目录 FieldMeta.aliases 动态构建（单一事实源）。"""
    from semantic.catalog import COLUMNS

    assert COLUMNS["order_amount"].aliases  # 内置默认已登记
    assert "gmv" in COLUMNS["order_amount"].aliases
    assert COLUMNS["province"].aliases  # 维度字段已登记
    # 长词优先仍成立
    profile = classify_intent("5月退款金额是多少")
    assert profile.anchor_fields == ("refund_amount",)
```

`tests/test_catalog_loader.py` 追加（复用文件内既有 `_overlay` helper 与 `conn` fixture）：

```python
def test_loader_aliases_fallback_and_override(conn, tmp_path):
    """aliases：无覆写键回退内置默认；有覆写键则采用 json 值（终审 RF#1）。"""
    from semantic.catalog import COLUMNS

    assert COLUMNS["order_amount"].aliases  # 前置：内置默认已登记

    # 无 aliases 覆写键 => 回退内置默认（向后兼容旧 semantic.json）
    cat = build_catalog(conn=conn, overlay_path=_overlay(tmp_path))
    assert cat.columns["order_amount"].aliases == COLUMNS["order_amount"].aliases

    # 显式覆写 aliases => 采用 json 值
    p = _overlay(
        tmp_path,
        extra_fields={
            "order_amount": {
                "table": "fact_orders",
                "column": "order_amount",
                "dtype": "float",
                "label": "订单金额",
                "aliases": ["gmv", "自定义别名"],
            }
        },
    )
    cat2 = build_catalog(conn=conn, overlay_path=p)
    assert cat2.columns["order_amount"].aliases == ("gmv", "自定义别名")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_intent.py::test_terms_built_from_catalog_aliases -v`
Expected: FAIL（`FieldMeta` 无 `aliases` 属性）

- [ ] **Step 3: 最小实现**

3a. `semantic/catalog.py` 的 `FieldMeta` 加字段：

```python
@dataclass(frozen=True)
class FieldMeta:
    """字段元数据：物理表 + 列名 + 类型（dtype 用于字面量安全转义）。

    label：中文语义标签（如「订单金额」），供 Web 侧栏展示与 Planner
    提示词注入；None 时消费方回退物理列名，保持旧契约逐字不变。
    aliases：业务别名（意图分类词源，二期收编）——新字段登记时别名随
    登记自动进入意图分类器，单一事实源。
    """

    table: str
    column: str
    dtype: str  # 用于字面量安全转义：str / int / float / bool / timestamp
    label: str | None = None
    aliases: tuple[str, ...] = ()
```

3b. `COLUMNS` 各字段登记别名（从一期 `intent.py` 词表全量迁移，逐字对齐）：

```python
    "order_id": FieldMeta("fact_orders", "order_id", "int", label="订单ID", aliases=("订单量", "订单数")),
    "user_id": FieldMeta("fact_orders", "user_id", "int", label="用户ID", aliases=("买家数", "用户数")),
    "order_amount": FieldMeta("fact_orders", "order_amount", "float", label="订单金额", aliases=("gmv", "销售额", "订单金额", "成交金额")),
    "discount_amount": FieldMeta("fact_orders", "discount_amount", "float", label="优惠金额", aliases=("优惠金额", "折扣金额")),
    "refund_amount": FieldMeta("fact_refunds", "refund_amount", "float", label="退款金额", aliases=("退款金额",)),
    "province": FieldMeta(..., aliases=("省份", "省", "地区", "地域", "区域", "大区", "城市", "广东", "浙江", "江苏", "北京", "上海", "四川", "湖北", "山东")),
    "category": FieldMeta(..., aliases=("品类", "类目", "品类结构")),
    "brand": FieldMeta(..., aliases=("品牌",)),
    "shop_name": FieldMeta(..., aliases=("店铺", "门店")),
```

（`...` 处保留各字段现有 table/column/dtype/label 参数不变；`province/category/brand/shop_name` 的英文字段名一词在一期词表中也存在——将其并入 aliases 首位：`"province"` 字段 aliases 以 `("province",)` 开头，`category/brand/shop_name` 同理，确保英文字面匹配零漂移。其余字段不加 aliases。）

3c. `semantic/catalog_loader.py:192` 的构造行替换为：

```python
        aliases_raw = spec.get("aliases")
        if aliases_raw is None:
            default_meta = _DEFAULT_COLUMNS.get(str(name))
            aliases = default_meta.aliases if default_meta is not None else ()
        else:
            aliases = tuple(str(a) for a in aliases_raw)
        columns[str(name)] = FieldMeta(table, column, dtype, label=label, aliases=aliases)
```

3d. `core/orchestrator/intent.py` 删除 `_METRIC_TERMS` 与 `DIMENSION_TERMS` 字面词表，新增：

```python
def _build_metric_terms() -> tuple[tuple[str, str], ...]:
    """从语义目录 FieldMeta.aliases 构建指标词表（数值字段 = int/float）。"""
    from semantic.catalog import COLUMNS

    terms: list[tuple[str, str]] = []
    for name, meta in COLUMNS.items():
        if meta.dtype in ("int", "float"):
            terms.extend((alias, name) for alias in meta.aliases)
    return tuple(terms)


def _build_dimension_terms() -> dict[str, str]:
    """从语义目录 FieldMeta.aliases 构建维度词表（非数值字段）。"""
    from semantic.catalog import COLUMNS

    terms: dict[str, str] = {}
    for name, meta in COLUMNS.items():
        if meta.dtype not in ("int", "float"):
            for alias in meta.aliases:
                terms.setdefault(alias, name)
    return terms
```

`classify_intent` 内两行取词表改为：

```python
    metrics = _extract_anchors(query, _build_metric_terms())
    dims = _extract_anchors(query, _build_dimension_terms())
```

`DIMENSION_TERMS` 的其他消费方（`nodes.py` 的 `from core.orchestrator.intent import DIMENSION_TERMS`、`capability_catalog_lines` 内 `set(DIMENSION_TERMS.values())`）处理：
- `nodes.py` 的 import 与 `_explicit_dimensions` 改为 `from core.orchestrator.intent import dimension_terms`，其中 intent 提供函数：

```python
def dimension_terms() -> dict[str, str]:
    """维度词表（动态构建；nodes 的显式维度识别消费）。"""
    return _build_dimension_terms()
```

`_explicit_dimensions` 内 `for term, field in DIMENSION_TERMS.items():` 改为 `for term, field in dimension_terms().items():`。
- `capability_catalog_lines` 内 `dim_fields = sorted(set(DRILLDOWN_DIM_FIELDS) | set(DIMENSION_TERMS.values()))` 改为 `sorted(set(DRILLDOWN_DIM_FIELDS) | set(_build_dimension_terms().values()))`。

- [ ] **Step 4: 跑等价性红线与全量**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_intent.py tests/test_catalog_loader.py tests/test_orchestrator.py tests/test_profiling.py -q`
Expected: PASS（一期全部 intent/编排用例零漂移 = 等价性红线；RF#1 回退与覆写断言通过）

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m eval.intent_eval`
Expected: 8/8 passed

- [ ] **Step 5: Commit**

```bash
git add semantic/catalog.py semantic/catalog_loader.py core/orchestrator/intent.py core/orchestrator/nodes.py tests/test_intent.py tests/test_catalog_loader.py
git commit -m "refactor(semantic): 意图词表收编FieldMeta.aliases单一事实源——intent动态构建+loader覆写回退"
```

---

### Task 5: Grounding 定向重试闭环

**Files:**
- Modify: `core/orchestrator/nodes.py`（`_synthesize_with_llm` 加 `extra_instruction` 参数；`synthesize_node` LLM 分支重构为重试逻辑）
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `grounding_review` / `collect_allowed_values`（一期签名不变）、`_synthesize_with_llm(state, material)`（扩为三参）。
- Produces: `_synthesize_with_llm(state, material, extra_instruction=None) -> str | None`；Grounding 闭环行为（重试 1 次 → 降级）。

- [ ] **Step 1: 写失败测试**

`tests/test_orchestrator.py` 追加两个 e2e（重试正路径 + 失败降级路径）：

```python
def test_grounding_retry_success_keeps_llm_report(tmp_path, monkeypatch):
    """首版报告超阈值 -> 定向重写 grounded => 保留 LLM 报告（终审 RF#2）。"""
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    plan_payload = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取GMV",
                "kind": "query",
                "depends_on": [],
                "dsl": {
                    "metrics": [
                        {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                    ],
                    "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
                    "time_filter": {
                        "range_type": "absolute",
                        "absolute": {"start": "2024-05-01", "end": "2024-05-15"},
                    },
                },
            },
            {"id": "s2", "goal": "综合作答", "kind": "synthesize", "depends_on": ["s1"], "dsl": None, "code": None},
        ],
    }
    # planner 首轮给计划；synthesize 层首轮编造、重写 grounded
    plan_seen = iter([plan_payload])
    synth_reports = iter(["编造报告：转化率高达 42.5%、留存 88.6%、复购 77.3%、曝光 99.2%。", "GMV 为 115.69 万元。"])

    def _fake_llm_json(llm, system, user):
        return next(plan_seen)

    def _fake_synth(state, material, extra_instruction=None):
        assert extra_instruction is not None  # 重写必须带修正指令
        return next(synth_reports)

    monkeypatch.setattr(nodes, "_llm_json", _fake_llm_json)
    monkeypatch.setattr(nodes, "_synthesize_with_llm", _fake_synth)
    events: list[dict] = []
    trace = run_agent("2024年5月GMV是多少", session_id="retryq", on_event=events.append)
    assert trace.phase == "done"
    assert "115.69" in trace.report
    assert "42.5" not in trace.report
    assert "数据溯源提示" not in trace.report  # 重写后 grounded，不标注


def test_grounding_retry_exhausted_falls_back_to_deterministic(tmp_path, monkeypatch):
    """重试后仍超阈值 => 放弃 LLM 报告，降级确定性渲染（终审 RF#2 失败路径）。"""
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    fabricated = "编造报告：转化率高达 42.5%、留存 88.6%、复购 77.3%、曝光 99.2%。"
    plan_payload = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取GMV",
                "kind": "query",
                "depends_on": [],
                "dsl": {
                    "metrics": [
                        {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                    ],
                    "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
                    "time_filter": {
                        "range_type": "absolute",
                        "absolute": {"start": "2024-05-01", "end": "2024-05-15"},
                    },
                },
            },
            {"id": "s2", "goal": "综合作答", "kind": "synthesize", "depends_on": ["s1"], "dsl": None, "code": None},
        ],
    }
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: plan_payload)
    synth_calls = iter([fabricated, fabricated])

    def _fake_synth(state, material, extra_instruction=None):
        return next(synth_calls)

    monkeypatch.setattr(nodes, "_synthesize_with_llm", _fake_synth)
    trace = run_agent("2024年5月GMV是多少", session_id="retryfail")
    assert trace.phase == "done"
    assert "查询答案" in trace.report  # 确定性渲染接管（数据真实）
    assert "转化率" not in trace.report  # 编造叙事被整体放弃
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py::test_grounding_retry_success_keeps_llm_report tests/test_orchestrator.py::test_grounding_retry_exhausted_falls_back_to_deterministic -v`
Expected: FAIL（现行为只标注不重试：报告含"数据溯源提示"且保留编造叙事）

- [ ] **Step 3: 最小实现**

3a. `_synthesize_with_llm` 签名扩为：

```python
def _synthesize_with_llm(
    state: AgentState, material: str, extra_instruction: str | None = None
) -> str | None:
    """LLM 商业分析师综合（审计修复 R1）；失败返回 None 走确定性兜底。

    ``extra_instruction``：Grounding 定向重写的修正指令（二期）——拼在
    user_prompt 末尾，仅约束本次调用。
    """
```

user_prompt 组装处追加：

```python
    if extra_instruction:
        user_prompt += "\n\n" + extra_instruction
```

3b. `synthesize_node` 的 LLM 分支（现有 grounding 块整体替换）：

```python
    # LLM 商业分析师综合（R1）；素材 = 去重后的 summary 产物 + 数据集预览
    llm_report: str | None = None
    if deduped_summaries or state.datasets:
        material = _analysis_material(state, include_trace=bool(state.error_context.errors))
        if material:
            llm_report = _synthesize_with_llm(state, material)
            if llm_report:
                # Grounding 定向重试闭环（二期）：不可溯源超阈值 => 携修正指令
                # 重写 1 次；仍超阈值 => 放弃 LLM 叙事，降级确定性渲染
                # （宁弃叙事不弃真实，终审 RF#2）
                from core.orchestrator.grounding import collect_allowed_values, grounding_review

                allowed = collect_allowed_values(state, workspace)
                ungrounded = grounding_review(llm_report, allowed)
                if len(ungrounded) > 3:
                    events.emit_reflection(
                        "报告数值溯源失败，定向重写",
                        "retry",
                        "不可溯源数值清单喂回 LLM 定向修正（上限 1 次）",
                    )
                    retry_instruction = (
                        "# 数值溯源修正（硬性）\n"
                        "你上一版报告中的以下数值未能对应到真实查询结果，"
                        "严禁保留或再编造：\n- " + "\n- ".join(ungrounded[:10]) + "\n"
                        "重写报告：仅允许引用素材中出现过的数值及其万元/百分比换算；"
                        "素材中没有的数据必须如实写明「未获取到」。"
                    )
                    retry_report = _synthesize_with_llm(
                        state, material, extra_instruction=retry_instruction
                    )
                    retry_ok = False
                    if retry_report:
                        ungrounded = grounding_review(retry_report, allowed)
                        if len(ungrounded) <= 3:
                            llm_report = retry_report
                            retry_ok = True
                    if not retry_ok:
                        logger.warning(
                            "Grounding 重写后仍不可溯源，降级确定性渲染",
                            extra={"error": str(ungrounded[:10])[:400]},
                        )
                        llm_report = None
                elif len(ungrounded):
                    logger.warning(
                        "LLM 报告存在少量不可溯源数值，追加溯源提示",
                        extra={"error": str(ungrounded[:10])[:400]},
                    )
                    llm_report = (
                        llm_report
                        + "\n\n---\n**数据溯源提示**：以下数值未能对应到本次真实查询结果，"
                        "请谨慎采信："
                        + "、".join(ungrounded[:10])
                    )
```

（一期"超阈值追加提示"逻辑并入 `elif len(ungrounded)` 分支——重试后才有的两级行为：1-3 个标注、>3 重试。）

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_grounding.py tests/test_orchestrator.py tests/test_synthesizer_rebuild.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): Grounding定向重试闭环——修正指令重写1次，仍超阈值降级确定性渲染"
```

---

### Task 6: 选项式澄清（后端契约 + 中置信路径）

**Files:**
- Modify: `core/orchestrator/state.py`（`AgentState` 加 `clarification_options`）
- Modify: `core/orchestrator/prompts.py`（`PLANNER_SYSTEM` 澄清纪律补 options 与中置信路径）
- Modify: `core/orchestrator/nodes.py`（`planner_node` 澄清分支双形态规范化）
- Modify: `core/orchestrator/langgraph_engine.py`（`_plan_gate` clarify interrupt payload 加 options）
- Modify: `core/orchestrator/agent.py:126-128`（run_agent 收尾 EVENT_HITL_REQUEST clarify 分支加 options）
- Modify: `web/runs.py`（`set_paused` clarify payload 加 options）
- Test: `tests/test_orchestrator.py`、`tests/test_web_runs.py`、`tests/test_langgraph_parity.py`

**Interfaces:**
- Consumes: 无新依赖。
- Produces: `AgentState.clarification_options: list[str]`；clarify interrupt / `hitl_request` payload 携带 `options`（前端 `elHitl` 已消费字符串数组，零改动）。

- [ ] **Step 1: 写失败测试**

`tests/test_orchestrator.py` 追加：

```python
def test_planner_clarification_object_form_with_options(monkeypatch):
    """clarification 对象形态（question+options）规范化；纯字符串旧契约兼容。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())

    def _obj_form(llm, system, user):
        return {
            "clarification": {
                "question": "销售额按哪个口径？",
                "options": ["含退款的净销售额", "不含退款的总销售额", ""],  # 空串被过滤
            },
            "steps": [],
        }

    monkeypatch.setattr(nodes, "_llm_json", _obj_form)
    state = nodes.planner_node(AgentState(user_query="销售额是多少"))
    assert state.phase == "clarify"
    assert state.clarification == "销售额按哪个口径？"
    assert state.clarification_options == ["含退款的净销售额", "不含退款的总销售额"]

    # 纯字符串旧契约：options 为空
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: {"clarification": "哪个指标？", "steps": []})
    state2 = nodes.planner_node(AgentState(user_query="销售额是多少"))
    assert state2.clarification == "哪个指标？"
    assert state2.clarification_options == []

    # 对象缺 question => 回退默认问题文本，options 保留（终审 RF#3）
    monkeypatch.setattr(
        nodes,
        "_llm_json",
        lambda llm, system, user: {"clarification": {"options": ["口径A"]}, "steps": []},
    )
    state3 = nodes.planner_node(AgentState(user_query="销售额是多少"))
    assert state3.phase == "clarify"
    assert state3.clarification
    assert state3.clarification_options == ["口径A"]


def test_hitl_event_carries_options(tmp_path, monkeypatch, planner_clarify_then_plan):
    """hitl_request 事件携带 options（前端 elHitl 消费，终审 RF#4 过滤后为空则无碍）。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    events: list[dict] = []
    run_agent("GMV呢？", session_id="optq", on_event=events.append)
```

> 注：`planner_clarify_then_plan` fixture（一期 conftest）需同步扩展——其 clarification 响应改为对象形态 `{"clarification": {"question": "你关注的指标与时间范围是什么？", "options": ["2024年5月GMV", "2024年5月订单量"]}, "steps": []}`，本测试追加断言：

```python
    hitl = [e for e in events if e["event"] == "hitl_request"]
    assert hitl and hitl[0]["payload"]["hitl"].get("options") == [
        "2024年5月GMV",
        "2024年5月订单量",
    ]
```

`tests/test_web_runs.py` 的 `test_hitl_pause_resume_seq_continuous` 追加断言（事件缓冲中首条 hitl_request）：

```python
    kinds_before = kinds  # 既有断言行之后
```

> 注：web_runs 的 options 断言并入 Task 6 Step 4 全量——runs.py payload 与 agent.py 事件走同一 state 字段，单点验证（事件测试）即可覆盖，web_runs 只验证不回归（既有断言全过）。

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py::test_planner_clarification_object_form_with_options tests/test_orchestrator.py::test_hitl_event_carries_options -v`
Expected: FAIL（`clarification_options` 属性不存在 / payload 无 options）

- [ ] **Step 3: 最小实现**

3a. `state.py` 的 `clarification` 字段后追加：

```python
    clarification_options: list[str] = Field(
        default_factory=list, description="选项式澄清候选（前端 pill 按钮，点击即答复）"
    )
```

3b. `planner_node` 澄清分支（`if payload.get("clarification"):` 块）替换为：

```python
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
```

3c. `_plan_gate` 的 clarify interrupt payload 替换为：

```python
        resume_value = interrupt(
            {
                "kind": "clarify",
                "clarification": state.clarification,
                "options": list(state.clarification_options),
            }
        )
```

3d. `agent.py` run_agent 收尾 clarify 分支的 hitl dict 替换为：

```python
                        "hitl": {
                            "question": final.clarification or "请补充分析需求",
                            "options": list(final.clarification_options),
                        },
```

3e. `web/runs.py` `set_paused` clarify payload 的 hitl dict 替换为：

```python
                    "hitl": {
                        "question": state.clarification or "请补充分析需求",
                        "options": list(state.clarification_options),
                        "resume_token": token,
                    },
```

3f. `conftest.py` 的 `planner_clarify_then_plan` fixture 首响应改为对象形态：

```python
            {
                "clarification": {
                    "question": "你关注的指标与时间范围是什么？",
                    "options": ["2024年5月GMV", "2024年5月订单量"],
                },
                "steps": [],
            },
```

3g. `PLANNER_SYSTEM` 的"澄清判定纪律"小节末尾追加：

```
- 输出 clarification 时尽量同时给出 2-4 个候选口径选项：clarification 用对象
  形态 {"question": "...", "options": ["候选1", "候选2"]}，每个选项是一句可
  直接作为答复发送的完整表述；用户可能无候选时 options 给空数组；
- 中置信路径：问题可理解但存在口径歧义（如"销售额"含/不含退款、多指标
  同名）时，优先选项澄清而非拒答——把选择权交给用户，不替用户做主。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest tests/test_orchestrator.py tests/test_web_runs.py tests/test_web_api.py tests/test_agent_stream.py tests/test_agent_e2e.py tests/test_langgraph_parity.py -q`
Expected: PASS（一期 HITL 测试经 fixture 扩展后不回归）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/state.py core/orchestrator/prompts.py core/orchestrator/nodes.py core/orchestrator/langgraph_engine.py core/orchestrator/agent.py web/runs.py tests/conftest.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): 选项式澄清——clarification对象形态规范化+options全链透传+中置信路径"
```

---

### Task 7: 评测挂 CI + 全量回归

**Files:**
- Modify: `.github/workflows/ci.yml`（`test` 与 `nightly` job）
- Test: 无新测试（既有 eval.intent_eval 即验收）

**Interfaces:**
- Consumes: `eval/intent_eval.py`（一期产出，`python -m eval.intent_eval`，退出码 0/1）。

- [ ] **Step 1: 修改 ci.yml**

`test` job 的 `Run golden eval (agent heuristic)` 步骤后追加：

```yaml
      - name: Run intent routing eval
        run: python -m eval.intent_eval
```

`nightly` job 的 `Golden eval (agent heuristic)` 步骤后追加同样的步骤。

- [ ] **Step 2: 本地预演 CI 步骤**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m eval.intent_eval`
Expected: `8/8 passed`，退出码 0

- [ ] **Step 3: 全量回归**

Run: `C:/Users/cwt15/.conda/envs/futurebi/python.exe -m black --check . && C:/Users/cwt15/.conda/envs/futurebi/python.exe -m ruff check . && C:/Users/cwt15/.conda/envs/futurebi/python.exe -m pytest -q`
Expected: 全绿（black/ruff 报差异时先 `black <file>` / `ruff check --fix` 修复再复跑）

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: 意图路由评测挂入test与nightly（答非所问率防回归）"
```

---

## 红线自查记录（编写者已执行）

1. **规格覆盖**：§2.1→Task 1/2/3、§2.2→Task 4、§2.3→Task 5、§2.4→Task 6、§2.5→Task 7、契约变更→Task 4/6，无缺口；
2. **占位符**：Task 4 Step 1 与 Task 5 Step 1 中的占位说明均已标注"写入时删除/不保留"，Task 4 Step 1 的 loader 测试明确指示执行者读现有入口函数后落笔——这是"执行者需读实现现状"的显式指令而非 TBD；
3. **类型一致性**：`_synthesize_with_llm(state, material, extra_instruction=None)`、`FieldMeta.aliases: tuple[str, ...] = ()`、`clarification_options: list[str]`、`_build_metric_terms/_build_dimension_terms/dimension_terms` 各任务引用一致；
4. **Review Focus 落实**：RF1→Task 4（loader 回退测试）、RF2→Task 5（重试失败降级 e2e）、RF3→Task 6（缺 question 回退）、RF4→Task 6（空串过滤）、RF5→Task 4（一期语料全量复跑等价性红线）。

## 移交执行

- 推荐：**本会话逐任务**（Task 4/5/6 与一期产出的接口强耦合，子代理上下文重建成本高；7 任务、每任务自带测试周期）。
- 执行交 dev-executing-plans；测试纪律见 dev-tdd。
