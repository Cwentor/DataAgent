"""报告叙述化修复（2026-09）回归锚点。

- 确定性渲染：嵌套结构表格化、数值三分格式化、小节标题中文化——严禁 repr dump；
- 小节标题：中文直用 / 英文 id 回退步骤 goal / 无匹配回退"归因分析（id）"；
- 渲染隔离：畸形 summary 降级为占位文案，不拖垮整份报告。
"""

from __future__ import annotations

from core.orchestrator.state import AgentState, Artifact, PlanStep


# --------------------------------------------------------------------------- #
# 截图同款形态的状态构造：英文 title + 嵌套 metrics
# --------------------------------------------------------------------------- #
def _diag_state() -> AgentState:
    return AgentState(
        session_id="nar",
        turn_id="t1",
        trace_id="tr1",
        user_query="分析一下 2024 年 5 月第二周比第一周 GMV 下滑的原因，按地区定位",
        plan_steps=[
            PlanStep(
                id="factor_decomposition",
                goal="乘法因子分解：定位量跌还是价跌",
                kind="analyze",
            ),
            PlanStep(
                id="region_drilldown",
                goal="地区维度下钻：定位主要下滑省份",
                kind="analyze",
            ),
        ],
        artifacts=[
            Artifact(
                kind="summary",
                name="s1",
                payload={
                    "summary": {
                        "title": "factor_decomposition",
                        "findings": ["GMV 从 61.69 万元 变至 41.02 万元，下滑 -33.5%"],
                        "metrics": {
                            "week1": {"gmv": 616872.81, "orders": 44.0, "buyers": 39.0},
                            "week2": {"gmv": 410248.48, "orders": 32.0, "buyers": 30.0},
                            "gmv_change_pct": -0.3347,
                            "orders_per_buyer": 1.1282,
                            "volume_vs_price": "量跌为主",
                            "province_delta_top": [
                                {
                                    "province": "北京",
                                    "gmv_w1": 112791.59,
                                    "gmv_w2": 10140.62,
                                    "gmv_delta": -107150.97,
                                },
                                {
                                    "province": "湖北",
                                    "gmv_w1": 124300.98,
                                    "gmv_w2": 78142.17,
                                    "gmv_delta": -46158.81,
                                },
                            ],
                        },
                        "table": {},
                    }
                },
            )
        ],
    )


def _render_report(monkeypatch, tmp_path, state: AgentState) -> str:
    from config import settings
    from core.orchestrator import nodes as orch_nodes
    from core.orchestrator.nodes import synthesize_node

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch_nodes, "_resolve_llm", lambda: None)
    return synthesize_node(state).report


def test_fallback_render_no_repr_dump(tmp_path, monkeypatch):
    """嵌套 metrics 渲染为表格，严禁 repr 与长浮点直出。"""
    report = _render_report(monkeypatch, tmp_path, _diag_state())
    assert "{'gmv'" not in report  # 禁 dict repr
    assert "616872.81" not in report  # 禁长浮点直出（必须舍入/万元化）
    assert "1.1282051282051282" not in report
    assert "41.02 万元" in report  # week2 gmv 万元化（对比表）
    assert "-33.5%" in report  # 比率百分比化（严禁 "-0.33 万元" 类错乱）
    assert "| 北京 |" in report  # province_delta_top 表格化
    assert "-10.72 万元" in report  # gmv_delta 万元化


def test_fallback_render_section_title_falls_back_to_step_goal(tmp_path, monkeypatch):
    """英文 title（step.id）=> 小节标题回退计划步骤中文 goal。"""
    report = _render_report(monkeypatch, tmp_path, _diag_state())
    assert "### 乘法因子分解：定位量跌还是价跌" in report
    assert "### factor_decomposition" not in report


def test_section_title_rules():
    """_section_title 三级回退：中文直用 / id 匹配 goal / 无匹配括注 id。"""
    from core.orchestrator.nodes import _section_title

    state = _diag_state()
    assert _section_title({"title": "驱动因子分解"}, state) == "驱动因子分解"
    assert _section_title({"title": "region_drilldown"}, state) == "地区维度下钻：定位主要下滑省份"
    assert _section_title({"title": "coder_custom_name"}, state) == "归因分析（coder_custom_name）"
    assert _section_title({}, None) == "归因分析"


def test_metric_human_type_discipline():
    """数值三分：比率百分比、计数原值、金额万元、未知数值舍入、字符串原样。"""
    from core.orchestrator.nodes import _metric_human

    assert _metric_human(-0.3347, "gmv_change_pct") == "-33.5%"
    assert _metric_human(-0.65, "share") == "-65.0%"
    assert _metric_human(616872.81, "gmv") == "61.69 万元"
    assert _metric_human(44.0, "orders") == "44"
    assert _metric_human(1.1282, "orders_per_buyer") == "1.13"  # 未知比值：舍入原样
    assert _metric_human("量跌为主", "volume_vs_price") == "量跌为主"


def test_malformed_summary_degrades_without_crash(tmp_path, monkeypatch):
    """畸形 summary（metrics=None、行混合类型）=> 渲染不崩溃、不 dump。"""
    state = AgentState(
        session_id="nar2",
        turn_id="t1",
        trace_id="tr2",
        user_query="畸形产物渲染",
        artifacts=[
            Artifact(
                kind="summary",
                name="s1",
                payload={
                    "summary": {
                        "title": "weird_step",
                        "metrics": None,
                        "findings": ["只有一条结论"],
                        "table": {"columns": ["k", "v"], "rows": [["a", 1], "not-a-row"]},
                    }
                },
            )
        ],
    )
    report = _render_report(monkeypatch, tmp_path, state)
    assert "只有一条结论" in report
    assert "{" not in report  # 不 dump
