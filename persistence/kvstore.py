"""轻量 SQLite 键值持久化后端（整改指令3-2：会话/澄清/限流存储外置化）。

三个进程内存储（SessionStore / ClarifySlotStore / LoginRateLimiter）在配置
``STATE_STORE_DB``（或各自的 db_path）后，通过本模块把状态落盘到同一 SQLite
文件，使多 worker / 重启后状态一致，避免"单机态存储"在多实例下互相覆盖。

设计约束：
- 接口与进程内 dict 语义一致：get / set / delete / clear / close；
- get 支持惰性 TTL 失效（过期记录读取时删除）；
- 线程安全（RLock 串行化 SQLite 读写）；
- 值统一 JSON 序列化（ensure_ascii=False 保中文；datetime/date 走 default=str），
  由调用方负责反序列化为领域对象；
- 只做键值读写，不承担任何鉴权/审计职责（安全防线仍在业务层）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


def _json_default(obj: Any) -> str:
    """JSON 序列化兜底：datetime/date 等转 ISO 字符串。"""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


class SqliteKVStore:
    """线程安全 SQLite 键值存储：key -> JSON 值，可选 TTL 惰性失效。"""

    def __init__(self, db_path: str | Path, table: str = "kv") -> None:
        self._db_path = str(db_path)
        self._table = table
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{table}" ('
            "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        self._conn.commit()
        self._lock = threading.RLock()

    def get(self, key: str, ttl_seconds: float | None = None) -> Any | None:
        """读取键值；TTL 过期记录读取即删除并返回 None。"""
        with self._lock:
            row = self._conn.execute(
                f'SELECT value, updated_at FROM "{self._table}" WHERE key = ?', (key,)
            ).fetchone()
            if row is None:
                return None
            value, updated_at = row
            if ttl_seconds is not None and time.time() - updated_at > ttl_seconds:
                self.delete(key)
                return None
            return json.loads(value)

    def set(self, key: str, value: Any) -> None:
        """写入（或覆盖）键值，刷新 updated_at。"""
        with self._lock:
            self._conn.execute(
                f'INSERT OR REPLACE INTO "{self._table}" (key, value, updated_at) '
                "VALUES (?, ?, ?)",
                (key, json.dumps(value, ensure_ascii=False, default=_json_default), time.time()),
            )
            self._conn.commit()

    def delete(self, key: str) -> bool:
        """删除键值，返回是否存在。"""
        with self._lock:
            cur = self._conn.execute(f'DELETE FROM "{self._table}" WHERE key = ?', (key,))
            self._conn.commit()
            return cur.rowcount > 0

    def clear(self) -> int:
        """清空全部键值，返回清理条数。"""
        with self._lock:
            cur = self._conn.execute(f'DELETE FROM "{self._table}"')
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        """关闭连接（进程退出前调用，避免残留 WAL）。"""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __len__(self) -> int:
        with self._lock:
            row = self._conn.execute(f'SELECT count(*) FROM "{self._table}"').fetchone()
            return int(row[0]) if row else 0


__all__ = ["SqliteKVStore"]
