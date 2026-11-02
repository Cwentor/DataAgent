"""CLI 入口：``python -m risk``（issue #26 最小闭环的定时化触发面）。

定时化形态为 CLI + 外部 cron 调度（数据层边界分析"边界宣言"既定形态），
本模块零业务逻辑，只做装配：规则加载 -> 巡检执行 -> 报告渲染落盘 -> 退出码。

退出码三态（外部监控可感知）：

- ``0``：巡检完成且全部规则绿；
- ``1``：存在红项，或存在未知项（数据缺失 / 权限不足 / 执行失败，需人工核查）；
- ``2``：规则加载失败或巡检无法执行（数仓缺失等）。

示例：``python -m risk --date 2025-12-31 --principal admin``
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from datetime import date
from pathlib import Path
from typing import Any

from config import settings
from risk.executor import STATUS_RED, run_inspection
from risk.report import render_report
from risk.rules import RiskRules, load_rules


def _override_current(rules: RiskRules, day: date) -> RiskRules:
    """以 CLI ``--date`` 统一覆盖全部规则的当期代表日（重放历史日用）。"""
    overridden = tuple(
        (
            dataclasses.replace(r, window=dataclasses.replace(r.window, current=day))
            if r.window.current != day
            else r
        )
        for r in rules.rules
    )
    return RiskRules(version=rules.version, rules=overridden)


def main(argv: list[str] | None = None, *, conn: Any | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="risk", description="风险巡检与风险报告生成（确定性，零 LLM）"
    )
    parser.add_argument(
        "--rules",
        default=str(settings.RISK_RULES_PATH),
        help="规则文件路径（默认 config/risk_rules.json）",
    )
    parser.add_argument(
        "--date", default=None, help="覆盖全部规则的当期代表日 YYYY-MM-DD（默认按规则登记值）"
    )
    parser.add_argument("--principal", default="admin", help="RLS 数据权限主体（默认 admin）")
    parser.add_argument(
        "--out",
        default=str(settings.RISK_REPORT_DIR),
        help="报告输出目录（默认 logs/risk_reports）",
    )
    parser.add_argument(
        "--workspace",
        default=str(settings.RISK_WORKSPACE_DIR),
        help="巡检索 Parquet 产物工作区（默认 logs/risk_workspace）",
    )
    parser.add_argument(
        "--no-audit", action="store_true", help="不写审计记录（默认写，复用 audit.jsonl 双写通道）"
    )
    args = parser.parse_args(argv)

    if conn is None and settings.DB_BACKEND == "duckdb" and not Path(settings.DB_PATH).exists():
        # 生产路径预检；测试经 conn 注入内存库时由调用方保证数据面
        print(
            f"[risk] 数仓不存在：{settings.DB_PATH}（先执行 python -m mock.init_duckdb）",
            file=sys.stderr,
        )
        return 2

    try:
        rules = load_rules(args.rules)
        if args.date:
            rules = _override_current(rules, date.fromisoformat(args.date))
    except (ValueError, OSError) as exc:
        print(f"[risk] 规则加载失败：{exc}", file=sys.stderr)
        return 2

    audit_store = None
    if not args.no_audit and settings.AUDIT_ENABLED:
        from audit.store import AuditStore

        audit_store = AuditStore(
            jsonl_path=settings.AUDIT_LOG_PATH,
            db_path=settings.AUDIT_DB_PATH,
        )

    result = run_inspection(
        rules,
        principal=args.principal,
        workspace=args.workspace,
        conn=conn,
        audit_store=audit_store,
    )
    report = render_report(result)

    report_date = max(f.rule.window.current for f in result.rules)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"risk_report_{report_date.strftime('%Y%m%d')}.md"
    report_path.write_text(report, encoding="utf-8")

    counts = result.status_counts
    print(
        f"[risk] 巡检完成：{len(result.rules)} 条规则，"
        f"红 {counts[STATUS_RED]} / 黄 {counts['yellow']} / 绿 {counts['green']} / "
        f"未知 {counts['unknown']}"
    )
    print(f"[risk] 报告已写入：{report_path}")
    if counts[STATUS_RED]:
        return 1
    if counts["unknown"]:
        # 未知项（数据缺失 / 权限不足 / 执行失败）同样需要人介入，监控不可静默
        print("[risk] 存在未知项，请人工核查报告", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
