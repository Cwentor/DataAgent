"""risk.rules 契约测试：fail-closed 校验全清单 + 窗口推算纯函数。"""

from datetime import date
from pathlib import Path

import pytest

from risk.rules import (
    load_rules,
    previous_bounds,
    window_bounds,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEED_RULES = PROJECT_ROOT / "config" / "risk_rules.json"

# 测试注入用白名单（隔离语义目录全局状态）
_METRICS = frozenset({"gmv", "refund_rate", "order_count"})
_FIELDS = frozenset({"province", "category_name", "order_time"})
_DOMAIN_END = date(2025, 12, 31)


def _write(tmp_path: Path, raw: str) -> Path:
    path = tmp_path / "rules.json"
    path.write_text(raw, encoding="utf-8")
    return path


def _load(path: Path):
    return load_rules(
        path, metric_keys=_METRICS, allowed_fields=_FIELDS, data_domain_end=_DOMAIN_END
    )


# --------------------------------------------------------------------------- #
# 正常加载
# --------------------------------------------------------------------------- #
def test_seed_rules_load(tmp_path: Path) -> None:
    """种子规则文件可加载且三条规则齐备（生产默认白名单）。"""
    rules = load_rules(SEED_RULES)
    assert rules.version == 1
    assert [r.key for r in rules.rules] == [
        "refund_rate_daily",
        "refund_rate_by_province",
        "gmv_dod_drop",
    ]
    by_key = {r.key: r for r in rules.rules}
    assert by_key["refund_rate_daily"].thresholds.red == pytest.approx(0.05)
    assert by_key["refund_rate_daily"].thresholds.yellow == pytest.approx(0.03)
    assert by_key["refund_rate_by_province"].dimension == "province"
    assert by_key["gmv_dod_drop"].comparison is not None
    assert by_key["gmv_dod_drop"].comparison.direction == "drop"


def test_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="规则文件不存在"):
        _load(tmp_path / "nope.json")


def test_invalid_json_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "{not json")
    with pytest.raises(ValueError, match="规则文件读取失败"):
        _load(path)


# --------------------------------------------------------------------------- #
# 契约结构校验
# --------------------------------------------------------------------------- #
def test_unknown_top_field_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"version": 1, "rules": [], "extra": 1}',
    )
    with pytest.raises(ValueError, match="契约外顶层字段"):
        _load(path)


def test_unknown_rule_field_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"version": 1, "rules": [{"key": "r1", "title": "t", "metric": "gmv",'
        ' "query_domain": "trade", "typo_field": 1,'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.1, "yellow": 0.05}}]}',
    )
    with pytest.raises(ValueError, match="契约外字段"):
        _load(path)


