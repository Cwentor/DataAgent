"""通用超时看门狗（二十一期自 exec.guards 下沉，引擎无关）。

worker 线程内经 adapter 执行查询，主线程等待超时阈值；超时回调
adapter.cancel 尽力取消（DuckDB interrupt / MySQL 服务端已自限则空操作），
随后抛 QueryTimeoutError。线程框架对两引擎完全一致，取消手段引擎自决。
"""

from __future__ import annotations

import threading
from typing import Any

from audit.logging import get_logger
from exec.engine.connstate import mark_unusable
from exec.errors import QueryTimeoutError

# 沿用 exec.guards 日志通道：超时留痕的可观测性锚点与既有测试断言不变
logger = get_logger("exec.guards")


def run_with_timeout(adapter, conn, sql: str, timeout_ms: int | None):
    """在工作线程中经 adapter 执行查询并取回结果；超时则取消并抛超时异常。

    返回 (columns, rows)。

    超时后连接**不再可复用**：外部后端的 ``adapter.cancel`` 是空操作（取消能力由
    服务端超时或客户端 socket 超时承载），``join(2.0)`` 返回时 worker 可能仍阻塞在
    同一条连接上。故超时分支统一经 ``connstate.mark_unusable`` 打标，由
    ``pool.release`` 自动转废弃——调用方无需感知，见 connstate 模块注释与审计
    报告 §二。
    """
    if timeout_ms is None:
        return adapter.run_query(conn, sql)

    outcome: dict[str, Any] = {}

    def _worker() -> None:
        try:
            outcome["result"] = adapter.run_query(conn, sql)
        except BaseException as exc:  # 线程内任意异常均须回传
            outcome["exc"] = exc

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(timeout_ms / 1000.0)

    if worker.is_alive():
        # 超时：尽力取消，等待线程真正退出
        try:
            adapter.cancel(conn)
        except Exception:  # cancel 失败也须继续走超时分支
            pass
        worker.join(2.0)
        # 无条件打标：即使 worker 已在 2 秒窗口内退出，连接上也可能残留未读完的
        # 结果集（MySQL/PG 需 drain 才能复用）。重建一条连接是毫秒级代价，
        # 而复用脏连接的代价是错误答案或协议错乱——不值得赌。
        mark_unusable(conn)
        logger.error("查询超时被强制取消", extra={"error": f"timeout_ms={timeout_ms}"})
        raise QueryTimeoutError(f"查询超时（>{timeout_ms}ms），已强制取消")

    if "exc" in outcome:
        raise outcome["exc"]
    return outcome["result"]
