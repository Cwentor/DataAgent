"""指标分解树（Metric Decomposition Tree）：乘法 / 加法因子归因。

乘法分解（链式对数贡献，例：GMV = UV × CR × AOV）：
- Δln(GMV) = Σ Δln(factor)，各因子贡献占比 = Δln(factor) / Σ|Δln|；
- 还原为对总偏差的加法贡献：contribution_i = share_i × ΔGMV_total；
- 因子含零值时回退为加法口径并标注（对数链式要求全因子 > 0）。

加法分解（分项贡献）：
- Δ = current_i - baseline_i 直接求差，贡献占比 = Δ_i / Σ|Δ_i|。

输入形态：
- multiplicative：{"baseline": {factor: v}, "current": {factor: v}}；
- additive：分组明细 DataFrame（dimension/value/period）。
"""

from __future__ import annotations

import math
from typing import Any


def multiplicative_decomposition(
    baseline: dict[str, float],
    current: dict[str, float],
    *,
    metric_name: str = "metric",
) -> dict[str, Any]:
    """乘法指标分解树：对数链式贡献归因。

    例：GMV = UV × CR × AOV，baseline/current 为各因子值（同一因子集合）。
    返回各因子的变化率、对数贡献占比、还原加法贡献与结论。
    """
    factors = sorted(set(baseline) & set(current))
    missing = (set(baseline) | set(current)) - set(factors)
    if missing:
        raise ValueError(f"两期因子集合不一致: {sorted(missing)}")
    if any(baseline[f] <= 0 or current[f] <= 0 for f in factors):
        raise ValueError("乘法分解要求所有因子在两期均 > 0（可改用加法分解）")

    base_total = math.prod(baseline[f] for f in factors)
    curr_total = math.prod(current[f] for f in factors)
    total_delta = curr_total - base_total

    log_deltas = {f: math.log(current[f]) - math.log(baseline[f]) for f in factors}
    abs_sum = sum(abs(v) for v in log_deltas.values())
    findings: list[str] = []
    rows: list[dict[str, Any]] = []
    for f in factors:
        share = (log_deltas[f] / abs_sum) if abs_sum > 0 else 0.0
        contribution = share * total_delta
        change_rate = current[f] / baseline[f] - 1.0
        rows.append(
            {
                "factor": f,
                "baseline": round(baseline[f], 6),
                "current": round(current[f], 6),
                "change_rate": round(change_rate, 4),
                "log_delta": round(log_deltas[f], 6),
                "share": round(share, 4),
                "contribution": round(contribution, 6),
            }
        )
    rows.sort(key=lambda item: abs(item["contribution"]), reverse=True)
    if rows:
        top = rows[0]
        direction = "下降" if total_delta < 0 else "上升"
        findings.append(
            f"{metric_name} {direction} {abs(round(total_delta, 4))}；"
            f"主要驱动因子 [{top['factor']}]（变动 {top['change_rate']:+.1%}，"
            f"贡献占比 {top['share']:.0%}）。"
        )
    return {
        "metric": metric_name,
        "baseline_total": round(base_total, 6),
        "current_total": round(curr_total, 6),
        "total_delta": round(total_delta, 6),
        "factors": rows[:100],
        "findings": findings,
    }


def additive_decomposition(
    df,  # pd.DataFrame: [dimension, value, period]
    *,
    dimension_col: str = "dimension",
    value_col: str = "value",
    period_col: str = "period",
    baseline_label: str = "baseline",
    current_label: str = "current",
) -> dict[str, Any]:
    """加法指标分解：分项差额贡献（占比按 |Δ| 归一）。"""
    base = df[df[period_col] == baseline_label].set_index(dimension_col)[value_col]
    curr = df[df[period_col] == current_label].set_index(dimension_col)[value_col]
    base, curr = base.align(curr, fill_value=0.0)
    deltas = {k: float(curr[k] - base[k]) for k in curr.index}
    total_delta = sum(deltas.values())
    abs_sum = sum(abs(v) for v in deltas.values())

    rows = [
        {
            "dimension": str(k),
            "baseline": round(float(base[k]), 6),
            "current": round(float(curr[k]), 6),
            "delta": round(deltas[k], 6),
            "share": round(deltas[k] / abs_sum, 4) if abs_sum > 0 else 0.0,
        }
        for k in curr.index
    ]
    rows.sort(key=lambda item: abs(item["delta"]), reverse=True)
    findings = []
    if rows and abs(total_delta) > 1e-12:
        top = rows[0]
        findings.append(
            f"分项 [{top['dimension']}] 贡献了 {abs(top['share']):.0%} 的偏差"
            f"（Δ={top['delta']}）。"
        )
    return {
        "total_delta": round(total_delta, 6),
        "items": rows[:100],
        "findings": findings,
    }


__all__ = ["additive_decomposition", "multiplicative_decomposition"]
