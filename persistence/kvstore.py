"""轻量 SQLite 键值持久化后端（整改指令3-2：会话/澄清/限流存储外置化）。

三个进程内存储（SessionStore / ClarifySlotStore / LoginRateLimiter）在配置
``STATE_STORE_DB``（或各自的 db_path）后，通过本模块把状态落盘到同一 SQLite
文件，使多 worker / 重启后状态一致，避免"单机态存储"在多实例下互相覆盖。

设计约束：
- 接口与进程内 dict 语义一致：get / set / delete / clear / close；
- get 支持惰性 TTL 失效（过期记录读取时删除）；
- 线程安全（RLock 串行化 SQLite 读写）；
- 多 worker 并发写加固（遗留风险项2）：连接启用 WAL 日志模式（并发读不阻塞写）
  + busy_timeout 等锁重试（写锁冲突时等待而非立即报 database is locked）；
- 值统一 JSON 序列化（ensure_ascii=False 保中文；datetime/date 走 default=str），
  由调用方负责反序列化为领域对象；
- 只做键值读写，不承担任何鉴权/审计职责（安全防线仍在业务层）。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

# 表名标识符白名单：仅允许安全字符，杜绝 SQL 标识符注入面
_TABLE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _validate_table_name(table: str) -> str:
    """校验表名为合法 SQL 标识符（字母/下划线开头，仅含字母数字下划线）。"""
    if not _TABLE_NAME_RE.fullmatch(table):
        raise ValueError(f"非法表名标识符: {table!r}")
    return table


def _json_default(obj: Any) -> str:
    """JSON 序列化兜底：datetime/date 等转 ISO 字符串。"""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


class SqliteKVStore:
    """线程安全 SQLite 键值存储：key -> JSON 值，可选 TTL 惰性失效。"""

    def __init__(self, db_path: str | Path, table: str = "kv", busy_timeout_ms: int = 5000) -> None:
        """打开 SQLite 连接并完成 WAL / busy_timeout 加固，按需建表。"""
        self._db_path = str(db_path)
        # 表名标识符先经白名单校验再进入任何 SQL 文本（参数绑定只适用于值，
        # 标识符必须走白名单）；busy_timeout 仅允许档位白名单（PRAGMA 不支持
        # 参数绑定，只能静态字面量分派，杜绝任何运行期 SQL 构造）。
        self._table = _validate_table_name(table)
        timeout_ms = int(busy_timeout_ms)
        if timeout_ms not in (1000, 3000, 5000):
            raise ValueError(f"busy_timeout_ms 仅允许档位 [1000, 3000, 5000]: {busy_timeout_ms!r}")
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        # 多 worker 并发写加固：busy_timeout 为连接级属性（须在持锁操作前设置）；
        # WAL 为数据库级持久属性，仅首次切换需要排他锁。
        if timeout_ms == 1000:
            self._conn.execute("PRAGMA busy_timeout=1000")
        elif timeout_ms == 3000:
            self._conn.execute("PRAGMA busy_timeout=3000")
        else:
            self._conn.execute("PRAGMA busy_timeout=5000")
        self._enable_wal()
        tbl = _validate_table_name(self._table)
        self._conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{tbl}" ('
            "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        self._conn.commit()
        self._lock = threading.RLock()

    def _enable_wal(self) -> None:
        """切换 WAL 日志模式（已处于 WAL 则跳过）。

        journal_mode 切换需要排他锁且不走 busy_timeout 重试（SQLite 行为），
        多 worker 并发初始化时可能撞锁，故显式短重试；耗尽仍失败则抛出，
        不静默降级（不吞错原则）。
        """
        current = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(current).lower() == "wal":
            return
        delay = 0.05
        for _ in range(20):
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(delay)
        raise sqlite3.OperationalError(f"WAL 模式切换重试耗尽（数据库被并发占用）: {self._db_path}")

    def get(self, key: str, ttl_seconds: float | None = None) -> Any | None:
        """读取键值；TTL 过期记录读取即删除并返回 None。"""
        tbl = _validate_table_name(self._table)
        with self._lock:
            row = self._conn.execute(
                f'SELECT value, updated_at FROM "{tbl}" WHERE key = ?', (key,)
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
        tbl = _validate_table_name(self._table)
        with self._lock:
            self._conn.execute(
                f'INSERT OR REPLACE INTO "{tbl}" (key, value, updated_at) ' "VALUES (?, ?, ?)",
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
        tbl = _validate_table_name(self._table)
        with self._lock:
            cur = self._conn.execute(f'DELETE FROM "{tbl}"', ())
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
        """表内键值条目总数（Number of stored keys）。"""
        tbl = _validate_table_name(self._table)
        with self._lock:
            row = self._conn.execute(f'SELECT count(*) FROM "{tbl}"', ()).fetchone()
            return int(row[0]) if row else 0


__all__ = ["SqliteKVStore"]
