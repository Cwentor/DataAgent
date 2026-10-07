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
from semantic import catalog


def _real_window_gmv_wan() -> str:
    """窗口 [2024-05-01, 2024-05-15) order_status=1002 的真实 GMV（万元，2 位小数）。

    数据 pin 清理（M-P0）：grounding 桩值改由测试查库取真值注入 stub 报告，
    防夹具数值随数仓漂移。
    """
    import duckdb

    from config import settings

    conn = duckdb.connect(str(settings.DB_PATH), read_only=True)
    try:
        value = conn.execute(
            "SELECT SUM(split_total_amount) FROM order_detail "
            "WHERE order_status = '1002' AND order_time >= '2024-05-01' "
            "AND order_time < '2024-05-15'"
        ).fetchone()[0]
    finally:
        conn.close()
    return f"{value / 10000:.2f}"


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
                        {
                            "kind": "aggregate",
                            "field": "split_total_amount",
                            "agg": "sum",
                            "alias": "gmv",
                        }
                    ],
                    "filters": [{"field": "order_status", "operator": "eq", "value": "1002"}],
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


def test_clarify_pending_then_new_question_routes_fresh(tmp_path, monkeypatch):
    """澄清挂起后新问题按新查询路由（docs/reviews/20261002-audit-clarify-pending-hijack.md 回归锚点）。

    同会话内上一轮以 phase=clarify 挂起后，用户忽略澄清直接提出的新问题
    必须独立成轮作答，严禁被合并进旧查询（答非所问禁区）；同时旧澄清卡
    在新问题 intervening 后仍须可在本轮线程上恢复（线程按轮隔离）。
    """
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: None)

    # Gmall 数仓无"琼崖省"，枚举未命中 => 澄清挂起
    paused = run_agent("琼崖省的GMV是多少", session_id="hijack-seq", autonomy_level="L4")
    assert paused.phase == "clarify"

    trace = run_agent("有多少个省份", session_id="hijack-seq", autonomy_level="L4")
    assert trace.phase == "done"
    assert f"查询答案：{len(catalog.DIMENSION_MEMBERS['province'])}" in trace.report
    assert "用户补充" not in trace.report

    final = run_agent(
        "琼崖省的GMV是多少",
        session_id="hijack-seq",
        autonomy_level="L4",
        resume_state=paused.apply(human_reply="广东省"),
    )
    assert final.phase == "done"
    assert "广东省" in final.report


