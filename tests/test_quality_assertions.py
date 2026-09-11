"""DataQAAgent 结果断言单元测试（pi-agent-harness 对齐：行动项 2）。

覆盖：
- 四类断言：empty_result / null_rate / negative_metric / dimension_uniqueness；
- 语义边界：ratio/window 派生指标不做负值断言；无维度查询跳过唯一性；
- execute_dsl_query 端到端：QA 发现随 ParquetRef.audit 契约字段输出。
"""

from __future__ import annotations

from core.retrieval.quality import NULL_RATE_THRESHOLD, run_quality_assertions
from core.retrieval.tools import execute_dsl_query
from semantic.dsl_schema import QueryDSL

DSL = QueryDSL.model_validate(
    {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
        "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
    }
)


def _checks(findings):
    """findings -> {check: [severity, ...]} 便于断言。"""
    result: dict[str, list[str]] = {}
    for f in findings:
        result.setdefault(f.check, []).append(f.severity)
    return result


def test_clean_aggregate_no_findings():
    """正常聚合结果：零发现。"""
    findings = run_quality_assertions(["province", "gmv"], [["广东", 100.0], ["浙江", 50.0]], DSL)
    assert findings == []


def test_empty_result_warning():
    """0 行结果：empty_result warning（不否决，提示合法空集与逻辑缺陷双可能）。"""
    findings = run_quality_assertions(["province", "gmv"], [], DSL)
    assert _checks(findings) == {"empty_result": ["warning"]}


def test_null_rate_threshold():
    """指标列空值率超阈值：null_rate warning；恰好达阈值即触发。"""
    rows = [[None]] * 5 + [["广东", 1.0]] * 5
    findings = run_quality_assertions(["province", "gmv"], rows, DSL)
    assert "null_rate" in _checks(findings)
    assert NULL_RATE_THRESHOLD == 0.5


def test_null_rate_all_null_message():
    """指标列全空：null_rate warning 且消息含'全部为空'。"""
    findings = run_quality_assertions(["gmv"], [[None], [None]], DSL)
    assert "null_rate" in _checks(findings)
    assert any("全部为空" in f.message for f in findings)


def test_negative_metric_sum_rejected_semantically():
    """sum 聚合出现负值：negative_metric warning。"""
    rows = [["广东", -30.0], ["浙江", 50.0]]
    findings = run_quality_assertions(["province", "gmv"], rows, DSL)
    checks = _checks(findings)
    assert checks.get("negative_metric") == ["warning"]


def test_negative_ratio_not_flagged():
    """ratio 派生指标为负：不做负值断言（派生语义可正可负）。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {
                    "kind": "ratio",
                    "numerator": {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "num",
                    },
                    "denominator": {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "den",
                    },
                    "alias": "rate",
                }
            ],
        }
    )
    findings = run_quality_assertions(["rate"], [[-0.5]], dsl)
    assert "negative_metric" not in _checks(findings)


def test_dimension_uniqueness_duplicate_combo_error():
    """聚合查询维度组合重复：dimension_uniqueness error（编译/聚合缺陷信号）。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "province"}],
        }
    )
    rows = [["广东", 10.0], ["广东", 20.0]]
    findings = run_quality_assertions(["province", "gmv"], rows, dsl)
    assert _checks(findings) == {"dimension_uniqueness": ["error"]}


def test_dimension_uniqueness_skipped_without_dimensions():
    """无维度查询：跳过唯一性检查（标量/单行合法）。"""
    findings = run_quality_assertions(["gmv"], [[100.0], [200.0]], DSL)
    assert "dimension_uniqueness" not in _checks(findings)


def test_execute_dsl_query_audit_contract(tmp_path):
    """端到端：QA 发现（含纯标量无 LIMIT 的 guard warning）随 ParquetRef.audit 输出。"""
    ref = execute_dsl_query(DSL, principal="admin", workspace=tmp_path, name="qa_audit")
    assert isinstance(ref.audit, dict)
    assert set(ref.audit.keys()) == {"guard", "qa"}
    # 正常 mock 数仓聚合：qa 零发现
    assert ref.audit["qa"] == []
    # 纯标量聚合无 LIMIT：guard 层 warning（编译器按约定抹除冗余 LIMIT）
    assert any(
        f["check"] == "unbounded_output" and f["severity"] == "warning" for f in ref.audit["guard"]
    )


# --------------------------------------------------------------------------- #
# 编排消费层（DataQAAgent 角色接线）
# --------------------------------------------------------------------------- #
def test_synthesize_report_includes_qa_findings(tmp_path, monkeypatch):
    """synthesize 确定性报告必须呈现数据质检发现（不吞错）。"""
    from config import settings
    from core.orchestrator.nodes import synthesize_node
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    state = AgentState(
        session_id="qa-synth",
        turn_id="t1",
        user_query="查一下 GMV",
        phase="synthesize",
        datasets={
            "s1": {
                "path": "nonexistent.parquet",
                "rows": 0,
                "columns": ["gmv"],
                "audit": {
                    "guard": [],
                    "qa": [
                        {
                            "check": "empty_result",
                            "severity": "warning",
                            "message": "查询返回 0 行：可能是合法空集，也可能是过滤条件冲突",
                        }
                    ],
                },
            }
        },
    )
    final = synthesize_node(state)
    assert "数据质检（s1）" in final.report
    assert "empty_result" in final.report
    assert "过滤条件冲突" in final.report


def test_analysis_material_carries_qa_findings(tmp_path, monkeypatch):
    """LLM 综合素材必须携带 QA 质检发现（报告层如实引用）。"""
    from config import settings
    from core.orchestrator.nodes import _analysis_material
    from core.orchestrator.state import AgentState

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    state = AgentState(
        session_id="qa-mat",
        turn_id="t1",
        user_query="查一下 GMV",
        datasets={
            "s1": {
                "path": "nonexistent.parquet",
                "rows": 3,
                "columns": ["gmv"],
                "audit": {
                    "guard": [],
                    "qa": [
                        {"check": "negative_metric", "severity": "warning", "message": "出现负值"}
                    ],
                },
            }
        },
    )
    material = _analysis_material(state)
    assert "[数据质检发现 s1]" in material
    assert "negative_metric" in material


# --------------------------------------------------------------------------- #
# web service 链路接入（后续项 2：run_guarded_query 同一质检）
# --------------------------------------------------------------------------- #
def test_run_guarded_query_carries_qa_findings():
    """web 链路：注入桩执行器返回负值行 -> qa_findings 含 negative_metric。"""
    import duckdb

    from exec.guards import ExecutionResult
    from tools.builtins._query_core import run_guarded_query

    def fake_executor(conn, sql, **kwargs):
        return ExecutionResult(columns=["gmv"], rows=[[-5.0]], scan_rows=0, duration_ms=0.0)

    c = duckdb.connect(":memory:")
    try:
        result = run_guarded_query(
            "查 GMV",
            principal="admin",
            conn=c,
            executor=fake_executor,
            dsl=DSL,
        )
    finally:
        c.close()
    checks = [f["check"] for f in result.qa_findings]
    assert "negative_metric" in checks
    assert result.rows == [[-5.0]]  # QA 不否决执行，原始结果照常返回


def test_run_guarded_query_clean_result_no_findings(conn):
    """web 链路：mock 数仓正常聚合 -> qa_findings 为空（零误报）。"""
    from tools.builtins._query_core import run_guarded_query

    result = run_guarded_query(
        "2024年5月成功支付订单的GMV总额是多少", principal="admin", conn=conn, dsl=DSL
    )
    assert result.qa_findings == []
