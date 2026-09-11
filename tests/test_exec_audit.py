"""GuardrailAgent 执行前审计单元测试（pi-agent-harness 对齐：行动项 1）。

覆盖：
- audit_compiled_sql 三类检查：只读结构 / 笛卡尔积 / 无界输出；
- assert_guardrails 裁决语义：REJECTED 抛 GuardrailRejected，WARNING 返回；
- execute_sql 集成：REJECTED 熔断不执行、WARNING 随 ExecutionResult.findings 输出；
- 正常编译产物（带 LIMIT / 纯标量聚合）不误杀。
"""

from __future__ import annotations

import duckdb
import pytest

from exec.audit import (
    GuardrailRejected,
    assert_guardrails,
    audit_compiled_sql,
)
from exec.guards import UnsafeSqlError, execute_sql


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE orders (id INT, amount DOUBLE, province VARCHAR)")
    c.execute("CREATE TABLE shops (id INT, name VARCHAR)")
    c.execute("INSERT INTO orders VALUES (1, 10.0, '广东'), (2, 20.0, '浙江')")
    c.execute("INSERT INTO shops VALUES (1, 'A'), (2, 'B')")
    yield c
    c.close()


def test_audit_clean_query_no_findings():
    """带 LIMIT 的常规查询：APPROVED（无发现）。"""
    findings = audit_compiled_sql("SELECT province, SUM(amount) AS gmv FROM orders GROUP BY 1 LIMIT 10")
    assert findings == []


def test_audit_scalar_aggregate_warning_only():
    """纯标量聚合（无 LIMIT）：仅 warning（编译器按约定抹除冗余 LIMIT）。"""
    findings = audit_compiled_sql("SELECT SUM(amount) FROM orders")
    assert [f.severity for f in findings] == ["warning"]
    assert findings[0].check == "unbounded_output"


def test_audit_unbounded_output_warning():
    """无 LIMIT 且非标量聚合：warning（真实边界由运行时返回行数硬上限承担）。"""
    findings = audit_compiled_sql("SELECT province FROM orders")
    assert any(f.check == "unbounded_output" and f.severity == "warning" for f in findings)


def test_audit_cartesian_comma_join_rejected():
    """逗号分隔多表 FROM（笛卡尔积）：REJECTED。"""
    findings = audit_compiled_sql("SELECT o.id, s.name FROM orders o, shops s LIMIT 5")
    assert any(f.check == "cartesian_product" and f.severity == "rejected" for f in findings)


def test_audit_cartesian_cross_join_rejected():
    """CROSS JOIN：REJECTED。"""
    findings = audit_compiled_sql(
        "SELECT o.id, s.name FROM orders o CROSS JOIN shops s LIMIT 5"
    )
    assert any(f.check == "cartesian_product" and f.severity == "rejected" for f in findings)


def test_audit_cartesian_join_without_on_rejected():
    """JOIN 缺少 ON/USING 关联条件：REJECTED。"""
    findings = audit_compiled_sql("SELECT o.id, s.name FROM orders o JOIN shops s LIMIT 5")
    assert any(f.check == "cartesian_product" and f.severity == "rejected" for f in findings)


def test_audit_join_with_on_clean():
    """带 ON 条件的合法 JOIN：APPROVED。"""
    findings = audit_compiled_sql(
        "SELECT o.id, s.name FROM orders o JOIN shops s ON o.id = s.id LIMIT 5"
    )
    assert findings == []


def test_audit_non_select_rejected_as_readonly():
    """非 SELECT 语句：readonly_structure 拒绝（审计层冗余防御）。"""
    findings = audit_compiled_sql("DELETE FROM orders")
    assert any(f.check == "readonly_structure" and f.severity == "rejected" for f in findings)


def test_audit_multi_statement_rejected():
    """多语句：readonly_structure 拒绝。"""
    findings = audit_compiled_sql("SELECT 1; SELECT 2")
    assert any(f.check == "readonly_structure" and f.severity == "rejected" for f in findings)


def test_assert_guardrails_rejects_and_raises():
    """assert_guardrails：REJECTED 级发现（笛卡尔积）抛 GuardrailRejected。"""
    with pytest.raises(GuardrailRejected) as excinfo:
        assert_guardrails("SELECT o.id, s.name FROM orders o, shops s LIMIT 5")
    assert "cartesian_product" in str(excinfo.value)


def test_assert_guardrails_returns_warnings():
    """assert_guardrails：纯标量聚合返回 warning 列表（不拒绝）。"""
    warnings = assert_guardrails("SELECT SUM(amount) FROM orders")
    assert len(warnings) == 1
    assert warnings[0].severity == "warning"


def test_assert_guardrails_readonly_defense():
    """只读防线冗余：非 SELECT 在审计门同样被拒（包装为 GuardrailRejected）。"""
    with pytest.raises(GuardrailRejected):
        assert_guardrails("UPDATE orders SET amount = 0")
    with pytest.raises(GuardrailRejected):
        assert_guardrails("DROP TABLE orders")
    # 兜底确认：UnsafeSqlError 主防线语义不变（assert_read_only_sql 直连验证）
    with pytest.raises(UnsafeSqlError):
        from exec.guards import assert_read_only_sql

        assert_read_only_sql("DROP TABLE orders")


def test_execute_sql_integration_rejects_cartesian(conn):
    """execute_sql 集成：笛卡尔积在执行前熔断（GuardrailRejected）。"""
    with pytest.raises(GuardrailRejected):
        execute_sql(conn, "SELECT o.id, s.name FROM orders o, shops s LIMIT 5")


def test_execute_sql_integration_unbounded_warning(conn):
    """execute_sql 集成：无 LIMIT 多行形态不拒绝执行，warning 随结果输出。"""
    result = execute_sql(conn, "SELECT province FROM orders")
    assert len(result.rows) == 2
    assert any(f["check"] == "unbounded_output" for f in result.findings)


def test_execute_sql_integration_warning_passthrough(conn):
    """execute_sql 集成：WARNING 级发现随 ExecutionResult.findings 输出。"""
    result = execute_sql(conn, "SELECT SUM(amount) FROM orders")
    assert result.rows == [[30.0]]
    assert len(result.findings) == 1
    assert result.findings[0]["check"] == "unbounded_output"
    assert result.findings[0]["severity"] == "warning"


def test_execute_sql_integration_clean_query(conn):
    """execute_sql 集成：常规查询审计零发现，不改变结果行为。"""
    result = execute_sql(conn, "SELECT province FROM orders WHERE id = 1 LIMIT 1")
    assert result.rows == [["广东"]]
    assert result.findings == []
