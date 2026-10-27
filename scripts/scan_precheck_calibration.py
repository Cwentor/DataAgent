"""扫描行预检安全系数（SF）校准：估算偏差实测 + 跳过率矩阵 + 建议系数。

## 为什么需要这个脚本

两段式预检的放行条件是 `est × SF < MAX_SCAN_ROWS`，安全性完全依赖
「实测 / 估算 ≤ SF」这个经验性质。它是**数据分布相关**的，不能拍脑袋定死：

- golden 35 条（28 单轮 + 7 多轮 turn）实测最大低估 3.21×；
- 但同一张表上 `WHERE col > 0` 全命中就是 **5.0×**——DuckDB 对 `>` / `<>` /
  `CAST` 类比较过滤统一套 20% 默认选择率，与真实命中率无关；
- 所以 SF 必须按「最坏形态」定，而不是按 golden 定。

设计见 `docs/designs/2026-10-27-scan-precheck-two-stage-design.md` §0 与 D2。

## 两类样本

1. **golden**：`eval/golden_dataset.json` 的全部可编译 DSL（真实问法，走真实编译器）；
2. **合成过滤族**：从语义目录里挑数值字段，用 DSL 的 `gt` / `ne` 算子构造
   「几乎全命中」的过滤（`gt MIN-1`、`ne MIN-1`），时间窗口开到全域避免干扰。
   这一族是 5× 低估的来源，**校准集少了它就必然把 SF 定小**（v1 评审 P0-3）。

## 用法

    python scripts/scan_precheck_calibration.py                  # 全量（golden + 合成）
    python scripts/scan_precheck_calibration.py --no-synthetic   # 只跑 golden
    python scripts/scan_precheck_calibration.py --verbose        # 逐条明细
    python scripts/scan_precheck_calibration.py --json logs/sf.json

退出码：0 = 正常产出；1 = golden 一条都编译不出来或库不可读。

## 与生产代码的关系

估算解析器直接复用生产实现 `exec.engine.plan_text.parse_estimated_scan_rows`
（设计 §D3），本脚本不再各写一份——校准口径必须与线上判定口径同源，
否则"校准通过"没有意义。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 允许直接以 `python scripts/scan_precheck_calibration.py` 运行（仓库根加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb

from compiler.sql_compiler import compile_sql
from config import settings
from exec.engine.plan_text import parse_estimated_scan_rows, parse_scan_rows
from semantic.catalog import COLUMNS
from semantic.dsl_schema import QueryDSL

# --------------------------------------------------------------------------- #
# 采样与测量
# --------------------------------------------------------------------------- #


@dataclass
class Case:
    """一条被测查询的两种计划口径与耗时。"""

    case_id: str
    source: str  # golden / synthetic
    est: int
    est_op: str
    actual: int
    measurable: bool
    t_explain_ms: float
    t_analyze_ms: float
    t_exec_ms: float


def _plan_text(conn: duckdb.DuckDBPyConnection, sql: str, analyze: bool) -> str:
    prefix = "EXPLAIN ANALYZE " if analyze else "EXPLAIN "
    rows = conn.execute(prefix + sql).fetchall()
    if not rows:
        return ""
    return str(rows[0][1]) if len(rows[0]) > 1 else str(rows[0][0])


def _measure(conn: duckdb.DuckDBPyConnection, case_id: str, source: str, sql: str) -> Case:
    started = time.perf_counter()
    est_text = _plan_text(conn, sql, analyze=False)
    t_explain = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    actual_text = _plan_text(conn, sql, analyze=True)
    t_analyze = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    conn.execute(sql).fetchall()
    t_exec = (time.perf_counter() - started) * 1000.0
    est, est_op = parse_estimated_scan_rows(est_text)
    return Case(
        case_id=case_id,
        source=source,
        est=est,
        est_op=est_op,
        actual=parse_scan_rows(actual_text),
        measurable=bool(est_op),
        t_explain_ms=t_explain,
        t_analyze_ms=t_analyze,
        t_exec_ms=t_exec,
    )


# --------------------------------------------------------------------------- #
# 样本构造
# --------------------------------------------------------------------------- #


def _golden_sql_list(golden_path: Path) -> list[tuple[str, str]]:
    """golden DSL → `(case_id, sql)`；多轮用例按 turn 展开（35 = 28 + 7）。"""
    items = json.loads(golden_path.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for item in items:
        if "dsl" in item:
            out.append((item["id"], compile_sql(QueryDSL.model_validate(item["dsl"]))))
        for index, turn in enumerate(item.get("turns", []), start=1):
            dsl = QueryDSL.model_validate(turn["dsl"])
            out.append((f"{item['id']}-T{index}", compile_sql(dsl)))
    return out


# 合成族只跑「有明确时间主轴」的表（DSL 的 time_filter 是必填项，字段必须落在该表上）
_TABLE_TIME_FIELD = {
    "order_detail": "order_time",
    "order_refund_info": "refund_time",
    "fact_page_view": "page_view_time",
    "fact_display": "display_time",
    "fact_start": "start_log_time",
    "fact_action": "action_time",
}

# 全域时间窗：让时间过滤不产生选择性，比较过滤才是唯一选择器
_WIDE_TIME_FILTER = {
    "granularity": "day",
    "range_type": "absolute",
    "relative": None,
    "absolute": {"start": "2021-01-01", "end": "2026-01-01"},
    "comparison": "none",
    "reference_date": None,
}


def _synthetic_sql_list(conn: duckdb.DuckDBPyConnection, limit: int) -> list[tuple[str, str]]:
    """构造「几乎全命中」的比较过滤族（`gt MIN-1` / `ne MIN-1`）。

    这两族正是 DuckDB 20% 默认选择率命中的形态，也是 5× 低估的来源。
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, meta in COLUMNS.items():
        table = meta.table
        time_field = _TABLE_TIME_FIELD.get(table)
        if time_field is None or meta.dtype not in ("int", "float"):
            continue
        try:
            row = conn.execute(f"SELECT MIN({meta.column}) FROM {table}").fetchone()
        except Exception:
            continue
        if not row or row[0] is None:
            continue
        low = row[0] - 1
        for operator in ("gt", "ne"):
            case_id = f"SYN-{table}.{meta.column}-{operator}"
            if case_id in seen:
                continue
            seen.add(case_id)
            dsl = {
                "metrics": [
                    {"kind": "aggregate", "field": name, "agg": "sum", "alias": "v"},
                ],
                "dimensions": [],
                "time_filter": {**_WIDE_TIME_FILTER, "time_field": time_field},
                "filters": [{"field": name, "operator": operator, "value": low}],
                "order_by": [],
                "having": [],
                "limit": 100,
                "fill_gaps": False,
                "top_n": None,
            }
            try:
                out.append((case_id, compile_sql(QueryDSL.model_validate(dsl))))
            except Exception:
                continue
            if len(out) >= limit:
                return out
    return out


