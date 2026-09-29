"""报告数值溯源校验（十八期：溯源校验 + 定向重试闭环）。

LLM 综合报告中的数值应能溯源到真实查询数据。归一化容差覆盖常见量纲：
千分位逗号、万/亿折算、百分比、四舍五入（<=0.5% 相对 + 0.01 绝对）——
合法的格式化呈现不得触发溯源报警（Review Focus 5）。

闭环行为（synthesize_node 消费本模块）：不可溯源数值超过阈值时，携带修正
指令定向重写报告 1 次；重写后仍超阈值则弃用 LLM 叙事、降级确定性渲染，
残留 1-3 个不可溯源数值必须显式标注数据溯源提示（诚实兜底可见化）。
"""

from __future__ import annotations

import re
from typing import Any

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_UNIT_PATTERN = re.compile(r"\s*(亿元|万元|万|亿|%)")
# 日期形态剥离：YYYY-MM-DD / YYYY/MM/DD / YYYY年MM月DD日 / YYYY年MM月 等
_DATE_PATTERN = re.compile(r"\d{4}\s*[-年/]\s*\d{1,2}(?:\s*[-月/]\s*\d{1,2})?\s*日?")
# Markdown/中文列表序号剥离（"1. 建议" / "2、建议" / "3) 建议"）。
# 2026-09-29 线上实证：四段式报告的"业务排查建议"小节通常 4 条编号列表，
# 序号被数字正则抠成业务数值 => 恰超阈值 >3 => 整份忠实报告被弃用。
# (?!\d) 防误剥行首小数（"1.5 万元" 的 "1." 后是数字，不剥离）。
_LIST_MARK_PATTERN = re.compile(r"(?m)^\s*\d{1,2}[.、)](?!\d)\s*")


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
    """返回报告中不可溯源的数值 token（原样文本，供报告引用）。

    日期上下文排除（终审 Important #4）：先剥离 YYYY-MM-DD / YYYY年MM月 等日期
    形态、再跳过紧邻"年/月/日/季度"的时间 token——LLM 报告必然引用日期，
    而日期几乎从不在 allowed 集，不排除会造成系统性误报。

    列表序号排除（2026-09-29 线上修复）：Markdown/中文编号（"1. 建议"）
    是结构标记不是数据，剥离后再做数值审查（同上：每份建议列表必含
    序号，不排除会造成 >3 阈值的系统性误杀）。
    """
    text = _DATE_PATTERN.sub(" ", report)
    text = _LIST_MARK_PATTERN.sub(" ", text)
    flagged: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0)
        tail = text[match.end() : match.end() + 3]
        if tail[:1] in ("年", "月", "日", "季") or tail.startswith("季度"):
            continue  # 时间上下文 token（如"4 月"），非业务数值
        unit_match = _UNIT_PATTERN.match(text[match.end() : match.end() + 4])
        unit = unit_match.group(1) if unit_match else ""
        try:
            number = float(token.replace(",", ""))
        except ValueError:
            continue
        if not any(_is_grounded(allowed, c) for c in _candidates(number, unit)):
            flagged.append(f"{token}{unit}".strip())
    return flagged


def _collect_string_numbers(text: str, out: set[float]) -> None:
    """字符串值里的数字按量纲候选入白名单（2026-09-29 线上修复）。

    summary 的字符串字段（findings 叙述"增长 6.2%"、被 Coder 误塞统计
    dict 的 title repr 串）里的数字来自确定性上游产物，不是 LLM 编造——
    LLM 忠实复述必须可溯源。单位上下文（%/万/亿）决定量纲候选，与报告侧
    审查共用 _candidates 同口径。
    """
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0)
        unit_match = _UNIT_PATTERN.match(text[match.end() : match.end() + 4])
        unit = unit_match.group(1) if unit_match else ""
        try:
            number = float(token.replace(",", ""))
        except ValueError:
            continue
        out.update(_candidates(number, unit))


def _collect_numbers(payload: Any, out: set[float]) -> None:
    if isinstance(payload, bool):
        return
    if isinstance(payload, (int, float)):
        out.add(float(payload))
    elif isinstance(payload, str):
        _collect_string_numbers(payload, out)
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
