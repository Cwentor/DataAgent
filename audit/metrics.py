"""进程内可观测性指标（P0 / §4 项5）：QPS、P50/P95 耗时、意图/动作分布、
自愈成功率、熔断次数、澄清触发率、降级次数、LLM token 用量（#22）。
线程安全，零外部依赖。

统一由 web.service.run_query 打点（一次问答一条记录），GET /api/metrics 暴露。
窗口语义：计数器为进程启动以来的累计值；latency 保留最近 N 条用于分位数。
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any


@dataclass
class _TokenCell:
    """Token 用量维度聚合单元（#22）。

    missing_usage 单独计数：usage 缺失的调用只有次数、没有 token 数——
    严禁按估算值虚构补齐（项目「不虚构」红线），丢失率由 snapshot 透出。
    """

    calls: int = 0
    missing_usage: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def bump(self, *, missing: bool, prompt: int, completion: int, total: int) -> None:
        """累计一次调用：missing 时只计次数，token 数保持不变。"""
        self.calls += 1
        if missing:
            self.missing_usage += 1
        else:
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.total_tokens += total

    def to_dict(self) -> dict[str, Any]:
        """导出聚合单元（snapshot 字段顺序即展示顺序）。"""
        return {
            "calls": self.calls,
            "missing_usage": self.missing_usage,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


class MetricsRegistry:
    """线程安全的进程内指标注册表。"""

    def __init__(self, latency_window: int = 1000) -> None:
        """初始化计数器与延迟窗口（latency_window：分位数采样容量）。"""
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._total = 0
        self._errors = 0
        self._degraded = 0
        self._clarify_triggers = 0
        self._self_heal_success = 0
        self._self_heal_failures = 0
        self._intent_counts: Counter[str] = Counter()
        self._action_counts: Counter[str] = Counter()
        self._error_kinds: Counter[str] = Counter()
        # 熔断事件：查询超时 / 扫描行数熔断 / 返回行数熔断 / 不安全 SQL
        self._circuit_breakers: Counter[str] = Counter()
        # 分层降级兜底（2026-09）：弱解析命中 / 计划获批准执行 / 拒绝或回落拒答
        self._degrade_outcomes: Counter[str] = Counter()
        # LLM 流式握手重试事件（2026-10）：handshake_timeout=握手期超时发生 /
        # retry_success=重试救回 / retry_fail=重试后仍失败
        self._llm_handshake: Counter[str] = Counter()
        # LLM token 用量（#22，2026-11）：全局 + 按模型（流式维度并入键）+ 按调用点。
        # 会话与时间维度不进内存聚合（键无界），由 audit.jsonl 的 per-call 明细承载
        self._llm_tokens = _TokenCell()
        self._llm_tokens_by_model: dict[str, _TokenCell] = {}
        self._llm_tokens_by_role: dict[str, _TokenCell] = {}
        # Multi-Tool Agent：工具调用次数与失败次数
        self._tools_called = 0
        self._tool_errors = 0
        # 审计落库失败次数（2026-10-27 审计 P2）：审计写失败被刻意设计为
        # 「不影响主链路」，代价是静默丢审计——必须有可观测计数兜住。
        self._audit_write_failures = 0
        self._latencies: deque[float] = deque(maxlen=latency_window)

    # ------------------------------------------------------------------ #
    # 打点
    # ------------------------------------------------------------------ #
    def record_query(
        self,
        *,
        intent: str,
        action: str,
        latency_ms: float,
        error: str | None,
        degraded: bool,
        rewrites: int,
        clarify_filled: bool,
        circuit_breaker: str | None = None,
    ) -> None:
        """打点一次问答：意图/动作分布、延迟、错误、降级、自愈与熔断事件。"""
        with self._lock:
            self._total += 1
            self._intent_counts[intent] += 1
            self._action_counts[action] += 1
            self._latencies.append(latency_ms)
            if degraded:
                self._degraded += 1
            if clarify_filled:
                self._clarify_triggers += 1
            if circuit_breaker:
                self._circuit_breakers[circuit_breaker] += 1
            if rewrites > 0:
                self._self_heal_success += 1
            if error:
                self._errors += 1
                self._error_kinds[error] += 1

    def record_circuit_breaker(self, kind: str) -> None:
        """登记一次资源治理熔断事件（超时 / 扫描行数 / 返回行数 / 不安全 SQL）。"""
        with self._lock:
            self._circuit_breakers[kind] += 1

    def record_degrade(self, outcome: str) -> None:
        """登记一次分层降级事件（parse_hit=弱解析命中 / confirmed=计划获批准 /
        rejected=用户拒绝或回落拒答）。"""
        with self._lock:
            self._degrade_outcomes[outcome] += 1

    def record_llm_handshake(self, kind: str) -> None:
        """登记一次 LLM 流式握手重试事件（handshake_timeout / retry_success / retry_fail）。"""
        with self._lock:
            self._llm_handshake[kind] += 1

    def record_llm_usage(
        self,
        *,
        role: str,
        provider_id: str,
        model_id: str,
        streamed: bool,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        missing: bool,
    ) -> None:
        """登记一次 LLM 调用的 token 用量（#22）：全局 + 按模型 + 按调用点。

        missing=True 表示上游未返回 usage（典型：中转站不支持 stream_options
        的流式路径）——calls 计数但 token 数不计入，丢失率由 snapshot 的
        missing_rate 透出。参数由调用方从 providers Usage 对象解包（audit
        不 import providers，保持叶子包依赖方向）。
        """
        with self._lock:

            def _bump(cell: _TokenCell) -> None:
                cell.bump(
                    missing=missing,
                    prompt=prompt_tokens,
                    completion=completion_tokens,
                    total=total_tokens,
                )

            _bump(self._llm_tokens)
            # 流式维度并入模型键（provider/model/stream|batch），不单设维度
            model_key = f"{provider_id}/{model_id}/{'stream' if streamed else 'batch'}"
            _bump(self._llm_tokens_by_model.setdefault(model_key, _TokenCell()))
            _bump(self._llm_tokens_by_role.setdefault(role, _TokenCell()))

    def record_self_heal_failure(self) -> None:
        """登记一次自愈失败（Record a self-heal failure）。"""
        with self._lock:
            self._self_heal_failures += 1

    def record_tool_call(self, *, success: bool) -> None:
        """Multi-Tool Agent 打点：每次工具调用记录成功/失败。"""
        with self._lock:
            self._tools_called += 1
            if not success:
                self._tool_errors += 1

    def record_audit_write_failure(self) -> int:
        """登记一次审计落库失败，返回累计次数（供调用方做阈值告警）。

        审计写入失败不阻断主链路是刻意设计，但"静默"会让缺陷长期潜伏——
        2026-10-27 审计发现的畸形列 bug 正是这样存活了数周（只有 JSONL 有数据）。
        """
        with self._lock:
            self._audit_write_failures += 1
            return self._audit_write_failures

    # ------------------------------------------------------------------ #
    # 聚合
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, Any]:
        """导出指标快照（P50/P95 分位数、累计计数与分布），供 /api/metrics。"""
        with self._lock:
            elapsed = max(time.time() - self._started_at, 1e-9)
            latencies = list(self._latencies)
            total = self._total
            self_heal_total = self._self_heal_success + self._self_heal_failures
            return {
                "uptime_seconds": round(time.time() - self._started_at, 3),
                "total_queries": total,
                "qps": round(total / elapsed, 3),
                "error_rate": round(self._errors / total, 4) if total else 0.0,
                "errors": self._errors,
                "degraded": self._degraded,
                "clarify_triggers": self._clarify_triggers,
                "intent_distribution": dict(self._intent_counts),
                "action_distribution": dict(self._action_counts),
                "error_kinds": dict(self._error_kinds),
                "circuit_breakers": dict(self._circuit_breakers),
                "degrade_outcomes": dict(self._degrade_outcomes),
                "llm_handshake": dict(self._llm_handshake),
                # Token 用量（#22）：零打点时也输出结构化零值（便于前端/告警消费）
                "llm_tokens": {
                    **self._llm_tokens.to_dict(),
                    "missing_rate": (
                        round(self._llm_tokens.missing_usage / self._llm_tokens.calls, 4)
                        if self._llm_tokens.calls
                        else None
                    ),
                    "by_model": {
                        key: cell.to_dict() for key, cell in self._llm_tokens_by_model.items()
                    },
                    "by_role": {
                        key: cell.to_dict() for key, cell in self._llm_tokens_by_role.items()
                    },
                },
                "tools": {
                    "called": self._tools_called,
                    "errors": self._tool_errors,
                    "error_rate": (
                        round(self._tool_errors / self._tools_called, 4)
                        if self._tools_called
                        else None
                    ),
                },
                "self_heal": {
                    "success": self._self_heal_success,
                    "failures": self._self_heal_failures,
                    "success_rate": (
                        round(self._self_heal_success / self_heal_total, 4)
                        if self_heal_total
                        else None
                    ),
                },
                # 审计落库失败累计：非零即代表审计链路有真实缺陷（JSONL 有、
                # 库无），运维应据此告警而非等到查历史时才发现缺数据
                "audit_write_failures": self._audit_write_failures,
                "latency_ms": {
                    "p50": round(statistics.median(latencies), 3) if latencies else None,
                    "p95": _percentile(latencies, 0.95),
                    "p99": _percentile(latencies, 0.99),
                    "max": round(max(latencies), 3) if latencies else None,
                    "samples": len(latencies),
                },
            }


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(p * len(ordered)))
    return round(ordered[idx], 3)


_default_registry = MetricsRegistry()
_registry_lock = threading.Lock()


def default_registry() -> MetricsRegistry:
    """进程内复用的默认指标注册表。"""
    return _default_registry


__all__ = ["MetricsRegistry", "default_registry"]
