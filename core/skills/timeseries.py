"""时间序列技能：DTW 相似性与 Holt-Winters 异常检测（纯 numpy 实现）。

- ``dtw_distance``：动态时间规整距离（O(n·m) DP），支持 Sakoe-Chiba 带宽约束；
- ``holt_winters_anomaly``：三次指数平滑（加法季节 + 加法趋势）拟合，
  残差 > k·σ（鲁棒 σ：MAD 缩放）的点判为异常，返回异常点索引与置信带。

两函数均为无状态纯计算：输入数值序列、输出结构化结果，不依赖
statsmodels/sklearn（与项目轻依赖哲学一致）。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def dtw_distance(
    series_a: list[float] | np.ndarray,
    series_b: list[float] | np.ndarray,
    *,
    window: int | None = None,
) -> dict[str, Any]:
    """动态时间规整（Dynamic Time Warping）距离。

    - ``window``：Sakoe-Chiba 带宽（None = 全带宽）；路径必须连接两首尾；
    - 返回 {"distance": 归一化距离, "path_length": 规整路径长度}。
    """
    a = np.asarray(series_a, dtype=float)
    b = np.asarray(series_b, dtype=float)
    if a.size == 0 or b.size == 0:
        raise ValueError("输入序列不能为空")
    n, m = a.size, b.size
    if window is None:
        window = max(n, m)
    window = max(window, abs(n - m))

    inf = float("inf")
    cost = np.full((n + 1, m + 1), inf)
    cost[0, 0] = 0.0
    for i in range(1, n + 1):
        lo = max(1, i - window)
        hi = min(m, i + window)
        for j in range(lo, hi + 1):
            substitution = abs(a[i - 1] - b[j - 1])
            cost[i, j] = substitution + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])

    distance = cost[n, m]
    if math.isinf(distance):
        raise ValueError("DTW 无有效路径（带宽约束过紧）")
    # 归一化：路径长度（对角步数）约为 n+m，除以步数消除长度偏置
    path_length = n + m
    return {"distance": round(float(distance / path_length), 6), "path_length": path_length}


def _robust_sigma(residuals: np.ndarray) -> float:
    """鲁棒标准差：MAD × 1.4826（正态一致），退化时回退普通 std。"""
    med = float(np.median(residuals))
    mad = float(np.median(np.abs(residuals - med)))
    sigma = mad * 1.4826
    if sigma < 1e-12:
        sigma = float(np.std(residuals))
    return sigma if sigma > 1e-12 else 1.0


def holt_winters_anomaly(
    series: list[float] | np.ndarray,
    *,
    season_length: int = 7,
    alpha: float = 0.3,
    beta: float = 0.1,
    gamma: float = 0.3,
    n_seasons_train: int = 2,
    sigma_multiplier: float = 3.0,
) -> dict[str, Any]:
    """Holt-Winters 三次指数平滑（加法趋势 + 加法季节）残差异常检测。

    - 前 ``n_seasons_train`` 个季节作为训练期（初始化水平/趋势/季节项）；
    - 逐点一步前瞻预测，残差 r_t = y_t - ŷ_t；
    - 异常判定：|r_t| > sigma_multiplier × σ（σ 用 MAD 鲁棒估计）；
    - 返回异常索引、残差序列与置信带宽度（供 ECharts markArea 可视化）。
    """
    y = np.asarray(series, dtype=float)
    n = y.size
    min_len = season_length * (n_seasons_train + 1)
    if n < min_len:
        raise ValueError(f"序列长度不足：至少 {min_len}（season_length×{n_seasons_train + 1}）")

    # 初始化：水平 = 第一个季节均值；趋势 = 季节均值一阶差；
    # 季节项 = 各训练期同位置偏差的均值
    season_avgs = [
        float(y[i * season_length : (i + 1) * season_length].mean()) for i in range(n_seasons_train)
    ]
    level = season_avgs[0]
    trend = (season_avgs[1] - season_avgs[0]) / season_length if n_seasons_train > 1 else 0.0
    seasonal = [
        float(
            np.mean(
                [float(y[i * season_length + j] - season_avgs[i]) for i in range(n_seasons_train)]
            )
        )
        for j in range(season_length)
    ]

    residuals = np.zeros(n)
    fitted = np.zeros(n)
    for t in range(n):
        s_idx = t % season_length
        forecast = level + trend + seasonal[s_idx]
        fitted[t] = forecast
        residuals[t] = float(y[t] - forecast)
        if t >= season_length * n_seasons_train:
            prev_level = level
            level = alpha * (y[t] - seasonal[s_idx]) + (1 - alpha) * (level + trend)
            trend = beta * (level - prev_level) + (1 - beta) * trend
            seasonal[s_idx] = gamma * (y[t] - level) + (1 - gamma) * seasonal[s_idx]

    train_slice = residuals[season_length * n_seasons_train :]
    sigma = _robust_sigma(train_slice)
    band = sigma_multiplier * sigma
    anomalies = [
        {
            "index": int(t),
            "value": round(float(y[t]), 6),
            "expected": round(float(fitted[t]), 6),
            "residual": round(float(residuals[t]), 6),
        }
        for t in range(n)
        if t >= season_length * n_seasons_train and abs(residuals[t]) > band
    ]
    return {
        "anomalies": anomalies[:100],
        "sigma": round(sigma, 6),
        "band": round(band, 6),
        "residuals_head": [round(float(r), 6) for r in residuals[:10]],
        "n_evaluated": int(n - season_length * n_seasons_train),
    }


__all__ = ["dtw_distance", "holt_winters_anomaly"]
