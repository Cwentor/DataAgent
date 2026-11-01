"""展示层单元测试：DSL -> 解释 + 可视化推荐。"""

from __future__ import annotations

from present.explain import explain
from present.viz import recommend_viz, viz_config
from semantic.dsl_schema import QueryDSL


def _dsl(**over):
    base = {
        "metrics": [
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
        ],
    }
    base.update(over)
    return QueryDSL.model_validate(base)


# --------------------------------------------------------------------------- #
# explain
# --------------------------------------------------------------------------- #
def test_explain_single_metric():
    dsl = _dsl(
        filters=[{"field": "order_status", "operator": "eq", "value": "1002"}],
    )
    text = explain(dsl)
    assert "gmv" in text
    assert "求和实付金额" in text
    assert "订单状态 等于 已支付" in text


def test_explain_dimension_and_time():
    dsl = _dsl(
        dimensions=[{"field": "category1_name"}],
        time_filter={
            "granularity": "day",
            "range_type": "absolute",
            "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
        },
    )
    text = explain(dsl)
    assert "按 一级类目 分组" in text
    assert "2024-06-01 至 2024-07-01" in text


def test_explain_ratio_metric():
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {
                    "kind": "ratio",
                    "numerator": {
                        "kind": "aggregate",
                        "field": "split_total_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "denominator": {
                        "kind": "aggregate",
                        "field": "user_id",
                        "agg": "count_distinct",
                        "alias": "active_users",
                    },
                    "alias": "arpu",
                }
            ],
        }
    )
    text = explain(dsl)
    assert "arpu" in text
    assert "除以" in text


def test_explain_comparison():
    dsl = _dsl(
        time_filter={
            "granularity": "day",
            "range_type": "absolute",
            "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
            "comparison": "mom",
        },
    )
    text = explain(dsl)
    assert "环比" in text


# --------------------------------------------------------------------------- #
# viz
# --------------------------------------------------------------------------- #
def test_viz_number():
    dsl = _dsl()
    assert recommend_viz(dsl, ("gmv",), ()) == "number"


def test_viz_line_for_time_trend():
    dsl = _dsl(dimensions=[{"field": "order_time"}])
    assert recommend_viz(dsl, ("order_time", "gmv"), [(1,), (2,)]) == "line"


def test_viz_bar_for_many_categories():
    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    rows = tuple((f"c{i}",) for i in range(20))
    assert recommend_viz(dsl, ("category1_name", "gmv"), rows) == "bar"


def test_viz_pivot_for_multi_dimension():
    dsl = _dsl(dimensions=[{"field": "category1_name"}, {"field": "tm_name"}])
    rows = (("a", "b", 1),)
    assert recommend_viz(dsl, ("category1_name", "tm_name", "gmv"), rows) == "pivot"


