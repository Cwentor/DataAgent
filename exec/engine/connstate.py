"""连接可用性标记：超时/中断后的连接状态未知，须废弃而非复用。

背景（2026-10-27 审计 P0，见 docs/reviews/2026-10-27-audit-project-issues.md §二）：
四个外部后端（mysql / postgres / clickhouse / hive）的 ``adapter.cancel`` 均为
空操作——取消能力由服务端超时或客户端 socket 超时承载。``watchdog.run_with_timeout``
在 ``join(2.0)`` 之后无论 worker 是否退出都抛 ``QueryTimeoutError``，此时 worker
可能仍阻塞在同一条连接的网络读上。

若调用方照常把连接归还到池，下一个请求就会拿到一条「仍有活跃查询」的连接：

- pymysql / psycopg：同一连接被两线程并发使用，报 ``Commands out of sync`` 或包序错乱；
- Hive：Thrift HTTP transport 并发复用直接串包（读到上一个查询的响应）；
- ClickHouse：HTTP 客户端相对无状态，风险最低但 session 仍共享。

Hive 上是**确定性触发**：其 socket 超时被设为 ``QUERY_TIMEOUT_MS + 5s`` 且服务端
无强制超时，看门狗必然先到点，留下约 3 秒的污染窗口。

本模块只提供「标记 / 查询 / 清除」三个原子操作，不导入任何其他模块——watchdog 与
pool 都需要它，放在 engine 下可避免 ``pool -> engine -> watchdog`` 的循环导入。
"""

from __future__ import annotations

import threading
import weakref
from typing import Any

_lock = threading.Lock()

# 绝大多数连接对象（pymysql.Connection / psycopg.Connection / clickhouse_connect
# 的 Client）都支持弱引用：用 WeakSet 让标记随对象回收自动消失，无泄漏风险。
_unusable: weakref.WeakSet = weakref.WeakSet()

# 少数 C 扩展连接对象不可弱引用（无 __weakref__）：以 id 为键并持强引用。
# 强引用是有意为之——若只存 id，对象被回收后 id 复用会让新连接被误判为不可用。
# 条目在 clear()/discard() 时移除，池容量恒定，数量上限即池容量。
_unusable_strong: dict[int, Any] = {}


def mark_unusable(conn: Any) -> None:
    """标记连接不可复用（超时/中断后状态未知）。幂等，None 安全。"""
    if conn is None:
        return
    with _lock:
        try:
            _unusable.add(conn)
        except TypeError:  # 不可弱引用：退回 id + 强引用
            _unusable_strong[id(conn)] = conn


def is_unusable(conn: Any) -> bool:
    """判断连接是否已被标记为不可复用。"""
    if conn is None:
        return False
    with _lock:
        try:
            return conn in _unusable
        except TypeError:
            return id(conn) in _unusable_strong


def clear(conn: Any) -> None:
    """清除标记（连接被关闭/废弃后调用，防止标记表无界增长）。"""
    if conn is None:
        return
    with _lock:
        try:
            _unusable.discard(conn)
        except TypeError:
            _unusable_strong.pop(id(conn), None)


__all__ = ["clear", "is_unusable", "mark_unusable"]