# --------------------------------------------------------------------------- #
# 统计与报告
# --------------------------------------------------------------------------- #


def _ratio_stats(cases: list[Case]) -> dict[str, Any]:
    pairs = [(c.est, c.actual) for c in cases if c.est > 0 and c.actual > 0]
    if not pairs:
        return {"pairs": 0}
    under = [(est, act) for est, act in pairs if act > est]
    over = [(est, act) for est, act in pairs if act < est]
    return {
        "pairs": len(pairs),
        "under_count": len(under),
        "max_under": round(max((act / est for est, act in under), default=1.0), 2),
        "max_over": round(max((est / act for est, act in over), default=1.0), 2),
        "median_est_over_actual": round(statistics.median(est / act for est, act in pairs), 4),
        "unusable": sum(1 for c in cases if not c.measurable),
    }


def _skip_matrix(
    cases: list[Case], factors: list[int], thresholds: list[int]
) -> list[dict[str, Any]]:
    usable = [c.est for c in cases if c.measurable and c.est >= 1]
    rows: list[dict[str, Any]] = []
    for factor in factors:
        for threshold in thresholds:
            skip = sum(1 for est in usable if est * factor < threshold)
            rows.append(
                {
                    "safety_factor": factor,
                    "threshold": threshold,
                    "skip": skip,
                    "total": len(usable),
                }
            )
    return rows


def _recommend_factor(stats: dict[str, Any]) -> int:
    """推荐系数：2 的幂，且 ≥ 最大低估 × 4（4 倍余量，覆盖分布漂移与未来问法）。

    golden 单跑会得到 3.21× → 16；带合成族会得到 5.0× → 32（= 设计 D2 的默认值）。
    """
    target = max(stats.get("max_under", 1.0) * 4.0, 4.0)
    factor = 4
    while factor < target:
        factor *= 2
    return factor


def _print_case_table(cases: list[Case]) -> None:
    print(f"{'case':<28}{'est':>10}{'actual':>10}{'act/est':>9}  operator")
    for case in cases:
        ratio = round(case.actual / case.est, 2) if case.est else None
        print(
            f"{case.case_id:<28}{case.est:>10}{case.actual:>10}"
            f"{('-' if ratio is None else ratio):>9}  {case.est_op or '不可用'}"
        )


