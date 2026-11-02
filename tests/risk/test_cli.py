"""CLI 冒烟测试（python -m risk）：三态退出码 + 报告落盘 + 规则加载失败路径。"""

from pathlib import Path

from risk.__main__ import main

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEED_RULES = PROJECT_ROOT / "config" / "risk_rules.json"


def test_cli_success_writes_report(tmp_path, conn) -> None:
    out_dir = tmp_path / "reports"
    rc = main(
        [
            "--rules",
            str(SEED_RULES),
            "--date",
            "2025-12-31",
            "--out",
            str(out_dir),
            "--workspace",
            str(tmp_path / "ws"),
            "--no-audit",
        ],
        conn=conn,
    )
    assert rc in (0, 1)  # 种子规则在 mock 数据上的真实红黄绿分布
    reports = list(out_dir.glob("risk_report_*.md"))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8")
    assert report.startswith("# 风险巡检报告（2025-12-31）")
    assert "## 总体结论" in report
    assert "## 证据链附录" in report
    # parquet 产物落工作区（证据链可回查）
    inputs = list((tmp_path / "ws" / "inputs").glob("*.parquet"))
    assert inputs


def test_cli_red_exit_code(tmp_path, conn) -> None:
    """人为把阈值调到必红，验证退出码 1（外部监控可感知）。"""
    path = tmp_path / "rules_red.json"
    path.write_text(
        '{"version": 1, "rules": [{"key": "always_red", "title": "必红规则",'
        ' "metric": "gmv", "query_domain": "trade",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.01, "yellow": 0.001}}]}',
        encoding="utf-8",
    )
    rc = main(
        [
            "--rules",
            str(path),
            "--date",
            "2025-12-31",
            "--out",
            str(tmp_path / "r"),
            "--workspace",
            str(tmp_path / "ws"),
            "--no-audit",
        ],
        conn=conn,
    )
    assert rc == 1


def test_cli_bad_rules_exit_code_2(tmp_path, conn) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"version": 1, "rules": "not-a-list"}', encoding="utf-8")
    rc = main(
        [
            "--rules",
            str(path),
            "--out",
            str(tmp_path / "r"),
            "--no-audit",
        ],
        conn=conn,
    )
    assert rc == 2


def test_cli_bad_date_exit_code_2(tmp_path, conn) -> None:
    rc = main(
        [
            "--rules",
            str(SEED_RULES),
            "--date",
            "not-a-date",
            "--out",
            str(tmp_path / "r"),
            "--no-audit",
        ],
        conn=conn,
    )
    assert rc == 2
