"""进程内可观测性指标（P0 / §4 项5）：QPS、P50/P95 耗时、意图/动作分布、
自愈成功率、熔断次数、澄清触发率、降级次数。线程安全，零外部依赖。

统一由 web.service.run_query 打点（一次问答一条记录），GET /api/metrics 暴露。
窗口语义：计数器为进程启动以来的累计值；latency 保留最近 N 条用于分位数。
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import Counter, deque
from typing import Any


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
        # Multi-Tool Agent：工具调用次数与失败次数
        self._tools_called = 0
        self._tool_errors = 0
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
