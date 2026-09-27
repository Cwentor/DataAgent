"""编排器单测：图引擎语义 / 状态契约 / HITL 中断恢复 / 端到端确定性路径。

覆盖（对应企业级 Data Agent 需求 §2.A + §4 步骤 3/4）：
- StateGraph：条件路由、HITL 中断与 resume、迭代护栏、缺出边报错；
- AgentState：契约冻结（extra=forbid）、token 预算修剪、错误上下文上限；
- 六节点端到端（确定性兜底路径，真实 mock 数仓 + 沙箱）：
  诊断问题 -> 多步日志 + 沙箱产物 + 报告；澄清门 HITL 两段式。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.orchestrator.agent import run_agent
from core.orchestrator.nodes import MAX_RETRIES
from core.orchestrator.state import AgentState, ToolRecord


def test_agent_state_forbids_extra_fields():
    with pytest.raises(ValidationError):
        AgentState(user_query="x", rogue_field=1)


def test_agent_state_intent_fields_contract():
    """十八期：诚实兜底链路的状态契约（blocked/answered_by/intent）。"""
    state = AgentState(user_query="有多少个省份")
    assert state.blocked_reason is None
    assert state.answered_by == ""
    assert state.intent_type is None
    assert state.intent_anchors == []
    # extra=forbid 不被破坏：未知字段仍拒绝
    with pytest.raises(ValidationError):
        AgentState(user_query="x", rogue_field=1)


def test_error_context_retry_cap():
    state = AgentState(user_query="x")
    for i in range(MAX_RETRIES):
        assert state.error_context.record(f"err {i}") is True
    assert state.error_context.record("final") is False
    assert state.error_context.retries == MAX_RETRIES + 1


def test_tool_history_pruning():
    state = AgentState(user_query="x")
    for _ in range(50):
        state.tool_calls.append(ToolRecord(tool="t", summary="s" * 5000))
    state.prune_tool_history(budget_chars=30_000)
    total = sum(len(r.summary) for r in state.tool_calls)
    assert total <= 30_000 + 5000  # 单条超预算时保留最后一条
    assert len(state.tool_calls) < 50


# --------------------------------------------------------------------------- #
# 六节点端到端（确定性兜底，真实 mock 数仓 + 沙箱）
# --------------------------------------------------------------------------- #
def test_run_agent_diagnostic_e2e(tmp_path, monkeypatch):
    """诊断问题端到端：多步日志 + 数据集 + 沙箱产物 + 报告 + ECharts。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    trace = run_agent(
        "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位", session_id="e2e"
    )
    assert isinstance(trace, dict) is False
    assert trace.phase == "done"
    # 多步日志：至少 取数 + 沙箱 两条成功轨迹
    ok_tools = [s for s in trace.steps if s["ok"]]
    assert any(s["tool"] == "execute_dsl_query" for s in ok_tools)
    assert any(s["tool"] == "run_code" for s in ok_tools)
    # 产物：summary + echarts
    kinds = {a["kind"] for a in trace.artifacts}
    assert "summary" in kinds and "echarts" in kinds
    # 报告含结论与自愈统计行
    assert "分析报告" in trace.report
    assert "执行轨迹" in trace.report


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
            {
                "id": "s1",
                "goal": "取GMV总量",
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
                "goal": "综合作答",
                "kind": "synthesize",
                "depends_on": ["s1"],
                "dsl": None,
                "code": None,
            },
        ],
    }
    responses = iter([{"clarification": "你关注哪个时间段的 GMV？", "steps": []}, plan_payload])
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: next(responses))

    paused = run_agent("GMV呢？", session_id="hitl")
    assert not isinstance(paused, dict)
    assert paused.phase == "clarify" and paused.clarification

    final = run_agent(
        "GMV呢？",
        session_id="hitl",
        resume_state=paused.apply(human_reply="2024 年 5 月按省份的订单金额"),
    )
    assert final.phase == "done"
    assert any(s["ok"] for s in final.steps)


def test_run_agent_factoid_bypasses_clarify_gate(tmp_path, monkeypatch):
    """LLM 在场时事实型短问句直达完成，不再被澄清门打断。

    回归锚点："有多少个省份"此前被确定性字符规则（过短/无指标词即澄清）
    误拦在 Planner 门外；判定权上交 Planner（clarification 契约）后应直达
    规划。钉 _llm_json 返回 None（Planner LLM 失败走启发式兜底），聚焦验证
    clarify_node 的"LLM 在场即放行"分支：全程无 clarify 中断。
    """
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: None)
    trace = run_agent("有多少个省份", session_id="factoid")
    assert trace.phase == "done"
    assert trace.clarification is None


