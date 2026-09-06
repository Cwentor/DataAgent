"""服务端会话存储（线程安全；进程内 或 SQLite 共享存储）。

会话是不透明的服务端签发凭证：客户端只持有 session_id，服务端据此从
SessionStore 解析出 username / principal。与 JWT 互补：支持"登出即失效"，
适合浏览器 Cookie 场景。到期自动失效并惰性清理。

P0-4 生产加固：新增 SqliteSessionStore —— 会话落盘 SQLite（WAL），
重启不丢、多 worker 可共享；配置 AUTH_SESSION_DB 后默认存储自动切换。
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from config import settings

_SESSION_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    principal  TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
)
"""


@dataclass(frozen=True)
class Session:
    """会话快照（不可变）：opaque session_id + 身份 + 起止时间（Immutable session snapshot）。"""

    session_id: str
    username: str
    principal: str
    created_at: float
    expires_at: float

    @property
    def expired(self) -> bool:
        """会话是否已过期（Whether the session has expired）。"""
        return time.time() >= self.expires_at


class SessionStore:
    """线程安全的会话注册表（默认进程内存储，TTL 可配）。

    子类可替换存储后端（见 SqliteSessionStore），接口保持一致：
    create / get / revoke / prune。
    """

    def __init__(self, ttl_seconds: int | None = None) -> None:
        """初始化进程内存储；ttl_seconds 缺省取 settings.AUTH_SESSION_TTL。"""
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.AUTH_SESSION_TTL
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, username: str, principal: str, ttl_seconds: int | None = None) -> Session:
        """签发并登记一个新会话（Create and register a new session）。"""
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        session = Session(
            session_id=uuid.uuid4().hex,
            username=username,
            principal=principal,
            created_at=now,
            expires_at=now + ttl,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """按 id 取会话；已过期 / 不存在返回 None。"""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.expired:
                self._sessions.pop(session_id, None)
                return None
            return session

    def revoke(self, session_id: str) -> bool:
        """主动登出：删除会话，返回是否存在。"""
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def prune(self) -> int:
        """清理全部过期会话，返回清理条数。"""
        now = time.time()
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if now >= s.expires_at]
            for sid in expired:
                self._sessions.pop(sid, None)
            return len(expired)


class SqliteSessionStore(SessionStore):
    """SQLite 持久化会话存储（P0-4）：重启不丢、多 worker 可共享。

    与进程内 SessionStore 接口完全一致；数据落盘单文件 SQLite（WAL 模式）。
    进程内用一把锁串行化访问，跨进程由 SQLite 自身锁 + busy_timeout 保证
    并发一致性。TTL 语义与进程内版本相同（惰性清理 + prune）。
    """

    def __init__(self, db_path: str | Path, ttl_seconds: int | None = None) -> None:
        """初始化 SQLite 存储：建表（WAL），TTL 缺省取全局配置。"""
        super().__init__(ttl_seconds)
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(_SESSION_DDL)
        self._conn.commit()

    def create(self, username: str, principal: str, ttl_seconds: int | None = None) -> Session:
        """签发并落盘新会话（Create and persist a new session）。"""
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        session = Session(
            session_id=uuid.uuid4().hex,
            username=username,
            principal=principal,
            created_at=now,
            expires_at=now + ttl,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (session_id, username, principal, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    session.session_id,
                    session.username,
                    session.principal,
                    session.created_at,
                    session.expires_at,
                ),
            )
            self._conn.commit()
        return session

    def get(self, session_id: str) -> Session | None:
        """按 id 读取会话；不存在 / 过期返回 None（惰性清理过期行）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id, username, principal, created_at, expires_at "
                "FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            session = Session(*row)
            if session.expired:
                self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                self._conn.commit()
                return None
            return session

    def revoke(self, session_id: str) -> bool:
        """登出：删除会话行，返回是否确实存在（Revoke a session）。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def prune(self) -> int:
        """清理全部过期会话，返回清理条数（Prune expired sessions）。"""
        now = time.time()
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        """关闭底层 SQLite 连接（Close the underlying connection）。"""
        with self._lock:
            self._conn.close()


_default_store: SessionStore | None = None
_store_lock = threading.Lock()


def default_session_store() -> SessionStore:
    """复用的默认会话存储（按 settings.AUTH_SESSION_TTL 配置 TTL）。

    配置 AUTH_SESSION_DB 时使用 SQLite 持久化存储（生产共享、重启不丢）；
    否则使用进程内存储（本地开发/演示）。
    """
    global _default_store
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                if settings.AUTH_SESSION_DB:
                    _default_store = SqliteSessionStore(settings.AUTH_SESSION_DB)
                else:
                    _default_store = SessionStore()
    return _default_store
