"""SQL 执行耗时登记收口（#23，2026-11）。

``exec.guards.execute_sql`` 是全链路唯一 SQL 执行收口点，本模块提供请求级
收集器把每次成功执行的耗时明细带出执行层：

- ``register_sql_exec``：一次成功执行的登记动作 = 请求级收集器追加（若有）。
  内存指标聚合（audit.metrics.record_sql_exec）不在此处发生——出口处
  （web.service / web.api）从收集器 snapshot 统一打点，保证 /api/metrics
  与 audit 记录同源（同一份明细双落）；
- ``set_exec_ledger`` / ``pop_exec_ledger``：请求入口绑定收集器，收尾取出
  写入 AuditRecord.sql_exec（未绑定时不收集，直接调 execute_sql 的测试/
  脚本链路零负担）；
- ``sql_exec_domain``：query_domain 标注包裹（#22 llm_call_role 同款模式）。
  domain（查询域锚点表名）只有调用点知道，exec 层不感知业务语义（铁律），
  由执行点 ``with sql_exec_domain(resolve_anchor_or_none(dsl)):`` 一行包裹，
  执行期间登记的明细自动带上标注；未包裹的执行（探索层 SQL 等）落 None。

失败执行（超时 / 扫描熔断 / 返回熔断）不登记耗时——到熔断为止的时长有偏，
熔断事件已由 metrics 的 circuit_breakers 计数覆盖，耗时口径只含成功执行。

依赖方向：guards -> usage（同包）；usage 仅标准库，不反向依赖 audit。
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "ExecCall",
    "ExecUsageLedger",
    "pop_exec_ledger",
    "register_sql_exec",
    "set_exec_ledger",
    "sql_exec_domain",
]

# 请求级收集器：入口绑定、收尾取出；None 时只透传不收集
_exec_ledger: contextvars.ContextVar[ExecUsageLedger | None] = contextvars.ContextVar(
    "exec_ledger", default=None
)

# query_domain 标注（#23）：由调用点包裹，未包裹的执行落 None（诚实缺省）
_exec_domain: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "exec_domain", default=None
)


@dataclass(frozen=True)
class ExecCall:
    """单次 SQL 成功执行的耗时明细（audit 快照 per-execution 记录与内存聚合同源）。

    duration_ms 口径 = exec.guards.execute_sql 的 ExecutionResult.duration_ms
    （扫描预检段 + 真实执行段合计，预检是治理开销，分析时按口径注明）。
    domain 为执行点标注的查询域锚点表名，None 表示无 DSL 语境（探索层等）。
    """

    duration_ms: float
    scan_rows: int
    row_count: int
    backend: str
    domain: str | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """全字段序列化（asdict）。"""
        return asdict(self)


class ExecUsageLedger:
    """请求级执行耗时收集器：请求内全部成功执行的明细与汇总。

    线程安全（编排链路可能并发执行 SQL）；汇总从明细实时派生，不存在
    第二份计数。零执行时 ``snapshot()`` 返回 None——AuditRecord 不写空
    结构（没有 SQL 执行与执行失败是两回事）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: list[ExecCall] = []

    def add(self, call: ExecCall) -> None:
        """追加一条执行明细。"""
        with self._lock:
            self._calls.append(call)

    def snapshot(self) -> dict[str, Any] | None:
        """导出汇总 + 明细；零执行返回 None。"""
        with self._lock:
            calls = list(self._calls)
        if not calls:
            return None
        return {
            "executions": len(calls),
            "total_duration_ms": round(sum(c.duration_ms for c in calls), 3),
            "last_duration_ms": calls[-1].duration_ms,
            "executions_detail": [c.to_dict() for c in calls],
        }


def set_exec_ledger(ledger: ExecUsageLedger) -> None:
    """绑定当前请求上下文的执行耗时收集器（请求入口调用）。"""
    _exec_ledger.set(ledger)


def pop_exec_ledger() -> ExecUsageLedger | None:
    """取出并清除当前请求上下文的收集器（请求收尾调用，防上下文泄漏）。"""
    ledger = _exec_ledger.get()
    _exec_ledger.set(None)
    return ledger


@contextlib.contextmanager
def sql_exec_domain(domain: str | None):
    """query_domain 标注包裹：``with sql_exec_domain(anchor):`` 包住取数调用，
    退出即恢复（含异常路径），防标注跨调用泄漏。"""
    token = _exec_domain.set(domain)
    try:
        yield
    finally:
        _exec_domain.reset(token)


def register_sql_exec(*, duration_ms: float, scan_rows: int, row_count: int, backend: str) -> None:
    """全仓唯一登记入口（由 execute_sql 成功路径调用）。

    domain 取当前标注（sql_exec_domain 包裹者）；仅进请求级收集器，内存
    指标聚合在出口处从 snapshot 统一进行（见模块 docstring 的同源说明）。
    """
    ledger = _exec_ledger.get()
    if ledger is not None:
        ledger.add(
            ExecCall(
                duration_ms=duration_ms,
                scan_rows=scan_rows,
                row_count=row_count,
                backend=backend,
                domain=_exec_domain.get(),
            )
        )
