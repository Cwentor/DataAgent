"""归因技能包单测：与已知解析解/手工解对拍（数值正确性守护）。

覆盖：
- drilldown：单一维度主导偏差时信息增益排序正确、贡献取值定位准确；
- decomposition：乘法对数链式（贡献和恒等于总偏差的份额结构）、加法差额；
- timeseries：DTW 已知序列距离、Holt-Winters 注入尖峰后必被检出；
- shapley：可加模型上 Shapley == 线性边际（解析解）、对称性校验。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.skills.decomposition import additive_decomposition, multiplicative_decomposition
from core.skills.drilldown import drilldown_by_information_gain
from core.skills.shapley import shapley_attribution
from core.skills.timeseries import dtw_distance, holt_winters_anomaly


# --------------------------------------------------------------------------- #
# drilldown
# --------------------------------------------------------------------------- #
def test_drilldown_ranks_guilty_dimension_first():
    # 华东在当期暴跌（-90），其余不变：region 的信息增益必须高于 category
    rows = []
    for region in ("华东", "华北"):
        for cat in ("A", "B"):
            rows.append({"region": region, "category": cat, "period": "baseline", "value": 50.0})
    rows.append({"region": "华东", "category": "A", "period": "current", "value": 10.0})
    for region in ("华东", "华北"):
        for cat in ("A", "B"):
            key = {"region": region, "category": cat}
            found = [r for r in rows if all(r[k] == v for k, v in key.items() if k in r)]
            if not any(r["period"] == "current" for r in found):
                rows.append({"region": region, "category": cat, "period": "current", "value": 50.0})
    df = pd.DataFrame(rows)
    result = drilldown_by_information_gain(
        df, dimension_cols=["region", "category"], value_col="value"
    )
    assert result["dimensions"][0]["dimension"] == "region"
    top = result["dimensions"][0]["top_values"][0]
    assert top["value"] == "华东" and top["delta"] < 0
    assert result["findings"]


def test_drilldown_no_delta_short_circuits():
    df = pd.DataFrame(
        {
            "region": ["华东", "华东"],
            "period": ["baseline", "current"],
            "value": [100.0, 100.0],
        }
    )
    result = drilldown_by_information_gain(df, dimension_cols=["region"], value_col="value")
    assert result["dimensions"] == []


# --------------------------------------------------------------------------- #
# decomposition
# --------------------------------------------------------------------------- #
def test_multiplicative_decomposition_known_solution():
    # GMV: UV 1000->1100, CR 0.05->0.045, AOV 80->85
    # 当前总量 = 1100 × 0.045 × 85 = 4207.5
    # ln 变化: +0.0953, -0.1054, +0.0606 => 唯一负贡献是 CR
    baseline = {"uv": 1000.0, "cr": 0.05, "aov": 80.0}
    current = {"uv": 1100.0, "cr": 0.045, "aov": 85.0}
    result = multiplicative_decomposition(baseline, current, metric_name="GMV")
    assert result["baseline_total"] == pytest.approx(4000.0)
    assert result["current_total"] == pytest.approx(4207.5)
    # 贡献占比绝对值之和 == 1（归一性）
    assert sum(abs(r["share"]) for r in result["factors"]) == pytest.approx(1.0)
    # CR 是唯一负贡献因子，且排序后排第一（按 |contribution|）
    assert result["factors"][0]["factor"] == "cr"
    assert result["factors"][0]["contribution"] < 0


def test_multiplicative_requires_positive_factors():
    with pytest.raises(ValueError):
        multiplicative_decomposition({"uv": 0.0, "cr": 1.0}, {"uv": 1.0, "cr": 1.0})


def test_additive_decomposition_sums():
    df = pd.DataFrame(
        {
            "dimension": ["华东", "华北", "华东", "华北"],
            "value": [100.0, 50.0, 80.0, 50.0],
            "period": ["baseline", "baseline", "current", "current"],
        }
    )
    result = additive_decomposition(df)
    assert result["total_delta"] == pytest.approx(-20.0)
    assert result["items"][0]["dimension"] == "华东"


# --------------------------------------------------------------------------- #
# timeseries
# --------------------------------------------------------------------------- #
def test_dtw_identical_series_zero():
    result = dtw_distance([1, 2, 3, 4], [1, 2, 3, 4])
    assert result["distance"] == pytest.approx(0.0)


def test_dtw_known_distance():
    # 常量序列 [1,1,1] vs [2,2,2]：每步代价 1，路径长 6 => 0.5
    result = dtw_distance([1, 1, 1], [2, 2, 2])
    assert result["distance"] == pytest.approx(0.5)


def test_holt_winters_detects_injected_spike():
    # 两周平稳日销 + 注入单点尖峰
    rng = np.random.default_rng(42)
    base = 100.0 + rng.normal(0, 2.0, 21)
    base[15] = 200.0  # 注入尖峰
    result = holt_winters_anomaly(base.tolist(), season_length=7)
    anomaly_idx = [a["index"] for a in result["anomalies"]]
    assert 15 in anomaly_idx


def test_holt_winters_short_series_rejected():
    with pytest.raises(ValueError):
        holt_winters_anomaly([1.0] * 10, season_length=7)


# --------------------------------------------------------------------------- #
# shapley
# --------------------------------------------------------------------------- #
def test_shapley_additive_model_analytic_solution():
    # 可加模型 M = a + b + c：Shapley 值 = 各因子变化量（解析解）
    baseline = {"a": 10.0, "b": 20.0, "c": 30.0}
    current = {"a": 15.0, "b": 18.0, "c": 30.0}
    result = shapley_attribution(baseline, current, model="additive")
    by_factor = {r["factor"]: r["shapley"] for r in result["factors"]}
    assert by_factor["a"] == pytest.approx(5.0)
    assert by_factor["b"] == pytest.approx(-2.0)
    assert by_factor["c"] == pytest.approx(0.0, abs=1e-9)
    assert result["total_delta"] == pytest.approx(3.0)


def test_shapley_symmetry():
    # 对称因子（基线/当前完全相同的变化）应获得相等的 Shapley 值
    baseline = {"a": 10.0, "b": 10.0}
    current = {"a": 12.0, "b": 12.0}
    result = shapley_attribution(baseline, current, model="additive")
    by_factor = {r["factor"]: r["shapley"] for r in result["factors"]}
    assert by_factor["a"] == pytest.approx(by_factor["b"])


def test_shapley_factor_cap():
    many = {f"f{i}": 1.0 for i in range(9)}
    with pytest.raises(ValueError):
        shapley_attribution(many, many, model="additive")
