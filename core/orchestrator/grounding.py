"""报告数值溯源校验（十八期最小版：只标注、不拦截）。

LLM 综合报告中的数值应能溯源到真实查询数据。归一化容差覆盖常见量纲：
千分位逗号、万/亿折算、百分比、四舍五入（<=0.5% 相对 + 0.01 绝对）——
合法的格式化呈现不得触发溯源报警（Review Focus 5）。拦截重试闭环属
二期（防死循环/超时）。
"""

from __future__ import annotations

import re
from typing import Any

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_UNIT_PATTERN = re.compile(r"\s*(亿元|万元|万|亿|%)")


def _candidates(number: float, unit: str) -> list[float]:
    """数值在不同量纲下的候选原值（含绝对值形态——"下降7.8%"溯源到 0.078）。"""
    cands = [number]
    if unit in ("万", "万元"):
        cands.append(number * 10000)
    elif unit in ("亿", "亿元"):
        cands.append(number * 1e8)
    elif unit == "%":
        cands.extend([number / 100, number])
    return cands + [abs(c) for c in cands]


def _is_grounded(allowed: set[float], value: float) -> bool:
    return any(abs(value - a) <= max(abs(a) * 0.005, 0.01) for a in allowed)


def grounding_review(report: str, allowed: set[float]) -> list[str]:
    """返回报告中不可溯源的数值 token（原样文本，供报告引用）。"""
    flagged: list[str] = []
    for match in _NUMBER_RE.finditer(report):
        token = match.group(0)
        unit_match = _UNIT_PATTERN.match(report[match.end() : match.end() + 4])
        unit = unit_match.group(1) if unit_match else ""
        try:
            number = float(token.replace(",", ""))
        except ValueError:
            continue
        if not any(_is_grounded(allowed, c) for c in _candidates(number, unit)):
            flagged.append(f"{token}{unit}".strip())
    return flagged


def _collect_numbers(payload: Any, out: set[float]) -> None:
    if isinstance(payload, bool):
        return
    if isinstance(payload, (int, float)):
        out.add(float(payload))
    elif isinstance(payload, dict):
        for v in payload.values():
            _collect_numbers(v, out)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            _collect_numbers(v, out)


def collect_allowed_values(state: Any, workspace: Any = None) -> set[float]:
    """从数据集预览与 summary 产物收集可溯源数值全集（读取失败容忍降级）。"""
    allowed: set[float] = set()
    for artifact in getattr(state, "artifacts", []) or []:
        _collect_numbers(artifact.payload, allowed)
    if workspace is not None:
        try:
            from core.orchestrator.nodes import _preview_rows

            for ref in (getattr(state, "datasets", {}) or {}).values():
                rows = _preview_rows(str(workspace / "inputs" / ref.get("path", "")))
                for row in rows:
                    for cell in row:
                        if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                            allowed.add(float(cell))
        except Exception:
            pass  # 预览不可用时容忍降级：校验退化为 summary 产物范围
    return allowed
