"""NL -> DSL 语义一致性校验。"""

from __future__ import annotations

from agent.errors import PipelineError
from agent.heuristic import DeterministicNL2DSL
from semantic.dsl_schema import QueryDSL

_H = DeterministicNL2DSL()


def validate_semantics(query: str, dsl: QueryDSL, principal: str | None = None) -> list[str]:
    """校验可由确定性规则确认的关键语义槽位。

    只检查“问题明确要求、DSL 却缺失”的槽位；不要求 LLM 复刻启发式的所有默认值。
    规则无法可靠解析的问题返回空列表，继续由 LLM 自行处理。
    """
    try:
        expected = _H.run(query, principal=principal)
    except PipelineError:
        return []

    errors: list[str] = []
    actual_metrics = {(m.field, m.agg.value) for m in dsl.metrics if m.kind == "aggregate"}
    expected_metrics = {(m.field, m.agg.value) for m in expected.metrics if m.kind == "aggregate"}
    if expected_metrics and not expected_metrics <= actual_metrics:
        errors.append(f"指标缺失：期望包含 {sorted(expected_metrics - actual_metrics)}")

    actual_dims = {d.field for d in dsl.dimensions}
    expected_dims = {d.field for d in expected.dimensions}
    if expected_dims - actual_dims:
        errors.append(f"分组维度缺失：期望包含 {sorted(expected_dims - actual_dims)}")

    actual_order = {(o.field, o.direction.value) for o in dsl.order_by}
    expected_order = {(o.field, o.direction.value) for o in expected.order_by}
    if expected_order - actual_order:
        errors.append(f"排序缺失：期望包含 {sorted(expected_order - actual_order)}")

    if expected.limit == 1 and dsl.limit != 1:
        errors.append(f"返回条数错误：问题要求单个结果，实际 limit={dsl.limit}")

    return errors


__all__ = ["validate_semantics"]
