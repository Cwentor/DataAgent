"""Shapley 值偏差归因：把指标偏差公平分摊到各因子（合作博弈论）。

场景：指标 M = f(x1..xn)（可乘可加），各因子从基线值变到当前值，
总偏差 ΔM 该记到谁头上？Shapley 值给出唯一满足有效性/对称性/哑元性
的公平分摊。

实现：精确排列法（因子 ≤8 时 n! 排列可枚举，本项目因子数远小于此）——
对每个因子的每个排列位置，计算该因子"加入联盟"时的边际贡献，取均值。

- ``factor_model``：f 的因子构成（multiplicative: 累乘 / additive: 累加）；
- 输入为基线/当前两期因子字典（同 multiplicative_decomposition 口径），
  输出各因子 Shapley 贡献与占比。
"""

from __future__ import annotations

import itertools
import math
from typing import Any


def _coalition_value(
    factors: list[str],
    coalition: frozenset[str],
    baseline: dict[str, float],
    current: dict[str, float],
    model: str,
) -> float:
    """联盟价值：联盟内因子取当前值、其余取基线值时 f 的输出。"""
    values = {f: (current[f] if f in coalition else baseline[f]) for f in factors}
    if model == "multiplicative":
        return math.prod(values[f] for f in factors)
    return math.fsum(values[f] for f in factors)


def shapley_attribution(
    baseline: dict[str, float],
    current: dict[str, float],
    *,
    model: str = "multiplicative",
) -> dict[str, Any]:
    """精确 Shapley 值归因（因子 ≤8；超出抛错以免 n! 爆炸）。

    返回：{"factors": [{factor, shapley, share}], "total_delta", "findings"}。
    """
    if model not in ("multiplicative", "additive"):
        raise ValueError(f"不支持的分解模型: {model!r}")
    factors = sorted(set(baseline) & set(current))
    missing = (set(baseline) | set(current)) - set(factors)
    if missing:
        raise ValueError(f"两期因子集合不一致: {sorted(missing)}")
    if len(factors) > 8:
        raise ValueError(f"因子数 {len(factors)} 超过精确法上限 8（请抽样或合并因子）")
    if model == "multiplicative" and any(baseline[f] <= 0 or current[f] <= 0 for f in factors):
        raise ValueError("乘法模型要求所有因子在两期均 > 0")

    def full(coalition: frozenset[str]) -> float:
        return _coalition_value(factors, coalition, baseline, current, model)

    total_delta = full(frozenset(factors)) - full(frozenset())

    n = len(factors)
    shapley: dict[str, float] = dict.fromkeys(factors, 0.0)
    for permutation in itertools.permutations(factors):
        coalition: frozenset[str] = frozenset()
        prev_value = full(coalition)
        for factor in permutation:
            new_coalition = coalition | {factor}
            marginal = full(new_coalition) - prev_value
            shapley[factor] += marginal
            coalition = new_coalition
            prev_value = marginal + prev_value
    for f in factors:
        shapley[f] /= math.factorial(n)

    abs_sum = sum(abs(v) for v in shapley.values())
    rows = [
        {
            "factor": f,
            "shapley": round(shapley[f], 6),
            "share": round(shapley[f] / abs_sum, 4) if abs_sum > 0 else 0.0,
        }
        for f in factors
    ]
    rows.sort(key=lambda item: abs(item["shapley"]), reverse=True)
    findings = []
    if rows and abs(total_delta) > 1e-12:
        top = rows[0]
        findings.append(
            f"Shapley 归因：因子 [{top['factor']}] 对总偏差（{round(total_delta, 4)}）"
            f"的公平贡献为 {top['shapley']}（占比 {top['share']:.0%}）。"
        )
    return {
        "model": model,
        "total_delta": round(total_delta, 6),
        "factors": rows,
        "findings": findings,
    }


__all__ = ["shapley_attribution"]