def test_viz_pivot_for_multi_metric():
    dsl = _dsl(
        metrics=[
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"},
            {"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "order_count"},
        ],
        dimensions=[{"field": "category1_name"}],
    )
    assert recommend_viz(dsl, ("category1_name", "gmv", "order_count"), (("a", 1, 2),)) == "pivot"


def test_explain_window_metric():
    dsl = _dsl(
        metrics=[
            {
                "kind": "window",
                "base": {
                    "kind": "aggregate",
                    "field": "split_total_amount",
                    "agg": "sum",
                    "alias": "gmv",
                },
                "func": "cumsum",
                "alias": "cum_gmv",
            }
        ],
        dimensions=[{"field": "order_time"}],
    )
    text = explain(dsl)
    assert "累计" in text
    assert "cum_gmv" in text


def test_explain_moving_avg():
    dsl = _dsl(
        metrics=[
            {
                "kind": "window",
                "base": {
                    "kind": "aggregate",
                    "field": "split_total_amount",
                    "agg": "sum",
                    "alias": "gmv",
                },
                "func": "moving_avg",
                "window_size": 7,
                "alias": "ma7_gmv",
            }
        ],
        dimensions=[{"field": "order_time"}],
    )
    text = explain(dsl)
    assert "7 日移动平均" in text


def test_explain_top_n():
    dsl = _dsl(
        dimensions=[{"field": "province"}, {"field": "category1_name"}],
        top_n={
            "n": 3,
            "partition_by": ["province"],
            "order_by": [{"field": "gmv", "direction": "desc"}],
        },
    )
    text = explain(dsl)
    assert "每个 省份 取前 3 条" in text


def test_explain_fill_gaps():
    dsl = _dsl(dimensions=[{"field": "order_time"}], fill_gaps=True)
    text = explain(dsl)
    assert "缺失日期补零" in text


def test_viz_window_metric_is_line():
    dsl = _dsl(
        metrics=[
            {
                "kind": "window",
                "base": {
                    "kind": "aggregate",
                    "field": "split_total_amount",
                    "agg": "sum",
                    "alias": "gmv",
                },
                "func": "cumsum",
                "alias": "cum_gmv",
            }
        ],
        dimensions=[{"field": "order_time"}],
    )
    rows = tuple((f"2024-06-{i:02d}", i) for i in range(5))
    assert recommend_viz(dsl, ("order_time", "cum_gmv"), rows) == "line"


def test_viz_config_shape():
    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    cfg = viz_config(dsl, ("category1_name", "gmv"), (("a",),))
    assert cfg["chart"] == "pie"
    assert cfg["x"] == "category1_name"
    assert cfg["y"] == "gmv"
    # ECharts 级渲染契约：series/axis/legend/tooltip 齐全（报告整改指令3-1）
    e = cfg["echarts"]
    assert e["tooltip"]["trigger"] == "item"
    assert e["legend"]["data"] == ["gmv"]
    assert e["series"][0]["type"] == "pie"
    assert e["series"][0]["encode"]["value"] == 1


def test_viz_config_line_has_axes():
    """时间趋势：折线契约包含 x/y 轴与平滑配置。"""
    dsl = _dsl(dimensions=[{"field": "order_time"}])
    cfg = viz_config(dsl, ("order_time", "gmv"), (("2024-06-01", 1),))
    assert cfg["chart"] == "line"
    e = cfg["echarts"]
    assert e["xAxis"]["type"] == "category"
    assert e["yAxis"]["type"] == "value"
    assert e["series"][0]["type"] == "line"
    assert e["series"][0]["smooth"] is True
    assert e["series"][0]["encode"] == {"x": 0, "y": 1}


def test_viz_table_for_non_numeric_y():
    """数值类型信号：y 列显著非数值（占比 < 0.5）时强制降级明细表。"""
    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    rows = (("数码", "高"), ("家电", "中"), ("服饰", "低"))
    assert recommend_viz(dsl, ("category1_name", "gmv"), rows) == "table"


def test_viz_pivot_contract():
    """多维结果：pivot 契约提供列结构标注（前端表格渲染依据）。"""
    dsl = _dsl(dimensions=[{"field": "category1_name"}, {"field": "tm_name"}])
    cfg = viz_config(dsl, ("category1_name", "tm_name", "gmv"), (("a", "b", 1),))
    assert cfg["chart"] == "pivot"
    e = cfg["echarts"]
    assert e["columns"] == [
        {"name": "category1_name", "index": 0},
        {"name": "tm_name", "index": 1},
        {"name": "gmv", "index": 2},
    ]


def test_build_chart_spec_echarts_contract():
    """ChartSpec 复合契约：携带 echarts option 且 to_dict 可序列化。"""
    from present.viz import build_chart_spec

    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    spec = build_chart_spec(dsl, ("category1_name", "gmv"), (("a", 1),))
    assert spec.chart == "pie"
    payload = spec.to_dict()
    assert payload["echarts"]["series"][0]["type"] == "pie"
    assert payload["columns"] == ["category1_name", "gmv"]
    assert payload["rows"] == [["a", 1]]


# --------------------------------------------------------------------------- #
# pie 正数信号：占比语义在全负/零值上误导，退回柱状图（整改指令3-1 信号补全）
# --------------------------------------------------------------------------- #
def test_viz_bar_when_all_values_nonpositive():
    """y 列可判定且全部非正 -> 拒绝 pie（退回 bar）。"""
    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    rows = tuple((f"c{i}", -10.0 - i) for i in range(5))
    assert recommend_viz(dsl, ("category1_name", "gmv"), rows) == "bar"


def test_viz_pie_when_any_positive_value():
    """存在正数值 -> pie 信号满足（即使夹杂负值）。"""
    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    rows = tuple((f"c{i}", -5.0 if i == 0 else 10.0 + i) for i in range(5))
    assert recommend_viz(dsl, ("category1_name", "gmv"), rows) == "pie"


def test_viz_pie_without_y_signal_unaffected():
    """无可判定 y 值（全部缺列）：信号不足不拦截，维持原 pie 行为。"""
    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    rows = tuple((f"c{i}",) for i in range(5))
    assert recommend_viz(dsl, ("category1_name", "gmv"), rows) == "pie"


# --------------------------------------------------------------------------- #
# 候选集契约（#24）：主推荐居首 + 按适配度排序的候选列表 + 排除原因。
# 误导性图表是一种虚构，切换必须限制在适配性候选集内——excluded 类型
# 不携带 option（前端切换控件不可达）。
# --------------------------------------------------------------------------- #
def _candidate(cfg, chart):
    return next(c for c in cfg["candidates"] if c["chart"] == chart)


def test_viz_candidates_contract_shape():
    """候选集契约：六类型齐全、主推荐居首、excluded 不带 option。"""
    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    cfg = viz_config(dsl, ("category1_name", "gmv"), (("a", 1),))
    # 既有契约不破坏：chart/x/y/echarts 键仍为既有语义
    assert cfg["chart"] == "pie"
    assert cfg["x"] == "category1_name"
    assert cfg["y"] == "gmv"
    # 候选集：六类型齐全，主推荐 recommended 居首且与 chart 一致
    assert len(cfg["candidates"]) == 6
    assert len({c["chart"] for c in cfg["candidates"]}) == 6
    assert cfg["candidates"][0]["chart"] == cfg["chart"]
    assert cfg["candidates"][0]["status"] == "recommended"
    # 每条候选带非空原因；非 excluded 携带预生成 option，excluded 为 None
    # （table/pivot 的 option 为列结构标注，不含 series）
    for cand in cfg["candidates"]:
        assert cand["reason"]
        if cand["status"] == "excluded":
            assert cand["echarts"] is None
        else:
            assert isinstance(cand["echarts"], dict) and cand["echarts"]


def test_viz_candidates_primary_matches_recommend_viz_all_shapes():
    """主推荐单一事实源：各形状下 candidates[0].chart 与 recommend_viz 一致。"""
    shapes = [
        (_dsl(), ("gmv",), ()),
        (
            _dsl(dimensions=[{"field": "order_time"}]),
            ("order_time", "gmv"),
            [("2024-06-01", 1.0), ("2024-06-02", 2.0)],
        ),
        (
            _dsl(dimensions=[{"field": "category1_name"}]),
            ("category1_name", "gmv"),
            tuple((f"c{i}", 10.0 + i) for i in range(20)),
        ),
        (
            _dsl(dimensions=[{"field": "category1_name"}, {"field": "tm_name"}]),
            ("category1_name", "tm_name", "gmv"),
            (("a", "b", 1),),
        ),
        (
            _dsl(dimensions=[{"field": "category1_name"}]),
            ("category1_name", "gmv"),
            (("数码", "高"), ("家电", "中"), ("服饰", "低")),
        ),
    ]
    for dsl, cols, rows in shapes:
        cfg = viz_config(dsl, cols, rows)
        assert cfg["chart"] == recommend_viz(dsl, cols, rows)
        assert cfg["candidates"][0]["chart"] == cfg["chart"]
        assert cfg["candidates"][0]["status"] == "recommended"


def test_viz_candidates_time_trend():
    """时间趋势：line 主推荐，bar/pie 可切换，number 排除带原因。"""
    dsl = _dsl(dimensions=[{"field": "order_time"}])
    rows = tuple((f"2024-06-{i + 1:02d}", 10.0 + i) for i in range(5))
    cfg = viz_config(dsl, ("order_time", "gmv"), rows)
    assert _candidate(cfg, "line")["status"] == "recommended"
    assert _candidate(cfg, "bar")["status"] == "available"
    assert _candidate(cfg, "pie")["status"] == "available"
    assert _candidate(cfg, "number")["status"] == "excluded"
    assert _candidate(cfg, "number")["reason"]
    assert _candidate(cfg, "table")["status"] == "available"


def test_viz_candidates_pie_excluded_for_many_categories():
    """基数信号：类别基数超限时 pie 排除（柱状更可读）。"""
    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    rows = tuple((f"c{i}", 10.0 + i) for i in range(20))
    cfg = viz_config(dsl, ("category1_name", "gmv"), rows)
    assert _candidate(cfg, "bar")["status"] == "recommended"
    assert _candidate(cfg, "pie")["status"] == "excluded"
    assert _candidate(cfg, "pie")["reason"]


def test_viz_candidates_non_numeric_y_excludes_all_charts():
    """数值信号：y 列非数值占比高时全部图形类排除，table 兜底主推荐。"""
    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    rows = (("数码", "高"), ("家电", "中"), ("服饰", "低"))
    cfg = viz_config(dsl, ("category1_name", "gmv"), rows)
    assert _candidate(cfg, "table")["status"] == "recommended"
    for chart in ("number", "line", "bar", "pie", "pivot"):
        assert _candidate(cfg, chart)["status"] == "excluded"


def test_viz_candidates_single_value():
    """单值：number 主推荐，table 兜底可切换，图形类排除。"""
    dsl = _dsl()
    cfg = viz_config(dsl, ("gmv",), ())
    assert _candidate(cfg, "number")["status"] == "recommended"
    assert _candidate(cfg, "table")["status"] == "available"
    assert _candidate(cfg, "line")["status"] == "excluded"
    assert _candidate(cfg, "bar")["status"] == "excluded"


def test_viz_candidates_pivot_for_multi_dimension():
    """多维结果：pivot 主推荐，table 兜底可用，pie 排除。"""
    dsl = _dsl(dimensions=[{"field": "category1_name"}, {"field": "tm_name"}])
    cfg = viz_config(dsl, ("category1_name", "tm_name", "gmv"), (("a", "b", 1),))
    assert _candidate(cfg, "pivot")["status"] == "recommended"
    assert _candidate(cfg, "table")["status"] == "available"
    assert _candidate(cfg, "pie")["status"] == "excluded"


def test_viz_candidates_multi_metric_bar_available():
    """单维多指标：pivot 主推荐，分组柱状为可用候选，pie 排除。"""
    dsl = _dsl(
        metrics=[
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"},
            {"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "order_count"},
        ],
        dimensions=[{"field": "category1_name"}],
    )
    cfg = viz_config(dsl, ("category1_name", "gmv", "order_count"), (("a", 1, 2),))
    assert _candidate(cfg, "pivot")["status"] == "recommended"
    assert _candidate(cfg, "bar")["status"] == "available"
    assert _candidate(cfg, "pie")["status"] == "excluded"


def test_chart_spec_carries_candidates():
    """ChartSpec 候选集透传：to_dict 两种模式均携带 candidates。"""
    from present.viz import build_chart_spec

    dsl = _dsl(dimensions=[{"field": "category1_name"}])
    spec = build_chart_spec(dsl, ("category1_name", "gmv"), (("a", 1),))
    assert spec.candidates and spec.candidates[0]["chart"] == "pie"
    payload = spec.to_dict()
    assert payload["candidates"][0]["chart"] == "pie"
    lean = spec.to_dict(include_data=False)
    assert lean["candidates"][0]["status"] == "recommended"
