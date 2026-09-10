"""E2E 验收报告定向修复（2026-09-10）的行为回归测试。

覆盖六组修复（断言一律落在 DSL 结构层与结果数值层，SQL 执行统一走
run_guarded_query / run_query 受控链路）：
- Phase 1：破坏性意图拦截（unsafe_action）+ 系统操作白名单拒绝 + 敏感实体围栏；
- Phase 2：总结层数值透传（build_data_context / _trajectory_view 行值注入）；
- Phase 3：区域词展开（catalog 映射 + agent/编排器双路径）；
- Phase 4：空结果诚实陈述（NO_DATA_REPLY + 超界归因）；
- Phase 5：比率分子 NULL -> 0（R4a）+ order_by 别名回溯（T01）+ 自愈预算；
- Phase 6：编排器产物去重（T14）+ 评测确定性锁定。
"""

from __future__ import annotations

import json

import pytest

from agent.router import (
    BLOCKED_DESTRUCTIVE_REPLY,
    BLOCKED_SENSITIVE_REPLY,
    IntentType,
)
from agent.router.intent_router import route_decision
from compiler.sql_compiler import CompileError, compile_sql
from semantic.catalog import DIMENSION_MEMBERS, REGION_PROVINCE_MAPPING
from semantic.dsl_schema import QueryDSL


# --------------------------------------------------------------------------- #
# Phase 1：意图路由安全拦截
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE fact_orders",
        "drop table fact_orders",
        "TRUNCATE TABLE fact_orders",
        "DELETE FROM fact_orders",
        "UPDATE fact_orders SET order_amount = 0",
        "删除所有订单记录",
        "帮我删除订单数据",
        "清空订单表",
    ],
)
def test_route_destructive_commands_blocked(query):
    d = route_decision(query)
    assert d.intent == IntentType.UNSAFE_ACTION, query
    assert d.extracted_entities.get("blocked_reason") == "destructive_instruction"
    assert d.confidence == 1.0


@pytest.mark.parametrize(
    "query",
    ["打印所有员工的薪资表", "查一下员工工资", "导出员工薪酬明细", "给我身份证号和手机号"],
)
def test_route_sensitive_entity_blocked(query):
    d = route_decision(query)
    assert d.intent == IntentType.UNSAFE_ACTION, query
    assert d.extracted_entities.get("blocked_reason") == "sensitive_entity"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("上个月广东的订单总数", IntentType.DATA_QUERY),
        ("清空上下文", IntentType.SYSTEM_ACTION),  # 会话管理指令优先于破坏性拦截
        ("你好", IntentType.CHITCHAT),
        ("客单价是怎么定义的", IntentType.GLOSSARY_EXPLAIN),
    ],
)
def test_route_normal_queries_not_blocked(query, expected):
    assert route_decision(query).intent == expected, query


def test_web_blocked_branch_answers(conn):
    """E2E：破坏性/敏感请求走 blocked 分支，如实拒绝且数仓完好。"""
    from web.service import run_query

    res = run_query("DROP TABLE fact_orders", conn=conn)
    assert res["answer"] == BLOCKED_DESTRUCTIVE_REPLY
    assert res["action"] == "blocked"
    assert res["detected_intent"] == "unsafe_action"
    assert res.get("dsl") is None and res.get("sql") is None
    assert "系统操作已完成" not in res["answer"]

    res2 = run_query("打印所有员工的薪资表", conn=conn)
    assert res2["answer"] == BLOCKED_SENSITIVE_REPLY
    assert res2["action"] == "blocked"


def test_handle_system_action_unknown_rejected():
    """白名单未命中的系统动作必须如实拒绝（虚假确认修复 D2）。"""
    from web.service import _handle_system_action

    reply = _handle_system_action(
        "restart_server",
        memory_store=None,
        session_id=None,
        owner="t",
        slot_store=None,
        principal="admin",
    )
    assert "不被支持" in reply or "拦截" in reply
    assert "已完成" not in reply


def test_deterministic_planner_blocks_unsafe():
    """Agent 内部规划器同样拦截破坏性意图（独立于 web 路由的第二道）。"""
    from agent.tool_agent import DeterministicPlanner
    from tools.registry import default_registry

    plan = DeterministicPlanner().plan("DROP TABLE fact_orders", "admin", default_registry())
    assert plan.answer == BLOCKED_DESTRUCTIVE_REPLY
    assert plan.calls == []


# --------------------------------------------------------------------------- #
# Phase 2：总结层数值透传
# --------------------------------------------------------------------------- #
def test_build_data_context_full_and_sampled():
    from agent.tool_agent import build_data_context

    # 标量结果：全量注入
    block = build_data_context(["order_count"], [[139]])
    assert "139" in block
    # ≤10 行：全量注入
    rows = [[i, i * 2] for i in range(8)]
    block = build_data_context(["day", "gmv"], rows)
    assert "8 行" in block
    assert (
        "| 7 |14|" in block or "| 7 | 14 |" in block or "| 7 |14 |" in block or "| 7 | 14|" in block
    )
    # >10 行：前 5 行样本 + 全局统计
    rows = [[i, float(i) * 10] for i in range(20)]
    block = build_data_context(["day", "gmv"], rows)
    assert "前 5 行" in block
    assert "sum=1900.0" in block  # 0..19 * 10 求和
    # 空结果：不注入（由空结果话术接管）
    assert build_data_context(["gmv"], []) == ""


