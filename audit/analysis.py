"""审计离线漏斗分析：按 detected_intent + routing_reason 聚合意图分流质量。

AuditRecord 已把每轮提问的路由判决（detected_intent / routing_reason /
routing_latency_ms）落盘到 audit_log 表（JSONL + DuckDB 双 sink），本模块
对该落盘数据做只读聚合，供生产环境离线评估意图分流质量：

- 意图漏斗：总请求数 -> 各意图（detected_intent）分流的数量与占比；
- 路由原因分解：每个意图内部 routing_reason 的分布（定位误判来源）；
- 路由耗时：routing_latency_ms 的 P50 / P95 / 均值 / 最大值；
- 分流质量代理：各意图的请求失败率（error 非空占比）——审计尚无人工
  ground-truth 标注字段，"分流准确率"以失败率为质量信号；若后续引入
  人工核正字段（如 corrected_intent），可在本模块扩展真准确率计算。

用法：
- 编程调用：routing_funnel_report(db_path=...) 或 conn=（注入连接便于测试）；
- CLI：python -m audit.analysis [--db path] [--json]。

只读分析：任何情况下不写审计表；表缺失 / 库缺失时返回空报表而非报错，
保证分析脚本在生产环境可安全随意执行。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb

# audit_log 缺失意图标注时的占位意图名（漏斗口径统一）
_UNROUTED = "(unrouted)"


def _table_exists(conn: duckdb.DuckDBPyConnection) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'audit_log'"
    ).fetchone()
    return bool(row and row[0])


def routing_funnel_report(
    conn: duckdb.DuckDBPyConnection | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """聚合意图分流漏斗报表（纯只读）。

    参数：
    - conn：已打开的 DuckDB 连接（测试注入）；与 db_path 二选一；
    - db_path：审计 DuckDB 文件路径（只读打开）。

    返回结构见模块 docstring；audit_log 表或库不存在时返回 total=0 的空报表。
    """
    own_conn: duckdb.DuckDBPyConnection | None = None
    if conn is None:
        if db_path is None or not Path(db_path).is_file():
            return _empty_report()
        own_conn = duckdb.connect(str(db_path), read_only=True)
        conn = own_conn
    try:
        if not _table_exists(conn):
            return _empty_report()

        total = int(conn.execute("SELECT count(*) FROM audit_log").fetchone()[0])
        if total == 0:
            return _empty_report()

        # 意图漏斗：各意图分流数量与占比
        rows = conn.execute(
            "SELECT coalesce(detected_intent, ?) AS intent, count(*) AS n "
            "FROM audit_log GROUP BY 1 ORDER BY n DESC",
            [_UNROUTED],
        ).fetchall()
        intents = [
            {"intent": intent, "count": int(n), "share": round(n / total, 4)} for intent, n in rows
        ]

        # 路由原因分解：意图 -> reason 分布
        rows = conn.execute(
            "SELECT coalesce(detected_intent, ?) AS intent, "
            "coalesce(routing_reason, '(none)') AS reason, count(*) AS n "
            "FROM audit_log GROUP BY 1, 2 ORDER BY 1, n DESC",
            [_UNROUTED],
        ).fetchall()
        reasons: dict[str, list[dict[str, Any]]] = {}
        for intent, reason, n in rows:
            intent_total = next(i["count"] for i in intents if i["intent"] == intent)
            reasons.setdefault(intent, []).append(
                {"reason": reason, "count": int(n), "share": round(n / intent_total, 4)}
            )

        # 路由耗时（仅统计已落盘耗时的样本）
        row = conn.execute(
            "SELECT quantile_cont(routing_latency_ms, 0.5), "
            "quantile_cont(routing_latency_ms, 0.95), "
            "avg(routing_latency_ms), max(routing_latency_ms), count(*) "
            "FROM audit_log WHERE routing_latency_ms IS NOT NULL"
        ).fetchone()
        latency = {
            "p50": round(float(row[0]), 3) if row[0] is not None else None,
            "p95": round(float(row[1]), 3) if row[1] is not None else None,
            "avg": round(float(row[2]), 3) if row[2] is not None else None,
            "max": round(float(row[3]), 3) if row[3] is not None else None,
            "samples": int(row[4]),
        }

        # 分流质量代理：各意图失败率（error 非空 = 该轮链路最终失败）
        rows = conn.execute(
            "SELECT coalesce(detected_intent, ?) AS intent, count(*) AS n, "
            "count(error) AS errors FROM audit_log GROUP BY 1 ORDER BY n DESC",
            [_UNROUTED],
        ).fetchall()
        error_rates = [
            {
                "intent": intent,
                "total": int(n),
                "errors": int(errors),
                "error_rate": round(errors / n, 4),
            }
            for intent, n, errors in rows
        ]

        return {
            "total": total,
            "intents": intents,
            "routing_reasons": reasons,
            "routing_latency_ms": latency,
            "error_rate_by_intent": error_rates,
        }
    finally:
        if own_conn is not None:
            own_conn.close()


def _empty_report() -> dict[str, Any]:
    return {
        "total": 0,
        "intents": [],
        "routing_reasons": {},
        "routing_latency_ms": {"p50": None, "p95": None, "avg": None, "max": None, "samples": 0},
        "error_rate_by_intent": [],
    }


def _render(report: dict[str, Any]) -> str:
    """报表的人类可读渲染（CLI 输出）。"""
    lines = [f"意图分流漏斗分析（共 {report['total']} 轮提问）"]
    if report["total"] == 0:
        lines.append("  （audit_log 无数据）")
        return "\n".join(lines)
    lines.append("\n[意图漏斗]")
    for item in report["intents"]:
        lines.append(f"  {item['intent']:<18} {item['count']:>6}  ({item['share']:.1%})")
    lines.append("\n[路由原因分解]")
    for intent, items in report["routing_reasons"].items():
        for item in items:
            lines.append(
                f"  {intent:<18} {item['reason']:<28} {item['count']:>6}  ({item['share']:.1%})"
            )
    lat = report["routing_latency_ms"]
    if lat["samples"]:
        lines.append("\n[路由耗时 ms]")
        lines.append(
            f"  P50={lat['p50']}  P95={lat['p95']}  avg={lat['avg']}  max={lat['max']}"
            f"  (samples={lat['samples']})"
        )
    lines.append("\n[分流质量代理：各意图失败率]")
    for item in report["error_rate_by_intent"]:
        lines.append(
            f"  {item['intent']:<18} errors={item['errors']:>4}/{item['total']:<6}"
            f" ({item['error_rate']:.1%})"
        )
    return "\n".join(lines)


def main() -> None:
    """命令行入口：只读审计库，输出意图分流漏斗分析报告（Read-only funnel analysis）。"""
    parser = argparse.ArgumentParser(description="审计意图分流漏斗分析（只读）")
    parser.add_argument(
        "--db", default=None, help="审计 DuckDB 文件路径（默认 settings.AUDIT_DB_PATH）"
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报表")
    args = parser.parse_args()

    from config import settings

    db_path = Path(args.db) if args.db else settings.AUDIT_DB_PATH
    report = routing_funnel_report(db_path=db_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render(report))


if __name__ == "__main__":
    main()
