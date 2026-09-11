"""DataQAAgent：执行后结果断言（数据质量质检员，确定性实现）。

对齐 pi-agent-harness 的 DataQAAgent 角色（结果断言与数据质量质检）：
在数据进入展示/沙箱分析**之前**，对原始执行结果做四类确定性断言
（映射 Harness DataQAAgent Checklist）：

1. empty_result：0 行结果——可能是合法空集，也可能是过滤条件冲突的
   查询逻辑缺陷信号（不否决执行，供 critic / 报告层引用）；
2. null_rate：关键指标列 NULL 占比超阈值（>=50% 即可疑）；
3. negative_metric：非负语义指标（sum/count/count_distinct）出现负值
   （负 GMV / 负订单量在业务上不可能，是取数口径或数仓异常信号）；
4. dimension_uniqueness：聚合查询的维度组合行必须唯一——重复即编译/
   聚合缺陷信号（severity=error）。

处置纪律（不误杀、不吞错）：
- 断言结果只作为**结构化审计发现**附加到 ParquetRef.audit 与编排轨迹，
  不自动改写编排路由（空结果可能是合法答案，编译产物重复需要人工证据）；
- 全部检查为确定性代码（零 LLM）：符合本项目"LLM 仅产 DSL"铁律，
  质检结论可复现、可单测、无幻觉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from semantic.dsl_schema import QueryDSL

# NULL 率告警阈值：指标列空值占比达到该比例即标记可疑
NULL_RATE_THRESHOLD = 0.5

# 非负语义聚合：业务上不可能为负的指标（GMV/订单量/计数类）
_NON_NEGATIVE_AGGS = frozenset({"sum", "count", "count_distinct"})


@dataclass
class QaFinding:
    """一条结果质检发现：检查项 + 严重级 + 说明（结构化审计轨迹）。"""

    check: str
    severity: str  # "error" | "warning"
    message: str

    def to_dict(self) -> dict[str, str]:
        """序列化（ParquetRef.audit / 事件轨迹 / 报告层消费）。"""
        return {"check": self.check, "severity": self.severity, "message": self.message}


def _column_index(columns: list[str], name: str) -> int | None:
    """列名 -> 索引（缺失返回 None）。"""
    try:
        return columns.index(name)
    except ValueError:
        return None


def _check_empty_result(columns: list[str], rows: list[list[Any]]) -> list[QaFinding]:
    """断言 1：0 行结果（合法空集与查询逻辑缺陷共用同一信号，不否决）。"""
    if rows:
        return []
    return [
        QaFinding(
            "empty_result",
            "warning",
            "查询返回 0 行：可能是过滤/时间窗口无匹配的合法空集，也可能是过滤条件"
            "冲突的查询逻辑缺陷，请结合业务语义确认",
        )
    ]


def _check_null_rate(columns: list[str], rows: list[list[Any]], dsl: QueryDSL) -> list[QaFinding]:
    """断言 2：指标列 NULL 率（阈值 NULL_RATE_THRESHOLD）。"""
    findings: list[QaFinding] = []
    if not rows:
        return findings
    for metric in dsl.metrics:
        alias = metric.alias
        idx = _column_index(columns, alias)
        if idx is None:
            continue
        nulls = sum(1 for r in rows if idx >= len(r) or r[idx] is None)
        rate = nulls / len(rows)
        if rate >= 1.0:
            findings.append(
                QaFinding(
                    "null_rate",
                    "warning",
                    f"指标列 {alias} 全部为空（{nulls}/{len(rows)} 行）：取数口径或字段"
                    "映射可能存在缺陷",
                )
            )
        elif rate >= NULL_RATE_THRESHOLD:
            findings.append(
                QaFinding(
                    "null_rate",
                    "warning",
                    f"指标列 {alias} 空值率 {rate:.0%}（{nulls}/{len(rows)} 行），"
                    "超过可接受阈值",
                )
            )
    return findings


def _check_negative_metric(
    columns: list[str], rows: list[list[Any]], dsl: QueryDSL
) -> list[QaFinding]:
    """断言 3：非负语义指标（sum/count/count_distinct）出现负值。"""
    findings: list[QaFinding] = []
    if not rows:
        return findings
    for metric in dsl.metrics:
        kind = getattr(metric, "kind", "")
        agg = str(getattr(metric, "agg", "") or "").lower()
        if kind != "aggregate" or agg not in _NON_NEGATIVE_AGGS:
            continue  # ratio/window 派生语义可正可负，不做负值断言
        alias = metric.alias
        idx = _column_index(columns, alias)
        if idx is None:
            continue
        negatives = 0
        for r in rows:
            if idx >= len(r):
                continue
            try:
                if float(r[idx]) < 0:
                    negatives += 1
            except (TypeError, ValueError):
                continue
        if negatives:
            findings.append(
                QaFinding(
                    "negative_metric",
                    "warning",
                    f"指标列 {alias}（{agg} 聚合）出现 {negatives} 个负值："
                    "非负语义指标不应为负，请核查数仓数据或取数口径",
                )
            )
    return findings


def _check_dimension_uniqueness(
    columns: list[str], rows: list[list[Any]], dsl: QueryDSL
) -> list[QaFinding]:
    """断言 4：聚合查询维度组合唯一（重复 = 编译/聚合缺陷，severity=error）。"""
    if not dsl.dimensions or not rows:
        return []
    dim_indexes = [_column_index(columns, d.alias or d.field) for d in dsl.dimensions]
    if any(i is None for i in dim_indexes):
        return []  # 维度列缺失属结果形状问题，交由调用方链路报错
    seen: set[tuple[Any, ...]] = set()
    duplicates = 0
    for r in rows:
        key = tuple(r[i] for i in dim_indexes)  # type: ignore[index]
        if key in seen:
            duplicates += 1
        seen.add(key)
    if duplicates:
        return [
            QaFinding(
                "dimension_uniqueness",
                "error",
                f"聚合结果存在 {duplicates} 组重复维度组合：GROUP BY 语义下维度组合"
                "必须唯一，疑似编译/聚合缺陷，请勿直接采信本数据集",
            )
        ]
    return []


def run_quality_assertions(
    columns: list[str], rows: list[list[Any]], dsl: QueryDSL
) -> list[QaFinding]:
    """DataQA 断言入口：对执行结果跑全部质检，返回结构化发现列表（空 = 全部通过）。"""
    findings: list[QaFinding] = []
    findings.extend(_check_empty_result(columns, rows))
    findings.extend(_check_null_rate(columns, rows, dsl))
    findings.extend(_check_negative_metric(columns, rows, dsl))
    findings.extend(_check_dimension_uniqueness(columns, rows, dsl))
    return findings


__all__ = ["NULL_RATE_THRESHOLD", "QaFinding", "run_quality_assertions"]
