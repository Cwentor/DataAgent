"""审计修复 R1/R2/R3 的回归锚点：报告综合重构 / 归因口径对齐 / 降级保护。

- R1：Synthesizer 商业分析师提示词契约 + LLM 四段式综合 + 反契约 JSON 防御
  + 确定性兜底的人读数值渲染（严禁 raw dict/json 直出给用户）；
- R2：归因拆分强制继承总览 WHERE 过滤与时间窗口（口径对齐层）；
- R3：自愈额度耗尽 => 降级 Summarize 简报，严禁吐未加工 scratchpad/工具结果。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from core.orchestrator.state import MAX_RETRIES, AgentState, Artifact, ToolRecord


# --------------------------------------------------------------------------- #
# R1：Synthesizer 提示词契约
# --------------------------------------------------------------------------- #
def test_synthesizer_prompt_contract():
    """商业分析师提示词必须含四段式标题、raw JSON 禁令与人读数值要求。"""
    from core.orchestrator.prompts import SYNTHESIZER_SYSTEM

    assert "资深商业数据分析师" in SYNTHESIZER_SYSTEM
    assert "严禁向用户输出 raw dict/json" in SYNTHESIZER_SYSTEM
    assert "### 核心结论" in SYNTHESIZER_SYSTEM
    assert "### 区域归因定位" in SYNTHESIZER_SYSTEM
    assert "### 驱动因素分析（买家数/客单价/转化率）" in SYNTHESIZER_SYSTEM
    assert "### 业务假设与排查建议" in SYNTHESIZER_SYSTEM
    assert "万元" in SYNTHESIZER_SYSTEM and "主要矛盾" in SYNTHESIZER_SYSTEM


def test_degraded_summarizer_prompt_contract():
    """降级简报器提示词必须带惩罚约束：禁内部参数、必须注明口径差异。"""
    from core.orchestrator.prompts import DEGRADED_SUMMARIZER_SYSTEM

    assert "惩罚约束" in DEGRADED_SUMMARIZER_SYSTEM
    assert "严禁输出 raw dict/json" in DEGRADED_SUMMARIZER_SYSTEM
    assert "数据口径差异" in DEGRADED_SUMMARIZER_SYSTEM
    assert "### 部分结论（基于已获取数据）" in DEGRADED_SUMMARIZER_SYSTEM


# --------------------------------------------------------------------------- #
# R1：确定性兜底渲染（无 LLM 路径同样禁 raw 直出）
# --------------------------------------------------------------------------- #
def _summary_state(tmp_path) -> AgentState:
    """构造带分省归因 summary 产物的状态（模拟沙箱 analyze 产物）。"""
    return AgentState(
        session_id="r1",
        turn_id="t1",
        trace_id="tr1",
        user_query="分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
        artifacts=[
            Artifact(
                kind="summary",
                name="s2",
                payload={
                    "summary": {
                        "title": "两期对比与分省归因",
                        "metrics": {"baseline": 669300.0, "current": 616800.0, "delta": -52500.0},
                        "table": {
                            "columns": ["province", "baseline", "current", "delta", "share"],
                            "rows": [
                                ["北京", 200000.0, 120000.0, -80000.0, -0.65],
                                ["浙江", 150000.0, 140000.0, -10000.0, -0.08],
                            ],
                        },
                        "findings": [
                            "GMV 从 669300.00 变至 616800.00，下滑 -7.8%",
                            "主要矛盾省份 [北京] 贡献了 65% 的总偏差",
                        ],
                    }
                },
            )
        ],
    )


def test_synthesize_deterministic_fallback_is_human_readable(tmp_path, monkeypatch):
    """无 LLM 时综合兜底渲染：metrics 万元化、归因表 Markdown 化，禁 raw dict。"""
    from config import settings
    from core.orchestrator.nodes import synthesize_node

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    out = synthesize_node(_summary_state(tmp_path))
    report = out.report
    assert "66.93 万元" in report and "61.68 万元" in report
    assert "| 北京 |" in report  # 归因表渲染为 Markdown 表格
    assert '{"baseline"' not in report  # 严禁 raw JSON 直出
    assert "json.dumps" not in report
    assert report.count("### 两期对比与分省归因") == 1


class _FakeLLM:
    """契约内 LLM 桩：返回四段式商业报告。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def chat(self, messages):
        return self._text