def test_unknown_metric_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"version": 1, "rules": [{"key": "r1", "title": "t", "metric": "nope",'
        ' "query_domain": "trade",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.1, "yellow": 0.05}}]}',
    )
    with pytest.raises(ValueError, match="未在语义目录 metrics\\[\\] 登记"):
        _load(path)


def test_unknown_query_domain_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"version": 1, "rules": [{"key": "r1", "title": "t", "metric": "gmv",'
        ' "query_domain": "logistics",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.1, "yellow": 0.05}}]}',
    )
    with pytest.raises(ValueError, match="query_domain"):
        _load(path)


def test_threshold_inversion_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"version": 1, "rules": [{"key": "r1", "title": "t", "metric": "gmv",'
        ' "query_domain": "trade",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.01, "yellow": 0.05}}]}',
    )
    with pytest.raises(ValueError, match="红黄倒挂"):
        _load(path)


def test_no_judgement_form_rejected(tmp_path: Path) -> None:
    """thresholds 与 comparison 全缺的规则无判定能力，拒绝。"""
    path = _write(
        tmp_path,
        '{"version": 1, "rules": [{"key": "r1", "title": "t", "metric": "gmv",'
        ' "query_domain": "trade",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"}}]}',
    )
    with pytest.raises(ValueError, match="至少配置一项"):
        _load(path)


def test_window_overflow_rejected(tmp_path: Path) -> None:
    """窗口越出数据域上界拒绝（严禁对不完整窗口出报告）。"""
    path = _write(
        tmp_path,
        '{"version": 1, "rules": [{"key": "r1", "title": "t", "metric": "gmv",'
        ' "query_domain": "trade",'
        ' "window": {"grain": "month", "current": "2026-01-15", "baseline": "previous"},'
        ' "thresholds": {"red": 0.1, "yellow": 0.05}}]}',
    )
    with pytest.raises(ValueError, match="越出数据域上界"):
        _load(path)


def test_bad_date_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"version": 1, "rules": [{"key": "r1", "title": "t", "metric": "gmv",'
        ' "query_domain": "trade",'
        ' "window": {"grain": "day", "current": "2025-13-40", "baseline": "previous"},'
        ' "thresholds": {"red": 0.1, "yellow": 0.05}}]}',
    )
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _load(path)


def test_duplicate_key_rejected(tmp_path: Path) -> None:
    rule = (
        '{"key": "r1", "title": "t", "metric": "gmv", "query_domain": "trade",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.1, "yellow": 0.05}}'
    )
    path = _write(tmp_path, '{"version": 1, "rules": [' + rule + "," + rule + "]}")
    with pytest.raises(ValueError, match="key 重复"):
        _load(path)


def test_unknown_dimension_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"version": 1, "rules": [{"key": "r1", "title": "t", "metric": "refund_rate",'
        ' "query_domain": "trade", "dimension": "not_a_field",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.1, "yellow": 0.05}}]}',
    )
    with pytest.raises(ValueError, match="未在语义目录 COLUMNS 登记"):
        _load(path)


def test_unknown_time_field_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"version": 1, "rules": [{"key": "r1", "title": "t", "metric": "gmv",'
        ' "query_domain": "trade", "time_field": "birthday",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.1, "yellow": 0.05}}]}',
    )
    with pytest.raises(ValueError, match="TIME_FIELDS"):
        _load(path)


def test_errors_aggregated(tmp_path: Path) -> None:
    """多条规则同时违规时报错聚合为一条（fail-closed 全清单）。"""
    bad_a = (
        '{"key": "a", "title": "t", "metric": "nope", "query_domain": "trade",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.1, "yellow": 0.05}}'
    )
    bad_b = (
        '{"key": "b", "title": "t", "metric": "gmv", "query_domain": "trade",'
        ' "window": {"grain": "day", "current": "2025-12-31", "baseline": "previous"},'
        ' "thresholds": {"red": 0.01, "yellow": 0.05}}'
    )
    path = _write(tmp_path, '{"version": 1, "rules": [' + bad_a + "," + bad_b + "]}")
    with pytest.raises(ValueError) as excinfo:
        _load(path)
    msg = str(excinfo.value)
    assert "rules[0]" in msg and "rules[1]" in msg


# --------------------------------------------------------------------------- #
# 窗口推算纯函数
# --------------------------------------------------------------------------- #
def test_window_bounds_day() -> None:
    assert window_bounds("day", date(2025, 12, 31)) == (
        date(2025, 12, 31),
        date(2026, 1, 1),
    )


def test_window_bounds_week_monday_anchor() -> None:
    """week 以周一对齐（与编译器 week 口径一致）。2025-12-31 是周三。"""
    start, end = window_bounds("week", date(2025, 12, 31))
    assert start == date(2025, 12, 29)  # 周一
    assert end == date(2026, 1, 5)  # 下周一


def test_window_bounds_month_quarter() -> None:
    assert window_bounds("month", date(2025, 12, 31)) == (
        date(2025, 12, 1),
        date(2026, 1, 1),
    )
    assert window_bounds("quarter", date(2025, 12, 31)) == (
        date(2025, 10, 1),
        date(2026, 1, 1),
    )


def test_previous_bounds_cross_year() -> None:
    """previous 推算跨年（12 月的上月、Q1 的上季、年初的上周）。"""
    assert previous_bounds("day", date(2026, 1, 1), date(2026, 1, 2)) == (
        date(2025, 12, 31),
        date(2026, 1, 1),
    )
    assert previous_bounds("month", date(2026, 1, 1), date(2026, 2, 1)) == (
        date(2025, 12, 1),
        date(2026, 1, 1),
    )
    assert previous_bounds("quarter", date(2026, 1, 1), date(2026, 4, 1)) == (
        date(2025, 10, 1),
        date(2026, 1, 1),
    )
    assert previous_bounds("week", date(2026, 1, 5), date(2026, 1, 12)) == (
        date(2025, 12, 29),
        date(2026, 1, 5),
    )