def test_run_agent_simple_query_path(tmp_path, monkeypatch):
    """非诊断问题走单查询路径：取数 + 综合即完成。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    trace = run_agent("2024 年 5 月成功支付订单的 GMV 总额是多少？", session_id="simple")
    assert trace.phase == "done"
    assert any(s["tool"] == "execute_dsl_query" and s["ok"] for s in trace.steps)


def test_synthesize_consumes_datasets_for_pure_query(tmp_path, monkeypatch):
    """纯查询问题（无沙箱 analyze 步骤）报告必须消费取数结果。

    回归锚点：此前 synthesize 只认沙箱 summary 产物，基数/标量问题即使
    取数成功也输出"未能获得有效的分析产物"（用户可见的能力缺口）。
    """
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    trace = run_agent("2024 年 5 月成功支付订单的 GMV 总额是多少？", session_id="pureq")
    assert trace.phase == "done"
    assert "查询结果" in trace.report
    assert "未能获得有效的分析产物" not in trace.report
    # 标量聚合直接给答案行，且数值必须人读化（万元，R1 严禁 raw 字节面值直出）
    assert "查询答案：" in trace.report
    assert "万元" in trace.report
    assert "gmv =" not in trace.report


def test_tool_end_preview_rows_points_at_inputs_dir(tmp_path, monkeypatch):
    """tool_end 审计预览必须读到物化 Parquet（路径 = workspace/inputs/<ref.path>）。

    回归锚点：此前拼成 workspace/<ref.path> 导致 preview_rows 恒为空。
    """
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    events: list[dict] = []
    trace = run_agent(
        "2024 年 5 月成功支付订单的 GMV 总额是多少？", session_id="preview", on_event=events.append
    )
    assert trace.phase == "done"
    ends = [
        e
        for e in events
        if e["event"] == "tool_end" and e["payload"]["tool"]["name"] == "futurebi_dsl_query"
    ]
    assert ends
    for e in ends:
        preview = e["payload"]["tool"]["output"].get("preview_rows")
        assert preview, "预览行不应为空（物化文件在 workspace/inputs/ 下）"
        assert all(len(r) >= 1 for r in preview)


# --------------------------------------------------------------------------- #
# LLM DSL 草稿规范化（宽容接受，严格校验）
# --------------------------------------------------------------------------- #
def test_normalize_dsl_draft_repairs_common_llm_typos():
    """裸字符串维度 / time_range 笔误 / ge-le 操作符自动纠正为契约形态。"""
    from core.orchestrator.nodes import _normalize_dsl_draft

    d = _normalize_dsl_draft(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": ["province", {"field": "category"}],
            "time_range": {
                "range_type": "absolute",
                "absolute": {"start": "2024-05-01", "end": "2024-05-08"},
            },
            "filters": [
                {"field": "pay_status", "operator": "eq", "value": "SUCCESS"},
                {"field": "order_amount", "operator": "ge", "value": 10},
            ],
        }
    )
    assert d["dimensions"] == [{"field": "province"}, {"field": "category"}]
    assert "time_range" not in d and "time_filter" in d
    assert [f["operator"] for f in d["filters"]] == ["eq", "gte"]


def test_normalize_dsl_draft_passes_valid_payload_unchanged():
    """合法载荷规范化后语义不变（可继续通过网关契约校验）。"""
    from core.orchestrator.nodes import _normalize_dsl_draft
    from core.retrieval.guardrails import validate_dsl_payload

    d = _normalize_dsl_draft(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "province"}],
            "time_filter": {
                "range_type": "absolute",
                "absolute": {"start": "2024-05-01", "end": "2024-05-08"},
            },
        }
    )
    validate_dsl_payload(d, where="test")  # 不抛即通过契约


def test_planner_prompt_contract_examples_align_with_schema():
    """提示词中的 DSL 示例必须与真实契约对齐（防再次系统性带偏 LLM）。

    回归锚点：dimensions 示例是对象数组、时间字段名是 time_filter、
    操作符白名单不含 like/ge/le。
    """
    from core.orchestrator.prompts import PLANNER_SYSTEM

    assert 'dimensions: [{"field"' in PLANNER_SYSTEM
    assert "严禁写成裸字符串" in PLANNER_SYSTEM
    assert "time_filter" in PLANNER_SYSTEM
    assert '"time_range"' not in PLANNER_SYSTEM
    assert 'operator": "eq|ne|in|gt|gte|lt|lte|between"' in PLANNER_SYSTEM


# --------------------------------------------------------------------------- #
# 重规划自愈上下文（pi-agent-harness 对齐：行动项 3）
# --------------------------------------------------------------------------- #
def test_planner_prompt_injects_error_context():
    """planner_prompt 带 error_context：必须注入失败记录并要求针对性修正。"""
    from core.orchestrator.prompts import planner_prompt

    prompt = planner_prompt(
        "查 GMV", "- gmv (fact_orders.order_amount)", error_context="CompileError: 字段不存在"
    )
    assert "上次失败记录" in prompt
    assert "CompileError: 字段不存在" in prompt
    assert "严禁原样重复上一轮计划" in prompt


def test_planner_prompt_without_error_context_unchanged():
    """planner_prompt 不带 error_context：不出现失败记录小节（首轮规划不变）。"""
    from core.orchestrator.prompts import planner_prompt

    prompt = planner_prompt("查 GMV", "- gmv (fact_orders.order_amount)")
    assert "上次失败记录" not in prompt


def test_planner_node_feeds_error_context_to_llm(monkeypatch):
    """重规划时 planner_node 把最近失败摘要注入 LLM 提示词（断裂点修复实锤）。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState

    captured: dict[str, str] = {}
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())

    def fake_llm_json(llm, system, user):
        captured["user"] = user
        return None  # 走启发式兜底，重点在捕获提示词

    monkeypatch.setattr(nodes, "_llm_json", fake_llm_json)
    state = AgentState(user_query="查 GMV")
    state.error_context.record("CompileError: 字段 nonexistent 不在语义目录")
    nodes.planner_node(state)
    assert "上次失败记录" in captured["user"]
    assert "nonexistent 不在语义目录" in captured["user"]


