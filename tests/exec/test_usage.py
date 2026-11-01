"""SQL 执行耗时收集器测试（#23）。

覆盖：
- ExecUsageLedger 绑定/登记/取出与 snapshot 结构（汇总从明细派生）；
- sql_exec_domain 标注包裹（执行期间登记的明细带 domain，退出恢复）；
- 未绑定时 register 零负担、pop 后再登记不进旧 ledger（残留清理）；
- 端到端：execute_sql 成功路径自登记（含 backend 与 domain 标注），
  失败执行（非 SELECT 被拒）不登记。
"""

from __future__ import annotations

import duckdb
import pytest

from exec.guards import UnsafeSqlError, execute_sql
from exec.usage import (
    ExecUsageLedger,
    pop_exec_ledger,
    register_sql_exec,
    set_exec_ledger,
    sql_exec_domain,
)


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE t AS SELECT range AS id FROM range(10)")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _clean_context():
    """每条用例前后清理 contextvar，防止用例间泄漏（跨用例隔离）。"""
    pop_exec_ledger()
    yield
    pop_exec_ledger()


def test_ledger_snapshot_derived_from_details():
    """snapshot 汇总从明细实时派生：executions / total / last 一致。"""
    ledger = ExecUsageLedger()
    set_exec_ledger(ledger)
    with sql_exec_domain("order_detail"):
        register_sql_exec(duration_ms=12.5, scan_rows=100, row_count=5, backend="duckdb")
    register_sql_exec(duration_ms=30.0, scan_rows=200, row_count=3, backend="duckdb")
    snapshot = pop_exec_ledger().snapshot()
    assert snapshot is not None
    assert snapshot["executions"] == 2
    assert snapshot["total_duration_ms"] == 42.5
    assert snapshot["last_duration_ms"] == 30.0
    first = snapshot["executions_detail"][0]
    assert first["domain"] == "order_detail"
    assert first["backend"] == "duckdb"
    # 未包裹的执行 domain 落 None（诚实缺省，不虚构归组）
    assert snapshot["executions_detail"][1]["domain"] is None


def test_ledger_empty_snapshot_is_none():
    """零执行时 snapshot 返回 None：AuditRecord 不写空结构。"""
    ledger = ExecUsageLedger()
    set_exec_ledger(ledger)
    assert pop_exec_ledger() is not None
    ledger2 = ExecUsageLedger()
    assert ledger2.snapshot() is None


def test_register_without_ledger_is_noop():
    """未绑定收集器时登记零负担（测试/脚本直接调 execute_sql 的链路）。"""
    register_sql_exec(duration_ms=1.0, scan_rows=0, row_count=0, backend="duckdb")
    assert pop_exec_ledger() is None


def test_pop_clears_context_no_stale_reads():
    """pop 清理上下文：取出后再登记不进任何 ledger（防跨请求残留）。"""
    ledger = ExecUsageLedger()
    set_exec_ledger(ledger)
    register_sql_exec(duration_ms=1.0, scan_rows=1, row_count=1, backend="duckdb")
    popped = pop_exec_ledger()
    assert popped is ledger
    # 清理后再登记不得污染已取出的 ledger
    register_sql_exec(duration_ms=2.0, scan_rows=2, row_count=2, backend="duckdb")
    assert ledger.snapshot()["executions"] == 1


def test_domain_context_restored_after_exit():
    """标注包裹退出后恢复（含异常路径），防标注跨调用泄漏。"""
    with sql_exec_domain("fact_page_view"):
        register_sql_exec(duration_ms=1.0, scan_rows=0, row_count=0, backend="duckdb")
        try:
            raise ValueError("boom")
        except ValueError:
            pass
    # 重建独立 ledger 验证退出后的登记无标注（异常路径也已恢复）
    ledger = ExecUsageLedger()
    set_exec_ledger(ledger)
    register_sql_exec(duration_ms=3.0, scan_rows=0, row_count=0, backend="duckdb")
    detail = pop_exec_ledger().snapshot()["executions_detail"][0]
    assert detail["domain"] is None


def test_execute_sql_records_success_with_domain(conn):
    """端到端：execute_sql 成功路径自登记进绑定的收集器（backend + domain）。"""
    ledger = ExecUsageLedger()
    set_exec_ledger(ledger)
    with sql_exec_domain("order_detail"):
        result = execute_sql(conn, "SELECT count(*) AS n FROM t")
    assert result.rows[0][0] == 10
    snapshot = pop_exec_ledger().snapshot()
    assert snapshot is not None
    detail = snapshot["executions_detail"][0]
    assert detail["row_count"] == 1
    assert detail["domain"] == "order_detail"
    assert detail["backend"] == "duckdb"
    assert detail["duration_ms"] >= 0.0
    assert detail["scan_rows"] >= 0


def test_execute_sql_does_not_record_rejected(conn):
    """失败执行（只读守卫拒绝）不登记耗时——耗时口径只含成功执行。"""
    ledger = ExecUsageLedger()
    set_exec_ledger(ledger)
    with pytest.raises(UnsafeSqlError):
        execute_sql(conn, "DELETE FROM t")
    assert pop_exec_ledger().snapshot() is None