def test_sequential_pending_clarifies_resume_independently(tmp_path, monkeypatch):
    """同会话两轮澄清挂起互不串扰（20261002 审计：checkpointer 线程按轮隔离）。

    回归锚点：thread_id 仅到会话粒度时，同线程叠加的两个 clarify interrupt
    会让先挂起卡片的答复被合并进后挂起轮的查询（答非所问）。按轮隔离后，
    各轮恢复必须在各自线程上完成，报告口径归属各自问题。
    """
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: None)

    # Gmall 数仓无"琼崖省/岭南省"，两轮均为枚举未命中 => 各自澄清挂起
    paused_first = run_agent("琼崖省的GMV是多少", session_id="dual-clarify", autonomy_level="L4")
    assert paused_first.phase == "clarify"
    paused_second = run_agent("岭南省的GMV是多少", session_id="dual-clarify", autonomy_level="L4")
    assert paused_second.phase == "clarify"

    final_first = run_agent(
        "琼崖省的GMV是多少",
        session_id="dual-clarify",
        autonomy_level="L4",
        resume_state=paused_first.apply(human_reply="广东"),
    )
    assert final_first.phase == "done"
    assert "广东" in final_first.report
    assert "河北" not in final_first.report

    final_second = run_agent(
        "岭南省的GMV是多少",
        session_id="dual-clarify",
        autonomy_level="L4",
        resume_state=paused_second.apply(human_reply="四川"),
    )
    assert final_second.phase == "done"
    assert "四川省" in final_second.report or "四川" in final_second.report
    assert "海南" not in final_second.report


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
                {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": ["province", {"field": "category"}],
            "time_range": {
                "range_type": "absolute",
                "absolute": {"start": "2024-05-01", "end": "2024-05-08"},
            },
            "filters": [
                {"field": "order_status", "operator": "eq", "value": "1002"},
                {"field": "split_total_amount", "operator": "ge", "value": 10},
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
                {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
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
        "查 GMV",
        "- gmv (order_detail.split_total_amount)",
        error_context="CompileError: 字段不存在",
    )
    assert "上次失败记录" in prompt
    assert "CompileError: 字段不存在" in prompt
    assert "严禁原样重复上一轮计划" in prompt


def test_planner_prompt_without_error_context_unchanged():
    """planner_prompt 不带 error_context：不出现失败记录小节（首轮规划不变）。"""
    from core.orchestrator.prompts import planner_prompt

    prompt = planner_prompt("查 GMV", "- gmv (order_detail.split_total_amount)")
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
    assert count_dimension_dsl("多少个品牌") is not None  # dim_shop 删除，店铺维度退役
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
    """LLM 在场但 Planner 调用失败时，事实型短问句不被澄清门拦截且兜底直答省份数。

    三重回归锚点（原三测合并：bypasses_clarify_gate / count_factoid / banner）：
    - clarify 门："有多少个省份"此前被确定性字符规则（过短/无指标词即澄清）
      误拦在 Planner 门外；判定权上交 Planner（clarification 契约）后 LLM
      在场即放行，全程无 clarify 中断；
    - 兜底直答：Planner LLM 失败（_llm_json 返回 None）走启发式兜底，此前
      一律 _scalar_dsl 取 sum(gmv)，产出"问省份数、答 115.69 万元 GMV"的
      离谱报告；Gmall 数仓 34 个省份应计数直答且严禁金额化；
    - 降级水印：兜底接管时报告顶部必须显著标注（降级不可静默）。
    """
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: None)
    trace = run_agent("有多少个省份", session_id="countq")
    assert trace.phase == "done"
    assert trace.clarification is None  # LLM 在场即放行：无澄清挂起
    # Gmall 数仓 34 个省份：计数直答，且严禁金额化（"0.00 万元"式离谱答案）
    assert f"查询答案：{len(catalog.DIMENSION_MEMBERS['province'])}" in trace.report
    assert "万元" not in trace.report
    assert "离线兜底引擎" in trace.report  # 降级水印显著标注


# --------------------------------------------------------------------------- #
# 多轮会话上下文（并行会话改造：同会话追问继承历史语境）
# --------------------------------------------------------------------------- #
def test_planner_prompt_injects_session_history():
    """planner_prompt 带 history_context：注入会话历史小节并要求消解省略指代。"""
    from core.orchestrator.prompts import planner_prompt

    prompt = planner_prompt(
        "那上海呢",
        "- gmv (order_detail.split_total_amount)",
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

    legacy = planner_prompt("查 GMV", "- gmv (order_detail.split_total_amount)")
    explicit_none = planner_prompt(
        "查 GMV",
        "- gmv (order_detail.split_total_amount)",
        error_context=None,
        history_context=None,
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
    """诊断兜底两期对必须同时带订单量与买家数因子（反思归因诉求首轮即满足）。

    兼守维度池与两期同口径断言（原 test_synthesizer_rebuild 的
    carries_dimension_pool 合并）：未点名维度时取候选维度池、状态过滤继承、
    两期时间窗口无缝衔接。
    """
    from core.orchestrator.nodes import _diagnostic_dsl_pair

    base, curr = _diagnostic_dsl_pair("分析 5 月第一周比第二周 GMV 下滑原因")
    for dsl in (base, curr):
        aliases = {m["alias"] for m in dsl["metrics"]}
        assert {"gmv", "orders", "buyers"} <= aliases
        # 候选池覆盖省份/品牌/品类：由分析层按信息增益裁决主因维度
        assert [d["field"] for d in dsl["dimensions"]] == ["province", "tm_name", "category1_name"]
        assert {"field": "order_status", "operator": "eq", "value": "1002"} in dsl["filters"]
    assert base["time_filter"]["absolute"]["end"] == curr["time_filter"]["absolute"]["start"]


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
    assert "split_total_amount" in scope and "province" in scope
    assert "流量" in scope  # 明示清单外概念不得作为重规划理由


@pytest.mark.parametrize(
    ("reasons", "missing", "preset_fingerprint", "expected"),
    [
        # 反思以数仓未采集的维度（流量/活动/异常单）为由判不充分 => 不可执行
        (
            ["缺少对订单量、客单价、流量、活动、异常单等影响因素的归因分析"],
            None,
            False,
            False,
        ),
        # 理由提到的概念已被本轮产物覆盖 => 属分析深度诉求，不可执行
        (
            ["最终结果只列出下降幅度较大的地区及指标，未解释具体下滑原因"],
            ["缺少订单量与买家数的归因分析"],
            False,
            False,
        ),
        # 理由指向可用域内尚未取到的数据（如品类）=> 可执行，允许重规划
        # （产物列为 province/gmv/orders/buyers，品类字段未取到）
        (
            ["未按品类拆分下滑贡献，无法定位品类级主因"],
            ["取品类维度的两期明细"],
            False,
            True,
        ),
        # 产物指纹与上次重规划相同 => 重规划无进展，直接综合（防空转）
        (
            ["未按品类拆分下滑贡献"],
            ["取品类维度明细"],
            True,
            False,
        ),
    ],
)
def test_insufficient_is_actionable_branches(reasons, missing, preset_fingerprint, expected):
    """_insufficient_is_actionable 四分支矩阵：域外拒绝/已覆盖拒绝/域内缺口放行/无进展拒绝。"""
    import core.orchestrator.nodes as nodes

    state = _critic_state()
    if preset_fingerprint:
        state = state.apply(last_replan_fingerprint=nodes._artifact_fingerprint(state))
    verdict = {"verdict": "insufficient", "reasons": reasons}
    if missing is not None:
        verdict["missing"] = missing
    assert nodes._insufficient_is_actionable(verdict, state) is expected


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
    scalar = _scalar_dsl(("split_total_amount",), "2030 年 5 月的 GMV 总额是多少？")
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
    assert settings.DATA_DOMAIN_END.isoformat() in trace.report
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
    from config import settings
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
    assert settings.DATA_DOMAIN_END.isoformat() in out.report
    assert "不会以其他时段的数据代替作答" in out.report


# --------------------------------------------------------------------------- #
# 十八期：兜底准入收敛（回归锚点：兜底曾固定 sum(gmv)，问省份数答 GMV 总额）
# --------------------------------------------------------------------------- #
def test_scalar_dsl_by_anchors():
    """标量兜底按锚点取数：金额 sum、计数 count、别名带语义（Review Focus 4）。"""
    from core.orchestrator.nodes import _scalar_dsl

    dsl = _scalar_dsl(("split_total_amount", "order_id"), "2024年5月GMV和订单量各多少")
    assert dsl is not None
    assert dsl["metrics"] == [
        {
            "kind": "aggregate",
            "field": "split_total_amount",
            "agg": "sum",
            "alias": "split_total_amount",
        },
        {"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "order_id_count"},
    ]
    assert dsl["filters"] == [{"field": "order_status", "operator": "eq", "value": "1002"}]
    # 跨表混合锚：兜底拒答（口径混乱风险，LLM 在场时由 Planner 规划）
    assert _scalar_dsl(("split_total_amount", "refund_amount"), "GMV和退款金额各多少") is None
    # 纯退款单锚：不带 order_status 过滤（order_refund_info 无该字段语义）
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


@pytest.mark.parametrize(
    ("llm_present", "session_id"),
    [(True, "blockedcfg"), (False, "blockednone")],
)
def test_blocked_report_llm_wording_by_config_state(tmp_path, monkeypatch, llm_present, session_id):
    """拒答建议按配置状态二分：已配置时提示查网关连通性，未配置时如实告知兜底模式；
    旧的无条件"或配置 LLM 模型后重试"文案必须消失。"""
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object() if llm_present else None)
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: None)
    trace = run_agent("帮我看看最近情况", session_id=session_id)
    assert trace.phase == "done"
    if llm_present:
        assert "LLM 已配置" in trace.report
        assert "未配置 LLM" not in trace.report
        assert "或配置 LLM 模型后重试" not in trace.report  # 旧的无条件文案必须消失
    else:
        assert "未配置 LLM" in trace.report
        assert "LLM 已配置" not in trace.report


def test_intent_dsl_guard_unit():
    """L3 守卫：基数+金额聚合拦截；合法 WHERE 不受限（Review Focus 2）。"""
    from core.orchestrator.nodes import _intent_dsl_mismatch

    bad = {
        "metrics": [
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
        ],
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
                                "field": "split_total_amount",
                                "agg": "sum",
                                "alias": "gmv",
                            }
                        ],
                        "filters": [{"field": "order_status", "operator": "eq", "value": "1002"}],
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


def test_planner_llm_intent_echoed_to_state(monkeypatch):
    """LLM 回传 intent => 落 state（诊断可观测）；缺失时不阻塞规划。"""
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(
        nodes,
        "_llm_json",
        lambda llm, system, user: {
            "intent": {"type": "metric_scalar", "anchors": ["split_total_amount"]},
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
                                "field": "split_total_amount",
                                "agg": "sum",
                                "alias": "gmv",
                            }
                        ],
                        "filters": [{"field": "order_status", "operator": "eq", "value": "1002"}],
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
    assert state.intent_anchors == ["split_total_amount"]
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
    assert "成功支付" in trace.report or "1002" in trace.report


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
                                "field": "split_total_amount",
                                "agg": "sum",
                                "alias": "gmv",
                            }
                        ],
                        "filters": [{"field": "order_status", "operator": "eq", "value": "1002"}],
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
                                "field": "split_total_amount",
                                "agg": "sum",
                                "alias": "gmv",
                            }
                        ],
                        "filters": [{"field": "order_status", "operator": "eq", "value": "1002"}],
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
                        {
                            "kind": "aggregate",
                            "field": "split_total_amount",
                            "agg": "sum",
                            "alias": "gmv",
                        }
                    ],
                    "filters": [{"field": "order_status", "operator": "eq", "value": "1002"}],
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
        [
            "编造报告：转化率高达 42.5%、留存 88.6%、复购 77.3%、曝光 99.2%。",
            f"GMV 为 {_real_window_gmv_wan()} 万元。",
        ]
    )

    def _fake_llm_json(llm, system, user):
        return next(plan_seen)

    synth_calls: list[str | None] = []

    def _fake_synth(state, material, extra_instruction=None):
        synth_calls.append(extra_instruction)
        # 新契约：_synthesize_with_llm 返回 (报告, 失败原因)
        return next(synth_reports), None

    monkeypatch.setattr(nodes, "_llm_json", _fake_llm_json)
    monkeypatch.setattr(nodes, "_synthesize_with_llm", _fake_synth)
    trace = run_agent("2024年5月GMV是多少", session_id="retryq")
    assert trace.phase == "done"
    # 桩值去 pin：窗口真值查库注入 stub，断言对齐动态真值（order_status=1002，半开窗口 [05-01, 05-15)）
    assert _real_window_gmv_wan() in trace.report
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
        # 新契约：_synthesize_with_llm 返回 (报告, 失败原因)；grounding 失败由
        # 节点内 grounding_review 判定，桩侧只承诺"LLM 调用本身成功"
        return next(synth_calls), None

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


