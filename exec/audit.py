"""GuardrailAgent：编译产物 SQL 的执行前确定性审计（结构化裁决）。

对齐 pi-agent-harness 的 GuardrailAgent 角色（SQL 安全与执行计划审计员）：
在既有四层只读防线（``exec.guards.assert_read_only_sql``）与 EXPLAIN ANALYZE
扫描熔断之外，补齐三类**执行前静态审计**并把裁决结构化输出：

1. readonly_structure：只读形态结构校验（复用 sqlglot AST 主防线，冗余防御）；
2. cartesian_product：笛卡尔积检测（无条件 JOIN / CROSS JOIN / 逗号 JOIN）；
3. unbounded_output：无界输出检测（无 LIMIT 且可能返回多行）。

裁决语义（对齐 GuardrailAgent 契约）：
- REJECTED 级发现 => 抛 ``GuardrailRejected``（继承 SqlExecutionError，携带
  结构化 findings），与既有自愈循环零改动兼容——拒绝原因可喂回重写 DSL。
  当前仅笛卡尔积/只读结构违规为 REJECTED（无运行时等价防线、纯逻辑错误）；
- WARNING 级发现 => 随 ``ExecutionResult.findings`` 输出（不否决执行），
  供编排层事件轨迹、ParquetRef 审计字段与报告层消费。无界输出（无 LIMIT）
  为 WARNING：真实边界由运行时返回行数硬上限（ResultLimitExceeded）承担。

审计对象是**编译器确定性产物**：正常链路永不触发 REJECTED；审计作为
不变式防御（编译器演进破坏"必有 LIMIT / 单表无笛卡尔积"约定时熔断）。
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from exec.guards import SqlExecutionError, UnsafeSqlError, _assert_read_only_structure


class GuardrailRejected(SqlExecutionError):
    """执行前审计裁决为 REJECTED：存在确定性高危形态，拒绝执行（可自愈）。"""

    code = "guardrail_rejected"


class GuardrailFinding:
    """一条审计发现：检查项 + 严重级 + 说明 + 优化建议（结构化审计轨迹）。"""

    __slots__ = ("check", "message", "severity", "suggestion")

    def __init__(self, check: str, severity: str, message: str, suggestion: str = "") -> None:
        self.check = check
        self.severity = severity  # "rejected" | "warning"
        self.message = message
        self.suggestion = suggestion

    def to_dict(self) -> dict[str, str]:
        """序列化（事件轨迹 / ParquetRef 审计字段 / 报告层消费）。"""
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"GuardrailFinding({self.check}, {self.severity}, {self.message})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GuardrailFinding):
            return NotImplemented
        return (
            self.check == other.check
            and self.severity == other.severity
            and self.message == other.message
            and self.suggestion == other.suggestion
        )


def _finding_cartesian(join: exp.Join) -> GuardrailFinding | None:
    """单个 JOIN 子句的笛卡尔积判定：CROSS / 无 ON / 无 USING。"""
    kind = (join.kind or "").upper()
    if kind == "CROSS":
        return GuardrailFinding(
            "cartesian_product",
            "rejected",
            "检测到 CROSS JOIN（笛卡尔积）：两表无关联条件全量相乘",
            "改写查询：为 JOIN 提供显式关联条件，或去除多余表引用",
        )
    if join.args.get("on") is None and join.args.get("using") is None and kind != "LATERAL":
        return GuardrailFinding(
            "cartesian_product",
            "rejected",
            "检测到无关联条件的 JOIN（笛卡尔积）：JOIN 子句缺少 ON/USING 条件",
            "改写查询：补充 JOIN 关联条件，或改用 CROSS JOIN 显式声明意图",
        )
    return None


def _is_scalar_aggregate(root: exp.Expression) -> bool:
    """纯标量聚合形态判定：无 GROUP BY / DISTINCT / JOIN / 窗口，SELECT 项全为聚合。"""
    if isinstance(root, exp.Select):
        if root.args.get("group") or root.args.get("distinct"):
            return False
        projections = root.expressions
        if not projections:
            return False
        return all(isinstance(p, exp.AggFunc) for p in projections)
    return False


def audit_compiled_sql(sql: str) -> list[GuardrailFinding]:
    """对编译产物 SQL 做执行前静态审计，返回全部发现（空列表 = APPROVED）。

    只读结构校验复用 sqlglot AST 主防线（fail-closed：解析失败按 REJECTED
    处理）；笛卡尔积与无界输出为增量检查。
    """
    findings: list[GuardrailFinding] = []
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        return [
            GuardrailFinding(
                "readonly_structure",
                "rejected",
                f"SQL 解析失败，拒绝执行: {exc}",
                "检查 SQL 语法合法性",
            )
        ]
    if len(statements) != 1:
        return [
            GuardrailFinding(
                "readonly_structure",
                "rejected",
                "拒绝执行多语句 SQL",
                "单次查询只允许一条 SELECT 语句",
            )
        ]
    root = statements[0]

    # 1) 只读结构（与 exec.guards._assert_read_only_structure 同源，冗余防御）
    try:
        _assert_read_only_structure(sql)
    except UnsafeSqlError as exc:
        findings.append(
            GuardrailFinding("readonly_structure", "rejected", str(exc), "改写为合法只读 SELECT")
        )
        return findings

    # 2) 笛卡尔积：FROM 多表（逗号 JOIN）与无关联条件 JOIN
    for from_node in root.find_all(exp.From):
        extra = [e for e in from_node.expressions if isinstance(e, (exp.Table, exp.Subquery))]
        if extra:
            findings.append(
                GuardrailFinding(
                    "cartesian_product",
                    "rejected",
                    "检测到逗号分隔的多表 FROM（笛卡尔积）：无关联条件全量相乘",
                    "改写查询：使用带 ON 条件的显式 JOIN",
                )
            )
    for join in root.find_all(exp.Join):
        finding = _finding_cartesian(join)
        if finding is not None:
            findings.append(finding)

    # 3) 无界输出：无 LIMIT 即 warning（真实边界由运行时 max_result_rows 硬上限
    #    熔断承担；静态层只做可见性提示——区分"编译器按约定抹除冗余 LIMIT 的
    #    纯标量聚合"与"可能多行且无 LIMIT 的其他形态"）
    if root.find(exp.Limit) is None:
        if _is_scalar_aggregate(root):
            findings.append(
                GuardrailFinding(
                    "unbounded_output",
                    "warning",
                    "纯标量聚合无 LIMIT（编译器按约定抹除冗余 LIMIT，单行结果安全）",
                    "无需处理；此记录仅为审计可见性",
                )
            )
        else:
            findings.append(
                GuardrailFinding(
                    "unbounded_output",
                    "warning",
                    "查询无 LIMIT 且可能返回多行（运行时返回行数硬上限兜底熔断）",
                    "DSL 链路应显式声明 limit 字段；直接 SQL 调用方建议显式 LIMIT",
                )
            )
    return findings


def assert_guardrails(sql: str) -> list[GuardrailFinding]:
    """执行前审计门：REJECTED 级发现即抛 GuardrailRejected；返回 WARNING 级发现。

    供 ``exec.guards.execute_sql`` 在只读断言后调用——REJECTED 语义与
    MaxRowsScannedExceeded 一致（执行前熔断、错误可自愈）。
    """
    findings = audit_compiled_sql(sql)
    rejected = [f for f in findings if f.severity == "rejected"]
    if rejected:
        detail = "；".join(f"[{f.check}] {f.message}" for f in rejected)
        raise GuardrailRejected(f"执行前审计拒绝（{len(rejected)} 项）：{detail}")
    return [f for f in findings if f.severity == "warning"]


__all__ = ["GuardrailFinding", "GuardrailRejected", "assert_guardrails", "audit_compiled_sql"]
