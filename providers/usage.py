"""LLM token 用量登记收口（#22，2026-11）。

采集层（adapters）已在 UnifiedChatResponse.usage 返回用量，但便捷入口
``chat_text`` 只回传文本，用量被丢弃。本模块提供全仓唯一的登记入口：

- ``register_llm_usage``：一次 LLM 调用的登记动作 = 内存指标聚合
  （audit.metrics.record_llm_usage，按模型/调用点/流式维度）+ 请求级
  收集器追加（若有，供 audit 快照同源落盘）；
- ``llm_call_role``：调用点标签上下文（planner / coder / self_heal 等），
  调用方在发起 LLM 调用处 ``with llm_call_role("...")`` 一行级包裹，
  不改任何函数签名；
- ``set_usage_ledger`` / ``pop_usage_ledger``：请求入口绑定收集器，
  收尾取出写入 AuditRecord.llm_usage（会话与时间维度由 audit.jsonl 承载，
  内存聚合不按会话分键——键无界）。

ContextVar 放本模块而非 ``providers/context.py``：context.py 反向依赖
adapters（DispatchingAdapter），adapters 挂点要读这些上下文，放那边
构成循环 import。依赖方向：adapters -> usage -> audit.metrics。
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
from dataclasses import asdict, dataclass
from typing import Any

from audit.metrics import default_registry

# 调用点标签：未包裹的调用（测试桩 / 探测等）落 unknown，诚实可见
_DEFAULT_ROLE = "unknown"
_llm_call_role: contextvars.ContextVar[str] = contextvars.ContextVar(
    "llm_call_role", default=_DEFAULT_ROLE
)

# 请求级收集器：入口绑定、收尾取出；None 时只进内存指标
_usage_ledger: contextvars.ContextVar[UsageLedger | None] = contextvars.ContextVar(
    "usage_ledger", default=None
)

__all__ = [
    "UsageCall",
    "UsageLedger",
    "current_llm_call_role",
    "llm_call_role",
    "pop_usage_ledger",
    "register_llm_usage",
    "set_usage_ledger",
]


@dataclass(frozen=True)
class UsageCall:
    """单次 LLM 调用的用量明细（audit.jsonl per-call 记录与内存聚合同源）。"""

    role: str
    provider_id: str
    model_id: str
    streamed: bool
    missing: bool
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def to_dict(self) -> dict[str, Any]:
        """全字段序列化（asdict）。"""
        return asdict(self)


class UsageLedger:
    """请求级 token 收集器：请求内全部 LLM 调用明细与汇总。

    线程安全（编排子图 fan-out 可能并发调用 LLM）；汇总从明细实时派生，
    不存在第二份计数。零调用时 ``snapshot()`` 返回 None——AuditRecord
    不写空结构（没有 LLM 调用与用量缺失是两回事）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: list[UsageCall] = []

    def add(self, call: UsageCall) -> None:
        """追加一条调用明细。"""
        with self._lock:
            self._calls.append(call)

    def snapshot(self) -> dict[str, Any] | None:
        """导出汇总 + 明细；零调用返回 None。"""
        with self._lock:
            calls = list(self._calls)
        if not calls:
            return None
        return {
            "calls": len(calls),
            "missing_usage": sum(1 for c in calls if c.missing),
            "prompt_tokens": sum(c.prompt_tokens for c in calls),
            "completion_tokens": sum(c.completion_tokens for c in calls),
            "total_tokens": sum(c.total_tokens for c in calls),
            "calls_detail": [c.to_dict() for c in calls],
        }


@contextlib.contextmanager
def llm_call_role(role: str):
    """调用点标签包裹：``with llm_call_role("orchestrator.planner"):`` 包住
    chat_text 调用，退出即恢复（含异常路径），防标签跨调用泄漏。"""
    token = _llm_call_role.set(role)
    try:
        yield
    finally:
        _llm_call_role.reset(token)


def current_llm_call_role() -> str:
    """读取当前调用点标签；未包裹时返回 unknown。"""
    return _llm_call_role.get()


def set_usage_ledger(ledger: UsageLedger) -> None:
    """绑定当前请求上下文的用量收集器（请求入口调用）。"""
    _usage_ledger.set(ledger)


def pop_usage_ledger() -> UsageLedger | None:
    """取出并清除当前请求上下文的收集器（请求收尾调用，防上下文泄漏）。"""
    ledger = _usage_ledger.get()
    _usage_ledger.set(None)
    return ledger


def register_llm_usage(
    *,
    provider_id: str,
    model_id: str,
    streamed: bool,
    usage: Any | None,
) -> None:
    """全仓唯一登记入口（由 BaseAdapter.chat_text 挂点调用）。

    usage=None（上游未返回用量，典型：中转站不支持 stream_options 的流式
    路径）登记为 missing——次数计入、token 数不计入，严禁估算补齐。
    """
    role = current_llm_call_role()
    missing = usage is None
    if missing:
        prompt_tokens = completion_tokens = total_tokens = 0
    else:
        prompt_tokens = int(usage.prompt_tokens or 0)
        completion_tokens = int(usage.completion_tokens or 0)
        total_tokens = int(usage.total_tokens or 0)
    default_registry().record_llm_usage(
        role=role,
        provider_id=provider_id,
        model_id=model_id,
        streamed=streamed,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        missing=missing,
    )
    ledger = _usage_ledger.get()
    if ledger is not None:
        ledger.add(
            UsageCall(
                role=role,
                provider_id=provider_id,
                model_id=model_id,
                streamed=streamed,
                missing=missing,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        )
