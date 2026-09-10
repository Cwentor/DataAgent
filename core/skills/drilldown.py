"""维度下钻定位（Dimensional Drill-down）：熵 / 信息增益异常定位器。

业务问题：指标在某窗口出现偏差（如 GMV 环比 -15%），哪个维度（地区/品类/
渠道）的哪个取值是主要贡献者？

方法：对每个候选维度计算"指标偏差贡献的信息增益"——
1. 按维度取值分组，计算各组对总偏差的加法贡献（Δ = 当前窗口值 - 基线值
   按组占比缩放后的差）；
2. 各组偏差绝对值占总偏差的比例构成一个分布，其熵越小 => 偏差越集中 =>
   该维度的定位价值越大；信息增益 = 总熵(H(均匀)) - 该维度分布熵；
3. 输出各维度 Top 贡献取值（偏差占比排序），信息增益最高的维度排最前。

输入数据形态（DataFrame）：列 = [dimension_col..., value_col, period_col]，
period ∈ {baseline, current}。只读输入、输出结构化摘要，零网络零 SQL。
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

MAX_OUTPUT_ROWS = 100


def drilldown_by_information_gain(
    df: pd.DataFrame,
    *,
    dimension_cols: list[str],
    value_col: str,
    period_col: str = "period",
    baseline_label: str = "baseline",
    current_label: str = "current",
    top_k_values: int = 5,
) -> dict[str, Any]:
    """按信息增益对维度排序，并给出每维度的主要贡献取值。

    返回结构（summary 协议兼容）：
    - ``dimensions``：[{dimension, gain, total_delta, concentration,
      top_values: [{value, delta, share}]}]，gain 降序；
    - ``findings``：人读结论。
    """
    if period_col not in df.columns:
        raise ValueError(f"缺少周期列: {period_col}")
    base = df[df[period_col] == baseline_label].groupby(dimension_cols)[value_col].sum()
    curr = df[df[period_col] == current_label].groupby(dimension_cols)[value_col].sum()
    # 多维度分组键对齐：转为统一索引后按组求贡献差
    base, curr = base.align(curr, fill_value=0.0)
    total_delta = float(curr.sum() - base.sum())
    if abs(total_delta) < 1e-12:
        return {
            "dimensions": [],
            "findings": ["两窗口指标总量无差异，无需下钻定位。"],
        }

    uniform_entropy = math.log(len(curr)) if len(curr) > 1 else 0.0
    rows: list[dict[str, Any]] = []
    for col in dimension_cols:
        # 按该维度的取值聚合贡献差
        deltas: dict[Any, float] = {}
        for key, c_val in curr.items():
            b_val = (
                base.get(key, 0.0)
                if not isinstance(base.index, pd.MultiIndex)
                else _multi_get(base, key)
            )
            group_key = (
                key if not isinstance(key, tuple) else _project_key(key, dimension_cols, col)
            )
            deltas[group_key] = deltas.get(group_key, 0.0) + float(c_val) - float(b_val)

        shares = {k: abs(v) / abs(total_delta) for k, v in deltas.items() if abs(total_delta) > 0}
        s = sum(shares.values())
        norm = {k: (v / s if s > 0 else 0.0) for k, v in shares.items()}
        entropy = -sum(p * math.log(p) for p in norm.values() if p > 0)
        gain = uniform_entropy - entropy
        concentration = max(norm.values()) if norm else 0.0

        top_values = sorted(
            (
                {"value": _json_safe(k), "delta": round(v, 6), "share": round(shares[k], 4)}
                for k, v in deltas.items()
                if k in shares
            ),
            key=lambda item: abs(item["delta"]),
            reverse=True,
        )[:top_k_values]

        rows.append(
            {
                "dimension": col,
                "gain": round(gain, 4),
                "total_delta": round(sum(deltas.values()), 6),
                "concentration": round(concentration, 4),
                "top_values": top_values,
            }
        )

    rows.sort(key=lambda item: item["gain"], reverse=True)
    findings = []
    if rows:
        best = rows[0]
        top = best["top_values"][0] if best["top_values"] else None
        if top:
            findings.append(
                f"维度 [{best['dimension']}] 的信息增益最高（{best['gain']}），"
                f"其取值 '{top['value']}' 贡献了 {top['share']:.0%} 的偏差。"
            )
    return {"dimensions": rows[:MAX_OUTPUT_ROWS], "findings": findings}


def _multi_get(series: pd.Series, key: Any) -> float:
    """MultiIndex 取值（缺失返回 0）。"""
    try:
        return float(series.get(key, 0.0))
    except (TypeError, KeyError):
        return 0.0


def _project_key(key: tuple, dimension_cols: list[str], target_col: str) -> Any:
    """把 MultiIndex 元组投影到单个维度的取值。"""
    idx = dimension_cols.index(target_col)
    return key[idx] if idx < len(key) else None


def _json_safe(value: Any) -> Any:
    """JSON 兼容化（pandas/numpy 标量 -> 原生类型）。"""
    if hasattr(value, "item"):
        return value.item()
    return value


__all__ = ["drilldown_by_information_gain"]
