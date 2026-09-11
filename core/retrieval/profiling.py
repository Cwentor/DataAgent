"""SchemaAgent 动态 profiling：语义字段枚举值探查（规划期元数据增强）。

对齐 pi-agent-harness 的 SchemaAgent 角色（schema inspection with dynamic
profiling）：静态 schema_digest 只给 Planner 字段名/类型清单，LLM 生成过滤
条件时仍可能臆造取值（如 "华东" 大区词、错误的 pay_status 字面值）。本模块
对**低基数枚举字段**做确定性探查，把数仓实际取值注入规划上下文：

1. 遍历语义目录 dtype=="str" 的字段，逐字段 ``SELECT DISTINCT`` 探查；
2. 基数 <= max_distinct 才收录（高基数字段如 product_name 不注入，
   防提示词膨胀）；
3. 进程级缓存（mock 数仓静态，同 exec.guards._SCAN_CACHE 先例）；
4. 失败降级：库不可用/表缺失时静默跳过该字段，返回空/部分结果——
   profiling 是增强能力，严禁阻断规划主链路。

安全说明：枚举值仅作为规划上下文注入提示词（字段名本就在 schema_digest
中可见，此步不扩大信息面）；任何取值进入 SQL 前仍经编译器字面量安全转义
与 RLS 策略，防线不因 profiling 减弱。
"""

from __future__ import annotations

import threading
from typing import Any

# 低基数字段收录阈值：基数超过该值的字符串字段不注入枚举值
DEFAULT_MAX_DISTINCT = 30

# 进程级缓存（数仓静态；容量上限防御，超限清空防膨胀）
_ENUM_CACHE: dict[str, list[str]] | None = None
_ENUM_CACHE_LOCK = threading.Lock()


def _acquire_conn() -> Any:
    """从进程内默认只读连接池取连接（调用方用完归还由本模块负责）。"""
    from exec.pool import default_pool

    return default_pool().acquire()


def profile_enum_values(
    conn: Any | None = None,
    *,
    max_distinct: int = DEFAULT_MAX_DISTINCT,
    use_cache: bool = True,
) -> dict[str, list[str]]:
    """探查语义目录中低基数字符串字段的实际取值。

    返回 ``{逻辑字段名: [取值...]}``（按值排序，确定性可复现）；
    探查失败的字段静默跳过（降级不阻断）。
    """
    global _ENUM_CACHE
    if use_cache:
        with _ENUM_CACHE_LOCK:
            if _ENUM_CACHE is not None:
                return dict(_ENUM_CACHE)

    from semantic.catalog import COLUMNS

    enum_fields = {
        name: meta for name, meta in COLUMNS.items() if (meta.dtype or "").lower() == "str"
    }
    result: dict[str, list[str]] = {}
    own_conn = conn is None
    if own_conn:
        try:
            conn = _acquire_conn()
        except Exception:
            return {}
    try:
        for name, meta in enum_fields.items():
            try:
                cursor = conn.execute(
                    f'SELECT DISTINCT "{meta.column}" FROM "{meta.table}" '
                    "ORDER BY 1 LIMIT ?",
                    [max_distinct + 1],
                )
                values = [str(row[0]) for row in cursor.fetchall() if row[0] is not None]
            except Exception:
                continue  # 单字段失败跳过（表缺失/权限等），不中断整批
            if len(values) > max_distinct:
                continue  # 高基数字段不注入（防提示词膨胀）
            result[name] = values
    finally:
        if own_conn and conn is not None:
            from exec.pool import default_pool

            default_pool().release(conn)

    if use_cache:
        with _ENUM_CACHE_LOCK:
            if _ENUM_CACHE is None or len(_ENUM_CACHE) < len(result):
                _ENUM_CACHE = result
    return result


def clear_profile_cache() -> None:
    """清空枚举探查缓存（数仓重建后调用；测试隔离用）。"""
    global _ENUM_CACHE
    with _ENUM_CACHE_LOCK:
        _ENUM_CACHE = None


__all__ = ["DEFAULT_MAX_DISTINCT", "clear_profile_cache", "profile_enum_values"]
