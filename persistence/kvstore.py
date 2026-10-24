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


# --------------------------------------------------------------------------- #
# Windows 并发初始化加固（CI 事故 2026-10-24）
#
# 现象：多线程/多进程同时构造 SqliteKVStore 写同一文件时，Windows 上稳定报
#   sqlite3.OperationalError: attempt to write a readonly database
# 根因：Windows 文件系统的"并发创建同名文件"竞争——多个句柄几乎同时以
#   O_CREAT 打开同一路径（WAL 切换要建 <db>-shm/<db>-wal，首个连接还要建库文件），
#   CreateFile 会瞬时返回 ERROR_ACCESS_DENIED，SQLite 将其映射为 SQLITE_READONLY。
#   POSIX 下同名并发创建是原子的，故 Linux CI 从不触发。
# 对策：进程内按库路径串行化初始化（收敛竞争窗口）+ 有界指数退避重试
#   （跨进程场景进程锁无效，仍须靠重试兜底）。不吞错：非竞争类错误立即抛出，
#   重试耗尽同样抛出原始上下文。
# --------------------------------------------------------------------------- #

# busy_timeout 仅允许档位白名单（PRAGMA 不支持参数绑定，只能静态字面量分派）
_ALLOWED_BUSY_TIMEOUT_MS = (1000, 3000, 5000)

_INIT_RETRY_ATTEMPTS = 20
_INIT_RETRY_DELAY_S = 0.05
_INIT_RETRY_MAX_DELAY_S = 0.5

# 进程内初始化锁表（按库绝对路径区分；保护 WAL 切换 + 建表这段一次性库级变更）
_INIT_LOCKS: dict[str, threading.Lock] = {}
_INIT_LOCKS_GUARD = threading.Lock()


def _init_lock_for(db_path: str) -> threading.Lock:
    """取（或创建）指定库路径的进程内初始化锁。"""
    key = str(Path(db_path).resolve())
    with _INIT_LOCKS_GUARD:
        lock = _INIT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _INIT_LOCKS[key] = lock
        return lock


def _is_write_contention(exc: sqlite3.OperationalError) -> bool:
    """判定是否为可重试的写竞争错误（锁冲突 / Windows 并发创建导致的只读误判）。"""
    text = str(exc).lower()
    return "locked" in text or "readonly" in text


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
        if timeout_ms not in _ALLOWED_BUSY_TIMEOUT_MS:
            raise ValueError(
                f"busy_timeout_ms 仅允许档位 {list(_ALLOWED_BUSY_TIMEOUT_MS)}: {busy_timeout_ms!r}"
            )
        # 锁先于连接建立：确保初始化异常时仍能安全关闭连接（防文件句柄泄漏）
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        try:
            with _init_lock_for(self._db_path):
                self._open_and_init(timeout_ms)
        except BaseException:
            # 初始化失败必须关闭连接：Windows 上残留句柄会让库文件无法删除/重开
            self._close_quietly()
            raise

    def _apply_busy_timeout(self, timeout_ms: int) -> None:
        """设置连接级 busy_timeout（档位白名单已在上游校验）。"""
        self._conn.execute(f"PRAGMA busy_timeout={timeout_ms}")

    def _open_and_init(self, timeout_ms: int) -> None:
        """建立连接并完成 WAL 切换 + 建表；写竞争时**重建连接**后重试。

        为什么必须重建连接而非原地重试：Windows 上并发首次创建同名库文件时，
        后到的 connect 会拿到尚未写完文件头的库，SQLite 判定为只读
        （``attempt to write a readonly database``）——该状态在连接上**持久**，
        原地重试永远失败；只有关掉重连（此时文件已建好）才能恢复。

        重试仅针对 write contention（locked / readonly database）；其余错误
        立即抛出，不做任何静默降级。
        """
        delay = _INIT_RETRY_DELAY_S
        last_exc: sqlite3.OperationalError | None = None
        for _ in range(_INIT_RETRY_ATTEMPTS):
            try:
                conn = sqlite3.connect(self._db_path, check_same_thread=False)
                self._conn = conn
                self._apply_busy_timeout(timeout_ms)
                self._enable_wal()
                conn.execute(
                    f'CREATE TABLE IF NOT EXISTS "{self._table}" ('
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)"
                )
                conn.commit()
                return
            except sqlite3.OperationalError as exc:
                self._close_quietly()
                self._conn = None
                if not _is_write_contention(exc):
                    raise
                last_exc = exc
                time.sleep(delay)
                delay = min(delay * 2, _INIT_RETRY_MAX_DELAY_S)
        raise sqlite3.OperationalError(
            f"初始化存储失败（建库/WAL 写竞争重试 {_INIT_RETRY_ATTEMPTS} 次耗尽）: "
            f"{self._db_path}: {last_exc}"
        )

    def _enable_wal(self) -> None:
        """切换 WAL 日志模式（已处于 WAL 则跳过）。

        单次尝试，失败交由 ``_init_schema`` 的重试循环处理——journal_mode
        切换需要排他锁且不走 busy_timeout 重试（SQLite 行为），并发初始化时
        撞锁/只读误判都属可重试竞争。
        """
        current = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(current).lower() == "wal":
            return
        self._conn.execute("PRAGMA journal_mode=WAL")

    def _close_quietly(self) -> None:
        """静默关闭连接（初始化失败路径专用，不掩盖原始异常）。"""
        if self._conn is None:
            return
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

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
            self._close_quietly()

    def __len__(self) -> int:
        """表内键值条目总数（Number of stored keys）。"""
        tbl = _validate_table_name(self._table)
        with self._lock:
            row = self._conn.execute(f'SELECT count(*) FROM "{tbl}"', ()).fetchone()
            return int(row[0]) if row else 0


__all__ = ["SqliteKVStore"]
