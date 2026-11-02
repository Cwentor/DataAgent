"""risk.report 渲染测试：确定性快照（双渲染全等）+ 章节结构断言 + 端到端。"""

from datetime import date
from pathlib import Path

from core.retrieval.parquet_ref import ParquetRef
from risk.executor import (
    STATUS_GREEN,
    STATUS_RED,
    STATUS_UNKNOWN,
    STATUS_YELLOW,
    InspectionResult,
    MemberFinding,
    RuleFinding,
    run_inspection,
)
from risk.report import render_report
from risk.rules import (
    RiskRule,
    ThresholdSpec,
    WindowSpec,
    load_rules,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEED_RULES = PROJECT_ROOT / "config" / "risk_rules.json"


def _rule(key: str, *, dimension: str | None = None, metric: str = "gmv") -> RiskRule:
    return RiskRule(
        key=key,
        title=f"规则-{key}",
        metric=metric,
        query_domain="trade",
        window=WindowSpec(grain="day", current=date(2025, 12, 31), baseline="previous"),
        thresholds=ThresholdSpec(red=0.05, yellow=0.03),
        comparison=None,
        dimension=dimension,
    )


def _ref(name: str, sha: str = "a" * 64, rows: int = 1) -> ParquetRef:
    return ParquetRef(
        name=name,
        path=f"{name}.parquet",
        rows=rows,
        columns=["gmv"],
        sha256=sha,
        dsl={"metrics": [{"kind": "aggregate", "field": "gmv", "agg": "sum", "alias": "gmv"}]},
        audit={"guard": [], "qa": []},
    )


def _fixed_result() -> InspectionResult:
    """固定输入（不依赖 DB），供确定性快照断言。"""
    f1 = RuleFinding(
        rule=_rule("scalar_yellow"),
        status=STATUS_YELLOW,
        current_value=0.042,
        baseline_value=0.03,
        reason="当期值 0.042 触及黄线 0.03",
        current_ref=_ref("scalar_yellow_cur", sha="b" * 64),
        current_sql="SELECT SUM(f.split_total_amount) FROM order_detail f WHERE 1=0",
    )
    f2 = RuleFinding(
        rule=_rule("scalar_red", metric="refund_rate"),
        status=STATUS_RED,
        current_value=60.0,
        baseline_value=100.0,
        change_ratio=0.4,
        reason="当期值 60 触及红线 0.05",
        current_ref=_ref("scalar_red_cur", sha="c" * 64),
    )
    f3 = RuleFinding(
        rule=_rule("by_province", dimension="province"),
        status=STATUS_RED,
        reason="2 个 province 成员中 1 项异常，最严成员 广东",
        current_ref=_ref("by_province_cur", rows=2),
        members=(
            MemberFinding("广东", STATUS_RED, 0.09, 0.05, "当期值 0.09 触及红线 0.05"),
            MemberFinding("浙江", STATUS_GREEN, 0.01, 0.01, "当期值 0.01 低于黄线 0.03"),
        ),
    )
    f4 = RuleFinding(
        rule=_rule("failed_one"),
        status=STATUS_UNKNOWN,
        reason="巡检执行失败，结果不可判定",
        error="CompileError: 字段不在白名单",
    )
    return InspectionResult(rules=(f1, f2, f3, f4), principal="analyst")


# --------------------------------------------------------------------------- #
# 确定性与总体结构
# --------------------------------------------------------------------------- #
def test_render_deterministic() -> None:
    """同输入双渲染逐字全等（纯确定性核心断言）。"""
    first = render_report(_fixed_result())
    second = render_report(_fixed_result())
    assert first == second


def test_render_section_order() -> None:
    report = render_report(_fixed_result())
    assert report.startswith("# 风险巡检报告（2025-12-31）")
    order = [
        "## 总体结论",
        "## 指标分区",
        "### 规则-scalar_yellow（scalar_yellow）",
        "### 规则-by_province（by_province）",
        "## 异常清单",
        "## 证据链附录",
        "## 口径与假设",
    ]
    positions = [report.index(section) for section in order]
    assert positions == sorted(positions)


def test_render_summary_counts() -> None:
    report = render_report(_fixed_result())
    assert "本期巡检 4 条规则，整体状态 **红**" in report
    assert "红 2 项，黄 1 项，绿 0 项，未知 1 项" in report
    assert (
        "需关注：规则-scalar_yellow（黄）、规则-scalar_red（红）、规则-by_province（红）" in report
    )


# --------------------------------------------------------------------------- #
# 指标分区与异常清单
# --------------------------------------------------------------------------- #
def test_render_scalar_table() -> None:
    report = render_report(_fixed_result())
    assert "| 当期值 | 基线值 | 变化幅度 | 状态 |" in report
    assert "| 0.042 | 0.03 | — | 黄 |" in report
    assert "| 60 | 100 | 40% | 红 |" in report
    assert "判定说明：当期值 0.042 触及黄线 0.03" in report


def test_render_dimension_member_table() -> None:
    report = render_report(_fixed_result())
    assert "| province | 当期值 | 基线值 | 状态 |" in report
    assert "| 广东 | 0.09 | 0.05 | 红 |" in report
    assert "| 浙江 | 0.01 | 0.01 | 绿 |" in report


def test_render_anomaly_list_covers_members() -> None:
    report = render_report(_fixed_result())
    assert "| 级别 | 规则 | 对象 | 当期值 | 判定说明 |" in report
    # 标量异常（整体）与维度异常成员均入清单，绿色成员不入
    assert "| 红 | 规则-scalar_red | 整体 |" in report
    assert "| 红 | 规则-by_province | 广东 | 0.09 |" in report
    assert "浙江" not in report.split("## 异常清单")[1].split("## 证据链附录")[0]


def test_render_no_anomaly_case() -> None:
    f = RuleFinding(
        rule=_rule("all_green"),
        status=STATUS_GREEN,
        current_value=0.01,
        baseline_value=0.01,
        reason="当期值 0.01 低于黄线 0.03",
        current_ref=_ref("all_green_cur"),
    )
    report = render_report(InspectionResult(rules=(f,), principal=None))
    assert "本期无黄红异常项" in report
    assert "全部规则未触及阈值，无异常项" in report


def test_render_error_rule_disclosed() -> None:
    """失败规则在报告如实透出错误（严禁静默吞错）。"""
    report = render_report(_fixed_result())
    assert "执行失败：CompileError: 字段不在白名单" in report


# --------------------------------------------------------------------------- #
# 证据链与口径
# --------------------------------------------------------------------------- #
def test_render_evidence_chain() -> None:
    report = render_report(_fixed_result())
    assert "- 当期数据集 `scalar_yellow_cur`（1 行，sha256 `bbbbbbbbbbbb`）" in report
    assert "- 质检发现 guard 0 项，qa 0 项" in report
    assert "- 审计标识 `risk-scalar_yellow-2025-12-31`" in report
    assert "```json" in report and '"alias": "gmv"' in report
    assert "```sql" in report and "SELECT SUM(f.split_total_amount)" in report


def test_render_caliber_uses_catalog_definition() -> None:
    """口径小节引用语义目录 definition 原文（单一事实源）。"""
    report = render_report(_fixed_result())
    # refund_rate 的目录原文（config/semantic.json metrics[] definition）
    assert "退款率" in report
    assert "数据权限主体：analyst" in report
    assert "零 LLM 参与" in report
    assert "未知项表示数据缺失或执行失败，不代表无风险" in report


# --------------------------------------------------------------------------- #
# 端到端（内存库巡检后渲染）
# --------------------------------------------------------------------------- #
def test_render_end_to_end(tmp_path, conn) -> None:
    rules = load_rules(SEED_RULES)
    result = run_inspection(rules, workspace=tmp_path, conn=conn)
    report = render_report(result)
    assert report.startswith("# 风险巡检报告（2025-12-31）")
    assert "## 总体结论" in report
    assert "## 指标分区" in report
    assert "## 异常清单" in report
    assert "## 证据链附录" in report
    assert "## 口径与假设" in report
    assert render_report(result) == report
