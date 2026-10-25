"""通用超时看门狗（二十一期自 exec.guards 下沉，引擎无关）。

worker 线程内经 adapter 执行查询，主线程等待超时阈值；超时回调
adapter.cancel 尽力取消（DuckDB interrupt / MySQL 服务端已自限则空操作），
随后抛 QueryTimeoutError。线程框架对两引擎完全一致，取消手段引擎自决。
"""

from __future__ import annotations

import threading
from typing import Any

from audit.logging import get_logger
from exec.errors import QueryTimeoutError

# 沿用 exec.guards 日志通道：超时留痕的可观测性锚点与既有测试断言不变
logger = get_logger("exec.guards")


def run_with_timeout(adapter, conn, sql: str, timeout_ms: int | None):
    """在工作线程中经 adapter 执行查询并取回结果；超时则取消并抛超时异常。

    返回 (columns, rows)。连接在中断后可继续复用（引擎侧保证）。
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
        logger.error("查询超时被强制取消", extra={"error": f"timeout_ms={timeout_ms}"})
        raise QueryTimeoutError(f"查询超时（>{timeout_ms}ms），已强制取消")

    if "exc" in outcome:
        raise outcome["exc"]
    return outcome["result"]
