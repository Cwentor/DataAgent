"""审计库回灌（JSONL → DuckDB）测试：批量写入、去重、CLI 端到端。

回归背景（2026-10-27 审计 §9.2）：`AuditStore.write` 先写 JSONL 后写 DB，DB 失败时
JSONL 已落盘 → 两通道不一致，且没有回灌工具。本机实测曾缺 3469 条（21%）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import duckdb

from audit.record import AuditRecord
from audit.store import AuditStore

ROOT = Path(__file__).resolve().parents[2]


def _make_records(n: int) -> list[AuditRecord]:
    return [
        AuditRecord(
            request_id=f"req-{i}", prompt=f"问题 {i}", user="u", created_at="2026-10-27T00:00:00Z"
        )
        for i in range(n)
    ]


def test_write_db_many_inserts_without_touching_jsonl(tmp_path):
    """批量仅写 DB 通道：JSONL 不被追加（回灌时它是源，再追加会翻倍）。"""
    jsonl = tmp_path / "audit.jsonl"
    store = AuditStore(jsonl_path=jsonl, db_path=tmp_path / "audit.duckdb")
    written = store.write_db_many(_make_records(5))
    assert written == 5
    assert not jsonl.exists(), "write_db_many 不得写 JSONL"

    with duckdb.connect(str(tmp_path / "audit.duckdb"), read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 5
    store.close()


def test_existing_request_ids_returns_intersection(tmp_path):
    """去重查询只返回库内已有的 id，用于回灌前算差集。"""
    store = AuditStore(jsonl_path=None, db_path=tmp_path / "audit.duckdb")
    store.write_db_many(_make_records(3))
    found = store.existing_request_ids(["req-0", "req-2", "req-missing"])
    assert found == {"req-0", "req-2"}
    store.close()


def test_existing_request_ids_on_missing_table_returns_empty(tmp_path):
    """库文件存在但无 audit_log 表时不得抛错（首次回灌场景）。"""
    store = AuditStore(jsonl_path=None, db_path=tmp_path / "empty.duckdb")
    duckdb.connect(str(tmp_path / "empty.duckdb")).close()
    assert store.existing_request_ids(["req-0"]) == set()
    store.close()


def test_rebuild_cli_restores_missing_rows(tmp_path):
    """端到端：JSONL 3 条、库内 1 条 → 脚本补齐到 3 条，且重复跑幂等。"""
    jsonl = tmp_path / "audit.jsonl"
    db = tmp_path / "audit.duckdb"

    records = _make_records(3)
    jsonl.write_text(
        "\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    # 只预置第一条，模拟「DB 写失败丢了两条」
    AuditStore(jsonl_path=None, db_path=db).write_db_many(records[:1])

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "audit_rebuild.py"),
        "--jsonl",
        str(jsonl),
        "--db",
        str(db),
    ]
    first = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "写入 2 条" in first.stdout

    with duckdb.connect(str(db), read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 3

    # 幂等：再跑一次不写任何东西
    second = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert second.returncode == 0
    assert "无需回灌" in second.stdout
    with duckdb.connect(str(db), read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 3


def test_rebuild_cli_dry_run_does_not_write(tmp_path):
    """--dry-run 只报告差额，库内行数不变。"""
    jsonl = tmp_path / "audit.jsonl"
    db = tmp_path / "audit.duckdb"
    records = _make_records(2)
    jsonl.write_text(
        "\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    AuditStore(jsonl_path=None, db_path=db).write_db_many(records[:1])

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "audit_rebuild.py"),
            "--jsonl",
            str(jsonl),
            "--db",
            str(db),
            "--dry-run",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "待回灌     : 1" in proc.stdout
    with duckdb.connect(str(db), read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 1
