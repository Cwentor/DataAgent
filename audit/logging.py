"""结构化日志：JSON 单行输出 + request_id / session_id / user 上下文贯穿。

- JsonFormatter 把每条日志打成一行 JSON，便于采集器（Filebeat/CLS 等）消费；
- set_request_context 用 contextvars 在当前线程（请求处理线程）注入
  request_id / session_id / user，日志与审计记录共享同一标识，实现 request_id 贯穿。
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from datetime import UTC, datetime

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("session_id", default=None)
_user: contextvars.ContextVar[str | None] = contextvars.ContextVar("user", default=None)

# 允许通过 logging extra= 透传并在 JSON 中出现的业务字段
_EXTRA_FIELDS = (
    "event",
    "method",
    "path",
    "client_ip",
    "status",
    "size",
    "latency_ms",
    "row_count",
    "error",
)


class JsonFormatter(logging.Formatter):
    """把 LogRecord 序列化为单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        """序列化 LogRecord 为单行 JSON，附带 request_id / session / user 上下文。"""
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _request_id.get(),
            "session_id": _session_id.get(),
            "user": _user.get(),
        }
        for key in _EXTRA_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def set_request_context(
    request_id: str | None = None,
    session_id: str | None = None,
    user: str | None = None,
) -> str:
    """注入请求上下文并返回 request_id（未提供时生成一个）。"""
    rid = request_id or uuid.uuid4().hex
    _request_id.set(rid)
    _session_id.set(session_id)
    _user.set(user)
    return rid


def get_request_id() -> str:
    """读取当前请求上下文中的 request_id（Current request ID）。"""
    return _request_id.get()


def setup_logging(level: int = logging.INFO) -> None:
    """把根 logger 配成结构化 JSON 输出（幂等）。"""
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """按名获取 logger（Get a named logger）。"""
    return logging.getLogger(name)