def test_planner_node_llm_clarification_triggers_hitl(monkeypatch):
    """Planner 判定歧义输出 clarification => phase=clarify（LLM 自主澄清通道）。

    澄清判定权上交 Planner 后（clarify_node 在 LLM 在场时放行），这是
    phase=clarify 的唯一触发源：契约消费链路必须实锤可达 HITL 中断。
    """
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(
        nodes,
        "_llm_json",
        lambda llm, system, user: {
            "clarification": "你关注的指标是 GMV 还是订单量？",
            "steps": [],
        },
    )
    state = nodes.planner_node(AgentState(user_query="表现怎么样？"))
    assert state.phase == "clarify"
    assert "GMV" in (state.clarification or "")


# --------------------------------------------------------------------------- #
# 基数类事实问题兜底（回归锚点："有多少个省份"曾被兜底成 sum(GMV) 答非所问）
# --------------------------------------------------------------------------- #
def test_count_dimension_dsl_matches_factoid_questions():
    """量词 + 单一维度词的基数问句 => count_distinct DSL；指标问句不误判。

    十八期：count_dimension_dsl 收编至 core.orchestrator.intent。
    """
    from core.orchestrator.intent import count_dimension_dsl

    dsl = count_dimension_dsl("有多少个省份")
    assert dsl is not None
    assert dsl["metrics"] == [
        {
            "kind": "aggregate",
            "field": "province",
            "agg": "count_distinct",
            "alias": "province_count",
        }
    ]
    assert count_dimension_dsl("多少家店铺") is not None
    assert count_dimension_dsl("有几个品类") is not None
    # 指标问句 / 多维度 / 无量词：不兜底（LLM 在场时由 Planner 裁决）
    assert count_dimension_dsl("各省GMV多少") is None
    assert count_dimension_dsl("GMV多少") is None
    assert count_dimension_dsl("有多少个省份和品类") is None


def test_fmt_scalar_answer_counts_are_not_wan():
    """单值答案渲染：计数列原样输出，金额列维持万元化。"""
    from core.orchestrator.nodes import _fmt_scalar_answer

    assert _fmt_scalar_answer("province_count", 9) == "9"
    assert _fmt_scalar_answer("gmv", 1156943.73) == "115.69 万元"


def test_run_agent_count_factoid_survives_planner_llm_failure(tmp_path, monkeypatch):
    """LLM 客户端在场但 Planner 调用失败时，基数问题兜底直答省份数。

    复现线上场景：clarify 放行（LLM 在场）-> Planner LLM 失败（_llm_json
    返回 None）-> 启发式兜底。回归锚点：兜底此前一律 _scalar_dsl 取
    sum(gmv)，产出"问省份数、答 115.69 万元 GMV"的离谱报告。
    """
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: None)
    trace = run_agent("有多少个省份", session_id="countq")
    assert trace.phase == "done"
    # mock 数仓 8 个省份：计数直答，且严禁金额化（"0.00 万元"式离谱答案）
    assert "查询答案：8" in trace.report
    assert "万元" not in trace.report


# --------------------------------------------------------------------------- #
# 多轮会话上下文（并行会话改造：同会话追问继承历史语境）
# --------------------------------------------------------------------------- #
def test_planner_prompt_injects_session_history():
    """planner_prompt 带 history_context：注入会话历史小节并要求消解省略指代。"""
    from core.orchestrator.prompts import planner_prompt

    prompt = planner_prompt(
        "那上海呢",
        "- gmv (fact_orders.order_amount)",
        history_context="用户: 2024年5月北京的GMV是多少\n助手: 北京GMV为1.2亿元",
    )
    assert "会话历史" in prompt
    assert "2024年5月北京的GMV是多少" in prompt
    assert "省略指代" in prompt


def test_planner_prompt_without_history_unchanged():
    """planner_prompt 不带 history_context：不出现会话历史小节，提示词逐字不变。

    回归锚点（AGENTS.md 同款契约）：history=None 时与旧单轮契约逐字一致——
    以全等断言兜底（不止小节标记缺席）。
    """
    from core.orchestrator.prompts import planner_prompt

    legacy = planner_prompt("查 GMV", "- gmv (fact_orders.order_amount)")
    explicit_none = planner_prompt(
        "查 GMV", "- gmv (fact_orders.order_amount)", error_context=None, history_context=None
    )
    assert legacy == explicit_none  # 逐字一致
    assert "会话历史" not in legacy
    # 注入顺序契约：history 小节在 error_context 之前（历史是规划语境，
    # 失败记录是修正指令，顺序颠倒会改变旧 error_context 用例的提示词）
    both = planner_prompt(
        "查 GMV", "- gmv", error_context="CompileError: x", history_context="用户: a"
    )
    assert both.index("会话历史") < both.index("上次失败记录")


