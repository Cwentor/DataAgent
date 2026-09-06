"""审计记录模型：一次 NL -> SQL 查询的完整可追溯快照。

审计把每次问答的上下文与产物落盘，供合规审计、问题回溯与质量分析使用。
字段与目标（P0）一一对应：session_id / user / prompt / 检索上下文 / DSL /
最终 SQL / 耗时 / 返回行数 / 扫描行数 / 自愈重写次数 / 错误。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AuditRecord:
    """单次查询的审计快照。"""

    request_id: str
    prompt: str
    session_id: str | None = None
    user: str | None = None
    principal: str | None = None
    retrieval_context: dict[str, Any] | None = None
    dsl: dict[str, Any] | None = None
    sql: str | None = None
    latency_ms: float | None = None
    row_count: int | None = None
    scan_rows: int | None = None
    rewrites: int | None = None
    error: str | None = None
    # Multi-Tool Agent 调度轨迹：每一步的工具名 / 入参 / 耗时 / 成功 / 异常
    steps: list[dict[str, Any]] | None = None
    # 意图路由与决策中心（Intent Router）：每轮提问的分类结果与路由性能，
    # 供生产环境离线统计分流准确率与漏斗分析
    detected_intent: str | None = None
    routing_latency_ms: float | None = None
    routing_reason: str | None = None
    created_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        """全字段序列化（asdict），供审计存储层消费（Full serialization）。"""
        return asdict(self)