def _print_stats(title: str, stats: dict[str, Any]) -> None:
    print(f"\n== {title} ==")
    if stats["pairs"] == 0:
        print("无可比对样本")
        return
    print(f"可比对样本: {stats['pairs']}    估算不可用: {stats['unusable']}")
    print(
        f"低估条数: {stats['under_count']}    最大低估: {stats['max_under']}x"
        f"    最大高估: {stats['max_over']}x"
        f"    中位 est/actual: {stats['median_est_over_actual']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描行预检安全系数校准")
    parser.add_argument("--db", default=str(settings.DB_PATH), help="DuckDB 文件路径")
    parser.add_argument(
        "--golden",
        default=str(Path(__file__).resolve().parents[1] / "eval" / "golden_dataset.json"),
        help="golden 数据集路径",
    )
    parser.add_argument("--no-synthetic", action="store_true", help="只跑 golden，不造合成过滤族")
    parser.add_argument("--max-synthetic", type=int, default=40, help="合成族条数上限")
    parser.add_argument(
        "--safety-factors",
        default="4,8,32,64",
        help="跳过率矩阵的系数列表（逗号分隔）",
    )
    parser.add_argument(
        "--thresholds",
        default="10000000,1000000,100000",
        help="跳过率矩阵的 MAX_SCAN_ROWS 列表（逗号分隔）",
    )
    parser.add_argument("--verbose", action="store_true", help="打印逐条明细")
    parser.add_argument("--json", dest="json_out", default="", help="把结果写成 JSON 文件")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在: {db_path}", file=sys.stderr)
        return 1
    golden_path = Path(args.golden)
    if not golden_path.exists():
        print(f"golden 数据集不存在: {golden_path}", file=sys.stderr)
        return 1

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        golden_sqls = _golden_sql_list(golden_path)
        synthetic_sqls = [] if args.no_synthetic else _synthetic_sql_list(conn, args.max_synthetic)
        cases: list[Case] = []
        failures = 0
        for case_id, sql in golden_sqls:
            try:
                cases.append(_measure(conn, case_id, "golden", sql))
            except Exception as exc:
                failures += 1
                print(f"跳过 {case_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
        for case_id, sql in synthetic_sqls:
            try:
                cases.append(_measure(conn, case_id, "synthetic", sql))
            except Exception as exc:
                failures += 1
                print(f"跳过 {case_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        conn.close()

    if not cases:
        print("没有可测量样本（golden 全部编译或执行失败）", file=sys.stderr)
        return 1

    golden_cases = [c for c in cases if c.source == "golden"]
    synthetic_cases = [c for c in cases if c.source == "synthetic"]
    factors = [int(x) for x in args.safety_factors.split(",") if x.strip()]
    thresholds = [int(x) for x in args.thresholds.split(",") if x.strip()]

    print(f"数据库: {db_path}")
    print(
        f"样本: golden {len(golden_cases)} 条 / 合成 {len(synthetic_cases)} 条 / 失败 {failures} 条"
    )

    if args.verbose:
        _print_case_table(cases)

    golden_stats = _ratio_stats(golden_cases)
    synthetic_stats = _ratio_stats(synthetic_cases)
    overall_stats = _ratio_stats(cases)
    _print_stats("golden（真实问法）", golden_stats)
    if synthetic_cases:
        _print_stats("合成过滤族（全命中比较过滤）", synthetic_stats)
    _print_stats("合计", overall_stats)

    total_explain = sum(c.t_explain_ms for c in cases)
    total_analyze = sum(c.t_analyze_ms for c in cases)
    total_exec = sum(c.t_exec_ms for c in cases)
    print("\n== 耗时合计 ==")
    print(
        f"EXPLAIN {total_explain:.1f}ms / EXPLAIN ANALYZE {total_analyze:.1f}ms / 执行 {total_exec:.1f}ms"
    )
    print(
        f"现在(ANALYZE+执行) {total_analyze + total_exec:.1f}ms -> 两段式 {total_explain + total_exec:.1f}ms"
    )

    matrix = _skip_matrix(cases, factors, thresholds)
    print("\n== 跳过率矩阵（分母 = 估算可用的样本）==")
    header = "SF".ljust(6) + "".join(f"TH={t:<12}" for t in thresholds)
    print(header)
    for factor in factors:
        cells = "".join(
            f"{row['skip']}/{row['total']}".ljust(16)
            for row in matrix
            if row["safety_factor"] == factor
        )
        print(f"{factor:<6}" + cells)

    recommended = _recommend_factor(overall_stats)
    print(f"\n== 建议 SCAN_PRECHECK_SAFETY_FACTOR = {recommended} ==")
    print(
        f"依据: 最大低估 {overall_stats.get('max_under', 1.0)}x × 4 倍余量 = "
        f"{overall_stats.get('max_under', 1.0) * 4:.1f}，向上取 2 的幂"
    )
    for row in matrix:
        if row["safety_factor"] == recommended:
            print(f"  阈值 {row['threshold']}: 可跳过 {row['skip']}/{row['total']}")

    if args.json_out:
        payload = {
            "db": str(db_path),
            "samples": {
                "golden": len(golden_cases),
                "synthetic": len(synthetic_cases),
                "failures": failures,
            },
            "stats": {
                "golden": golden_stats,
                "synthetic": synthetic_stats,
                "overall": overall_stats,
            },
            "timing_ms": {
                "explain": round(total_explain, 1),
                "analyze": round(total_analyze, 1),
                "exec": round(total_exec, 1),
            },
            "skip_matrix": matrix,
            "recommended_safety_factor": recommended,
            "cases": [
                {
                    "id": c.case_id,
                    "source": c.source,
                    "est": c.est,
                    "operator": c.est_op,
                    "actual": c.actual,
                }
                for c in cases
            ],
        }
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON 已写入 {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