def test_grounding_retry_residual_still_flagged(tmp_path, monkeypatch):
    """重写成功但残留 1-3 个不可溯源 => 报告保留但必须标注（终审 Important #1）。

    两级行为：首轮 1-3 沉默（一期契约）；重试过的报告可信度降低，
    残留必须让用户知情。
    """
    import core.orchestrator.nodes as nodes
    from config import settings

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    plan_seen = iter([_grounding_retry_plan_payload()])
    synth_reports = iter(
        [
            "编造报告：转化率高达 42.5%、留存 88.6%、复购 77.3%、曝光 99.2%。",
            f"GMV 为 {_real_window_gmv_wan()} 万元，测算转化率 42.5%。",
        ]
    )

    def _fake_llm_json(llm, system, user):
        return next(plan_seen)

    def _fake_synth(state, material, extra_instruction=None):
        # 新契约：_synthesize_with_llm 返回 (报告, 失败原因)
        return next(synth_reports), None

    monkeypatch.setattr(nodes, "_llm_json", _fake_llm_json)
    monkeypatch.setattr(nodes, "_synthesize_with_llm", _fake_synth)
    trace = run_agent("2024年5月GMV是多少", session_id="retryres")
    assert trace.phase == "done"
    assert _real_window_gmv_wan() in trace.report
    assert "数据溯源提示" in trace.report  # 重试残留必须标注
    assert "42.5" in trace.report  # 残留数值仍呈现但已警示


