"""trend_analysis_tool：趋势与周期对比分析（同环比 / 时间粒度 / 补零）。

职责：处理带时间粒度（day/week/month）、周期对比（MoM/YoY）、连续补零
（fill_gaps）的时序分析问题，如『分析过去半年各省份销售额环比趋势』、
『2024年6月每日GMV趋势』、『近30天GMV（补零）』。

实现要点：
1. 先用既有 NL->DSL 链路解析（含确定性兜底）；
2. 再按入参/问题关键词规范化趋势语义（确定性规则）：
   - 时间维度：确保存在唯一时间维度（order_time）；
   - 粒度：按入参或『每日/按天/每月/每周』等关键词确定；
   - 周期对比：环比->mom / 同比->yoy（编译器要求：contrast 不与时间维度
     同现，故规范化时剔除时间维度，按分组维度做 cur/prev 配对）；
   - 补零：fill_gaps 要求唯一时间维度 + 明确窗口 + day/month 粒度，
     且与 comparison/top_n 互斥（编译器显式校验）；
3. 编译 + 受控执行：与 query_metric 一样复用既有护栏（见 _query_core）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.errors import PipelineError
from agent.heuristic import DeterministicNL2DSL
from agent.pipeline import run_pipeline_with_status
from present.explain import explain
from present.viz import build_chart_spec, viz_config
from semantic.dsl_schema import (
    Comparison,
    Dimension,
    Granularity,
    OrderBy,
    QueryDSL,
    SortDirection,
    TimeFilter,
)
from tools.base import BaseTool, ToolContext, ToolResult
from tools.builtins._query_core import run_guarded_query

__all__ = ["TrendAnalysisArgs", "TrendAnalysisTool", "trend_analysis_tool"]


class TrendAnalysisArgs(BaseModel):
    """趋势分析的严格入参（均可选；未显式给出时由工具按问题关键词推断）。"""

    query: str = Field(min_length=1, description="自然语言问题，需含趋势/环比/同比/时间粒度等语义")
    granularity: Granularity | None = Field(
        default=None, description="时间粒度：day/week/month，缺省按问题推断"
    )
    comparison: Comparison | None = Field(
        default=None, description="周期对比：none/yoy(同比)/mom(环比)，缺省按问题推断"
    )
    fill_gaps: bool = Field(default=False, description="日期连续补零（需唯一时间维度 + 明确窗口）")

    model_config = {"extra": "forbid"}


# 问题关键词 -> 语义覆盖（确定性推断，与启发式兜底同源）
_GRANULARITY_KEYWORDS: tuple[tuple[tuple[str, ...], Granularity], ...] = (
    (("每日", "按天", "每天", "逐日", "日趋势"), Granularity.DAY),
    (("每周", "按周", "逐周", "周趋势"), Granularity.WEEK),
    (("每月", "按月", "逐月", "月趋势", "半年", "月度"), Granularity.MONTH),
)
_FILL_GAPS_KEYWORDS = ("补零", "补齐", "补全", "连续日期")


class TrendAnalysisTool(BaseTool):
    """趋势与对比分析工具：时序 / 同比环比 / 补零语义的受守卫查询入口（Trend analysis tool）。"""

    name = "trend_analysis"
    description = (
        "趋势与对比分析：承接带时间粒度、周期对比（同比yoy/环比mom）、双窗口对比"
        "或连续补零（fill_gaps）的时序问题，如『分析过去半年各省份销售额环比趋势』"
        "『2024年6月每日GMV趋势』。会规范化时间维度/粒度/对比/补零语义后执行。"
        "纯点查汇总类问题请改用 query_metric。"
    )
    args_schema = TrendAnalysisArgs

    def execute(self, validated_args: TrendAnalysisArgs, ctx: ToolContext) -> ToolResult:
        # 会话上下文继承：有 base_dsl 时以其为基础（省略指代/下钻语句本身无法独立
        # 解析出指标），否则走完整 NL->DSL 解析
        """规范化趋势 / 对比 / 补零语义后执行受守卫时序查询（Guarded trend query）。"""
        if ctx.base_dsl is not None:
            dsl, degraded = ctx.base_dsl, False
        else:
            dsl, degraded = self._parse(validated_args.query, ctx.principal)
        dsl = self._normalize_trend(dsl, validated_args)
        result = run_guarded_query(
            validated_args.query,
            principal=ctx.principal,
            conn=ctx.conn,
            executor=ctx.executor,
            rewriter=ctx.rewriter,
            dsl=dsl,
        )
        viz = viz_config(result.dsl, result.columns, result.rows)
        data = {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": len(result.rows),
            "dsl": result.dsl.model_dump(mode="json"),
            "sql": result.sql,
            "scan_rows": result.scan_rows,
            "rewrites": result.rewrites,
            "degraded": result.degraded or degraded,
            "explanation": explain(result.dsl),
            "viz": viz,
            "qa_findings": result.qa_findings,
            "chart_spec": build_chart_spec(
                result.dsl, result.columns, result.rows, data=True
            ).to_dict(),
        }
        return ToolResult(
            success=True,
            data=data,
            display_type=viz["chart"],
            meta={
                "sql": result.sql,
                "scan_rows": result.scan_rows,
                "rewrites": result.rewrites,
                "degraded": result.degraded or degraded,
                "duration_ms": result.duration_ms,
                "qa_findings": result.qa_findings,
            },
        )

    # ------------------------------------------------------------------ #
    # 解析
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse(query: str, principal: str | None) -> tuple[QueryDSL, bool]:
        """NL -> DSL（既有链路）；解析失败时用启发式规则兜底构造趋势 DSL。"""
        try:
            dsl, degraded = run_pipeline_with_status(query, principal)
            return dsl, degraded
        except PipelineError:
            pass
        return TrendAnalysisTool._fallback_dsl(query), False

    @staticmethod
    def _fallback_dsl(query: str) -> QueryDSL:
        """确定性兜底：复用启发式的指标/维度/过滤/排序解析，重建带时间窗口的 DSL。"""
        h = DeterministicNL2DSL()
        dsl_dict: dict = {
            "metrics": h._metrics(query),
            "dimensions": h._dimensions(query),
            "filters": h._filters(query),
            "order_by": h._order_by(query),
            "limit": h._limit(query),
        }
        comparison = h._comparison(query)
        if comparison:
            dsl_dict["time_filter"] = _default_window(comparison)
            if h._fill_gaps(query):
                raise PipelineError("环比/同比 与日期补零互斥，无法同时解析")
        else:
            time_filter = h._time_filter(query)
            if time_filter:
                dsl_dict["time_filter"] = time_filter
            if h._fill_gaps(query):
                dsl_dict["fill_gaps"] = True
        return QueryDSL.model_validate(dsl_dict)

    # ------------------------------------------------------------------ #
    # 趋势语义规范化（确定性规则）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_trend(dsl: QueryDSL, args: TrendAnalysisArgs) -> QueryDSL:
        q = args.query
        comparison = args.comparison or _infer_comparison(q)
        granularity = (
            args.granularity
            or _infer_granularity(q)
            or (dsl.time_filter.granularity if dsl.time_filter else Granularity.DAY)
        )
        fill_gaps = bool(args.fill_gaps) or any(k in q for k in _FILL_GAPS_KEYWORDS)
        if comparison != Comparison.NONE:
            # 周期对比与补零互斥（编译器显式校验）
            fill_gaps = False

        dimensions = list(dsl.dimensions)
        time_dims = [
            d for d in dimensions if d.field in ("order_time", "refund_time", "register_time")
        ]

        if comparison != Comparison.NONE:
            # 编译器要求：comparison 不与时间维度同现（按位配对语义未定义）
            dimensions = [
                d
                for d in dimensions
                if d.field not in ("order_time", "refund_time", "register_time")
            ]
            time_filter = (
                dsl.time_filter.model_copy(
                    update={"comparison": comparison, "granularity": granularity}
                )
                if dsl.time_filter
                else _default_window(comparison, granularity)
            )
            # 剔除指向时间维度的排序引用（对比 SQL 只允许维度/指标/对比列）
            dim_aliases = {d.alias or d.field for d in dimensions}
            metric_aliases = {m.alias for m in dsl.metrics}
            allowed = (
                dim_aliases
                | metric_aliases
                | {m.alias + "_prev" for m in dsl.metrics}
                | {
                    m.alias + ("_mom" if comparison == Comparison.MOM else "_yoy")
                    for m in dsl.metrics
                }
            )
            order_by = [o for o in dsl.order_by if o.field in allowed][:1]
            return dsl.model_copy(
                update={
                    "dimensions": dimensions,
                    "time_filter": time_filter,
                    "order_by": order_by,
                    "fill_gaps": False,
                    "top_n": None,
                }
            )

        # 纯趋势 / 补零：确保存在唯一时间维度
        if not time_dims:
            dimensions = [*dimensions, Dimension(field="order_time")]
            time_dims = [dimensions[-1]]

        if fill_gaps:
            if granularity == Granularity.WEEK:
                granularity = Granularity.DAY  # 补零仅支持 day/month
            time_filter = dsl.time_filter or _default_window(None, granularity)
            return dsl.model_copy(
                update={
                    "dimensions": dimensions,
                    "time_filter": time_filter.model_copy(update={"granularity": granularity}),
                    "fill_gaps": True,
                    "top_n": None,
                    "order_by": _ensure_time_order(dsl.order_by),
                }
            )

        # 纯趋势：补窗口 + 时间升序
        time_filter = dsl.time_filter or _default_window(None, granularity)
        time_filter = time_filter.model_copy(update={"granularity": granularity})
        return dsl.model_copy(
            update={
                "dimensions": dimensions,
                "time_filter": time_filter,
                "order_by": _ensure_time_order(dsl.order_by),
            }
        )


def _infer_comparison(query: str) -> Comparison:
    q = query.lower()
    if "同比" in q or "yoy" in q:
        return Comparison.YOY
    if "环比" in q or "mom" in q:
        return Comparison.MOM
    return Comparison.NONE


def _infer_granularity(query: str) -> Granularity | None:
    for keywords, granularity in _GRANULARITY_KEYWORDS:
        if any(k in query for k in keywords):
            return granularity
    return None


def _default_window(
    comparison: Comparison | None, granularity: Granularity = Granularity.MONTH
) -> TimeFilter:
    """无窗口时的默认趋势窗口（确定性，锚定 AS_OF_DATE）。

    复用共享实现以避免与 heuristic.py 重复。
    """
    from agent.time_utils import default_compare_window

    return default_compare_window(comparison, granularity)


def _ensure_time_order(order_by: list[OrderBy]) -> list[OrderBy]:
    """保证时间升序排序（时间序列有序性；已含则保留原顺序）。"""
    for o in order_by:
        if o.field in ("order_time", "refund_time", "register_time"):
            return order_by
    return [OrderBy(field="order_time", direction=SortDirection.ASC), *order_by]


trend_analysis_tool = TrendAnalysisTool()
