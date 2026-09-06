"""查询结果缓存（生产化：同 SQL 短期内重复查询免重复执行）。

键 = SHA256(principal + 规范化 SQL + 执行参数)：RLS 注入后的最终 SQL 已含权限
过滤，principal 再入键做双保险，杜绝跨主体结果泄漏；执行参数（超时 / 扫描
熔断 / 结果上限）变化会产生不同键，避免截断结果被误复用。

安全与正确性约束：
- 只读系统（编译器仅产 SELECT），无写失效问题；数据时效由 TTL 兜底；
- 默认关闭（QUERY_CACHE_ENABLED=0）：开启前应确认数据时效容忍 TTL 窗口；
- 线程安全（Lock + OrderedDict LRU）；
- 缓存仅存 JSON 可序列化产物（columns/rows/scan_rows），不含连接与游标。

与 EXPLAIN 预检缓存（exec.guards._SCAN_CACHE）职责不同：那是"扫描行数预估"
缓存（路由熔断降本），本模块是"结果"缓存（执行降本）。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any

from config import settings

__all__ = ["QueryCache", "default_query_cache", "query_cache_key"]


def query_cache_key(
    principal: str | None,
    sql: str,
    *,
    statement_timeout_ms: int,
    max_scan_rows: int,
    max_result_rows: int,
) -> str:
    """缓存键：主体 + SQL + 执行参数 的规范化 SHA256。"""
    normalized = " ".join(sql.strip().split())
    raw = "\x00".join(
        [
            principal or "",
            normalized,
            str(statement_timeout_ms),
            str(max_scan_rows),
            str(max_result_rows),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class QueryCache:
    """线程安全的 LRU + TTL 查询结果缓存。"""

    def __init__(self, max_entries: int = 256, ttl_seconds: float = 300.0) -> None:
        self._max_entries = max(1, int(max_entries))
        self._ttl = float(ttl_seconds)
        self._store: OrderedDict[str, tuple[float, tuple[list[str], list[list[Any]], int]]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[list[str], list[list[Any]], int] | None:
        """命中返回 (columns, rows, scan_rows)；过期 / 缺失返回 None。"""
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            stored_at, payload = item
            if time.monotonic() - stored_at > self._ttl:
                del self._store[key]
                return None
            self._store.move_to_end(key)  # LRU 触碰
            return payload

    def put(self, key: str, columns: list[str], rows: list[list[Any]], scan_rows: int) -> None:
        """写入结果；超容量淘汰最久未命中条目。"""
        with self._lock:
            self._store[key] = (
                time.monotonic(),
                (list(columns), [list(r) for r in rows], scan_rows),
            )
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    def clear(self) -> int:
        """清空缓存，返回清理条数（运维 / 测试用）。"""
        with self._lock:
            n = len(self._store)
            self._store.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


_default_cache: QueryCache | None = None


def default_query_cache() -> QueryCache:
    """进程内默认查询缓存（懒加载，容量/TTL 读 settings）。"""
    global _default_cache
    if _default_cache is None:
        _default_cache = QueryCache(
            max_entries=settings.QUERY_CACHE_MAX_ENTRIES,
            ttl_seconds=settings.QUERY_CACHE_TTL_SECONDS,
        )
    return _default_cache


def query_cache_enabled() -> bool:
    """缓存开关（settings.QUERY_CACHE_ENABLED，默认关闭）。"""
    return bool(settings.QUERY_CACHE_ENABLED)