# --------------------------------------------------------------------------- #
# 分层降级兜底：澄清轮次计数（二轮上限的状态载体）
# --------------------------------------------------------------------------- #
def test_clarification_rounds_field_defaults_zero():
    """澄清轮次计数字段存在且默认 0。"""
    from core.orchestrator.state import AgentState

    state = AgentState(session_id="cr", turn_id="t1", trace_id="tr1", user_query="x")
    assert state.clarification_rounds == 0
    bumped = state.apply(clarification_rounds=state.clarification_rounds + 1)
    assert bumped.clarification_rounds == 1


def test_heuristic_plan_enumeration():
    """十九期 M1：枚举问法的确定性两步计划（投影 DSL + 综合）。"""
    import core.orchestrator.nodes as _orch_nodes

    steps = _orch_nodes._heuristic_plan("把全部品牌名列举给我")
    assert steps is not None and len(steps) == 2
    s1 = steps[0]
    assert s1.kind == "query" and s1.dsl is not None
    assert s1.dsl["metrics"] == []
    assert s1.dsl["dimensions"] == [{"field": "tm_name"}]
    assert steps[1].kind == "synthesize"


def test_planner_pre_routes_enumeration_without_llm(monkeypatch):
    """枚举预路由：LLM 在场也不发起调用（现行规划契约表达不了投影）。"""
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.nodes import planner_node

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
    import core.orchestrator.nodes as _orch_nodes

    query = "把全部品牌名列举给我"
    ok_payload = {"metrics": [], "dimensions": [{"field": "brand"}], "filters": []}
    assert _orch_nodes._intent_dsl_mismatch(query, ok_payload) is None
    bad_payload = {
        "metrics": [
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
        ],
        "dimensions": [{"field": "brand"}],
        "filters": [],
    }
    mismatch = _orch_nodes._intent_dsl_mismatch(query, bad_payload)
    assert mismatch is not None and "投影" in mismatch


def test_agent_state_assumptions_field():
    """M3-T1：AgentState 契约新增口径假设字段（默认空，宽容消费）。"""
    state = AgentState(user_query="x")
    assert state.assumptions == []


def test_planner_prompt_injects_clarify_context():
    """M3-T1：二轮澄清上下文注入——提示词含"严禁再次澄清"硬性指令。"""
    from core.orchestrator.prompts import planner_prompt

    single = planner_prompt("q", "schema")
    double = planner_prompt("q", "schema", clarify_context="用户已答复过一轮澄清")
    assert "澄清" not in single or "仍不唯一" not in single
    assert "严禁再次澄清" in double and "口径假设" in double


def test_planner_node_consumes_assumptions(monkeypatch):
    """M3-T1：Planner 产出口径假设 → 状态透传（宽容消费）。"""
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.nodes import planner_node

    payload = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取数",
                "kind": "query",
                "dsl": {
                    "metrics": [
                        {
                            "kind": "aggregate",
                            "field": "split_total_amount",
                            "agg": "sum",
                            "alias": "gmv",
                        }
                    ],
                    "dimensions": [],
                    "filters": [],
                },
            },
        ],
        "assumptions": ["仅统计成功支付订单", "时间窗口取数仓最近完整期"],
    }
    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", lambda *a, **k: payload)
    state = planner_node(AgentState(user_query="5月GMV是多少"))
    assert state.assumptions == ["仅统计成功支付订单", "时间窗口取数仓最近完整期"]


def test_planner_node_tolerates_invalid_assumptions(monkeypatch):
    """M3-T1 Review Focus #2：非法 assumptions 宽容忽略，不阻塞规划。"""
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.nodes import planner_node

    payload = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取数",
                "kind": "query",
                "dsl": {
                    "metrics": [
                        {
                            "kind": "aggregate",
                            "field": "split_total_amount",
                            "agg": "sum",
                            "alias": "gmv",
                        }
                    ],
                    "dimensions": [],
                    "filters": [],
                },
            },
        ],
        "assumptions": "不是数组的假设",
    }
    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", lambda *a, **k: payload)
    state = planner_node(AgentState(user_query="5月GMV是多少"))
    assert state.assumptions == []
    assert state.plan_steps  # 规划不受阻