def test_synthesize_llm_report_replaces_raw_rendering(tmp_path, monkeypatch):
    """LLM 可用时综合报告采用四段式商业叙事（不再直出内部渲染）。"""
    from config import settings
    from core.orchestrator import nodes as orch_nodes
    from core.orchestrator.nodes import synthesize_node

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    four_part = (
        "### 核心结论\nGMV 下滑 7.8%，主要矛盾是北京（贡献 65%）。\n\n"
        "### 区域归因定位\n北京下滑 8.0 万元。\n\n"
        "### 驱动因素分析（买家数/客单价/转化率）\n本轮数据未覆盖。\n\n"
        "### 业务假设与排查建议\n排查北京促销活动退出影响。"
    )
    monkeypatch.setattr(orch_nodes, "_resolve_llm", lambda: _FakeLLM(four_part))
    out = synthesize_node(_summary_state(tmp_path))
    assert out.report == four_part
    assert "### 核心结论" in out.report and "### 业务假设与排查建议" in out.report


def test_synthesize_rejects_raw_json_llm_output(tmp_path, monkeypatch):
    """LLM 违反契约回吐 JSON => 视为失败，回落确定性分析师渲染（不直出 raw）。"""
    from config import settings
    from core.orchestrator import nodes as orch_nodes
    from core.orchestrator.nodes import synthesize_node

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    raw = json.dumps({"baseline": 669300.0, "current": 616800.0}, ensure_ascii=False)
    monkeypatch.setattr(orch_nodes, "_resolve_llm", lambda: _FakeLLM(raw))
    out = synthesize_node(_summary_state(tmp_path))
    assert '{"baseline"' not in out.report  # 反契约输出被拦截
    assert "66.93 万元" in out.report  # 走确定性人读兜底


# --------------------------------------------------------------------------- #
# R2：口径对齐层
# --------------------------------------------------------------------------- #
def test_inherit_overview_scope_inherits_filters_and_window():
    """拆分 DSL 缺失状态过滤与时间窗口时，强制继承总览口径并产出说明。"""
    from core.orchestrator.nodes import _inherit_overview_scope

    overview = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
        "filters": [
            {"field": "pay_status", "operator": "eq", "value": "SUCCESS"},
            {"field": "order_amount", "operator": "gte", "value": 10},
        ],
        "time_filter": {
            "range_type": "absolute",
            "absolute": {"start": "2024-05-01", "end": "2024-05-08"},
        },
    }
    split = {"metrics": overview["metrics"], "dimensions": [{"field": "province"}]}
    aligned, notes = _inherit_overview_scope(split, [overview])
    # 状态类 eq 条件被继承；范围类 gte 条件不盲继承
    fields = [f["field"] for f in aligned["filters"]]
    assert "pay_status" in fields and "order_amount" not in fields
    assert aligned["time_filter"] == overview["time_filter"]
    assert any("pay_status" in n for n in notes) and any("时间窗口" in n for n in notes)


def test_inherit_overview_scope_keeps_declared_window():
    """拆分 DSL 已声明时间窗口（两期差异属合法）时保留，但记入口径说明。"""
    from core.orchestrator.nodes import _inherit_overview_scope

    overview = {
        "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
        "time_filter": {
            "range_type": "absolute",
            "absolute": {"start": "2024-05-01", "end": "2024-05-08"},
        },
    }
    split = {
        "dimensions": [{"field": "province"}],
        "time_filter": {
            "range_type": "absolute",
            "absolute": {"start": "2024-05-08", "end": "2024-05-15"},
        },
    }
    aligned, _notes = _inherit_overview_scope(split, [overview])
    assert aligned["time_filter"]["absolute"]["start"] == "2024-05-08"


def test_diagnostic_dsl_pair_carries_province_dimension():
    """诊断兜底两期对必须带省份维度且两期窗口/过滤完全同口径。"""
    from core.orchestrator.nodes import _diagnostic_dsl_pair

    base, curr = _diagnostic_dsl_pair("分析 5 月第一周比第二周 GMV 下滑原因")
    for dsl in (base, curr):
        assert dsl["dimensions"] == [{"field": "province"}]
        assert {"field": "pay_status", "operator": "eq", "value": "SUCCESS"} in dsl["filters"]
    assert base["time_filter"]["absolute"]["end"] == curr["time_filter"]["absolute"]["start"]


