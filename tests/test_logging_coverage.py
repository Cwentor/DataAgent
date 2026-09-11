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


def test_dataqa_findings_logged(tmp_path, monkeypatch, caplog):
    """DataQA 质检发现：warning 级留痕（负值场景，注入桩执行器）。"""
    import core.retrieval.tools as tools
    from exec.guards import ExecutionResult

    def fake_execute_sql(conn, sql, **kwargs):
        return ExecutionResult(columns=["gmv"], rows=[[-5.0]], scan_rows=0, duration_ms=0.0)

    monkeypatch.setattr(tools, "execute_sql", fake_execute_sql)
    dsl = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
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


def test_graph_iteration_guard_logs_error(caplog):
    """图迭代护栏触发：error 级留痕。"""
    from core.orchestrator.graph import StateGraph
    from core.orchestrator.state import AgentState

    graph = StateGraph(max_iterations=3)
    graph.add_node("loop", lambda s: s.apply())  # 相位不变（非 clarify）形成自环
    graph.add_edge("loop", "loop")
    graph.set_entry("loop")
    final = graph.run(AgentState())
    assert "迭代步数超限" in (final.report or "")
    assert any("迭代步数超限" in r.message for r in _records(caplog, "core.orchestrator.graph"))


def test_run_agent_exception_logs(monkeypatch, caplog):
    """编排顶层异常：logger.exception 留痕后原样抛出。"""
    import core.orchestrator.agent as agent_mod
    from core.orchestrator.graph import StateGraph

    def boom(state):
        raise RuntimeError("模拟编排崩溃")

    graph = StateGraph(max_iterations=3)
    graph.add_node("plan", boom)
    graph.set_entry("plan")
    monkeypatch.setattr(agent_mod, "build_graph", lambda **kwargs: graph)

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