def test_planner_node_feeds_history_to_llm(monkeypatch):
    """planner_node 把 state.history_digest 注入 LLM 提示词（多轮语境实锤）。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState

    captured: dict[str, str] = {}
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())

    def fake_llm_json(llm, system, user):
        captured["user"] = user
        return None

    monkeypatch.setattr(nodes, "_llm_json", fake_llm_json)
    state = AgentState(user_query="那上海呢", history_digest="用户: 2024年5月北京的GMV是多少")
    nodes.planner_node(state)
    assert "会话历史" in captured["user"]
    assert "2024年5月北京的GMV是多少" in captured["user"]


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


def test_run_agent_accepts_history_digest(tmp_path, monkeypatch):
    """run_agent 透传 history_digest 端到端：短句追问带历史可直达 done。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    from core.orchestrator.agent import run_agent

    trace = run_agent(
        "那上海呢", session_id="s-hist", history_digest="用户: 2024年5月北京GMV是多少"
    )
    assert trace.phase == "done"
    assert trace.report


def test_critic_trace_digest_carries_error_history(monkeypatch):
    """LLM 反思的执行轨迹必须包含自愈错误记录（反思层看得见失败历史）。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState, Artifact

    captured: dict[str, str] = {}
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())

    def fake_llm_json(llm, system, user):
        captured["user"] = user
        return {"verdict": "sufficient", "reasons": ["ok"]}

    monkeypatch.setattr(nodes, "_llm_json", fake_llm_json)
    state = AgentState(
        user_query="为什么下滑",
        datasets={"s1": {"path": "x.parquet", "rows": 1, "columns": ["gmv"]}},
        artifacts=[Artifact(kind="summary", name="s2", payload={"summary": {"title": "归因"}})],
    )
    state.error_context.record("沙箱执行失败: NameError")
    nodes.critic_node(state)
    assert "自愈错误记录" in captured["user"]
    assert "NameError" in captured["user"]


# --------------------------------------------------------------------------- #
# 重规划数据集归属（回归锚点：跨轮次错配 -> 假下滑结论）
# --------------------------------------------------------------------------- #
def test_resolve_step_inputs_follows_dependency_not_dict_order():
    """analyze 输入必须取自依赖步骤的本轮产出，而不是 datasets 字典首尾。

    回归锚点：此前按 list(state.datasets)[0]/[-1] 取两期输入，重规划后
    datasets 累积上轮键，首尾会指向上轮遗留数据集，产出"下滑 57.9%"式错配。
    """
    from core.orchestrator.nodes import _resolve_step_inputs
    from core.orchestrator.state import AgentState, PlanStep

    state = AgentState(user_query="分析 5 月 GMV 下滑原因")
    state = state.apply(
        datasets={
            "s1_v0": {"path": "a.parquet", "rows": 8, "columns": ["province", "gmv"]},
            "s1_v1": {"path": "b.parquet", "rows": 8, "columns": ["province", "gmv"]},
        },
        step_outputs={"s1": ["s1_v0", "s1_v1"]},
        plan_steps=[
            PlanStep(id="s1", goal="取两期明细", kind="query"),
            PlanStep(id="s2", goal="归因", kind="analyze", depends_on=["s1"]),
        ],
    )
    assert _resolve_step_inputs(state, state.plan_steps[1]) == ["s1_v0", "s1_v1"]


def test_resolve_step_inputs_ignores_stale_datasets_from_prior_round():
    """重规划后 datasets 含上轮遗留键时，必须只读本轮依赖产出（不复用旧键）。"""
    from core.orchestrator.nodes import _resolve_step_inputs
    from core.orchestrator.state import AgentState, PlanStep

    state = AgentState(user_query="分析 5 月 GMV 下滑原因")
    # 上轮遗留 s1/s2/s3，本轮 s1 覆盖为 s1_v0/s1_v1
    state = state.apply(
        datasets={
            "s1": {"path": "old1.parquet", "rows": 8, "columns": ["province", "gmv"]},
            "s2": {"path": "old2.parquet", "rows": 8, "columns": ["province", "gmv"]},
            "s3": {"path": "old3.parquet", "rows": 8, "columns": ["province", "gmv"]},
            "s1_v0": {"path": "new0.parquet", "rows": 8, "columns": ["province", "gmv"]},
            "s1_v1": {"path": "new1.parquet", "rows": 8, "columns": ["province", "gmv"]},
        },
        step_outputs={"s1": ["s1_v0", "s1_v1"]},
        plan_steps=[
            PlanStep(id="s1", goal="取两期明细", kind="query"),
            PlanStep(id="s2", goal="归因", kind="analyze", depends_on=["s1"]),
        ],
    )
    assert _resolve_step_inputs(state, state.plan_steps[1]) == ["s1_v0", "s1_v1"]


def test_diagnostic_dsl_pair_carries_driver_factor_metrics():
    """诊断兜底两期对必须同时带订单量与买家数因子（反思归因诉求首轮即满足）。"""
    from core.orchestrator.nodes import _diagnostic_dsl_pair

    base, curr = _diagnostic_dsl_pair("分析 5 月第一周比第二周 GMV 下滑原因")
    for dsl in (base, curr):
        aliases = {m["alias"] for m in dsl["metrics"]}
        assert {"gmv", "orders", "buyers"} <= aliases


# --------------------------------------------------------------------------- #
# 反思护栏（回归锚点：不可执行缺口 / 计划无进展 -> 禁止空转重规划）
# --------------------------------------------------------------------------- #
def _critic_state(**overrides):
    """构造带 summary 产物的诊断状态（默认产物列为 gmv/orders/buyers）。"""
    from core.orchestrator.state import AgentState, Artifact

    state = AgentState(user_query="分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位")
    defaults = {
        "datasets": {
            "s1_v0": {
                "path": "a.parquet",
                "rows": 8,
                "columns": ["province", "gmv", "orders", "buyers"],
            }
        },
        "artifacts": [Artifact(kind="summary", name="s2", payload={"summary": {"title": "归因"}})],
    }
    defaults.update(overrides)
    return state.apply(**defaults)


def test_reflector_scope_lists_available_fields():
    """反思提示词必须携带数仓可用字段清单（判定边界的客观依据）。"""
    from core.orchestrator.nodes import _reflector_available_scope

    scope = _reflector_available_scope()
    assert "数仓可用字段清单" in scope
    assert "order_amount" in scope and "province" in scope
    assert "流量" in scope  # 明示清单外概念不得作为重规划理由


def test_guard_rejects_out_of_scope_reasons():
    """反思以数仓未采集的维度（流量/活动/异常单）为由判不充分 => 不可执行。"""
    import core.orchestrator.nodes as nodes

    verdict = {
        "verdict": "insufficient",
        "reasons": ["缺少对订单量、客单价、流量、活动、异常单等影响因素的归因分析"],
    }
    assert nodes._insufficient_is_actionable(verdict, _critic_state()) is False


def test_guard_rejects_when_reasons_all_covered_by_products():
    """理由提到的概念已被本轮产物覆盖 => 属分析深度诉求，不可执行。"""
    import core.orchestrator.nodes as nodes

    verdict = {
        "verdict": "insufficient",
        "reasons": ["最终结果只列出下降幅度较大的地区及指标，未解释具体下滑原因"],
        "missing": ["缺少订单量与买家数的归因分析"],
    }
    assert nodes._insufficient_is_actionable(verdict, _critic_state()) is False


def test_guard_allows_actionable_gap_within_scope():
    """理由指向可用域内尚未取到的数据（如品类）=> 可执行，允许重规划。"""
    import core.orchestrator.nodes as nodes

    verdict = {
        "verdict": "insufficient",
        "reasons": ["未按品类拆分下滑贡献，无法定位品类级主因"],
        "missing": ["取品类维度的两期明细"],
    }
    # 产物列为 province/gmv/orders/buyers，品类字段未取到 => 可执行
    assert nodes._insufficient_is_actionable(verdict, _critic_state()) is True


def test_guard_rejects_when_replan_makes_no_progress():
    """产物指纹与上次重规划相同 => 重规划无进展，直接综合（防空转）。"""
    import core.orchestrator.nodes as nodes

    state = _critic_state()
    state = state.apply(last_replan_fingerprint=nodes._artifact_fingerprint(state))
    verdict = {
        "verdict": "insufficient",
        "reasons": ["未按品类拆分下滑贡献"],
        "missing": ["取品类维度明细"],
    }
    assert nodes._insufficient_is_actionable(verdict, state) is False


def test_critic_guard_converts_unsatisfiable_replan_to_synthesize(monkeypatch):
    """LLM 反思判定不充分但缺口不可执行时，critic 直接转综合（不空烧重试）。"""
    import core.orchestrator.nodes as nodes

    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(
        nodes,
        "_llm_json",
        lambda llm, system, user: {
            "verdict": "insufficient",
            "reasons": ["缺少对订单量、客单价、流量、活动、异常单等影响因素的归因分析"],
        },
    )
    out = nodes.critic_node(_critic_state())
    assert out.phase == "synthesize"
    assert out.error_context.retries == 0  # 未消耗重试额度（非空转重规划）


def test_critic_replans_and_records_progress_fingerprint(monkeypatch):
    """缺口可执行时照常重规划，并记录本轮产物指纹供下轮无进展判定。"""
    import core.orchestrator.nodes as nodes

    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(
        nodes,
        "_llm_json",
        lambda llm, system, user: {
            "verdict": "insufficient",
            "reasons": ["未按品类拆分下滑贡献"],
            "missing": ["取品类维度明细"],
        },
    )
    state = _critic_state()
    out = nodes.critic_node(state)
    assert out.phase == "plan"
    assert out.error_context.retries == 1
    assert out.last_replan_fingerprint == nodes._artifact_fingerprint(state)


# --------------------------------------------------------------------------- #
# 无数据诚实守卫（2026-09 审计修复：编造时段严禁产出归因报告）
# --------------------------------------------------------------------------- #
def test_parse_explicit_time_window():
    """显式年份/月份解析：年月 / 整年；无年份月份返回 None 由调用方锚定。"""
    from agent.time_utils import parse_explicit_time_window

    assert parse_explicit_time_window("分析一下 2030 年 5 月第二周比第三周 GMV 下滑") == (
        "2030-05-01",
        "2030-06-01",
    )
    assert parse_explicit_time_window("2024年GMV是多少") == ("2024-01-01", "2025-01-01")
    assert parse_explicit_time_window("12月GMV是多少") is None
    assert parse_explicit_time_window("上个月GMV") is None


def test_time_window_outside_domain():
    """数据域守卫判据：窗口整体晚于数据域上界 = 必然空集（确定性可证）。"""
    from agent.time_utils import time_window_outside_domain
    from semantic.dsl_schema import TimeFilter

    future = TimeFilter.model_validate(
        {"range_type": "absolute", "absolute": {"start": "2030-05-01", "end": "2030-06-01"}}
    )
    past = TimeFilter.model_validate(
        {"range_type": "absolute", "absolute": {"start": "2024-05-01", "end": "2024-06-01"}}
    )
    assert time_window_outside_domain(future) is True
    assert time_window_outside_domain(past) is False


def test_diagnostic_dsl_pair_respects_explicit_time():
    """兜底两期对必须尊重用户显式时间（严禁静默替换成 2024-05 域内窗口）。"""
    from core.orchestrator.nodes import _diagnostic_dsl_pair, _scalar_dsl

    baseline, current = _diagnostic_dsl_pair("分析一下 2030 年 5 月 GMV 下滑的原因")
    assert baseline["time_filter"]["absolute"]["start"] == "2030-05-01"
    assert current["time_filter"]["absolute"]["end"] == "2030-06-01"
    # 两期相邻不重叠（半开区间共用分界）
    assert baseline["time_filter"]["absolute"]["end"] == current["time_filter"]["absolute"]["start"]

    # 十八期：_scalar_dsl 锚点化（anchors + query 两参），显式时间语义不变
    scalar = _scalar_dsl(("order_amount",), "2030 年 5 月的 GMV 总额是多少？")
    assert scalar is not None
    assert scalar["time_filter"]["absolute"] == {"start": "2030-05-01", "end": "2030-06-01"}

    # 无显式时间 => 缺省锚不变（评测确定性回归锚点）
    b_default, c_default = _diagnostic_dsl_pair("为什么 GMV 下滑了")
    assert b_default["time_filter"]["absolute"] == {"start": "2024-05-01", "end": "2024-05-08"}
    assert c_default["time_filter"]["absolute"] == {"start": "2024-05-08", "end": "2024-05-15"}


def test_run_agent_fabricated_year_reports_no_data(tmp_path, monkeypatch):
    """E2E：编造年份（2030）严禁产出归因报告，必须如实说明无数据。

    回归锚点（2026-09 审计）：此前兜底窗口硬编码 2024-05，用域内数据冒充
    用户问的 2030 时段产出"下滑归因"报告 = 数据造假。
    """
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    trace = run_agent(
        "分析一下 2030 年 5 月第二周比第三周 GMV 下滑的原因，按地区定位",
        session_id="fab-year",
    )
    assert trace.phase == "done"
    # 严禁沙箱假产物：无数据时不做因子分解/维度下钻
    assert not any(a["kind"] == "summary" for a in trace.artifacts)
    assert not any(a["kind"] == "echarts" for a in trace.artifacts)
    # 取数被守卫拦截（不产出数据集）
    query_steps = [s for s in trace.steps if s["tool"] == "execute_dsl_query"]
    assert query_steps and all(not s["ok"] for s in query_steps)
    # 报告如实说明超界与数据域边界，且不出现编造结论话术
    assert "无任何数据" in trace.report
    assert "2024-06-30" in trace.report
    assert "驱动因子分解" not in trace.report
    assert "归因矩阵" not in trace.report


def test_critic_short_circuits_on_no_data_reason(monkeypatch):
    """时间域守卫拦截后 critic 直接转综合（严禁重规划空转、不进 LLM 反思）。"""
    import core.orchestrator.nodes as nodes

    calls: list[int] = []
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: calls.append(1) or object())
    state = _critic_state(
        no_data_reason="查询时间范围 2030-05-01 ~ 2030-06-01 整体晚于数仓数据域上界",
        datasets={},
        artifacts=[],
    )
    out = nodes.critic_node(state)
    assert out.phase == "synthesize"
    assert out.error_context.retries == 0
    assert not calls


def test_critic_short_circuits_on_all_empty_datasets(monkeypatch):
    """全部数据集 0 行：critic 转综合如实说明（不判'缺归因产物'触发重规划）。"""
    import core.orchestrator.nodes as nodes

    calls: list[int] = []
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: calls.append(1) or object())
    state = _critic_state(
        datasets={
            "s1_v0": {"path": "a.parquet", "rows": 0, "columns": ["province", "gmv"]},
            "s1_v1": {"path": "b.parquet", "rows": 0, "columns": ["province", "gmv"]},
        },
        artifacts=[],
    )
    out = nodes.critic_node(state)
    assert out.phase == "synthesize"
    assert out.error_context.retries == 0
    assert not calls


def test_analysis_template_guards_empty_inputs():
    """依赖数据集全 0 行 => 无匹配数据模板（严禁在空 DataFrame 上跑分解/下钻）。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState, PlanStep

    state = AgentState(
        user_query="分析一下 2024 年 5 月 GMV 下滑的原因",
        plan_steps=[
            PlanStep(id="s1", goal="取数", kind="query"),
            PlanStep(id="s2", goal="沙箱内做乘法因子分解", kind="analyze", depends_on=["s1"]),
        ],
        datasets={"s1_v0": {"path": "a.parquet", "rows": 0, "columns": ["province", "gmv"]}},
        step_outputs={"s1": ["s1_v0"]},
    )
    code = nodes._analysis_template(state, state.plan_steps[1])
    assert "无匹配数据" in code
    # 严禁落到因子分解模板（不读数据集、不产出分解小节）
    assert "read_input" not in code
    assert "驱动因子分解" not in code


