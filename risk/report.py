"""风险报告确定性渲染（issue #26 最小闭环）。

``InspectionResult -> Markdown``，纯函数零依赖外部状态，同输入必出同报告
（渲染快照可逐字断言）。章节顺序固定（结论前置、佐证在后）：

1. 总体结论（红黄绿计数与整体状态）；
2. 指标分区（逐规则状态表与判定说明）；
3. 异常清单（黄红项汇总，维度规则精确到成员）；
4. 证据链附录（DSL、SQL、数据 sha256、质检发现、审计标识）；
5. 口径与假设（指标 definition 取语义目录原文、窗口口径、数据域锚定）。

"""

from __future__ import annotations

import json

from risk.executor import (
    STATUS_GREEN,
    STATUS_RED,
    STATUS_UNKNOWN,
    STATUS_YELLOW,
    InspectionResult,
    RuleFinding,
)
from semantic import catalog

_STATUS_LABELS: dict[str, str] = {
    STATUS_RED: "红",
    STATUS_YELLOW: "黄",
    STATUS_GREEN: "绿",
    STATUS_UNKNOWN: "未知",
}

_STATUS_ORDER: tuple[str, ...] = (STATUS_RED, STATUS_YELLOW, STATUS_GREEN, STATUS_UNKNOWN)

_NO_DATA = "—"


def render_report(result: InspectionResult) -> str:
    """渲染风险巡检 Markdown 报告（确定性，可快照断言）。"""
    lines: list[str] = []
    report_date = max(f.rule.window.current for f in result.rules)
    lines.append(f"# 风险巡检报告（{report_date.isoformat()}）")
    lines.append("")
    _render_summary(result, lines)
    lines.append("## 指标分区")
    lines.append("")
    for f in result.rules:
        _render_rule_section(f, lines)
    _render_anomaly_list(result, lines)
    _render_evidence(result, lines)
    _render_caliber(result, lines)
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# 总体结论（前置）
# --------------------------------------------------------------------------- #
def _render_summary(result: InspectionResult, lines: list[str]) -> None:
    counts = result.status_counts
    total = len(result.rules)
    overall = _STATUS_LABELS[result.overall_status]
    lines.append("## 总体结论")
    lines.append("")
    lines.append(
        f"本期巡检 {total} 条规则，整体状态 **{overall}**。"
        f"红 {counts[STATUS_RED]} 项，黄 {counts[STATUS_YELLOW]} 项，"
        f"绿 {counts[STATUS_GREEN]} 项，未知 {counts[STATUS_UNKNOWN]} 项。"
    )
    abnormal = [f for f in result.rules if f.is_abnormal]
    if abnormal:
        names = "、".join(f"{f.rule.title}（{_STATUS_LABELS[f.status]}）" for f in abnormal)
        lines.append("")
        lines.append(f"需关注：{names}。")
    elif total and not counts[STATUS_UNKNOWN]:
        lines.append("")
        lines.append("全部规则未触及阈值，无异常项。")
    lines.append("")


# --------------------------------------------------------------------------- #
# 指标分区
# --------------------------------------------------------------------------- #
def _render_rule_section(f: RuleFinding, lines: list[str]) -> None:
    rule = f.rule
    status = _STATUS_LABELS[f.status]
    lines.append(f"### {rule.title}（{rule.key}）")
    lines.append("")
    if f.error is not None:
        lines.append(f"状态 **{status}**。执行失败：{f.error}")
        lines.append("")
        return
    grain = rule.window.grain
    cur = rule.window.current.isoformat()
    lines.append(f"状态 **{status}**。窗口口径 {grain}，当期 {cur}。")
    lines.append("")
    if rule.dimension is None:
        lines.append("| 当期值 | 基线值 | 变化幅度 | 状态 |")
        lines.append("| --- | --- | --- | --- |")
        change = _pct(f.change_ratio) if f.change_ratio is not None else _NO_DATA
        lines.append(
            f"| {_num(f.current_value)} | {_num(f.baseline_value)} | {change} | {status} |"
        )
    else:
        lines.append(f"| {rule.dimension} | 当期值 | 基线值 | 状态 |")
        lines.append("| --- | --- | --- | --- |")
        for m in f.members:
            lines.append(
                f"| {m.member} | {_num(m.current_value)} | {_num(m.baseline_value)} "
                f"| {_STATUS_LABELS[m.status]} |"
            )
    lines.append("")
    lines.append(f"判定说明：{f.reason}")
    lines.append("")


