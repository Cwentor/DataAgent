"""可视化推荐：根据 DSL + 结果形状推荐图表类型（确定性规则）并输出渲染契约。

规则（按优先级）：
1. 无维度、单指标 -> "number"（单值卡片）；
2. 维度含时间字段（order_time/refund_time/register_time）-> "line"（折线）；
3. 单维度、单指标 -> "bar"（柱状），类别数 <= 8 且 y 数值占比高可 "pie"（饼图）；
4. 多维或单维多指标 -> "pivot"（分组表格，前端真实渲染）；
5. 其余 -> "table"（明细表）。

信号增强（报告整改指令3-1）：
- 数值类型信号：y 列非数值占比高（如 >50%）时强制降级 "table"，避免 bar/pie
  在非数值列上渲染出 NaN；
- 基数信号：类别基数 <= 8 且 y 存在正数才建议 pie；
- 时序间距信号：时间维度且基数大时优先 line（保持时间序），不因基数小降级 bar。

渲染契约（ChartSpec / viz_config）：
除 chart/x/y 外，输出 ECharts option 级结构（echarts 子对象），包含：
- title / tooltip / legend 配置；
- xAxis / yAxis 的 type/name/axisLabel；
- series 列表（name/type/encode 字段映射到 columns 索引），数据仍由
  columns/rows 独立传递，前端据此渲染，避免大结果重复内嵌数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from semantic.dsl_schema import QueryDSL

# 时间字段集合（可视化时序判定，与 dsl_schema.TIME_FIELDS 同源）
TIME_DIM_FIELDS = {"order_time", "refund_time", "register_time"}

# pie 基数信号上限：类别基数超过该值时柱状图比饼图更可读
_PIE_MAX_CATEGORIES = 8


def _numeric_share(
    columns: tuple[str, ...] | list[str], rows: tuple[tuple, ...] | list[tuple], y_col: str | None
) -> float:
    """y 列中数值单元格的占比（0.0~1.0）。

    行为约定：行内缺 y 列（长度不足）视为"数据不足"，不参与判定（跳过），
    避免把不完整的测试/边界行误判为非数值而强制降级明细表。
    """
    if y_col is None or y_col not in columns:
        return 1.0
    idx = list(columns).index(y_col)
    numeric = judged = 0
    for r in rows:
        if idx >= len(r):
            continue  # 缺列：数据不足，跳过不判
        judged += 1
        try:
            float(r[idx])
            numeric += 1
        except (TypeError, ValueError):
            pass
    return numeric / judged if judged else 1.0


def _has_positive(
    columns: tuple[str, ...] | list[str], rows: tuple[tuple, ...] | list[tuple], y_col: str | None
) -> bool | None:
    """y 列是否存在正数值（pie 正数信号）。

    返回 None 表示无可判定单元格（y 列缺失 / 全部缺列 / 全部非数值）——
    信号不足，不据此拦截（与 _numeric_share 的缺列跳过约定一致）。
    """
    if y_col is None or y_col not in columns:
        return None
    idx = list(columns).index(y_col)
    saw_value = False
    for r in rows:
        if idx >= len(r):
            continue
        try:
            value = float(r[idx])
        except (TypeError, ValueError):
            continue
        saw_value = True
        if value > 0:
            return True
    return False if saw_value else None


def recommend_viz(
    dsl: QueryDSL,
    columns: tuple[str, ...] | list[str],
    rows: tuple[tuple, ...] | list[tuple],
) -> str:
    """返回推荐图表类型：number / line / bar / pie / pivot / table。"""
    dims = [d.alias or d.field for d in dsl.dimensions]
    n_metrics = len(dsl.metrics)
    n_rows = len(rows)
    y_col = dsl.metrics[0].alias if dsl.metrics else None

    # 数值类型信号：y 列显著非数值 -> 明细表（拒绝在非数值列上画图）
    if _numeric_share(columns, rows, y_col) < 0.5:
        return "table"

    # 单值卡片
    if not dims and n_metrics == 1 and n_rows <= 1:
        return "number"

    # 时间趋势 -> 折线（时序间距信号：时间维度始终优先折线）
    if any(d in TIME_DIM_FIELDS for d in dims):
        return "line"

    # 单维度单指标 -> 柱状 / 饼图（基数信号：类别数 <= 8；正数信号：y 列
    # 存在正数值才 pie——占比语义在全负/零值上会误导，此时退回柱状图）
    if len(dims) == 1 and n_metrics == 1:
        if n_rows <= _PIE_MAX_CATEGORIES and _has_positive(columns, rows, y_col) is not False:
            return "pie"
        return "bar"

    # 多维或单维多指标 -> 透视表（P0 / §4 项5）
    if len(dims) >= 2 or n_metrics >= 2:
        return "pivot"

    # 其余 -> 明细表
    return "table"


def viz_config(
    dsl: QueryDSL,
    columns: tuple[str, ...] | list[str],
    rows: tuple[tuple, ...] | list[tuple],
) -> dict:
    """返回可视化配置：chart/x/y + ECharts option 级渲染契约（echarts 子对象）。

    兼容字段 chart/x/y 保持不变（既有调用方无感）；新增 echarts 字段为
    前端渲染提供 axis/series/legend/tooltip 结构化指令。
    """
    chart = recommend_viz(dsl, columns, rows)
    dims = [d.alias or d.field for d in dsl.dimensions]
    metrics = [m.alias for m in dsl.metrics]
    x = dims[0] if dims else None
    y = metrics[0] if metrics else None

    return {
        "chart": chart,
        "x": x,
        "y": y,
        "echarts": _echarts_option(chart, dsl, columns, rows),
    }


def _field_label(field: str) -> str:
    """字段中文标签（与 present.labels 同源，避免循环导入）。"""
    try:
        from present.labels import FIELD_LABELS

        return FIELD_LABELS.get(field, field)
    except ImportError:  # pragma: no cover - 依赖缺失时回退原始字段名
        return field


def _echarts_option(
    chart: str,
    dsl: QueryDSL,
    columns: tuple[str, ...] | list[str],
    rows: tuple[tuple, ...] | list[tuple],
) -> dict[str, Any]:
    """构造 ECharts option 级结构（axis/series/legend/tooltip，数据外置）。"""
    cols = list(columns)
    dims = [d.alias or d.field for d in dsl.dimensions]
    metrics = [m.alias for m in dsl.metrics]
    n_metrics = len(metrics)
    is_time_series = chart == "line" and dims and dims[0] in TIME_DIM_FIELDS

    # 系列类型与柱状/折线间距
    series_type = "line" if chart == "line" else "bar" if chart == "bar" else None
    tooltip_trigger = "item" if chart in ("number", "pie") else "axis"

    # x/y 列索引（数据由 columns/rows 独立传递，series 仅声明字段映射）
    x_idx = cols.index(dims[0]) if dims and dims[0] in cols else 0
    y_idxs = [cols.index(m) for m in metrics if m in cols] or [1]

    option: dict[str, Any] = {
        "tooltip": {"trigger": tooltip_trigger},
        "legend": {
            "show": n_metrics > 1 or chart == "pie",
            "data": list(metrics),
        },
    }
    if chart in ("number",):
        option["series"] = [{"name": metrics[0] if metrics else "value", "type": "line"}]
    elif series_type:
        option["xAxis"] = {
            "type": "category",
            "name": _field_label(dims[0]) if dims else None,
            "axisLabel": {"rotate": 30 if len(rows) > 12 else 0},
        }
        option["yAxis"] = {"type": "value"}
        option["series"] = [
            {
                "name": m,
                "type": series_type,
                "smooth": is_time_series,
                "showSymbol": not is_time_series or len(rows) <= 12,
                "encode": {"x": x_idx, "y": y_idx},
            }
            for m, y_idx in zip(metrics, y_idxs, strict=False)
        ]
    elif chart == "pie":
        option["series"] = [
            {
                "name": metrics[0] if metrics else "分布",
                "type": "pie",
                "radius": "60%",
                "encode": {"itemName": x_idx, "value": y_idxs[0]},
            }
        ]
    else:
        # pivot / table：无独立图形系列，标注列结构供前端表格渲染
        option["columns"] = [{"name": c, "index": i} for i, c in enumerate(cols)]
    return option


@dataclass
class ChartSpec:
    """复合输出中的图表渲染指令（类型 + 轴映射 + ECharts option + 可选数据）。"""

    chart: str
    x: str | None = None
    y: str | None = None
    columns: list[str] | None = None
    rows: list[list[Any]] | None = field(default=None)
    echarts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_data: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chart": self.chart,
            "x": self.x,
            "y": self.y,
            "echarts": self.echarts,
        }
        if include_data and self.columns is not None:
            payload["columns"] = self.columns
            payload["rows"] = self.rows if self.rows is not None else []
        return payload


def build_chart_spec(
    dsl: QueryDSL,
    columns: tuple[str, ...] | list[str],
    rows: tuple[tuple, ...] | list[tuple],
    *,
    data: bool = True,
) -> ChartSpec:
    """由 DSL + 结果形状构造 ChartSpec（类型 + 轴 + ECharts option + 可选数据）。"""
    cfg = viz_config(dsl, columns, rows)
    return ChartSpec(
        chart=cfg["chart"],
        x=cfg["x"],
        y=cfg["y"],
        echarts=cfg["echarts"],
        columns=list(columns) if data else None,
        rows=[list(r) for r in rows] if data else None,
    )


__all__ = [
    "ChartSpec",
    "build_chart_spec",
    "recommend_viz",
    "viz_config",
]