def test_synthesize_no_data_skips_llm(monkeypatch):
    """无数据时 synthesize 跳过 LLM 综合，输出确定性数据说明（严禁编故事）。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState

    calls: list[str] = []
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(
        nodes, "_synthesize_with_llm", lambda state, material: calls.append(material)
    )
    state = AgentState(user_query="2030 年 5 月 GMV 下滑原因", no_data_reason="查询时间范围超界")
    out = nodes.synthesize_node(state)
    assert out.phase == "done"
    assert not calls
    assert "无法进行" in out.report
    assert "2024-06-30" in out.report
    assert "不会以其他时段的数据代替作答" in out.report


# --------------------------------------------------------------------------- #
# 十八期：兜底准入收敛（回归锚点：兜底曾固定 sum(gmv)，问省份数答 GMV 总额）
# --------------------------------------------------------------------------- #
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
            {
                "kind": "aggregate",
                "field": "province",
                "agg": "count_distinct",
                "alias": "province_count",
            }
        ],
        "filters": [{"field": "refund_amount", "operator": "gt", "value": 0}],
    }
    assert _intent_dsl_mismatch("有退款的省份有多少个", legal) is None

    metric_bad = {
        "metrics": [
            {"kind": "aggregate", "field": "refund_amount", "agg": "sum", "alias": "refund_amount"}
        ],
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
                {
                    "id": "s1",
                    "goal": "取GMV",
                    "kind": "query",
                    "depends_on": [],
                    "dsl": {
                        "metrics": [
                            {
                                "kind": "aggregate",
                                "field": "order_amount",
                                "agg": "sum",
                                "alias": "gmv",
                            }
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
                    "goal": "综合作答",
                    "kind": "synthesize",
                    "depends_on": ["s1"],
                    "dsl": None,
                    "code": None,
                },
            ],
        },
    )
    trace = run_agent("有多少个省份", session_id="guardq")
    assert trace.phase == "done"
    assert "无法作答" in trace.report
    assert "万元" not in trace.report


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
                {
                    "id": "s1",
                    "goal": "取GMV",
                    "kind": "query",
                    "depends_on": [],
                    "dsl": {
                        "metrics": [
                            {
                                "kind": "aggregate",
                                "field": "order_amount",
                                "agg": "sum",
                                "alias": "gmv",
                            }
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
                    "goal": "综合作答",
                    "kind": "synthesize",
                    "depends_on": ["s1"],
                    "dsl": None,
                    "code": None,
                },
            ],
        },
    )
    state = nodes.planner_node(AgentState(user_query="5月GMV是多少"))
    assert state.intent_type == "metric_scalar"
    assert state.intent_anchors == ["order_amount"]
    assert state.phase == "query"

    # 无 intent 字段：宽容不阻塞
    monkeypatch.setattr(
        nodes, "_llm_json", lambda llm, system, user: {"clarification": None, "steps": []}
    )
    state2 = nodes.planner_node(AgentState(user_query="5月GMV是多少"))
    assert state2.intent_type is None


def test_planner_blocked_clears_edit_instruction(monkeypatch):
    """plan_review 修改指令 + 兜底拒答 => 诚实拒答而非无限回环（终审 Important #2）。

    回归锚点：blocked 提前 return 发生在 plan_edit_instruction 清除之前，
    plan_gate 的 edit 路由会无条件回 plan 形成无限循环，用户看到
    "迭代步数超限"而非拒答报告。
    """
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: None)
    state = AgentState(user_query="帮我看看最近情况", plan_edit_instruction="改成按品类")
    out = nodes.planner_node(state)
    assert out.blocked_reason
    assert out.plan_edit_instruction is None


def test_scalar_answer_includes_default_scope_note(tmp_path, monkeypatch):
    """缺省口径必须进报告（规格 §3.2；终审 Important #5）。

    兜底 scalar 直答使用缺省时间窗与支付过滤，用户必须能看到口径说明。
    """
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: None)
    trace = run_agent("GMV呢？", session_id="scopeq")
    assert trace.phase == "done"
    assert "2024-05-01" in trace.report
    assert "成功支付" in trace.report or "SUCCESS" in trace.report


# --------------------------------------------------------------------------- #
# 二期 M1/M2：守卫审计面完整化 + code_exec 对 blocked 短路
# --------------------------------------------------------------------------- #
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
                            {
                                "kind": "aggregate",
                                "field": "order_amount",
                                "agg": "sum",
                                "alias": "gmv",
                            }
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
                    "goal": "综合作答",
                    "kind": "synthesize",
                    "depends_on": ["s1"],
                    "dsl": None,
                    "code": None,
                },
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
                            {
                                "kind": "aggregate",
                                "field": "order_amount",
                                "agg": "sum",
                                "alias": "gmv",
                            }
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


# --------------------------------------------------------------------------- #
# 二期：Grounding 定向重试闭环（重写 1 次 -> 仍超阈值降级确定性渲染）
# --------------------------------------------------------------------------- #
def _grounding_retry_plan_payload():
    return {
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
                "goal": "综合作答",
                "kind": "synthesize",
                "depends_on": ["s1"],
                "dsl": None,
                "code": None,
            },
        ],
    }


def test_grounding_retry_success_keeps_llm_report(tmp_path, monkeypatch):
    """首版报告超阈值 -> 定向重写 grounded => 保留 LLM 报告（二期 RF#2）。"""
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    plan_seen = iter([_grounding_retry_plan_payload()])
    synth_reports = iter(
        ["编造报告：转化率高达 42.5%、留存 88.6%、复购 77.3%、曝光 99.2%。", "GMV 为 115.69 万元。"]
    )

    def _fake_llm_json(llm, system, user):
        return next(plan_seen)

    synth_calls: list[str | None] = []

    def _fake_synth(state, material, extra_instruction=None):
        synth_calls.append(extra_instruction)
        return next(synth_reports)

    monkeypatch.setattr(nodes, "_llm_json", _fake_llm_json)
    monkeypatch.setattr(nodes, "_synthesize_with_llm", _fake_synth)
    trace = run_agent("2024年5月GMV是多少", session_id="retryq")
    assert trace.phase == "done"
    assert "115.69" in trace.report
    assert "42.5" not in trace.report
    assert "数据溯源提示" not in trace.report  # 重写后 grounded，不标注
    # 重写轮必须携带修正指令（首轮 None、第二轮非 None）
    assert synth_calls[0] is None
    assert synth_calls[1] is not None and "数值溯源修正" in synth_calls[1]