def test_degraded_second_round_ambiguity_answers_with_assumptions(monkeypatch):
    """M3-T2：二轮歧义不再拒答——多候选筛选维度转分组 + 口径假设。

    兼守十九期分级透明修订的 answered_by 契约（原 test_planner_clarify_round_limit_
    blocks_second_round 合并）：作答来源必须落在降级枚举并集内。
    """
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.nodes import planner_node

    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: None)
    # "琼崖省的GMV" 式问法（取值未命中枚举）一轮已澄清（rounds=1）仍歧义
    # => 带假设作答（Gmall 数仓省份枚举已含"海南"，不再构成歧义）
    state = planner_node(
        AgentState(
            user_query="琼崖省的GMV是多少",
            clarification_rounds=1,
            autonomy_level="L4",
        )
    )
    assert state.phase == "query"
    assert state.plan_steps, "二轮歧义必须产计划而非拒答"
    assert state.assumptions, "口径假设必须非空"
    assert any("分组" in a or "不筛选" in a for a in state.assumptions)
    assert state.answered_by in ("degraded_confirmed", "degraded_auto")


def test_synthesize_report_prepends_assumptions(monkeypatch):
    """M3-T2：口径假设在报告头部确定性呈现（LLM 成功路径同样前置）。"""
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.agent import run_agent

    payload = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取5月GMV",
                "kind": "query",
                "dsl": {
                    "metrics": [
                        {
                            "kind": "aggregate",
                            "field": "split_total_amount",
                            "agg": "sum",
                            "alias": "gmv",
                        }
                    ],
                    "dimensions": [],
                    "filters": [],
                    "time_filter": {
                        "range_type": "absolute",
                        "absolute": {"start": "2024-05-01", "end": "2024-06-01"},
                    },
                },
            },
            {"id": "s2", "goal": "综合作答", "kind": "synthesize", "depends_on": ["s1"]},
        ],
        "assumptions": ["时间窗口按 2024-05 全月假设"],
    }
    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", lambda *a, **k: payload)
    trace = run_agent("5月GMV是多少", session_id="m3-assume", autonomy_level="L4")
    assert trace.phase == "done"
    assert "口径假设" in (trace.report or "")
    assert "2024-05 全月" in (trace.report or "")


def test_ambiguity_routing_state_machine(monkeypatch):
    """M3-T4 验收锚点：澄清-标注路由状态机（口径模糊三级行为）。

    spec §7 题库第 3 层验收判据：要么选项式澄清、要么 assumptions 非空且
    报告可见——两种都算通过，静默猜口径直答算失败。
    """
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.nodes import planner_node

    # 一轮歧义（LLM 判定无法选口径）=> 选项式澄清挂起
    payload_clarify = {
        "clarification": {"question": "要按省份还是品类看？", "options": ["按省份", "按品类"]},
        "steps": None,
    }
    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", lambda *a, **k: payload_clarify)
    first = planner_node(AgentState(user_query="华南的表现怎么样"))
    assert first.phase == "clarify" and first.clarification_options

    # 二轮（用户答复后仍歧义）=> LLM 再反问被硬拦截，转带假设作答
    # （"琼崖省的GMV"：维度锚在但取值未命中枚举，走 assume 分支；
    #   "华南的表现"无任何锚点属 not_exist 诚实拒答，不经此路径）
    second = planner_node(
        AgentState(user_query="琼崖省的GMV是多少", clarification_rounds=1, autonomy_level="L4")
    )
    assert second.phase != "clarify"
    assert second.plan_steps and second.assumptions, "二轮必须带假设作答"

    # 中置信（LLM 直接管假设作答）=> assumptions 非空 + 正常计划
    payload_assume = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取数",
                "kind": "query",
                "dsl": {
                    "metrics": [
                        {
                            "kind": "aggregate",
                            "field": "split_total_amount",
                            "agg": "sum",
                            "alias": "gmv",
                        }
                    ],
                    "dimensions": [{"field": "province"}],
                    "filters": [],
                },
            },
        ],
        "assumptions": ["华南展开为广东/广西/海南等省份 IN 列表"],
    }
    monkeypatch.setattr(_orch_nodes, "_llm_json", lambda *a, **k: payload_assume)
    third = planner_node(AgentState(user_query="华南的表现怎么样"))
    assert third.phase == "query" and third.assumptions


def test_planner_sql_step_lifted_to_dsl(monkeypatch):
    """M4-T3：LLM 步骤带可升 SQL → 提升为 DSL（sql 置空，scratchpad 记录）。"""
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.nodes import planner_node

    payload = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取GMV",
                "kind": "query",
                "sql": "SELECT SUM(f.split_total_amount) AS gmv FROM order_detail f "
                "WHERE f.order_status = '1002'",
            },
        ],
    }
    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", lambda *a, **k: payload)
    state = planner_node(AgentState(user_query="5月GMV是多少"))
    assert state.phase == "query"
    assert state.plan_steps[0].dsl is not None
    assert state.plan_steps[0].dsl["metrics"][0]["field"] == "split_total_amount"
    assert state.plan_steps[0].sql is None, "提升后 sql 必须置空（永不持久化）"
    assert any("sql-lifted" in s for s in state.scratchpad)


