"""审计持久化：JSONL 对象存储 + DuckDB 审计表。

两种 sink 可同时启用，任一写入失败都只记录日志、不影响主查询链路：
- JSONL 对象存储：逐行追加，人类可读、跨进程可追写；
- DuckDB 审计表 audit_log：可 SQL 查询、便于聚合分析。
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from audit.record import AuditRecord

# 锁文件并发创建重试（Windows CreateFile 竞争，详见 _CrossProcessLock._open_lock_file）
_LOCK_OPEN_RETRY_ATTEMPTS = 50

# 审计列名标识符白名单（迁移 DDL 使用，杜绝标识符注入面）
_AUDIT_COLUMN_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

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
    # 编排特有字段（P3）：裁决方与 LLM 规划失败原因
    "answered_by": "VARCHAR",
    "planner_llm_error": "VARCHAR",
    "created_at": "VARCHAR",
}

# DDL 全字面量（无运行期拼接）；列清单必须与 _AUDIT_COLUMN_TYPES 保持一致
# （由 tests/audit/test_audit.py 的一致性断言守护），迁移逻辑以 information_schema 对比兜底。
_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    request_id         VARCHAR,
    session_id         VARCHAR,
    user_name          VARCHAR,
    principal          VARCHAR,
    prompt             VARCHAR,
    retrieval_context  VARCHAR,
    dsl                VARCHAR,
    sql                VARCHAR,
    latency_ms         DOUBLE,
    row_count          BIGINT,
    scan_rows          BIGINT,
    rewrites           BIGINT,
    error              VARCHAR,
    detected_intent    VARCHAR,
    routing_latency_ms DOUBLE,
    routing_reason     VARCHAR,
    answered_by        VARCHAR,
    planner_llm_error  VARCHAR,
    created_at         VARCHAR
)
"""


