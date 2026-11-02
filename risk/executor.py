"""确定性巡检执行器（issue #26 最小闭环）。

流程：规则直产 DSL（shape 直译，口径引用 semantic.json metrics[] 单一事实源）
-> 当期与基线两次 ``execute_dsl_query`` 全护栏取数（RLS / 扫描熔断 / 超时 /
PII 脱敏零旁路）-> 数值提取 -> 红黄绿判定（含理由与阈值快照）-> 审计落盘。

确定性约束：

- 零 LLM 依赖，同输入必出同判定；
- 单规则失败不拖垮整个巡检，但严禁静默吞错：失败规则以 ``unknown`` 状态
  与 ``error`` 字段如实透出到报告；
- 基线缺失或为零时异动不可判定，如实标注，严禁虚构变化率。

依赖方向：risk 位于治理管道消费侧，只依赖 semantic/catalog、core/retrieval、
audit，不依赖 agent / orchestrator。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from risk.rules import RiskRule, RiskRules, previous_bounds, window_bounds
from semantic import catalog

# 状态常量与严重度序（判定取更严；unknown 仅在不可判定时出现）
STATUS_RED = "red"
STATUS_YELLOW = "yellow"
STATUS_GREEN = "green"
STATUS_UNKNOWN = "unknown"

_SEVERITY: dict[str, int] = {
    STATUS_RED: 3,
    STATUS_YELLOW: 2,
    STATUS_GREEN: 1,
    STATUS_UNKNOWN: 0,
}

# 巡检 DSL 行上限（维度成员分组场景，契约上限 10000 内；语义目录成员重建
# 上限为 512，此处取 1000 覆盖全部成员）
_INSPECTION_LIMIT = 1000


@dataclass(frozen=True)
class MemberFinding:
    """维度成员级发现（dimension 非空时逐成员判定）。"""

    member: str
    status: str
    current_value: float | None
    baseline_value: float | None
    reason: str


@dataclass(frozen=True)
class RuleFinding:
    """单条规则巡检发现（报告渲染的统一输入）。"""

    rule: RiskRule
    status: str
    current_value: float | None = None
    baseline_value: float | None = None
    change_ratio: float | None = None
    reason: str = ""
    current_ref: Any | None = None
    baseline_ref: Any | None = None
    current_sql: str | None = None
    baseline_sql: str | None = None
    members: tuple[MemberFinding, ...] = ()
    error: str | None = None

    @property
    def is_abnormal(self) -> bool:
        """黄或红即异常项（报告异常清单的准入条件）。"""
        return self.status in (STATUS_RED, STATUS_YELLOW)


@dataclass(frozen=True)
class InspectionResult:
    """一次巡检的完整结果（确定性产物，不含执行时刻等易变元信息）。"""

    rules: tuple[RuleFinding, ...] = field(default_factory=tuple)
    principal: str | None = None

    @property
    def status_counts(self) -> dict[str, int]:
        counts = {STATUS_RED: 0, STATUS_YELLOW: 0, STATUS_GREEN: 0, STATUS_UNKNOWN: 0}
        for f in self.rules:
            counts[f.status] = counts.get(f.status, 0) + 1
        return counts

    @property
    def overall_status(self) -> str:
        """整体状态 = 各规则状态取最严（全部 unknown 时为 unknown）。"""
        if not self.rules:
            return STATUS_UNKNOWN
        return max(self.rules, key=lambda f: _SEVERITY[f.status]).status


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def run_inspection(
    rules: RiskRules,
    *,
    principal: str | None = None,
    workspace: Path | str,
    conn: Any | None = None,
    audit_store: Any | None = None,
) -> InspectionResult:
    """执行全部规则巡检（单规则失败透出为 unknown，不中断）。"""
    findings = tuple(
        _inspect_rule(rule, principal=principal, workspace=workspace, conn=conn)
        for rule in rules.rules
    )
    result = InspectionResult(rules=findings, principal=principal)
    if audit_store is not None:
        _write_audit(audit_store, result)
    return result


# --------------------------------------------------------------------------- #
# 单规则执行
# --------------------------------------------------------------------------- #
def _inspect_rule(
    rule: RiskRule,
    *,
    principal: str | None,
    workspace: Path | str,
    conn: Any | None,
) -> RuleFinding:
    from core.retrieval.tools import compile_dsl_to_duckdb, execute_dsl_query

    try:
        shape_entries, primary_alias = _metric_shape(rule.metric)
        cur_start, cur_end = window_bounds(rule.window.grain, rule.window.current)
        cur_dsl = _build_rule_dsl(rule, shape_entries, cur_start, cur_end)
        cur_ref = execute_dsl_query(
            cur_dsl,
            principal=principal,
            workspace=workspace,
            name=f"{rule.key}_cur",
            query=f"[risk:{rule.key}] {rule.title}",
            conn=conn,
        )
        cur_sql = compile_dsl_to_duckdb(cur_dsl, principal=principal)

        base_ref = None
        base_sql = None
        cur_rows = _read_rows(workspace, cur_ref)
        base_rows: list[dict[str, Any]] = []
        if rule.comparison is not None:
            base_start, base_end = previous_bounds(rule.window.grain, cur_start, cur_end)
            base_dsl = _build_rule_dsl(rule, shape_entries, base_start, base_end)
            base_ref = execute_dsl_query(
                base_dsl,
                principal=principal,
                workspace=workspace,
                name=f"{rule.key}_base",
                query=f"[risk:{rule.key}:baseline] {rule.title}",
                conn=conn,
            )
            base_sql = compile_dsl_to_duckdb(base_dsl, principal=principal)
            base_rows = _read_rows(workspace, base_ref)

        if rule.dimension is None:
            return _judge_scalar(
                rule, cur_rows, base_rows, primary_alias, cur_ref, base_ref, cur_sql, base_sql
            )
        return _judge_by_dimension(
            rule, cur_rows, base_rows, primary_alias, cur_ref, base_ref, cur_sql, base_sql
        )
    except Exception as exc:  # 宽捕获为单规则隔离语义服务，失败必须透出不吞错
        return RuleFinding(
            rule=rule,
            status=STATUS_UNKNOWN,
            reason="巡检执行失败，结果不可判定",
            error=f"{type(exc).__name__}: {exc}",
        )


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #
def _judge_scalar(
    rule: RiskRule,
    cur_rows: list[dict[str, Any]],
    base_rows: list[dict[str, Any]],
    alias: str,
    cur_ref: Any,
    base_ref: Any,
    cur_sql: str,
    base_sql: str | None,
) -> RuleFinding:
    cur_val = _scalar_value(cur_rows, alias)
    base_val = _scalar_value(base_rows, alias) if base_rows else None
    status, reason, change_ratio = _evaluate(rule, cur_val, base_val)
    return RuleFinding(
        rule=rule,
        status=status,
        current_value=cur_val,
        baseline_value=base_val,
        change_ratio=change_ratio,
        reason=reason,
        current_ref=cur_ref,
        baseline_ref=base_ref,
        current_sql=cur_sql,
        baseline_sql=base_sql,
    )


def _judge_by_dimension(
    rule: RiskRule,
    cur_rows: list[dict[str, Any]],
    base_rows: list[dict[str, Any]],
    alias: str,
    cur_ref: Any,
    base_ref: Any,
    cur_sql: str,
    base_sql: str | None,
) -> RuleFinding:
    dim = rule.dimension or ""
    cur_by_member = {str(r.get(dim)): r for r in cur_rows}
    base_by_member = {str(r.get(dim)): r for r in base_rows}
    members: list[MemberFinding] = []
    for member, row in cur_by_member.items():
        cur_val = _number(row.get(alias))
        base_row = base_by_member.get(member)
        base_val = _number(base_row.get(alias)) if base_row else None
        m_status, m_reason, _ = _evaluate(rule, cur_val, base_val)
        members.append(
            MemberFinding(
                member=member,
                status=m_status,
                current_value=cur_val,
                baseline_value=base_val,
                reason=m_reason,
            )
        )
    if not members:
        overall, reason = STATUS_UNKNOWN, "当期无数据，全部成员不可判定"
        change_ratio = None
    else:
        worst = max(members, key=lambda m: _SEVERITY[m.status])
        n_bad = sum(1 for m in members if m.status in (STATUS_RED, STATUS_YELLOW))
        overall = worst.status
        reason = f"{len(members)} 个 {dim} 成员中 {n_bad} 项异常，最严成员 {worst.member}"
        change_ratio = None
        if rule.comparison is not None:
            ratios = [
                _change_ratio(rule.comparison.direction, m.current_value, m.baseline_value)
                for m in members
            ]
            known = [r for r in ratios if r is not None]
            change_ratio = max(known) if known else None
    return RuleFinding(
        rule=rule,
        status=overall,
        current_value=None,
        baseline_value=None,
        change_ratio=change_ratio,
        reason=reason,
        current_ref=cur_ref,
        baseline_ref=base_ref,
        current_sql=cur_sql,
        baseline_sql=base_sql,
        members=tuple(members),
    )


def _evaluate(
    rule: RiskRule, cur_val: float | None, base_val: float | None
) -> tuple[str, str, float | None]:
    """对单点值做全部已配置判定，返回（最严状态，聚合理由，变化率）。"""
    statuses: list[str] = []
    reasons: list[str] = []
    change_ratio: float | None = None

    if rule.thresholds is not None:
        if cur_val is None:
            statuses.append(STATUS_UNKNOWN)
            reasons.append("当期无数据，绝对阈值不可判定")
        elif cur_val >= rule.thresholds.red:
            statuses.append(STATUS_RED)
            reasons.append(f"当期值 {_fmt(cur_val)} 触及红线 {_fmt(rule.thresholds.red)}")
        elif cur_val >= rule.thresholds.yellow:
            statuses.append(STATUS_YELLOW)
            reasons.append(f"当期值 {_fmt(cur_val)} 触及黄线 {_fmt(rule.thresholds.yellow)}")
        else:
            statuses.append(STATUS_GREEN)
            reasons.append(f"当期值 {_fmt(cur_val)} 低于黄线 {_fmt(rule.thresholds.yellow)}")

    if rule.comparison is not None:
        if cur_val is None or base_val is None or base_val == 0:
            statuses.append(STATUS_UNKNOWN)
            reasons.append("基线缺失或为零，异动不可判定")
        else:
            change_ratio = _change_ratio(rule.comparison.direction, cur_val, base_val)
            magnitude = change_ratio if change_ratio is not None else 0.0
            if magnitude >= rule.comparison.red:
                statuses.append(STATUS_RED)
                reasons.append(
                    f"较基线{'下跌' if rule.comparison.direction == 'drop' else '上涨'} "
                    f"{_fmt(magnitude * 100)}%，触及红线 {_fmt(rule.comparison.red * 100)}%"
                )
            elif magnitude >= rule.comparison.yellow:
                statuses.append(STATUS_YELLOW)
                reasons.append(
                    f"较基线{'下跌' if rule.comparison.direction == 'drop' else '上涨'} "
                    f"{_fmt(magnitude * 100)}%，触及黄线 {_fmt(rule.comparison.yellow * 100)}%"
                )
            else:
                statuses.append(STATUS_GREEN)
                reasons.append(f"较基线变化 {_fmt(magnitude * 100)}%，未触及异动阈值")

    if not statuses:
        # loader 已保证至少一个判定形态，此处为防御兜底
        return STATUS_UNKNOWN, "规则无可判定形态", None
    status = max(statuses, key=lambda s: _SEVERITY[s])
    return status, "；".join(reasons), change_ratio


def _change_ratio(direction: str, cur_val: float, base_val: float) -> float:
    """相对变化幅度（非负语义：drop 取跌幅，raise 取涨幅）。"""
    if direction == "drop":
        return max(0.0, (base_val - cur_val) / abs(base_val))
    return max(0.0, (cur_val - base_val) / abs(base_val))


# --------------------------------------------------------------------------- #
# DSL 组装与 shape 直译
# --------------------------------------------------------------------------- #
def _build_rule_dsl(
    rule: RiskRule, shape_entries: list[dict[str, Any]], start: date, end: date
) -> dict[str, Any]:
    """规则契约到 QueryDSL dict 的确定性组装（时间窗口为 absolute 绝对窗口）。"""
    dsl: dict[str, Any] = {
        "metrics": shape_entries,
        "dimensions": [{"field": rule.dimension}] if rule.dimension else [],
        "time_filter": {
            "granularity": rule.window.grain,
            "range_type": "absolute",
            "absolute": {"start": start.isoformat(), "end": end.isoformat()},
            "time_field": rule.time_field,
            "comparison": "none",
        },
        "filters": [],
        "limit": _INSPECTION_LIMIT,
    }
    return dsl


def _metric_shape(metric_key: str) -> tuple[list[dict[str, Any]], str]:
    """按 key 取语义目录指标 shape 契约并直译（主 alias = 最后条目的 alias）。"""
    for m in catalog.METRICS:
        if m.get("key") != metric_key:
            continue
        shape = m.get("shape")
        entries = shape if isinstance(shape, list) else [shape] if shape else []
        if not entries:
            raise ValueError(f"指标 {metric_key!r} 缺少 shape 契约，无法组装巡检 DSL")
        for entry in entries:
            if isinstance(entry, dict) and entry.get("kind") == "window":
                raise ValueError(f"指标 {metric_key!r} 为窗口指标，风险规则暂不支持窗口形态")
        primary = entries[-1].get("alias") if isinstance(entries[-1], dict) else None
        if not primary:
            raise ValueError(f"指标 {metric_key!r} shape 末条目缺少 alias")
        return entries, primary
    raise ValueError(f"指标 {metric_key!r} 未在语义目录 metrics[] 登记")


# --------------------------------------------------------------------------- #
# 数值提取
# --------------------------------------------------------------------------- #
def _read_rows(workspace: Path | str, ref: Any) -> list[dict[str, Any]]:
    """读取 ParquetRef 物化产物为行 dict 列表（治理管道产物只读消费）。"""
    import pyarrow.parquet as pq

    path = Path(workspace) / "inputs" / ref.path
    table = pq.read_table(path)
    cols = table.to_pydict()
    return [{name: cols[name][i] for name in cols} for i in range(table.num_rows)]


def _scalar_value(rows: list[dict[str, Any]], alias: str) -> float | None:
    if not rows:
        return None
    return _number(rows[0].get(alias))


def _number(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _fmt(value: float) -> str:
    """判定理由内的数值呈现（.6g 足够巡检语义，展示格式化归报告层）。"""
    return f"{value:.6g}"


# --------------------------------------------------------------------------- #
# 审计落盘
# --------------------------------------------------------------------------- #
def _write_audit(store: Any, result: InspectionResult) -> None:
    """每规则写一条审计记录（复用 AuditStore，失败由 store 内部只记日志）。"""
    from audit.record import AuditRecord

    records = []
    for f in result.rules:
        records.append(
            AuditRecord(
                request_id=f"risk-{f.rule.key}-{f.rule.window.current.isoformat()}",
                prompt=f"[risk] {f.rule.title}（{f.rule.key}）",
                session_id="risk-inspection",
                principal=result.principal,
                dsl=f.current_ref.dsl if f.current_ref is not None else None,
                sql=f.current_sql,
                row_count=f.current_ref.rows if f.current_ref is not None else None,
                answered_by="risk_inspection",
                error=f.error,
            )
        )
    for record in records:
        store.write(record)