def test_planner_sql_lift_retry_with_rejection_list(monkeypatch):
    """M4-T3：拒升清单注入重规划提示词，第二次喂修正 SQL 后成功。"""
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.nodes import planner_node

    bad = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取GMV",
                "kind": "query",
                "sql": "SELECT CASE WHEN f.order_status = '1002' THEN f.split_total_amount ELSE 0 END AS gmv "
                "FROM order_detail f",
            },
        ],
    }
    good = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取GMV",
                "kind": "query",
                "sql": "SELECT SUM(f.split_total_amount) AS gmv FROM order_detail f "
                "WHERE f.order_status = '1002'",
            },
        ],
    }
    calls: list[str] = []

    def fake_llm(system, user):
        calls.append(user)
        return bad if len(calls) == 1 else good

    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", lambda _llm, _sys, user: fake_llm(_sys, user))
    state = planner_node(AgentState(user_query="5月GMV是多少"))
    assert len(calls) == 2
    assert "拒升清单" in calls[1] and "CASE" in calls[1], "拒升清单必须喂回自愈提示词"
    assert state.plan_steps[0].dsl is not None


def test_planner_sql_lift_exhausted_falls_back_to_heuristic(monkeypatch):
    """M6-T2 修订：拒升自愈耗尽 → 保留 SQL 计划交探索层审批（不再回落兜底）。

    原 M4 行为（回落启发式）已按 spec §3.5/§4 案例 B 修订：SQL 步骤保留在
    计划中，由执行层探索审批门裁决——严禁静默丢 SQL 换一个口径作答。
    """
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.nodes import planner_node

    bad = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取GMV",
                "kind": "query",
                "sql": "SELECT CASE WHEN f.order_status = '1002' THEN f.split_total_amount ELSE 0 END AS gmv "
                "FROM order_detail f",
            },
        ],
    }
    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", lambda _llm, _sys, user: bad)
    state = planner_node(AgentState(user_query="2024年5月GMV是多少", autonomy_level="L4"))
    assert state.phase == "query"
    assert state.answered_by == "llm", "M6：保留 LLM 计划交探索审批，不静默换口径"
    sql_steps = [s for s in state.plan_steps if s.kind == "query"]
    assert sql_steps and all(s.sql for s in sql_steps), "待审批 SQL 必须保留在计划中"
    assert any("sql-pending-exploration" in x for x in state.scratchpad)


def test_planner_dsl_takes_precedence_over_sql(monkeypatch):
    """M4-T3：dsl 与 sql 同给时 dsl 优先（sql 字段被忽略并置空）。"""
    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.nodes import planner_node

    payload = {
        "clarification": None,
        "steps": [
            {
                "id": "s1",
                "goal": "取GMV",
                "kind": "query",
                "dsl": {
                    "metrics": [
                        {
                            "kind": "aggregate",
                            "field": "split_total_amount",
                            "agg": "sum",
                            "alias": "gmv",
                        }
                    ],
                    "dimensions": [],
                    "filters": [],
                },
                "sql": "SELECT COUNT(*) AS x FROM order_detail f",
            },
        ],
    }
    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", lambda *a, **k: payload)
    state = planner_node(AgentState(user_query="5月GMV是多少"))
    assert state.answered_by == "llm", "dsl 优先时合法 dsl 步骤不应被连坐丢弃落兜底"
    assert state.plan_steps[0].dsl is not None
    assert state.plan_steps[0].dsl["metrics"][0]["alias"] == "gmv", "dsl 原样保留"
    assert state.plan_steps[0].sql is None
    assert not any("sql-lifted" in s for s in state.scratchpad), "dsl 优先时不走提升闸门"


# --------------------------------------------------------------------------- #
# 探索层审批门（M6-T2，spec §3.5/§6.5）：第四类 interrupt + L4 边界
# --------------------------------------------------------------------------- #
def _make_sql_step_payload(sql: str) -> dict:
    return {
        "clarification": None,
        "steps": [
            {"id": "s1", "goal": "复杂分析", "kind": "query", "sql": sql},
            {"id": "s2", "goal": "综合作答", "kind": "synthesize", "depends_on": ["s1"]},
        ],
    }


def test_exploration_step_executes_on_l4_auto(monkeypatch, tmp_path):
    """L4 + 无敏感列 → 自动放行执行（maybe_interrupt 直通），数据集带探索标记。"""
    import core.orchestrator.nodes as _orch_nodes

    executed: list[str] = []

    def fake_execute(sql, **kwargs):
        executed.append(sql)
        from core.retrieval.export import export_to_parquet

        return export_to_parquet(
            ["gmv"], [[123.0]], tmp_path / "inputs", "s1", audit={"exploration": True}
        )

    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(
        _orch_nodes,
        "_llm_json",
        lambda *a, **k: _make_sql_step_payload(
            "WITH t AS (SELECT sku_id, SUM(1) AS c FROM order_detail GROUP BY sku_id) "
            "SELECT SUM(t.sku_id) AS x FROM t"
        ),
    )
    monkeypatch.setattr("core.retrieval.exploration.execute_exploration_query", fake_execute)
    state = _orch_nodes.planner_node(AgentState(user_query="复杂分析", autonomy_level="L4"))
    state, record = _orch_nodes._execute_exploration_step(state, state.plan_steps[0], tmp_path)
    assert executed, "L4 无敏感列必须自动放行执行"
    assert record.ok
    assert state.datasets["s1"]["audit"]["exploration"] is True