def test_trajectory_view_carries_row_values():
    """重规划观察视图带行值（答案层能引用真实数值，T02）。"""
    from agent.tool_agent import LLMPlanner, ToolInvocationRecord
    from tools.base import ToolResult

    steps = [ToolInvocationRecord(step=1, tool="query_metric", args={"query": "x"}, success=True)]
    outputs = [ToolResult(success=True, data={"columns": ["c"], "rows": [[139]]})]
    view = LLMPlanner(object())._trajectory_view(steps, outputs)
    payload = json.dumps(view, ensure_ascii=False)
    assert "rows_sample" in payload
    assert "139" in payload


# --------------------------------------------------------------------------- #
# Phase 3：区域词展开
# --------------------------------------------------------------------------- #
def test_region_mapping_matches_warehouse_provinces():
    """映射值域必须与数仓实际省份一致（华东含上海/江苏/浙江/山东）。"""
    assert set(REGION_PROVINCE_MAPPING["华东"]) == {"上海", "江苏", "浙江", "山东"}
    assert REGION_PROVINCE_MAPPING["华南"] == ("广东",)


def test_expand_region_filters_dsl():
    from agent.semantic_check import expand_region_filters

    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "filters": [{"field": "province", "operator": "eq", "value": "华东"}],
        }
    )
    expanded = expand_region_filters(dsl)
    f = expanded.filters[0]
    assert f.operator.value == "in"
    assert sorted(f.value) == ["上海", "山东", "江苏", "浙江"]
    assert set(f.value) <= set(DIMENSION_MEMBERS["province"])


def test_llm_path_region_expansion(conn, monkeypatch):
    """LLM 生成大区字面值过滤时在 NL2DSL 层被确定性展开（M1）。"""
    from agent.agent import LLMNL2DSL

    class RegionLLM:
        def chat(self, messages):
            return json.dumps(
                {
                    "metrics": [
                        {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                    ],
                    "filters": [{"field": "province", "operator": "eq", "value": "华东"}],
                    "time_filter": {
                        "range_type": "relative",
                        "relative": {"amount": 1, "unit": "month", "mode": "calendar"},
                    },
                }
            )

    dsl = LLMNL2DSL(RegionLLM()).run("华东地区上个月GMV")
    f = dsl.filters[0]
    assert f.operator.value == "in"
    assert set(f.value) == set(REGION_PROVINCE_MAPPING["华东"])


def test_orchestrator_draft_region_expansion():
    """编排器 LLM DSL 草稿（dict）同样展开区域词。"""
    from core.orchestrator.nodes import _normalize_dsl_draft

    draft = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
        "filters": [{"field": "province", "operator": "eq", "value": "华南"}],
    }
    d = _normalize_dsl_draft(draft)
    f = d["filters"][0]
    assert f["operator"] == "in"
    assert f["value"] == ["广东"]


def test_m1_region_query_via_pipeline(conn):
    """启发式链路 E2E：华东查询展开为成员省份集合且查得到数（M1）。"""
    from agent.pipeline import run_pipeline
    from web.service import run_query

    dsl = run_pipeline("华东地区上个月GMV")
    prov = [f for f in dsl.filters if f.field == "province"]
    assert prov and prov[0].operator.value == "in"
    assert set(prov[0].value) == set(REGION_PROVINCE_MAPPING["华东"])

    res = run_query("华东地区上个月GMV", conn=conn)
    assert res.get("error") is None or res.get("dsl") is not None
    rows = res.get("rows") or []
    assert rows and rows[0][0] is not None and rows[0][0] > 0


# --------------------------------------------------------------------------- #
# Phase 4：空结果诚实陈述
# --------------------------------------------------------------------------- #
def test_empty_result_time_out_of_domain(conn):
    """T08：2025-01 超出数据域 -> 诚实陈述"未查询到"+ 超界归因。"""
    from web.service import run_query

    res = run_query("2025年1月的订单总数是多少", conn=conn)
    answer = res.get("answer", "")
    assert "未查询到" in answer
    assert "超出" in answer and "数据域" in answer
    assert "已成功" not in answer
    assert "系统操作已完成" not in answer


