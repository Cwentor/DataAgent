"""日志可见性测试：关键异常/熔断/降级路径必须产生可采集的结构化日志。

覆盖（纯观测性补齐的回归锚点——行为不变，日志必须留痕）：
- 执行层：只读拦截 / 执行前审计拒绝 / 扫描熔断（exec.guards / exec.audit）；
- 检索层：DataQA 质检发现 / profiling 降级（core.retrieval）；
- 编排层：图迭代超限 / 编排顶层异常（core.orchestrator）。
"""

from __future__ import annotations

import logging

import duckdb
import pytest

from audit.logging import get_logger
from exec.audit import GuardrailRejected
from exec.guards import MaxRowsScannedExceeded, UnsafeSqlError, execute_sql


@pytest.fixture(autouse=True)
def _capture(caplog: pytest.LogCaptureFixture):
    """捕获全部相关 logger 的 WARNING 及以上记录。"""
    caplog.set_level(logging.WARNING)
    yield


@pytest.fixture
def big_conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE big AS SELECT range AS id FROM range(200000)")
    yield c
    c.close()


def _records(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == name]


def test_readonly_interception_logs_error(big_conn, caplog):
    """只读白名单拦截：error 级留痕（安全审计必需）。"""
    with pytest.raises(UnsafeSqlError):
        execute_sql(big_conn, "DROP TABLE big")
    assert any("只读白名单拦截" in r.message for r in _records(caplog, "exec.guards"))


def test_guardrail_rejection_logs_error(big_conn, caplog):
    """执行前审计拒绝（笛卡尔积）：经 execute_sql 包装链，exec.audit 与 exec.guards 双留痕。"""
    with pytest.raises(GuardrailRejected):
        execute_sql(big_conn, "SELECT a.id, b.id FROM big a, big b LIMIT 5")
    assert any("REJECTED" in r.message for r in _records(caplog, "exec.audit"))
    assert any("执行前审计拒绝" in r.message for r in _records(caplog, "exec.guards"))


def test_scan_cap_logs_error(big_conn, caplog):
    """扫描行数熔断：error 级留痕。"""
    with pytest.raises(MaxRowsScannedExceeded):
        execute_sql(big_conn, "SELECT count(id) FROM big", max_scan_rows=100)
    assert any("扫描行数上限熔断" in r.message for r in _records(caplog, "exec.guards"))


def test_scan_cap_logs_warning_when_plan_unmeasurable(big_conn, caplog, monkeypatch):
    """计划无可解析行数（如 DuckDB 版本变更）：warning 留痕，且不得静默当成 0 行。

    回归背景（CI 2026-10-22，duckdb 2.0.0）：EXPLAIN ANALYZE 只渲染 Summary 块时
    parse_scan_rows 退化为 0，熔断静默失效。此处桩掉解析结果，断言护栏
    在"测不到"时仍有可采集的 warning，而非无声放行。

    两段式（二十六期）后本用例**仍然有效**，且仍然有意义：`max_scan_rows=100`
    远小于 `est × SCAN_PRECHECK_SAFETY_FACTOR`（20 万 × 32），第一段不会放行，
    走的正是被桩掉的第二段。⚠️ 若将来把该阈值调大到能命中估算放行，本用例会
    失去覆盖——届时要么显式 `SCAN_PRECHECK_TWO_STAGE=0`，要么把桩点挪到第一段。
    """
    import exec.engine.duckdb_adapter as duckdb_adapter
    from exec.guards import reset_unmeasurable_warnings

    # 告警按后端去重（同一后端只 WARNING 一次），先清状态以免受用例顺序影响
    reset_unmeasurable_warnings()

    # 模拟 duckdb 2.0 的计划渲染：无 "N rows" 算子行数（不可计量）+ 解析结果退化为 0
    # （二十一期：解析器迁 exec.engine.plan_text，执行路径经 duckdb_adapter 消费）
    monkeypatch.setattr(duckdb_adapter, "parse_scan_rows", lambda _plan: 0)
    monkeypatch.setattr(duckdb_adapter, "plan_rows_measurable", lambda _plan: False)

    # 用与 test_scan_cap_logs_error 不同的 SQL，避免命中全局扫描行缓存（缓存命中即不走预检）
    result = execute_sql(big_conn, "SELECT count(*) FROM big", max_scan_rows=100)
    assert result.rows == [[200000]]
    assert any("扫描行数预检不可计量" in r.message for r in _records(caplog, "exec.guards"))