def test_exploration_l4_with_sensitive_column_interrupts(monkeypatch, tmp_path):
    """Review Focus #3：L4 但 SQL 含主体禁列 → 必须挂起人工审批（不自动放行）。"""
    import langgraph.types as lg_types

    import core.orchestrator.nodes as _orch_nodes

    captured: list[dict] = []

    def fake_interrupt(payload):
        captured.append(payload)
        return {"action": "deny"}

    monkeypatch.setattr(lg_types, "interrupt", fake_interrupt)
    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(
        _orch_nodes,
        "_llm_json",
        lambda *a, **k: _make_sql_step_payload(
            "WITH t AS (SELECT * FROM order_detail) SELECT split_coupon_amount FROM t"
        ),
    )
    state = _orch_nodes.planner_node(
        AgentState(user_query="复杂分析", autonomy_level="L4", principal="restricted")
    )
    state, record = _orch_nodes._execute_exploration_step(state, state.plan_steps[0], tmp_path)
    assert captured and captured[0]["kind"] == "exploration"
    assert captured[0]["sensitive"] is True
    assert record.ok is False
    assert state.answered_by == "blocked"
    assert "拒绝" in state.blocked_reason


def test_exploration_dataset_annotated_in_report(monkeypatch, tmp_path):
    """报告必须醒目标注"探索查询产出"（带假设作答 ≠ 静默降级）。"""
    from core.orchestrator.nodes import _dataset_analyst_markdown

    ref = {
        "columns": ["gmv"],
        "rows": 1,
        "path": "s1.parquet",
        "audit": {"exploration": True},
    }
    section, _artifact = _dataset_analyst_markdown("s1", ref, tmp_path, scope_note="")
    assert "探索查询产出" in section


# --------------------------------------------------------------------------- #
# 审批状态机真中断恢复分支 + 审计事件（评审 [S-3]）+ 图级端到端（CRITICAL #1）
# --------------------------------------------------------------------------- #
def _fake_exploration_execute(executed: list[str], tmp_path):
    """构造受监督的 execute_exploration_query 替身（记录执行、返回合法 ParquetRef）。"""

    def fake_execute(sql, **kwargs):
        executed.append(sql)
        from core.retrieval.export import export_to_parquet

        return export_to_parquet(
            ["gmv"], [[1.0]], tmp_path / "inputs", "s1", audit={"exploration": True}
        )

    return fake_execute


def test_exploration_allow_once_resume_executes(monkeypatch, tmp_path):
    """真中断恢复分支：resume 值 allow_once → 执行且不置轮级标记（下次仍询问）。"""
    import langgraph.types as lg_types

    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.state import AgentState, PlanStep

    captured: list[dict] = []

    def fake_interrupt(payload):
        captured.append(payload)
        return {"action": "allow_once"}

    monkeypatch.setattr(lg_types, "interrupt", fake_interrupt)
    executed: list[str] = []
    monkeypatch.setattr(
        "core.retrieval.exploration.execute_exploration_query",
        _fake_exploration_execute(executed, tmp_path),
    )
    state = AgentState(user_query="复杂分析", autonomy_level="L2")
    step = PlanStep(
        id="s1",
        goal="复杂分析",
        kind="query",
        sql="SELECT SUM(split_total_amount) AS gmv FROM order_detail",
    )
    state, record = _orch_nodes._execute_exploration_step(state, step, tmp_path)
    assert captured and captured[0]["kind"] == "exploration", "L2 默认档必须真中断挂起"
    assert record.ok and executed, "allow_once 恢复后必须执行"
    assert not state.exploration_allowed, "allow_once 严禁置轮级允许标记"


def test_exploration_allow_session_resume_sets_round_flag(monkeypatch, tmp_path):
    """真中断恢复分支：resume 值 allow_session → 置轮级标记，同轮后续不再询问。"""
    import langgraph.types as lg_types

    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.state import AgentState, PlanStep

    captured: list[dict] = []

    def fake_interrupt(payload):
        captured.append(payload)
        return {"action": "allow_session"}

    monkeypatch.setattr(lg_types, "interrupt", fake_interrupt)
    executed: list[str] = []
    monkeypatch.setattr(
        "core.retrieval.exploration.execute_exploration_query",
        _fake_exploration_execute(executed, tmp_path),
    )
    state = AgentState(user_query="复杂分析", autonomy_level="L2")
    step1 = PlanStep(
        id="s1",
        goal="复杂分析",
        kind="query",
        sql="SELECT SUM(split_total_amount) AS gmv FROM order_detail",
    )
    state, record1 = _orch_nodes._execute_exploration_step(state, step1, tmp_path)
    assert record1.ok
    assert state.exploration_allowed is True, "allow_session 必须置轮级允许标记"
    step2 = PlanStep(
        id="s2",
        goal="再取一次",
        kind="query",
        sql="SELECT COUNT(order_id) AS cnt FROM order_detail",
    )
    state, record2 = _orch_nodes._execute_exploration_step(state, step2, tmp_path)
    assert len(captured) == 1, "同轮后续 sql 步骤不得再次挂起询问"
    assert record2.ok and len(executed) == 2