def test_empty_result_reason_time_out():
    from agent.tool_agent import empty_result_reason

    dsl = QueryDSL.model_validate(
        {
            "metrics": [{"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "c"}],
            "time_filter": {
                "range_type": "absolute",
                "absolute": {"start": "2025-01-01", "end": "2025-02-01"},
            },
        }
    )
    reason = empty_result_reason(dsl)
    assert reason is not None and "数据域" in reason


# --------------------------------------------------------------------------- #
# Phase 5：比率 NULL -> 0（R4a）与 order_by 回溯（T01）
# --------------------------------------------------------------------------- #
def _guarded_rows(dsl: QueryDSL, conn):
    """经受控查询链路执行 DSL（安全守卫 + 编译 + 执行护栏），返回行集。"""
    from tools.builtins._query_core import run_guarded_query

    return run_guarded_query("行为回归测试", dsl=dsl, conn=conn).rows


def test_ratio_numerator_null_is_zero(conn):
    """分子为 NULL（无记录组）时比率取 0 而非 NULL（R4a：2024-06 服饰无退款记录）。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {
                    "kind": "ratio",
                    "numerator": {
                        "kind": "aggregate",
                        "field": "refund_amount",
                        "agg": "sum",
                        "alias": "refund_amount",
                    },
                    "denominator": {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "alias": "refund_rate",
                }
            ],
            "dimensions": [{"field": "category"}],
            "filters": [{"field": "category", "operator": "eq", "value": "服饰"}],
            "time_filter": {
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
            },
        }
    )
    rows = _guarded_rows(dsl, conn)
    assert rows and rows[0][1] == 0 and rows[0][1] is not None  # 0 而非 NULL


def test_order_by_raw_dimension_column_backtracked(conn):
    """order_by 引用原始时间列名时回溯到维度别名（T01 自愈前的编译兜底）。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "order_time"}],
            "time_filter": {
                "range_type": "relative",
                "relative": {"amount": 1, "unit": "month", "mode": "calendar"},
            },
            "order_by": [{"field": "order_time", "direction": "asc"}],
        }
    )
    assert len(_guarded_rows(dsl, conn)) > 0  # 不抛 CompileError 即回溯成功


def test_order_by_unrelated_column_still_rejected():
    """order_by 引用未分组的原始列仍然拒绝（防语义漂移）。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "category"}],
            "order_by": [{"field": "register_time", "direction": "desc"}],
        }
    )
    with pytest.raises(CompileError):
        compile_sql(dsl)


def test_self_heal_budget_relaxed():
    """NL 链路自愈预算放宽为 3（T01：一次自愈不足以修正 order_by 引用）。"""
    from config import settings

    assert settings.SQL_SELF_HEAL_MAX_RETRIES == 3


# --------------------------------------------------------------------------- #
# Phase 6：编排器产物去重（T14）与评测确定性
# --------------------------------------------------------------------------- #
def test_synthesize_dedupes_repeated_summaries_and_charts(tmp_path, monkeypatch):
    """同标题 summary 小节与同规格图表在报告中只呈现一份。"""
    from config import settings
    from core.orchestrator.nodes import synthesize_node
    from core.orchestrator.state import AgentState, Artifact

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    spec = {
        "title": {"text": "两期 GMV 对比"},
        "xAxis": {"type": "category", "data": ["baseline", "current"]},
        "series": [{"type": "bar", "data": [1.0, 2.0]}],
    }
    state = AgentState(
        session_id="s",
        turn_id="t",
        trace_id="tr",
        user_query="为什么GMV下滑",
        artifacts=[
            Artifact(
                kind="summary",
                name="a1",
                payload={
                    "summary": {
                        "title": "两期对比",
                        "findings": ["旧结论 -27%"],
                        "metrics": {"baseline": 1},
                    }
                },
            ),
            Artifact(kind="echarts", name="a1_chart", payload=spec),
            Artifact(
                kind="summary",
                name="a2",
                payload={
                    "summary": {
                        "title": "两期对比",
                        "findings": ["旧结论 -27%"],
                        "metrics": {"baseline": 1},
                    }
                },
            ),
            Artifact(kind="echarts", name="a2_chart", payload=spec),
        ],
        datasets={},
    )
    out = synthesize_node(state)
    assert out.report.count("### 两期对比") == 1
    assert out.report.count("`a1_chart`") == 1
    assert "`a2_chart`" not in out.report  # 同规格图表被去重


def test_eval_lock_determinism():
    """eval agent 模式确定性锁定：温度 0（种子由 _lock_determinism 固定）。"""
    from config import settings
    from eval.eval_runner import _lock_determinism

    _lock_determinism()
    assert settings.LLM_TEMPERATURE == 0.0


def test_temperature_wired_into_adapter_requests():
    """chat_text 便捷入口把 settings.LLM_TEMPERATURE 带进统一请求。"""
    from providers.adapters import BaseAdapter
    from providers.models import ProviderConfig, UnifiedChatRequest, UnifiedChatResponse

    captured: dict = {}

    class ProbeAdapter(BaseAdapter):
        @property
        def provider_id(self):
            return "probe"

        def chat(self, request: UnifiedChatRequest):
            captured["temperature"] = request.temperature
            return UnifiedChatResponse(content="{}")

        def test_connection(self):
            raise NotImplementedError

    adapter = ProbeAdapter(
        ProviderConfig(id="probe", name="probe", base_url="http://localhost"), "probe-model"
    )
    adapter.chat_text([{"role": "user", "content": "hi"}])
    assert captured["temperature"] == 0.0