def test_grounding_retry_exhausted_falls_back_to_deterministic(tmp_path, monkeypatch):
    """重试后仍超阈值 => 放弃 LLM 报告，降级确定性渲染（二期 RF#2 失败路径）。"""
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    fabricated = "编造报告：转化率高达 42.5%、留存 88.6%、复购 77.3%、曝光 99.2%。"
    monkeypatch.setattr(
        nodes, "_llm_json", lambda llm, system, user: _grounding_retry_plan_payload()
    )
    synth_calls = iter([fabricated, fabricated])

    def _fake_synth(state, material, extra_instruction=None):
        return next(synth_calls)

    monkeypatch.setattr(nodes, "_synthesize_with_llm", _fake_synth)
    trace = run_agent("2024年5月GMV是多少", session_id="retryfail")
    assert trace.phase == "done"
    assert "查询答案" in trace.report  # 确定性渲染接管（数据真实）
    assert "转化率" not in trace.report  # 编造叙事被整体放弃


# --------------------------------------------------------------------------- #
# 二期：选项式澄清（clarification 对象形态 + options 全链透传）
# --------------------------------------------------------------------------- #
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
    monkeypatch.setattr(
        nodes, "_llm_json", lambda llm, system, user: {"clarification": "哪个指标？", "steps": []}
    )
    state2 = nodes.planner_node(AgentState(user_query="销售额是多少"))
    assert state2.clarification == "哪个指标？"
    assert state2.clarification_options == []

    # 对象缺 question => 回退默认问题文本，options 保留（二期 RF#3）
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
    """hitl_request 事件携带 options（前端 elHitl 消费契约）。"""
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    events: list[dict] = []
    run_agent("GMV呢？", session_id="optq", on_event=events.append)
    hitl = [e for e in events if e["event"] == "hitl_request"]
    assert hitl and hitl[0]["payload"]["hitl"].get("options") == [
        "2024年5月GMV",
        "2024年5月订单量",
    ]