def test_exploration_audit_event_trio_emitted(monkeypatch, tmp_path):
    """三枚审计事件留痕（M6 计划 / 设计 §6 第 5 条）：lift_reject、
    exploration_approval、exploration_execute 全链路可捕获。"""
    import langgraph.types as lg_types

    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator import events as orch_events
    from core.orchestrator.prompts import PLANNER_SYSTEM
    from core.orchestrator.state import AgentState

    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        orch_events, "emit_event", lambda name, payload: emitted.append((name, payload))
    )

    def fake_llm_json(llm, system, user):
        # 仅 Planner 系统提示词返回 SQL 计划；reflector 等返回 None 走确定性判定
        return (
            _make_sql_step_payload(
                "WITH t AS (SELECT sku_id, SUM(1) AS c FROM order_detail GROUP BY sku_id) "
                "SELECT SUM(t.sku_id) AS x FROM t"
            )
            if system == PLANNER_SYSTEM
            else None
        )

    def _no_interrupt(payload):
        raise AssertionError("本用例 planner 阶段不应触发挂起")

    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", fake_llm_json)
    monkeypatch.setattr(lg_types, "interrupt", _no_interrupt)

    state = _orch_nodes.planner_node(AgentState(user_query="复杂分析", autonomy_level="L2"))
    names = [n for n, _ in emitted]
    assert "lift_reject" in names, "拒升清单必须留痕"
    # 拒升自愈耗尽后 SQL 计划保留交探索层（拒升 ≠ 拒答）
    assert state.plan_steps[0].sql and state.plan_steps[0].dsl is None

    executed: list[str] = []
    monkeypatch.setattr(
        "core.retrieval.exploration.execute_exploration_query",
        _fake_exploration_execute(executed, tmp_path),
    )
    monkeypatch.setattr(lg_types, "interrupt", lambda payload: {"action": "allow_once"})
    state, record = _orch_nodes._execute_exploration_step(state, state.plan_steps[0], tmp_path)
    assert record.ok
    names = [n for n, _ in emitted]
    assert "exploration_approval" in names, "审批动作必须留痕"
    assert "exploration_execute" in names, "探索执行必须留痕"
    approval = next(p for n, p in emitted if n == "exploration_approval")
    assert approval["action"] == "allow_once"
    execute_evt = next(p for n, p in emitted if n == "exploration_execute")
    assert execute_evt["approval"] == "allow_once"


def test_exploration_approval_graph_e2e_pause_and_resume(monkeypatch, tmp_path):
    """图级端到端（CRITICAL #1 回归锚点）：L2 + SQL 步骤经真实 LangGraph 图执行，
    plan_review approve 后探索审批门必须真挂起（__interrupt__ kind=exploration），
    deny/allow 两向恢复收敛终态。此前 GraphInterrupt 被 dsl_query_node 通用
    异常兜底吞掉，审批卡永远到不了用户——本测试防其回归。"""
    from langgraph.checkpoint.memory import MemorySaver

    import core.orchestrator.nodes as _orch_nodes
    from core.orchestrator.langgraph_engine import (
        build_langgraph_app,
        invoke_langgraph,
        resume_langgraph,
    )
    from core.orchestrator.prompts import PLANNER_SYSTEM
    from core.orchestrator.state import AgentState

    sql_payload = _make_sql_step_payload(
        "WITH t AS (SELECT sku_id, SUM(1) AS c FROM order_detail GROUP BY sku_id) "
        "SELECT SUM(t.sku_id) AS x FROM t"
    )

    def fake_llm_json(llm, system, user):
        return sql_payload if system == PLANNER_SYSTEM else None

    monkeypatch.setattr(_orch_nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(_orch_nodes, "_llm_json", fake_llm_json)
    executed: list[str] = []
    monkeypatch.setattr(
        "core.retrieval.exploration.execute_exploration_query",
        _fake_exploration_execute(executed, tmp_path),
    )

    # ---- 两向共享前半程：invoke → plan_review 挂起 → approve → 探索门挂起 ---- #
    def _reach_exploration_gate(thread_id: str):
        app = build_langgraph_app(checkpointer=MemorySaver())
        state = AgentState(
            user_query="复杂分析", autonomy_level="L2", session_id=thread_id, turn_id="t1"
        )
        final, pending = invoke_langgraph(state, thread_id=thread_id, app=app)
        assert pending and pending.get("kind") == "plan_review", "L2 多步计划先挂计划审批"
        final, pending = resume_langgraph(
            final, {"resume_value": {"action": "approve"}}, thread_id=thread_id, app=app
        )
        assert (
            pending and pending.get("kind") == "exploration"
        ), "探索审批门必须真挂起（GraphInterrupt 被吞即在此失败）"
        assert pending.get("sensitive") is False
        assert "order_detail" in (pending.get("tables") or [])
        assert not executed, "审批前 SQL 严禁执行（不泄露）"
        return final, pending, app

    # ---- deny 向：诚实拒答收敛，SQL 零执行 ---- #
    final, _pending, app = _reach_exploration_gate("e2e-exp-deny")
    final, _pending2 = resume_langgraph(
        final, {"resume_value": {"action": "deny"}}, thread_id="e2e-exp-deny", app=app
    )
    assert final.phase == "done"
    assert final.blocked_reason and "拒绝" in final.blocked_reason
    assert not executed, "deny 后 SQL 严禁执行"

    # ---- allow 向：allow_once 恢复执行，数据集带探索标记 ---- #
    final, _pending, app = _reach_exploration_gate("e2e-exp-allow")
    final, _pending2 = resume_langgraph(
        final, {"resume_value": {"action": "allow_once"}}, thread_id="e2e-exp-allow", app=app
    )
    assert final.phase == "done"
    assert executed, "allow_once 恢复后必须执行探索查询"
    dataset = final.datasets.get("s1")
    assert dataset is not None
    assert (dataset.get("audit") or {}).get("exploration") is True
