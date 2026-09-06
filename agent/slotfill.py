"""会话级澄清上下文与槽位回填（P0-5 / §4 项3）。

首次询问缺失时间窗口 / 未定义业务指标时，按 session_id 缓存"原始 query +
待填槽位"；用户随后用短语作答（如"最近30天"、"全部历史"）时，把答案合并回
原始 query 再走完整链路，无需整句重述。槽位按 TTL 失效，避免无限悬挂。

设计约束：
- 槽位回填只做"结构化拼接"（答案 + 原问题文本），绝不篡改语义；
- 答案不符合任何待填槽位时视为全新问题，上下文随即作废；
- 回填后的 query 仍走完整路由 + 安全守卫，无任何特权路径。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from agent.clarify import (
    _ALL_TIME_MARKERS,
    _TIME_RE,
    _contains_defined_metric,
    _has_all_time_marker,
    _has_time_expression,
)
from config import settings
from persistence.kvstore import SqliteKVStore

# 待填槽位 kind（与 agent.clarify.Clarification.kind 对齐）
SLOT_MISSING_TIME = "missing_time_window"
SLOT_UNDEFINED_METRIC = "undefined_metric"


@dataclass(frozen=True)
class ClarifyContext:
    """一次挂起的澄清：原始 query + 待填槽位清单 + 创建时间。"""

    original_query: str
    pending: tuple[str, ...]
    created_at: float = field(default_factory=time.time)

    def expired(self, ttl_seconds: int | None = None) -> bool:
        """槽位上下文是否超过 TTL（Whether the pending context has expired）。"""
        ttl = ttl_seconds if ttl_seconds is not None else settings.CLARIFY_SLOT_TTL
        return time.time() - self.created_at > ttl


class ClarifySlotStore:
    """线程安全的会话级澄清上下文缓存（随 TTL 惰性失效）。

    就绪度评审 R2 处置：条目登记属主（user_id），get/clear 带身份时校验
    归属——跨用户 / 未登记属主的条目一律视同不存在（fail-closed），与会话
    记忆 SessionStore 的 (session_id, user_id) 双键隔离对齐；user_id=None
    时不做归属校验（纯 session 键，向后兼容既有调用与测试）。

    配置 db_path 时通过 SqliteKVStore 落盘（整改指令3-2：多 worker/重启一致）。
    """

    def __init__(self, ttl_seconds: int | None = None, db_path: str | None = None) -> None:
        """初始化 TTL 与进程内缓存；db_path 配置时经 SqliteKVStore 落盘。"""
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.CLARIFY_SLOT_TTL
        # 值为 (owner, ctx)：属主与上下文一并缓存/落盘，供归属校验
        self._items: dict[str, tuple[str | None, ClarifyContext]] = {}
        self._lock = threading.Lock()
        self._kv = SqliteKVStore(db_path, table="clarify_slots") if db_path else None

    def get(self, session_id: str, user_id: str | None = None) -> ClarifyContext | None:
        """读取槽位上下文；过期或属主不符（fail-closed）一律视同不存在。"""
        with self._lock:
            entry = self._items.get(session_id)
            if entry is None and self._kv is not None:
                raw = self._kv.get(session_id, ttl_seconds=self._ttl)
                if raw is not None:
                    ctx = ClarifyContext(
                        original_query=raw["original_query"],
                        pending=tuple(raw["pending"]),
                        created_at=float(raw["created_at"]),
                    )
                    entry = (raw.get("owner"), ctx)
                    if not ctx.expired(self._ttl):
                        self._items[session_id] = entry
                    else:
                        self._kv.delete(session_id)
                        entry = None
            if entry is None:
                return None
            owner, ctx = entry
            if user_id is not None and owner != user_id:
                return None  # 跨用户 / 未登记属主：拒绝继承（fail-closed）
            if ctx.expired(self._ttl):
                self._items.pop(session_id, None)
                if self._kv is not None:
                    self._kv.delete(session_id)
                return None
            return ctx

    def set(self, session_id: str, ctx: ClarifyContext, user_id: str | None = None) -> None:
        """登记槽位上下文与属主（Store the context with owner binding）。"""
        with self._lock:
            self._items[session_id] = (user_id, ctx)
            if self._kv is not None:
                self._kv.set(
                    session_id,
                    {
                        "original_query": ctx.original_query,
                        "pending": list(ctx.pending),
                        "created_at": ctx.created_at,
                        "owner": user_id,
                    },
                )

    def clear(self, session_id: str, user_id: str | None = None) -> None:
        """删除槽位上下文；带身份且归属不一致时不误删（视为不存在）。"""
        with self._lock:
            if user_id is not None:
                entry = self._items.get(session_id)
                if entry is not None and entry[0] != user_id:
                    return
                if entry is None and self._kv is not None:
                    raw = self._kv.get(session_id)
                    if raw is not None and raw.get("owner") != user_id:
                        return
            self._items.pop(session_id, None)
            if self._kv is not None:
                self._kv.delete(session_id)

    def clear_all(self) -> int:
        """清空全部槽位上下文（管理 / 测试用），返回清理条数。"""
        with self._lock:
            n = len(self._items)
            self._items.clear()
            if self._kv is not None:
                self._kv.clear()
            return n


_default_store: ClarifySlotStore | None = None
_store_lock = threading.Lock()


def default_slot_store() -> ClarifySlotStore:
    """进程内复用的默认澄清槽位存储（配置 STATE_STORE_DB 时自动落盘）。"""
    global _default_store
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                _default_store = ClarifySlotStore(db_path=settings.STATE_STORE_DB)
    return _default_store


def attempt_fill(ctx: ClarifyContext, answer: str) -> str | None:
    """尝试用用户的短语答案填槽，成功返回合并后的完整 query，否则 None。

    - missing_time_window：答案含时间表达式（最近30天/上个月/2024年6月等）
      或全量历史标记（全部/所有/历史）时，拼回原问题；
    - undefined_metric：答案含已定义指标词（视为补充口径）时拼回原问题，
      交给生成层尝试解析；不含则无法可靠回填，按全新问题处理。
    """
    answer = answer.strip()
    if not answer:
        return None
    if SLOT_MISSING_TIME in ctx.pending and (
        _has_time_expression(answer) or _has_all_time_marker(answer)
    ):
        return f"{ctx.original_query} {answer}".strip()
    if SLOT_UNDEFINED_METRIC in ctx.pending and _contains_defined_metric(answer):
        return f"{ctx.original_query} {answer}".strip()
    return None


def pending_kinds(ctx: ClarifyContext | None) -> tuple[str, ...]:
    """返回当前挂起槽位（无上下文则空）。"""
    return () if ctx is None else ctx.pending


__all__ = [
    "SLOT_MISSING_TIME",
    "SLOT_UNDEFINED_METRIC",
    "ClarifyContext",
    "ClarifySlotStore",
    "attempt_fill",
    "default_slot_store",
    "pending_kinds",
]

# 保持 _ALL_TIME_MARKERS / _TIME_RE 导入被使用（供外部宽松时间判断复用）
assert _ALL_TIME_MARKERS and _TIME_RE