def test_diagnostic_e2e_report_contains_region_attribution(tmp_path, monkeypatch):
    """端到端：诊断问题报告必须含分省归因定位与驱动因素叙述（不再只有对比图）。"""
    from config import settings
    from core.orchestrator.agent import run_agent

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    trace = run_agent(
        "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位", session_id="r2e2e"
    )
    assert trace.phase == "done"
    assert "分省" in trace.report  # 归因表标题/表格
    assert "主要矛盾" in trace.report  # 主要矛盾省份叙述
    # ECharts 必须是分省对比（不再是 baseline/current 两根柱）
    charts = [a for a in trace.artifacts if a["kind"] == "echarts"]
    assert charts and charts[0]["payload"]["xAxis"]["data"] != ["baseline", "current"]


# --------------------------------------------------------------------------- #
# R3：额度耗尽降级保护
# --------------------------------------------------------------------------- #
def _exhausted_state(tmp_path) -> AgentState:
    """重试额度耗尽 + 无产物的状态（模拟多轮自愈失败后的终局）。"""
    state = AgentState(
        session_id="r3",
        turn_id="t1",
        trace_id="tr1",
        user_query="分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因",
        scratchpad=["[planner] heuristic", "[critic-llm] LLM 反思判定产物不充分"],
        tool_calls=[ToolRecord(tool="run_code", ok=False, error="KeyError: 'uv'（内部调试细节）")],
    )
    for i in range(MAX_RETRIES + 1):
        state.error_context.record(f"error {i}: KeyError 内部细节")
    return state


def test_exhausted_retries_degrade_to_structured_brief(tmp_path, monkeypatch):
    """额度耗尽 => 降级简报：结构化小节 + 口径差异说明，无内部轨迹泄漏。"""
    from config import settings
    from core.orchestrator.nodes import synthesize_node

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    out = synthesize_node(_exhausted_state(tmp_path))
    assert out.phase == "done"
    assert "### 部分结论（基于已获取数据）" in out.report
    assert "### 数据口径差异说明" in out.report
    # 严禁吐内部调试信息：scratchpad / 工具错误原文不得出现
    assert "critic-llm" not in out.report
    assert "KeyError" not in out.report
    assert "自愈记录" not in out.report
    assert "执行轨迹" not in out.report


def test_exhausted_retries_llm_brief_with_penalty_constraints(tmp_path, monkeypatch):
    """LLM 可用时降级路径调用带惩罚约束的 Summarize，输出结构化简报。"""
    from config import settings
    from core.orchestrator import nodes as orch_nodes
    from core.orchestrator.nodes import synthesize_node

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    brief = (
        "### 部分结论（基于已获取数据）\n北京、浙江等省份出现下滑。\n\n"
        "### 已定位的明细\n（表格）\n\n### 数据口径差异说明\n明细合计与总览存在口径出入。\n\n"
        "### 后续建议\n缩小范围重试。"
    )
    monkeypatch.setattr(orch_nodes, "_resolve_llm", lambda: _FakeLLM(brief))
    out = synthesize_node(_exhausted_state(tmp_path))
    assert out.report == brief  # 降级简报来自 Summarize 模型
    assert "KeyError" not in out.report  # 未加工错误仍被拦截


def test_pure_query_path_unaffected_by_diagnostic_dimensions(tmp_path, monkeypatch):
    """非诊断问题兜底取数保持单期总量（标量问题不带省份维度）。"""
    from config import settings
    from core.orchestrator.agent import run_agent

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    trace = run_agent("2024 年 5 月成功支付订单的 GMV 总额是多少？", session_id="r3scalar")
    assert trace.phase == "done"
    assert "查询答案：" in trace.report
    assert "万元" in trace.report


# --------------------------------------------------------------------------- #
# 辅助：归因模板在沙箱外的等价性验证（pandas 逻辑单测）
# --------------------------------------------------------------------------- #
def test_region_attribution_math_matches_template_logic():
    """分省加法归因的数学与模板一致：Δ = 当前期 - 基线期，share 按 |Δ| 归一。"""
    from core.skills.decomposition import additive_decomposition

    df = pd.DataFrame(
        {
            "dimension": ["北京", "浙江", "北京", "浙江"],
            "value": [200000.0, 150000.0, 120000.0, 140000.0],
            "period": ["baseline", "baseline", "current", "current"],
        }
    )
    out = additive_decomposition(df)
    assert out["total_delta"] == pytest.approx(-90000.0, abs=0.01)
    top = out["items"][0]
    assert top["dimension"] == "北京"
    assert abs(top["share"]) == pytest.approx(8 / 9, abs=0.01)  # 下滑贡献为负份额
