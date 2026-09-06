"""审计持久化：JSONL 对象存储 + DuckDB 审计表。

两种 sink 可同时启用，任一写入失败都只记录日志、不影响主查询链路：
- JSONL 对象存储：逐行追加，人类可读、跨进程可追写；
- DuckDB 审计表 audit_log：可 SQL 查询、便于聚合分析。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import duckdb

from audit.record import AuditRecord

# audit_log 列 -> 类型（DDL 与迁移共用，保证新旧表结构一致）
_AUDIT_COLUMN_TYPES: dict[str, str] = {
    "request_id": "VARCHAR",
    "session_id": "VARCHAR",
    "user_name": "VARCHAR",
    "principal": "VARCHAR",
    "prompt": "VARCHAR",
    "retrieval_context": "VARCHAR",
    "dsl": "VARCHAR",
    "sql": "VARCHAR",
    "latency_ms": "DOUBLE",
    "row_count": "BIGINT",
    "scan_rows": "BIGINT",
    "rewrites": "BIGINT",
    "error": "VARCHAR",
    # 意图路由字段（Intent Router）：detected_intent / 路由耗时 / 决策原因
    "detected_intent": "VARCHAR",
    "routing_latency_ms": "DOUBLE",
    "routing_reason": "VARCHAR",
    "created_at": "VARCHAR",
}

_COLUMN_DEFS = ",\n".join(f"    {name:<18} {ctype}" for name, ctype in _AUDIT_COLUMN_TYPES.items())
_AUDIT_DDL = f"""
CREATE TABLE IF NOT EXISTS audit_log (
{_COLUMN_DEFS}
)
"""

_INSERT_SQL = """
INSERT INTO audit_log
    (request_id, session_id, user_name, principal, prompt, retrieval_context,
     dsl, sql, latency_ms, row_count, scan_rows, rewrites, error,
     detected_intent, routing_latency_ms, routing_reason, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class _CrossProcessLock:
    """跨进程互斥文件锁（P0-4 审计多写者）：Windows msvcrt / POSIX fcntl。

    用于保护 JSONL 追加与 DuckDB 写入：多 worker 进程写同一审计文件时
    串行化，避免交错行与 DuckDB 文件锁冲突。
    """

    def __init__(self, target: Path) -> None:
        self._lock_path = target.with_name(target.name + ".lock")

    def __enter__(self) -> _CrossProcessLock:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._lock_path, "a+b")
        # 确保文件至少一个字节：msvcrt.locking 不能锁空文件
        if self._fh.seek(0, 2) == 0:
            self._fh.write(b"\0")
            self._fh.flush()
        self._fh.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            self._fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()


def _json_dump(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


class AuditStore:
    """线程安全的审计写入器（JSONL + DuckDB 审计表）。"""

    def __init__(self, jsonl_path: Path | None = None, db_path: Path | None = None) -> None:
        """初始化写入器：JSONL 与 DuckDB 路径均可选（None 表示关闭该通道）。"""
        self.jsonl_path = jsonl_path
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._schema_ready = False  # 每个进程只做一次迁移

    def write(self, record: AuditRecord) -> None:
        """线程安全写入一条审计记录（JSONL 追加 + DuckDB 插入，通道按配置启用）。"""
        with self._lock:
            if self.jsonl_path is not None:
                self._append_jsonl(record)
            if self.db_path is not None:
                self._insert_db(record)

    def close(self) -> None:
        """关闭 DuckDB 连接（幂等；JSONL 每次追加后即关闭句柄）。"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> AuditStore:
        """支持 with 上下文管理（Context-manager entry）。"""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """退出时关闭连接（Close the store on exit）。"""
        self.close()

    # ------------------------------------------------------------------ #
    def _migrate_schema(self) -> None:
        """幂等迁移：补齐旧版本 audit_log 表缺失的列（不丢历史数据）。

        兼容不同时期建的表（如 scan_rows / principal 是后续版本新增列），
        逐列对比 information_schema 后 ALTER 补齐，类型与 DDL 保持一致。
        """
        rows = self._conn.execute(
            "SELECT column_name FROM information_schema.columns " "WHERE table_name = 'audit_log'"
        ).fetchall()
        existing = {row[0] for row in rows}
        for column, column_type in _AUDIT_COLUMN_TYPES.items():
            if column not in existing:
                self._conn.execute(f'ALTER TABLE audit_log ADD COLUMN "{column}" {column_type}')

    def _append_jsonl(self, record: AuditRecord) -> None:
        with _CrossProcessLock(self.jsonl_path):
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record.to_dict(), ensure_ascii=False)
            with self.jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _insert_db(self, record: AuditRecord) -> None:
        # 跨进程文件锁 + 短连接：多 worker 写同一审计库时串行化，互不冲突
        with _CrossProcessLock(self.db_path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = duckdb.connect(str(self.db_path))
            try:
                conn.execute(_AUDIT_DDL)
                if not self._schema_ready:
                    self._conn = conn
                    self._migrate_schema()
                    self._conn = None
                    self._schema_ready = True
                conn.execute(
                    _INSERT_SQL,
                    [
                        record.request_id,
                        record.session_id,
                        record.user,
                        record.principal,
                        record.prompt,
                        _json_dump(record.retrieval_context),
                        _json_dump(record.dsl),
                        record.sql,
                        record.latency_ms,
                        record.row_count,
                        record.scan_rows,
                        record.rewrites,
                        record.error,
                        record.detected_intent,
                        record.routing_latency_ms,
                        record.routing_reason,
                        record.created_at,
                    ],
                )
            finally:
                conn.close()
