"""query_metric_tool：查即时指标（点查 / 汇总类分析）。

承接"XX 是多少 / 各品类分布 / 各省排名"等即时指标查询，复用完整的
NL -> DSL -> Compiler -> Exec 受控链路（见 tools.builtins._query_core），
底层安全护栏（DSL 校验 / AST 只读检查 / 超时熔断 / 扫描行数熔断 /
RLS 行级权限注入）与自愈重写全部沿用既有实现，不绕过任何防线。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from present.explain import explain
from present.viz import build_chart_spec, viz_config
from tools.base import BaseTool, ToolContext, ToolResult
from tools.builtins._query_core import run_guarded_query

__all__ = ["QueryMetricArgs", "QueryMetricTool", "query_metric_tool"]


class QueryMetricArgs(BaseModel):
    """查即时指标的严格入参。"""

    query: str = Field(min_length=1, description="自然语言问题，如：2024年6月成功订单的GMV是多少")

    model_config = {"extra": "forbid"}


class QueryMetricTool(BaseTool):
    """即时指标查询工具：点查与汇总类分析的受守卫查询入口（Instant metric query tool）。"""

    name = "query_metric"
    description = (
        "查即时指标：承接点查与汇总类分析（如『2024年6月成功订单的GMV是多少？』"
        "『各品类成功订单的GMV分布？』『各省份销售额排名？』）。"
        "适用于单指标/多指标聚合、维度分组、分布与排行的即时查询。"
        "返回结构化数据与可视化配置；若问题带明确时间窗口可直接执行，"
        "若缺少时间窗口会先触发澄清。"
    )
    args_schema = QueryMetricArgs

    def execute(self, validated_args: QueryMetricArgs, ctx: ToolContext) -> ToolResult:
        """执行受守卫的即时指标查询，返回数据 + 可视化配置（Guarded point query）。"""
        result = run_guarded_query(
            validated_args.query,
            principal=ctx.principal,
            conn=ctx.conn,
            executor=ctx.executor,
            rewriter=ctx.rewriter,
            dsl=ctx.base_dsl,  # 会话上下文继承：非 None 时跳过 NL->DSL，仍强制安全守卫
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
            "degraded": result.degraded,
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
                "degraded": result.degraded,
                "duration_ms": result.duration_ms,
                "qa_findings": result.qa_findings,
            },
        )


query_metric_tool = QueryMetricTool()

__all__ = ["QueryMetricArgs", "QueryMetricTool", "query_metric_tool"]