_INSERT_SQL = """
INSERT INTO audit_log
    (request_id, session_id, user_name, principal, prompt, retrieval_context,
     dsl, sql, latency_ms, row_count, scan_rows, rewrites, error,
     detected_intent, routing_latency_ms, routing_reason,
     answered_by, planner_llm_error, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class _CrossProcessLock:
    """跨进程互斥文件锁（P0-4 审计多写者）：Windows msvcrt / POSIX fcntl。

    用于保护 JSONL 追加与 DuckDB 写入：多 worker 进程写同一审计文件时
    串行化，避免交错行与 DuckDB 文件锁冲突。
    """

    def __init__(self, target: Path) -> None:
        self._lock_path = target.with_name(target.name + ".lock")

    def _open_lock_file(self) -> Any:
        """打开（必要时创建）锁文件，Windows 并发创建竞争时有界重试。

        事故背景（CI 2026-10-24）：多线程同时首次 open 同一不存在的锁文件时，
        Windows 的 CreateFile 会对后到者瞬时返回 ERROR_ACCESS_DENIED
        （``PermissionError: [Errno 13]``），POSIX 下同名并发创建是原子的，
        故 Linux CI 从不触发。此错误可重试且窗口极短，重试即可消除。
        """
        delay = 0.01
        last_exc: Exception | None = None
        for attempt in range(_LOCK_OPEN_RETRY_ATTEMPTS):
            try:
                return open(self._lock_path, "a+b")
            except PermissionError as exc:
                if attempt == _LOCK_OPEN_RETRY_ATTEMPTS - 1:
                    last_exc = exc
                    break
                time.sleep(delay)
                delay = min(delay * 2, 0.2)
        raise PermissionError(
            f"锁文件打开失败（并发创建竞争重试 {_LOCK_OPEN_RETRY_ATTEMPTS} 次耗尽）: "
            f"{self._lock_path}"
        ) from last_exc

    def __enter__(self) -> _CrossProcessLock:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._open_lock_file()
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


def _record_params(record: AuditRecord) -> list[Any]:
    """AuditRecord → ``_INSERT_SQL`` 的位置参数（顺序必须与列名严格对应）。

    抽成函数是为了让单条写入与批量回灌共用同一份映射——两处各写一遍的话，
    加字段时必然漏一处（本项目已有"审计写入两个入口"的前车之鉴，见审计报告 §8.3）。
    """
    return [
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
        record.answered_by,
        record.planner_llm_error,
        record.created_at,
    ]


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
            "SELECT column_name FROM information_schema.columns " "WHERE table_name = 'audit_log'",
            (),
        ).fetchall()
        existing = {row[0] for row in rows}
        # 历史污染清理：早期版本的迁移把 re.Match 对象直接插进了 DDL，列名落成
        # `<re.Match object; span=(0, 11), match='answered_by'>` 这种畸形串。这类
        # 列永不参与读写（_INSERT_SQL 用显式列名），但会让下面的 existing 判定
        # 失真（answered_by 缺失 → 再次 ADD → CatalogException: already exists），
        # 结果是每条审计记录都写库失败、静默落到 JSONL。就地删除以自愈。
        for name in sorted(existing):
            if _AUDIT_COLUMN_IDENTIFIER_RE.fullmatch(name) is None:
                quoted = name.replace('"', '""')
                self._conn.execute(f'ALTER TABLE audit_log DROP COLUMN "{quoted}"', ())
                existing.discard(name)
        for column, column_type in _AUDIT_COLUMN_TYPES.items():
            if column not in existing:
                # 列名/类型来自模块级常量字典（非外部输入），仍带参数元组走安全形态
                col = _AUDIT_COLUMN_IDENTIFIER_RE.fullmatch(column)
                if col is None:
                    raise ValueError(f"非法审计列名标识符: {column!r}")
                # group(0)：fullmatch 返回 Match 对象，直接插值会把整个 repr 写进
                # 列名——这里必须是匹配到的字符串本身
                self._conn.execute(
                    f'ALTER TABLE audit_log ADD COLUMN "{col.group(0)}" {column_type}', ()
                )

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
                self._ensure_schema(conn)
                conn.execute(_INSERT_SQL, _record_params(record))
            finally:
                conn.close()

    def write_db_many(self, records: Sequence[AuditRecord]) -> int:
        """批量**仅**写 DuckDB 通道，返回写入条数（回灌用）。

        与 ``write`` 的区别：不碰 JSONL（回灌时 JSONL 本身就是源，再追加会翻倍），
        且一次跨进程锁 + 一次连接 + ``executemany``，避免逐条开关连接——3469 条
        逐条插入要几分钟，批量是秒级。

        去重由调用方负责（见 ``existing_request_ids``）：这里不做 UPSERT，保持
        「审计只追加不覆盖」的语义。
        """
        if not records:
            return 0
        with _CrossProcessLock(self.db_path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = duckdb.connect(str(self.db_path))
            try:
                self._ensure_schema(conn)
                conn.executemany(_INSERT_SQL, [_record_params(r) for r in records])
            finally:
                conn.close()
        return len(records)

    def existing_request_ids(self, request_ids: Sequence[str]) -> set[str]:
        """查询给定 request_id 中已存在于审计库的部分（回灌幂等去重用）。

        分块查询：DuckDB 的 IN 列表过大会拖慢解析，按 500 一批。
        """
        found: set[str] = set()
        if not request_ids or self.db_path is None:
            return found
        with _CrossProcessLock(self.db_path):
            conn = duckdb.connect(str(self.db_path), read_only=True)
            try:
                has_table = conn.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name = 'audit_log'"
                ).fetchone()[0]
                if not has_table:
                    return found
                for start in range(0, len(request_ids), 500):
                    chunk = list(request_ids[start : start + 500])
                    placeholders = ", ".join("?" for _ in chunk)
                    rows = conn.execute(
                        f"SELECT request_id FROM audit_log WHERE request_id IN ({placeholders})",
                        chunk,
                    ).fetchall()
                    found.update(str(r[0]) for r in rows if r[0] is not None)
            finally:
                conn.close()
        return found

    def _ensure_schema(self, conn: duckdb.DuckDBPyConnection) -> None:
        """建表 + 迁移（幂等）：写库前必须先确保列齐全，否则 INSERT 列数不匹配。"""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                request_id         VARCHAR,
                session_id         VARCHAR,
                user_name          VARCHAR,
                principal          VARCHAR,
                prompt             VARCHAR,
                retrieval_context  VARCHAR,
                dsl                VARCHAR,
                sql                VARCHAR,
                latency_ms         DOUBLE,
                row_count          BIGINT,
                scan_rows          BIGINT,
                rewrites           BIGINT,
                error              VARCHAR,
                detected_intent    VARCHAR,
                routing_latency_ms DOUBLE,
                routing_reason     VARCHAR,
                answered_by        VARCHAR,
                planner_llm_error  VARCHAR,
                created_at         VARCHAR
            )
            """,
            (),
        )
        if not self._schema_ready:
            self._conn = conn
            self._migrate_schema()
            self._conn = None
            self._schema_ready = True