# --------------------------------------------------------------------------- #
# 异常清单
# --------------------------------------------------------------------------- #
def _render_anomaly_list(result: InspectionResult, lines: list[str]) -> None:
    lines.append("## 异常清单")
    lines.append("")
    abnormal = [f for f in result.rules if f.is_abnormal]
    member_rows: list[tuple[str, str, str, str, str]] = []
    for f in abnormal:
        level = _STATUS_LABELS[f.status]
        if f.rule.dimension is None:
            member_rows.append((level, f.rule.title, "整体", _num(f.current_value), f.reason))
        for m in f.members:
            if m.status in (STATUS_RED, STATUS_YELLOW):
                member_rows.append(
                    (
                        _STATUS_LABELS[m.status],
                        f.rule.title,
                        m.member,
                        _num(m.current_value),
                        m.reason,
                    )
                )
    if not member_rows:
        lines.append("本期无黄红异常项。")
        lines.append("")
        return
    lines.append("| 级别 | 规则 | 对象 | 当期值 | 判定说明 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in member_rows:
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")


# --------------------------------------------------------------------------- #
# 证据链附录
# --------------------------------------------------------------------------- #
def _render_evidence(result: InspectionResult, lines: list[str]) -> None:
    lines.append("## 证据链附录")
    lines.append("")
    for f in result.rules:
        lines.append(f"### {f.rule.key}")
        lines.append("")
        if f.current_ref is None:
            lines.append("当期取数失败，无证据产物。")
            lines.append("")
            continue
        cur = f.current_ref
        lines.append(f"- 当期数据集 `{cur.name}`（{cur.rows} 行，sha256 `{_short(cur.sha256)}`）")
        if f.baseline_ref is not None:
            base = f.baseline_ref
            lines.append(
                f"- 基线数据集 `{base.name}`（{base.rows} 行，sha256 `{_short(base.sha256)}`）"
            )
        guard = cur.audit.get("guard", []) if isinstance(cur.audit, dict) else []
        qa = cur.audit.get("qa", []) if isinstance(cur.audit, dict) else []
        lines.append(f"- 质检发现 guard {len(guard)} 项，qa {len(qa)} 项")
        lines.append(f"- 审计标识 `risk-{f.rule.key}-{f.rule.window.current.isoformat()}`")
        lines.append("- 当期 DSL：")
        lines.append("")
        lines.append("  ```json")
        lines.append(f"  {json.dumps(cur.dsl, ensure_ascii=False, sort_keys=True)}")
        lines.append("  ```")
        if f.current_sql:
            lines.append("- 当期 SQL（RLS 注入后）：")
            lines.append("")
            lines.append("  ```sql")
            for sql_line in f.current_sql.strip().splitlines():
                lines.append(f"  {sql_line}")
            lines.append("  ```")
        lines.append("")


# --------------------------------------------------------------------------- #
# 口径与假设
# --------------------------------------------------------------------------- #
def _render_caliber(result: InspectionResult, lines: list[str]) -> None:
    lines.append("## 口径与假设")
    lines.append("")
    seen: set[str] = set()
    for f in result.rules:
        if f.rule.metric in seen:
            continue
        seen.add(f.rule.metric)
        definition = _metric_definition(f.rule.metric)
        lines.append(f"- `{f.rule.metric}`：{definition}")
    principal = result.principal or "内部默认身份"
    lines.append(f"- 数据权限主体：{principal}（RLS 随执行注入，行级过滤以此为准）")
    lines.append(
        "- 窗口口径：当期与基线均为绝对窗口（锚定确定性，不使用相对时间）；"
        "基线为按粒度推算的上一期"
    )
    lines.append(
        "- 本报告由确定性巡检链路生成，零 LLM 参与；未知项表示数据缺失或" "执行失败，不代表无风险"
    )
    lines.append("")


def _metric_definition(metric_key: str) -> str:
    for m in catalog.METRICS:
        if m.get("key") == metric_key:
            title = m.get("title", metric_key)
            definition = m.get("definition", "").strip()
            return f"{title}。{definition}" if definition else title
    return f"指标 {metric_key} 未在语义目录登记"


# --------------------------------------------------------------------------- #
# 格式化辅助
# --------------------------------------------------------------------------- #
def _num(value: float | None) -> str:
    """数值呈现：None 显示占位符，其余千分位加有效数字。"""
    if value is None:
        return _NO_DATA
    if float(value).is_integer() and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.6g}"


def _pct(ratio: float | None) -> str:
    if ratio is None:
        return _NO_DATA
    return f"{ratio * 100:,.4g}%"


def _short(sha: str) -> str:
    return sha[:12] if sha else _NO_DATA
