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


# --------------------------------------------------------------------------- #
# 修复轮（评审 Important #1 + Minor #2/#4）
# --------------------------------------------------------------------------- #
def test_nested_containers_never_repr_dump(tmp_path, monkeypatch):
    """二层嵌套 dict 与 list-of-list 严禁 repr 直出（修复轮 Important #1）。"""
    state = AgentState(
        session_id="nar3",
        turn_id="t1",
        trace_id="tr3",
        user_query="嵌套结构渲染",
        artifacts=[
            Artifact(
                kind="summary",
                name="s1",
                payload={
                    "summary": {
                        "title": "嵌套结构",
                        "findings": ["存在二层嵌套与配对列表"],
                        "metrics": {
                            "extra_stat": {"week1": {"gmv": 1.0, "inner": {"a": 1}}},
                            "pairs": [["A", 1], ["B", 2]],
                        },
                        "table": {
                            "columns": ["dim", "payload"],
                            "rows": [{"dim": "北京", "payload": ["x", "y"]}],
                        },
                    }
                },
            )
        ],
    )
    report = _render_report(monkeypatch, tmp_path, state)
    assert "{'" not in report  # 禁 dict repr（单引号形态）
    assert "['" not in report  # 禁 list repr
    assert '{"' not in report  # 禁 dict repr（双引号形态）
    assert "（嵌套明细）" in report  # depth >= 2 折叠
    assert "（共 2 项明细）" in report  # list 值折叠（pairs 与 table 单元格）


def test_metric_human_nested_depth_semantics():
    """_metric_human 容器守卫与 depth 语义（修复轮 Important #1）。"""
    from core.orchestrator.nodes import _metric_human

    assert _metric_human([1, 2, 3], "") == "（共 3 项明细）"
    assert _metric_human(("a", "b"), "pair") == "（共 2 项明细）"
    assert _metric_human({"gmv": 1.0}, "") == "（GMV 0.00 万元）"
    # depth 耗尽：二层以下折叠，键值经 _metric_label 中文化
    assert _metric_human({"a": 1}, "", depth=2) == "（嵌套明细）"
    assert (
        _metric_human({"week1": {"inner": {"a": 1}}}, "extra_stat")
        == "（第 1 周 （inner （嵌套明细）））"
    )


def test_list_dict_table_missing_cell_placeholder(tmp_path, monkeypatch):
    """list[dict] 行缺列 => 缺失单元格渲染为 — 占位对齐（修复轮 Minor #2）。"""
    state = AgentState(
        session_id="nar4",
        turn_id="t1",
        trace_id="tr4",
        user_query="缺列表格渲染",
        artifacts=[
            Artifact(
                kind="summary",
                name="s1",
                payload={
                    "summary": {
                        "title": "缺列表格",
                        "findings": [],
                        "metrics": {
                            "region_rows": [
                                {"province": "北京", "gmv_delta": -107150.97},
                                {"province": "湖北"},
                            ]
                        },
                        "table": {},
                    }
                },
            )
        ],
    )
    report = _render_report(monkeypatch, tmp_path, state)
    assert "| — |" in report  # 缺失单元格占位
    assert "| 湖北 | — |" in report  # 第二行缺 gmv_delta 列，占位对齐


def test_render_isolation_degrades_section_on_crash(tmp_path, monkeypatch):
    """单 summary 渲染抛错 => 小节降级占位、整体不抛（修复轮 Minor #4）。"""
    from core.orchestrator import nodes as orch_nodes

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    state = AgentState(
        session_id="nar5",
        turn_id="t1",
        trace_id="tr5",
        user_query="渲染隔离",
        artifacts=[
            Artifact(kind="summary", name="s1", payload={"summary": {"title": "会炸的小节"}})
        ],
    )
    monkeypatch.setattr(orch_nodes, "_summary_analyst_markdown", _boom)
    report = _render_report(monkeypatch, tmp_path, state)
    assert "### 归因分析" in report
    assert "（该分析产物无法渲染，原始文件已留存工作区）" in report


# --------------------------------------------------------------------------- #
# Task 6 收尾修复（台账裁决）：_render_table 三个显式分类分支的容器值守卫
# --------------------------------------------------------------------------- #
def test_render_table_ratio_column_container_value_folds():
    """比率列单元格容器值 => 折叠占位，严禁 _fmt_pct str 兜底 repr 直出。"""
    from core.orchestrator.nodes import _render_table

    table = {
        "columns": ["province", "gmv_change_pct"],
        "rows": [
            {
                "province": "北京",
                "gmv_change_pct": {"baseline": 616872.81, "current": 410248.48},
            }
        ],
    }
    text = "\n".join(_render_table(table))
    assert "{'" not in text and '["' not in text  # 严禁 repr 片段
    assert "（" in text  # 容器折叠/展开占位
    assert "基期" in text  # dict 一层展开且键经 _metric_label 中文化


def test_render_table_money_column_container_value_folds():
    """currency=True 金额列单元格容器值 => 折叠占位，严禁 _fmt_wan str 兜底 repr。"""
    from core.orchestrator.nodes import _render_table

    table = {
        "columns": ["province", "gmv_delta"],
        "rows": [{"province": "北京", "gmv_delta": {"w1": 112791.59, "w2": 10140.62}}],
    }
    text = "\n".join(_render_table(table, currency=True))
    assert "{'" not in text  # 严禁 dict repr
    assert "（" in text  # 容器折叠/展开占位
    # dict 一层展开：内部键 w1/w2 不命中金额 token，按既有守卫语义舍入渲染
    # （"宁可无单位，不可错单位"；修改内部分类逻辑超出本修复范围）
    assert "（w1 112791.59；w2 10140.62）" in text


def test_render_table_count_column_container_value_folds():
    """currency=False 计数列单元格容器值 => 折叠占位，严禁 _fmt_count str 兜底 repr。"""
    from core.orchestrator.nodes import _render_table

    table = {
        "columns": ["factor", "orders"],
        "rows": [{"factor": "买家数", "orders": ["线上", "线下"]}],
    }
    text = "\n".join(_render_table(table, currency=False))
    assert "['" not in text  # 严禁 list repr
    assert "（共 2 项明细）" in text  # list 折叠为明细占位
