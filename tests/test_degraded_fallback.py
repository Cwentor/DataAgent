"""分层降级兜底（2026-09）：UNKNOWN 二次判定与弱解析的回归锚点。

- 指标锚存在但模板不可拆 => 弱解析（plan/clarify），不再直接拒答；
- 无指标锚/跨表混合锚 => NOT_EXIST 拒答（宁拒不错）；
- 筛选条件仅来自显式时间/用户确认/枚举值精确命中，严禁猜测。

锚点验证记录（以 classify_intent 真实行为为准，详见 .plan-work 执行报告）：
- 复合问句（指标锚+维度限定共存）判 UNKNOWN 且 ``anchor_fields`` 为空
  （intent.py 契约），锚点由 ``_degraded_parse`` 内按同一词表确定性重提取；
- "西藏"不在维度词表（会被判 METRIC_SCALAR、无维度锚），澄清用例改用
  "西藏省的GMV"（"省"命中 province 锚、"西藏"未命中枚举）达成同一断言意图；
- ``parse_explicit_time_window`` 返回 [start, end) 排他上界
  （"2024年3月1日到3月31日" => end=2024-04-01）；
- "GMV趋势"被 classify_intent 判为 METRIC_SCALAR 而非 UNKNOWN（不影响本
  函数——``_degraded_parse`` 只吃锚点画像不判 intent 字段），断言依然成立。
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
    """ "各省份的GMV" => 分组用法（各+别名）=> dimensions 分组、无筛选。"""
    query = "各省份的GMV"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "plan"
    assert payload[0].dsl["dimensions"] == [{"field": "province"}]
    assert all(f["field"] != "province" for f in payload[0].dsl["filters"])


def test_enum_miss_asks_for_clarification():
    """取值未命中枚举（数仓没有"西藏"）=> clarify 留白确认，严禁猜条件。

    措辞按 classify_intent 真实锚点修正："西藏的GMV"会因"西藏"不在维度
    词表而判 METRIC_SCALAR（无维度锚）；"西藏省的GMV"中"省"命中 province
    锚、取值"西藏"未命中枚举，与原意（未命中留白澄清）一致。
    """
    query = "西藏省的GMV"
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
    """显式时间解析优先于缺省 2024-05 锚。

    parse_explicit_time_window 返回 [start, end) 排他上界："2024年3月1日
    到3月31日"解析为 3 月整月窗口，end=2024-04-01。
    """
    query = "海南省的GMV 2024年3月1日到3月31日"
    profile = classify_intent(query)
    mode, payload = _degraded_parse(query, profile, ENUMS)
    assert mode == "plan"
    window = payload[0].dsl["time_filter"]["absolute"]
    assert window["start"] == "2024-03-01" and window["end"].startswith("2024-04-01")


def test_default_window_and_pay_status_scope():
    """无显式时间 => 缺省 2024-05 锚；fact_orders 锚带 pay_status 口径。"""
    mode, payload = _degraded_parse("海南省的GMV", classify_intent("海南省的GMV"), ENUMS)
    assert mode == "plan"
    dsl = payload[0].dsl
    assert dsl["time_filter"]["absolute"]["start"] == "2024-05-01"
    assert {"field": "pay_status", "operator": "eq", "value": "SUCCESS"} in dsl["filters"]


# --------------------------------------------------------------------------- #
# planner 集成：三分流与降级可见化
# --------------------------------------------------------------------------- #
class _NoLLM:
    """resolve_default_client 返回 None 的替身。"""


def test_planner_unknown_with_metric_anchor_yields_degraded_plan(monkeypatch, tmp_path):
    """ "北京的GMV"（LLM 不可用）=> 弱解析计划，answered_by=degraded_confirmed。

    措辞按真实数仓 profiling 修正：mock 数仓 province 枚举为
    北京/上海/四川/山东/广东/江苏/浙江/湖北（无"海南"），计划原文
    "海南省的GMV"会因枚举未命中走 clarify 而非 plan——改用真实枚举
    唯一命中的"北京的GMV"，与原断言意图（弱解析计划+枚举精确筛选）一致。
    """
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    state = AgentState(session_id="dg", turn_id="t1", trace_id="tr1", user_query="北京的GMV")
    out = orch.planner_node(state)
    assert out.answered_by == "degraded_confirmed"
    assert [s.kind for s in out.plan_steps] == ["query", "synthesize"]
    assert out.plan_steps[0].dsl is not None
    assert any(
        f["field"] == "province" and f["value"] == "北京" for f in out.plan_steps[0].dsl["filters"]
    )


def test_planner_unknown_without_anchor_blocks(monkeypatch, tmp_path):
    """无锚点 UNKNOWN => 拒答（原语义保留）。"""
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    state = AgentState(session_id="dg2", turn_id="t1", trace_id="tr1", user_query="随便看看")
    out = orch.planner_node(state)
    assert out.answered_by == "blocked"
    assert out.blocked_reason


def test_planner_clarify_round_limit_blocks_second_round(monkeypatch, tmp_path):
    """二轮澄清仍歧义 => 拒答（防循环）。

    措辞同 Task 3："西藏的GMV"会被判 METRIC_SCALAR（无维度锚），澄清用例
    改用"西藏省的GMV"（"省"命中 province 锚、"西藏"未命中枚举）。
    """
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    state = AgentState(
        session_id="dg3",
        turn_id="t1",
        trace_id="tr1",
        user_query="西藏省的GMV",
        clarification_rounds=1,  # 已澄清过一轮
    )
    out = orch.planner_node(state)
    assert out.answered_by == "blocked"
    assert "澄清" in out.blocked_reason or "确认" in out.blocked_reason


def test_planner_clarify_first_round_emits_options(monkeypatch, tmp_path):
    """首轮多候选 => clarification + options（交 _plan_gate 挂起）。

    措辞同 Task 3："西藏省的GMV"（枚举未命中）触发留白澄清。
    """
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    state = AgentState(session_id="dg4", turn_id="t1", trace_id="tr1", user_query="西藏省的GMV")
    out = orch.planner_node(state)
    assert out.phase == "clarify"
    assert out.clarification
    assert out.clarification_options
    assert out.clarification_rounds == 1


# --------------------------------------------------------------------------- #
# 降级水印：degraded_confirmed 与 heuristic 文案区分
# --------------------------------------------------------------------------- #
def test_degradation_banner_distinguishes_confirmed():
    """degraded_confirmed 水印明示"条件经人工确认"；heuristic 保持原文案。"""
    from core.orchestrator.nodes import _degradation_banner
    from core.orchestrator.state import AgentState

    state = AgentState(
        session_id="wm",
        turn_id="t1",
        trace_id="tr1",
        user_query="x",
        answered_by="degraded_confirmed",
    )
    banner = _degradation_banner(state)
    assert "降级模式" in banner and "人工确认" in banner
    heuristic_state = AgentState(
        session_id="wm2",
        turn_id="t1",
        trace_id="tr2",
        user_query="x",
        answered_by="heuristic",
    )
    assert "离线兜底引擎" in _degradation_banner(heuristic_state)


def test_degraded_plan_reject_emits_event(monkeypatch):
    """降级计划被用户拒绝 => 发射 degrade/rejected 事件（可观测埋点）。

    实际签名 ``_plan_gate(state)`` 单参数（中断经 maybe_interrupt/interrupt
    完成），以桩替身模拟用户在 plan_review 审批卡上选择"拒绝"。
    """
    from core.orchestrator import events as orch_events
    from core.orchestrator import langgraph_engine
    from core.orchestrator.langgraph_engine import _plan_gate
    from core.orchestrator.state import AgentState, PlanStep

    seen: list[tuple[str, dict]] = []
    orig = orch_events.emit_event

    def spy(event: str, payload: dict) -> None:
        seen.append((event, payload))
        orig(event, payload)

    # langgraph_engine 以 `from core.orchestrator import events` 引用，
    # patch events 模块属性即可覆盖其调用点
    monkeypatch.setattr(orch_events, "emit_event", spy)
    monkeypatch.setattr(
        langgraph_engine,
        "maybe_interrupt",
        lambda state, payload, *, trigger: {"action": "reject", "instruction": None},
    )
    state = AgentState(
        session_id="rj",
        turn_id="t1",
        trace_id="tr1",
        user_query="海南省的GMV",
        answered_by="degraded_confirmed",
        plan_steps=[
            PlanStep(id="s1", goal="按确认条件查询GMV", kind="query"),
            PlanStep(id="s2", goal="汇总作答", kind="synthesize", depends_on=["s1"]),
        ],
        phase="query",
    )
    out = _plan_gate(state)
    assert out.phase == "done"
    assert any(e == "degrade" and p.get("outcome") == "rejected" for e, p in seen)


# --------------------------------------------------------------------------- #
# 端到端：LLM 不可用全链路（弱解析 → 审批 → 契约校验 → 水印报告）
# --------------------------------------------------------------------------- #
def test_e2e_degraded_compound_query_with_approval(monkeypatch, tmp_path):
    """ "北京的GMV"（LLM 不可用）→ 弱解析计划 → 契约校验 → 批准置位 → 水印。

    措辞按真实数仓 profiling 修正：province 枚举（上海/北京/四川/山东/广东/
    江苏/浙江/湖北）无"海南"，"海南省的GMV"会走 clarify（枚举未命中）而非
    plan——改用枚举唯一命中的"北京的GMV"，与原断言意图（降级计划 → DSL
    契约校验 → plan_reviewed 置位 → 降级水印）一致。
    """
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    state = orch.planner_node(
        AgentState(session_id="e2e", turn_id="t1", trace_id="tr1", user_query="北京的GMV")
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


def test_e2e_degraded_clarify_then_resume_yields_plan(monkeypatch, tmp_path):
    """双通道闭环："海南省的GMV"（LLM 不可用）→ 留白澄清 → 用户补充"查北京的"
    → 二次 planner 产出降级计划。

    合并语义逐字对齐 _plan_gate clarify 分支（langgraph_engine）：user_query
    追加"（用户补充：…）"、human_reply/clarification 清空、轮次 +1、
    plan_steps 置空回 plan。真实枚举无"海南"故首轮留白澄清（严禁猜测条件）；
    补充后"北京"唯一命中枚举 => plan 分支先于二轮轮次检查，产出降级计划
    而非拒答（防循环护栏只拦截"澄清后仍歧义"的场景）。
    """
    from config import settings
    from core.orchestrator import nodes as orch
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_resolve_llm", lambda: None)
    first = orch.planner_node(
        AgentState(session_id="e2e2", turn_id="t1", trace_id="tr1", user_query="海南省的GMV")
    )
    # 通道一：枚举未命中 => 留白澄清（options 不含任何具体省份值，严禁猜）
    assert first.phase == "clarify"
    assert first.clarification and first.clarification_options
    assert first.clarification_rounds == 1
    assert not any(o in ("北京", "上海") for o in first.clarification_options)
    # 模拟 _plan_gate clarify 恢复：用户在选项外补充真实存在的省份取值
    resumed = first.apply(
        user_query=f"{first.user_query}（用户补充：查北京的）",
        human_reply=None,
        clarification=None,
        clarification_rounds=first.clarification_rounds + 1,
        plan_steps=[],
        phase="plan",
    )
    # 通道二：二次 planner 唯一枚举命中 => 降级计划（非拒答）
    second = orch.planner_node(resumed)
    assert second.answered_by == "degraded_confirmed"
    assert [s.kind for s in second.plan_steps] == ["query", "synthesize"]
    assert any(
        f["field"] == "province" and f["value"] == "北京"
        for f in second.plan_steps[0].dsl["filters"]
    )
    from semantic.dsl_schema import QueryDSL

    QueryDSL.model_validate(second.plan_steps[0].dsl)
