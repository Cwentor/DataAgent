"""审计库回灌：把 audit.jsonl 补写进 DuckDB（幂等，按 request_id 去重）。

## 为什么需要这个脚本

`AuditStore.write` 是两个独立通道的顺序写——先 JSONL、后 DuckDB。DB 写失败时
JSONL 已经落盘，两通道就此不一致；而审计的用途是追溯与合规，缺口不能靠"下次
写入时顺带补"来填。

2026-10-27 审计（`docs/reviews/2026-10-27-audit-project-issues.md` §9.2）实测本机：

    JSONL 行数: 16461
    DuckDB 行数: 12992
    差额: 3469（21%，畸形列 bug 期间的记录）

当时的修复只加了失败计数（`audit_write_failures`，能报警了），但**修不了**。
本脚本补上"修得了"这一半：JSONL 是权威源，DB 是派生物，派生物坏了就从源重建。

## 幂等性

按 `request_id` 去重：先查库里已有的 id，只写差集。重复跑不会产生重复行，
也不会覆盖已有行（审计只追加）。

## 用法

    python scripts/audit_rebuild.py --dry-run     # 只看差额，不写
    python scripts/audit_rebuild.py               # 回灌（默认路径取 settings）
    python scripts/audit_rebuild.py --since 2026-10-01
    python scripts/audit_rebuild.py --jsonl logs/audit.jsonl --db logs/audit.duckdb

退出码：0 = 无需回灌或回灌成功；1 = 存在无法解析的行或写库失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

# 允许直接以 `python scripts/audit_rebuild.py` 运行（仓库根加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.record import AuditRecord
from audit.store import AuditStore
from config import settings

_RECORD_FIELDS = {f.name for f in fields(AuditRecord)}


def _parse_line(raw: str) -> AuditRecord | None:
    """JSONL 单行 → AuditRecord；结构不符返回 None（坏行跳过并计数）。

    字段白名单过滤：历史版本的 JSONL 可能带当前 dataclass 没有的字段（列增删），
    直接 ``AuditRecord(**obj)`` 会 TypeError，但那行数据本身是可用的。
    """
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not obj.get("request_id"):
        return None
    payload: dict[str, Any] = {k: v for k, v in obj.items() if k in _RECORD_FIELDS}
    try:
        return AuditRecord(**payload)
    except TypeError:
        return None


def _load_jsonl(path: Path, since: str | None) -> tuple[list[AuditRecord], int]:
    """读取 JSONL，返回 (可回灌记录, 坏行数)。since 为 ISO 日期前缀过滤。"""
    records: list[AuditRecord] = []
    bad = 0
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            record = _parse_line(raw)
            if record is None:
                bad += 1
                continue
            if since is not None and (record.created_at or "") < since:
                continue
            records.append(record)
    return records, bad


def _db_count(store: AuditStore) -> int:
    """当前审计库行数（表不存在时返回 0）。"""
    import duckdb

    with duckdb.connect(str(store.db_path), read_only=True) as conn:
        has_table = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'audit_log'"
        ).fetchone()[0]
        if not has_table:
            return 0
        return int(conn.execute("SELECT count(*) FROM audit_log").fetchone()[0])


def main(argv: list[str] | None = None) -> int:
    """命令行入口：统计差额 → 去重 → 批量回灌 → 复核。"""
    parser = argparse.ArgumentParser(description="从 audit.jsonl 回灌 DuckDB 审计库（幂等）")
    parser.add_argument("--jsonl", type=Path, default=settings.AUDIT_DIR / "audit.jsonl")
    parser.add_argument("--db", type=Path, default=settings.AUDIT_DB_PATH)
    parser.add_argument("--since", default=None, help="只回灌 created_at >= 该前缀的记录")
    parser.add_argument("--batch-size", type=int, default=1000, help="单批写入条数")
    parser.add_argument("--dry-run", action="store_true", help="只报告差额，不写库")
    args = parser.parse_args(argv)

    if not args.jsonl.exists():
        print(f"JSONL 不存在：{args.jsonl}", file=sys.stderr)
        return 1

    records, bad = _load_jsonl(args.jsonl, args.since)
    print(f"JSONL      : {args.jsonl}")
    print(f"可解析记录 : {len(records)}" + (f"（坏行 {bad}）" if bad else ""))

    store = AuditStore(jsonl_path=None, db_path=args.db)
    before = _db_count(store) if args.db.exists() else 0
    existing = store.existing_request_ids([r.request_id for r in records])
    pending = [r for r in records if r.request_id not in existing]

    print(f"库内已有   : {before}")
    print(f"去重命中   : {len(existing)}")
    print(f"待回灌     : {len(pending)}")

    if args.dry_run:
        print("dry-run：未写入。")
        return 1 if bad else 0

    if not pending:
        print("无需回灌（两通道已一致）。")
        return 1 if bad else 0

    written = 0
    for start in range(0, len(pending), args.batch_size):
        batch = pending[start : start + args.batch_size]
        written += store.write_db_many(batch)
        print(f"  已写入 {written}/{len(pending)}", end="\r", flush=True)
    print()

    after = _db_count(store)
    print(f"回灌完成   : 写入 {written} 条，库内行数 {before} -> {after}")
    if after != before + written:
        print("警告：库内增量与写入条数不一致（可能有并发写入或去重遗漏）", file=sys.stderr)
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
