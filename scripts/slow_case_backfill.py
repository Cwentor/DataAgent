"""慢案例回流评测候选生成（#23，2026-11）。

从审计库提取已标注的慢案例（组内 sql_latency_ms > 历史 P95，标注逻辑见
audit.analysis.query_latency_report），生成与 eval/golden_dataset.json 同构
的**候选**评测用例 JSON，供人工复核后并入评测集：

- 人工裁决是必须环节（issue #23：人工复核属实的案例固化进评测集），
  本脚本只生成候选，绝不自动并入 golden_dataset.json；
- 候选条目锚定 DSL / SQL 形态（契约层断言），不锚定耗时数值——耗时会随
  数据量与机器漂移，锚耗时等于锚噪声；
- 条目 id 以 SC 前缀编号（区分常规 golden 的 Q 前缀），并附
  ``_slow_case_meta`` 溯源字段（审计 request_id / 耗时 / 域 / 后端）；
  eval_runner 消费 golden 时忽略未知键，人工复核后保留或删除均可。

用法（只读审计库，生产环境可安全执行）：

    python -m scripts.slow_case_backfill --db path/to/audit.duckdb \
        [--min-samples 20] [--limit 10] [--out candidates.json]

mock 数仓量级有限，可能长期提取不到真实慢案例——流程本身由
tests/audit/test_analysis.py 与 tests/test_slow_case_backfill.py 以合成
审计库守护，真实规模出现慢案例时流程即为可用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from audit.analysis import query_latency_report
from audit.record import AuditRecord


def build_candidates(
    report: dict[str, Any],
    records_by_request: dict[str, AuditRecord],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """把慢案例清单转成 golden 候选用例（dsl 解析失败的案例跳过并留痕）。"""
    candidates: list[dict[str, Any]] = []
    for case in report["slow_cases"]:
        if len(candidates) >= limit:
            break
        record = records_by_request.get(case["request_id"])
        if record is None or record.dsl is None or record.sql is None:
            # dsl/sql 缺失（探索层执行 / 历史脏数据）：无契约形态可锚定，跳过
            print(
                f"[skip] request_id={case['request_id']} 缺少 dsl/sql 落盘，无法生成契约断言",
                file=sys.stderr,
            )
            continue
        candidates.append(
            {
                # 编号按已收录条数连续递增（跳过缺失案例不留空洞）
                "id": f"SC{len(candidates) + 1:02d}",
                "question": case["prompt"],
                "scenario": "slow-case-backfill",
                "dsl": record.dsl,
                "sql": record.sql,
                "_slow_case_meta": {
                    "audit_request_id": case["request_id"],
                    "sql_latency_ms": case["sql_latency_ms"],
                    "query_domain": case["query_domain"],
                    "db_backend": case["db_backend"],
                    "created_at": case["created_at"],
                },
            }
        )
    return candidates


def main() -> None:
    """命令行入口：提取慢案例 -> 生成 golden 候选 JSON（人工复核后并入评测集）。"""
    parser = argparse.ArgumentParser(description="慢案例回流评测候选生成（只读审计库）")
    parser.add_argument(
        "--db", default=None, help="审计 DuckDB 文件路径（默认 settings.AUDIT_DB_PATH）"
    )
    parser.add_argument(
        "--min-samples", type=int, default=20, help="慢案例标注的最小分组样本量（默认 20）"
    )
    parser.add_argument("--limit", type=int, default=10, help="最多生成的候选条数（默认 10）")
    parser.add_argument("--out", default=None, help="候选 JSON 输出路径（缺省打印到 stdout）")
    args = parser.parse_args()

    from config import settings

    db_path = Path(args.db) if args.db else settings.AUDIT_DB_PATH
    report = query_latency_report(db_path=db_path, min_samples=args.min_samples)
    if not report["slow_cases"]:
        print("（无慢案例：样本不足或无超 P95 执行记录；流程由测试以合成审计库守护）")
        return

    # 按慢案例的 request_id 回查完整审计记录（dsl / sql 契约形态来源）
    request_ids = [case["request_id"] for case in report["slow_cases"]]
    records_by_request = _load_records(db_path, request_ids)
    candidates = build_candidates(report, records_by_request, limit=args.limit)

    payload = json.dumps(candidates, ensure_ascii=False, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")
        print(
            f"已生成 {len(candidates)} 条候选用例 -> {out_path}（人工复核后并入 eval/golden_dataset.json）"
        )
    else:
        print(payload)


def _load_records(db_path: Path, request_ids: list[str]) -> dict[str, AuditRecord]:
    """按 request_id 从审计 DuckDB 读回完整记录（慢案例的 dsl / sql 来源）。

    列序与 audit.store 的列清单一致；dsl / sql 为 JSON 文本列，缺失跳过。
    """
    import duckdb

    records: dict[str, AuditRecord] = {}
    with duckdb.connect(str(db_path), read_only=True) as conn:
        placeholders = ", ".join("?" for _ in request_ids)
        rows = conn.execute(
            "SELECT request_id, prompt, session_id, user_name, principal, "
            "retrieval_context, dsl, sql, latency_ms, row_count, scan_rows, "
            "rewrites, error, detected_intent, routing_latency_ms, routing_reason, "
            "answered_by, planner_llm_error, query_domain, db_backend, "
            "sql_latency_ms, sql_exec, created_at "
            f"FROM audit_log WHERE request_id IN ({placeholders})",
            request_ids,
        ).fetchall()
    for row in rows:
        try:
            dsl = json.loads(row[6]) if row[6] else None
        except (TypeError, json.JSONDecodeError):
            dsl = None
        records[row[0]] = AuditRecord(
            request_id=row[0],
            prompt=row[1],
            session_id=row[2],
            user=row[3],
            principal=row[4],
            dsl=dsl,
            sql=row[7],
        )
    return records


if __name__ == "__main__":
    main()


__all__ = ["build_candidates"]
