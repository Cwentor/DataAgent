"""审计离线漏斗分析：按 detected_intent + routing_reason 聚合意图分流质量，
并按 query_domain + db_backend 聚合 SQL 执行耗时与慢案例标注（#23）。

AuditRecord 已把每轮提问的路由判决（detected_intent / routing_reason /
routing_latency_ms）落盘到 audit_log 表（JSONL + DuckDB 双 sink），本模块
对该落盘数据做只读聚合，供生产环境离线评估意图分流质量：

- 意图漏斗：总请求数 -> 各意图（detected_intent）分流的数量与占比；
- 路由原因分解：每个意图内部 routing_reason 的分布（定位误判来源）；
- 路由耗时：routing_latency_ms 的 P50 / P95 / 均值 / 最大值；
- 分流质量代理：各意图的请求失败率（error 非空占比）——审计尚无人工
  ground-truth 标注字段，"分流准确率"以失败率为质量信号；若后续引入
  人工核正字段（如 corrected_intent），可在本模块扩展真准确率计算。

查询耗时分析（#23，2026-11）：按（query_domain, db_backend）分组聚合
sql_latency_ms（成功执行的耗时，口径见 exec.usage）的分位数，并标注
**慢案例**——组内 sql_latency_ms 高于该组历史 P95 的执行记录。只标注、
不建议、不诊断；分组样本不足 min_samples 时输出 insufficient_sample
标记而不标注任何慢案例（样本不足时分位数无统计意义，严禁虚构显著性）。

用法：
- 编程调用：routing_funnel_report(...) / query_latency_report(...)，
  均支持 conn=（注入连接便于测试）或 db_path=；
- CLI：python -m audit.analysis [--db path] [--json] [--report intent|query_latency]。

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


# --------------------------------------------------------------------------- #
# 查询耗时分析与慢案例标注（#23，2026-11）
# --------------------------------------------------------------------------- #


# 分位数取法与 audit.metrics._percentile 同一口径（排序后按比例取下标），
# 保证离线报告与在线指标的 p95 数值在同一数据上可对账
def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(p * len(ordered)))
    return round(ordered[idx], 3)


def _empty_latency_report(min_samples: int) -> dict[str, Any]:
    return {
        "total_executions": 0,
        "min_samples": min_samples,
        "groups": [],
        "slow_cases": [],
    }


def query_latency_report(
    conn: duckdb.DuckDBPyConnection | None = None,
    db_path: Path | str | None = None,
    *,
    min_samples: int = 20,
) -> dict[str, Any]:
    """按（query_domain, db_backend）分组聚合 SQL 执行耗时并标注慢案例（只读）。

    慢案例定义：组内 sql_latency_ms > 组内历史 P95 的成功执行记录。分组样本
    < min_samples 时组标记 insufficient_sample=True 且不产出慢案例——样本
    不足时分位数无统计意义。只标注、不建议、不诊断（issue #23 边界）。

    参数：
    - conn：已打开的 DuckDB 连接（测试注入）；与 db_path 二选一；
    - db_path：审计 DuckDB 文件路径（只读打开）；
    - min_samples：慢案例标注的最小分组样本量（默认 20）。

    audit_log 表或库不存在、或无任何成功执行记录时返回空报表。
    """
    own_conn: duckdb.DuckDBPyConnection | None = None
    if conn is None:
        if db_path is None or not Path(db_path).is_file():
            return _empty_latency_report(min_samples)
        own_conn = duckdb.connect(str(db_path), read_only=True)
        conn = own_conn
    try:
        if not _table_exists(conn):
            return _empty_latency_report(min_samples)

        rows = conn.execute(
            "SELECT coalesce(query_domain, '(unspecified)'), "
            "coalesce(db_backend, '(unknown)'), "
            "request_id, prompt, sql_latency_ms, row_count, created_at "
            "FROM audit_log WHERE sql_latency_ms IS NOT NULL"
        ).fetchall()
        if not rows:
            return _empty_latency_report(min_samples)

        grouped: dict[tuple[str, str], list[tuple[Any, ...]]] = {}
        for row in rows:
            grouped.setdefault((row[0], row[1]), []).append(row)

        groups: list[dict[str, Any]] = []
        for (domain, backend), items in sorted(grouped.items()):
            durations = [float(item[4]) for item in items]
            stats = {
                "query_domain": domain,
                "db_backend": backend,
                "count": len(durations),
                "p50": _percentile(durations, 0.5),
                "p95": _percentile(durations, 0.95),
                "max": round(max(durations), 3),
                "insufficient_sample": len(durations) < min_samples,
            }
            groups.append(stats)

        # 慢案例：仅对样本充足的组标注（超组内 P95），按耗时降序
        slow_cases: list[dict[str, Any]] = []
        for stats in groups:
            if stats["insufficient_sample"]:
                continue
            threshold = stats["p95"]
            items = grouped[(stats["query_domain"], stats["db_backend"])]
            for item in items:
                if float(item[4]) > threshold:
                    slow_cases.append(
                        {
                            "request_id": item[2],
                            "prompt": (item[3] or "")[:120],
                            "query_domain": stats["query_domain"],
                            "db_backend": stats["db_backend"],
                            "sql_latency_ms": float(item[4]),
                            "row_count": item[5],
                            "created_at": item[6],
                        }
                    )
        slow_cases.sort(key=lambda case: case["sql_latency_ms"], reverse=True)

        return {
            "total_executions": len(rows),
            "min_samples": min_samples,
            "groups": groups,
            "slow_cases": slow_cases,
        }
    finally:
        if own_conn is not None:
            own_conn.close()


def _render_latency(report: dict[str, Any]) -> str:
    """耗时报告的人类可读渲染（CLI 输出）。"""
    lines = [
        f"查询耗时分析（成功执行 {report['total_executions']} 次，"
        f"慢案例标注最小样本量 {report['min_samples']}）"
    ]
    if report["total_executions"] == 0:
        lines.append("  （audit_log 无成功执行记录）")
        return "\n".join(lines)
    lines.append("\n[分组耗时 ms（query_domain × db_backend）]")
    for item in report["groups"]:
        flag = "  样本不足，不标注慢案例" if item["insufficient_sample"] else ""
        lines.append(
            f"  {item['query_domain']:<20} {item['db_backend']:<12}"
            f" count={item['count']:>5}  P50={item['p50']}  P95={item['p95']}"
            f"  max={item['max']}{flag}"
        )
    lines.append("\n[慢案例（组内 sql_latency_ms > 历史 P95，仅标注不诊断）]")
    if not report["slow_cases"]:
        lines.append("  （无）")
    for case in report["slow_cases"]:
        lines.append(
            f"  {case['sql_latency_ms']:>10.1f} ms  {case['query_domain']:<20}"
            f" {case['db_backend']:<12} rows={case['row_count']}  "
            f"req={case['request_id']}  「{case['prompt']}」"
        )
    return "\n".join(lines)


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
    """命令行入口：只读审计库，输出意图漏斗或查询耗时分析报告（Read-only）。"""
    parser = argparse.ArgumentParser(description="审计离线分析（只读：意图漏斗 / 查询耗时）")
    parser.add_argument(
        "--db", default=None, help="审计 DuckDB 文件路径（默认 settings.AUDIT_DB_PATH）"
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报表")
    parser.add_argument(
        "--report",
        choices=("intent", "query_latency"),
        default="intent",
        help="报表类型：intent=意图分流漏斗（默认，兼容）；query_latency=查询耗时与慢案例（#23）",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=20,
        help="慢案例标注的最小分组样本量（仅 query_latency 报表，默认 20）",
    )
    args = parser.parse_args()

    from config import settings

    db_path = Path(args.db) if args.db else settings.AUDIT_DB_PATH
    if args.report == "query_latency":
        report = query_latency_report(db_path=db_path, min_samples=args.min_samples)
    else:
        report = routing_funnel_report(db_path=db_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render_latency(report) if args.report == "query_latency" else _render(report))


if __name__ == "__main__":
    main()