def test_scan_cap_unmeasurable_warns_once_per_backend(big_conn, caplog, monkeypatch):
    """同一后端只告警一次：可计量性对给定引擎恒定，逐查询重复 WARNING 会淹没日志。

    回归背景（2026-10-27 审计 P1）：ClickHouse / Hive 恒不可计量，原实现每条查询
    都刷一条 warning，长期掩盖真正异常。首次保留 WARNING，后续降为 DEBUG。

    两段式后「不可计量」告警**只允许由第二段触发**（第一段拿不到可信估算是常态
    分支，不许消耗去重额度）；本用例两条 SQL 的估算都不足以放行，故仍走第二段，
    断言不变。去重额度不被第一段污染的专门用例见
    `tests/exec/test_scan_precheck.py::test_first_stage_failure_does_not_consume_warning_quota`。
    """
    import exec.engine.duckdb_adapter as duckdb_adapter
    from exec.guards import reset_unmeasurable_warnings

    reset_unmeasurable_warnings()
    monkeypatch.setattr(duckdb_adapter, "parse_scan_rows", lambda _plan: 0)
    monkeypatch.setattr(duckdb_adapter, "plan_rows_measurable", lambda _plan: False)

    # 两条不同 SQL（避开扫描行缓存），触发两次「不可计量」分支
    execute_sql(big_conn, "SELECT count(*) FROM big", max_scan_rows=100)
    execute_sql(big_conn, "SELECT count(*) FROM big WHERE 1=1", max_scan_rows=100)

    warnings = [
        r
        for r in _records(caplog, "exec.guards")
        if "扫描行数预检不可计量" in r.message and r.levelname == "WARNING"
    ]
    assert len(warnings) == 1, f"同一后端应只 WARNING 一次，实际 {len(warnings)} 次"


def test_dataqa_findings_logged(tmp_path, monkeypatch, caplog):
    """DataQA 质检发现：warning 级留痕（负值场景，注入桩执行器）。"""
    import core.retrieval.tools as tools
    from exec.guards import ExecutionResult

    def fake_execute_sql(conn, sql, **kwargs):
        return ExecutionResult(columns=["gmv"], rows=[[-5.0]], scan_rows=0, duration_ms=0.0)

    monkeypatch.setattr(tools, "execute_sql", fake_execute_sql)
    dsl = {
        "metrics": [
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
        ],
    }
    tools.execute_dsl_query(dsl, principal="admin", workspace=tmp_path, name="log_qa")
    assert any(
        "DataQA 质检发现" in r.message and "negative_metric" in r.message
        for r in _records(caplog, "core.retrieval.tools")
    )


def test_profiling_degradation_logs_warning(monkeypatch, caplog):
    """profiling 连接不可用：warning 级降级留痕（不抛异常）。"""
    import core.retrieval.profiling as profiling

    def no_conn():
        raise RuntimeError("连接池不可用")

    monkeypatch.setattr(profiling, "_acquire_conn", no_conn)
    result = profiling.profile_enum_values(conn=None, use_cache=False)
    assert result == {}
    assert any("降级" in r.message for r in _records(caplog, "core.retrieval.profiling"))


def test_graph_iteration_guard_logs_error(monkeypatch, caplog):
    """图迭代护栏触发（LangGraph recursion_limit）：error 级留痕 + done 收敛。"""
    from config import settings
    from core.orchestrator.agent import run_agent

    monkeypatch.setattr(settings, "ORCHESTRATOR_RECURSION_LIMIT", 3)
    out = run_agent(
        "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
        session_id="log-guard",
    )
    assert "迭代步数超限" in (out.report or "")
    assert any("迭代步数超限" in r.message for r in _records(caplog, "core.orchestrator.agent"))


def test_run_agent_exception_logs(monkeypatch, caplog):
    """编排顶层异常：logger.exception 留痕后原样抛出。"""
    import core.orchestrator.agent as agent_mod

    def boom(*args, **kwargs):
        raise RuntimeError("模拟编排崩溃")

    monkeypatch.setattr(agent_mod, "_run_agent_langgraph_path", boom)

    with pytest.raises(RuntimeError, match="模拟编排崩溃"):
        agent_mod.run_agent("测试问题", session_id="logtest")
    assert any(
        "编排执行失败" in r.message and r.exc_info
        for r in _records(caplog, "core.orchestrator.agent")
    )


def test_json_formatter_includes_exception_and_error_field():
    """JsonFormatter：exception 与 extra.error 字段进入结构化输出（采集器契约）。"""
    import sys

    from audit.logging import JsonFormatter

    lg = get_logger("jsonfmt.test")
    record = lg.makeRecord(
        "jsonfmt.test", logging.ERROR, __file__, 1, "出错了: %s", ("细节",), None
    )
    record.error = "额外错误字段"
    formatted = JsonFormatter().format(record)
    assert '"exception"' not in formatted  # 无 exc_info 时不应有 exception 键
    assert "出错了: 细节" in formatted
    assert '"error": "额外错误字段"' in formatted

    try:
        raise ValueError("崩溃堆栈")
    except ValueError:
        record2 = lg.makeRecord(
            "jsonfmt.test", logging.ERROR, __file__, 1, "带堆栈", (), sys.exc_info()
        )
    formatted2 = JsonFormatter().format(record2)
    assert "ValueError" in formatted2 and '"exception"' in formatted2
