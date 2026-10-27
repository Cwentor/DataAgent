"""两段式扫描行预检的判定链用例（二十六期 T4/T5）。

覆盖设计 §6 的 12 条验收：估算放行 / 估算不够小走实测 / 退化估算回退 /
告警额度不被第一段污染 / 缓存只含实测 / `scan_rows_estimated` 三分 / 采样复核。

用 `FakeAdapter` 而不是真实 DuckDB 的原因：这里要验的是**判定链**（谁被调用、
缓存写没写、告警发没发），真实引擎只会让调用序列变模糊。真实计划文本的解析
（并排布局 / `COLUMN_DATA_SCAN` / `EMPTY_RESULT`）由
`tests/exec/test_plan_text_estimate.py` 与 `tests/engine/test_duckdb_adapter.py` 守。
"""

from __future__ import annotations

import logging

import duckdb
import pytest

from config import settings
from exec.engine.base import ScanPrecheck
from exec.guards import (
    MaxRowsScannedExceeded,
    cached_scan_rows,
    clear_scan_cache,
    execute_sql,
    reset_unmeasurable_warnings,
)


class _BaseFakeAdapter:
    """记录调用序列的假适配器基类（不实现 `explain_scan_estimate`）。"""

    name = "fake"
    dialect = "duckdb"

    def __init__(self, exact: tuple[int, bool] = (0, True)) -> None:
        self.calls: list[str] = []
        self.exact = exact

    def explain_scan_rows(self, conn, sql, timeout_ms):
        self.calls.append("exact")
        return self.exact

    def run_query(self, conn, sql):
        self.calls.append("run")
        return ["v"], [[1]]

    def cancel(self, conn):
        return None

    def engine_error(self, exc):
        return None

    @property
    def estimate_calls(self) -> int:
        return self.calls.count("estimate")


class FakeAdapter(_BaseFakeAdapter):
    """实现了 `explain_scan_estimate` 的适配器（DuckDB 形态）。"""

    def __init__(self, est: ScanPrecheck, exact: tuple[int, bool] = (0, True)) -> None:
        super().__init__(exact=exact)
        self.est = est

    def explain_scan_estimate(self, conn, sql, timeout_ms):
        self.calls.append("estimate")
        if isinstance(self.est, Exception):
            raise self.est
        return self.est


class LegacyAdapter(_BaseFakeAdapter):
    """未实现 `explain_scan_estimate`（CH/Hive/MySQL/PG 的真实形态）。"""


@pytest.fixture(autouse=True)
def _clean_state():
    """缓存与告警去重都是进程级全局状态，逐用例清空避免串味。"""
    clear_scan_cache()
    reset_unmeasurable_warnings()
    yield
    clear_scan_cache()
    reset_unmeasurable_warnings()


def _usable(rows: int, operator: str = "SEQ_SCAN") -> ScanPrecheck:
    return ScanPrecheck(rows=rows, measurable=True, estimated=True, operator=operator)


def test_estimate_below_threshold_skips_exact(monkeypatch):
    """用例 1：估算远低于阈值 → 只发 EXPLAIN，EXPLAIN ANALYZE 一次都不发。"""
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 0.0)
    adapter = FakeAdapter(est=_usable(1000), exact=(999_999, True))

    result = execute_sql(object(), "SELECT v FROM t", max_scan_rows=1_000_000, adapter=adapter)

    assert adapter.calls == ["estimate", "run"]
    assert result.scan_rows == 1000
    assert result.scan_rows_estimated is True


def test_estimate_near_threshold_runs_exact_and_fuses(monkeypatch):
    """用例 2：估算不够小 → 两条都发，熔断判定用实测值。"""
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 0.0)
    adapter = FakeAdapter(est=_usable(40_000), exact=(2_000_000, True))

    with pytest.raises(MaxRowsScannedExceeded):
        execute_sql(object(), "SELECT v FROM t", max_scan_rows=1_000_000, adapter=adapter)

    assert adapter.calls == ["estimate", "exact"]
    assert "run" not in adapter.calls


def test_estimate_far_below_but_exact_over_limit_is_still_fused(monkeypatch):
    """估算放行的安全性边界：只有 est×SF < 阈值才放行，否则实测说话。"""
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 0.0)
    monkeypatch.setattr(settings, "SCAN_PRECHECK_SAFETY_FACTOR", 32)
    # 100_000 × 32 = 3.2M ≥ 1M → 不放行；实测 2M 超限 → 熔断
    adapter = FakeAdapter(est=_usable(100_000), exact=(2_000_000, True))

    with pytest.raises(MaxRowsScannedExceeded):
        execute_sql(object(), "SELECT v FROM t", max_scan_rows=1_000_000, adapter=adapter)

    assert adapter.calls == ["estimate", "exact"]


def test_real_count_star_does_not_short_circuit(tmp_path):
    """用例 3：真实 `count(*)` 走元数据快路径（`COLUMN_DATA_SCAN ~1 row`），必须走实测。

    文件库 + 重开是必要条件：内存表或刚建的表 `count(*)` 会老实 `SEQ_SCAN`，
    测不出目标形态（见 `tests/engine/test_duckdb_adapter.py` 的 `big_conn` 注释）。

    阈值取默认的 1000 万：若那个 `~1 row` 被当估算用，`1 × 32 < 1000 万` 就会放行，
    本用例的 `scan_rows == 0`（实测不可计量）与 `estimated is False` 会同时失败。
    """
    from exec.engine.duckdb_adapter import DuckDbAdapter

    db_path = tmp_path / "count.duckdb"
    writer = duckdb.connect(str(db_path))
    writer.execute("CREATE TABLE t AS SELECT i AS id FROM range(100000) tbl(i)")
    writer.close()
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        result = execute_sql(
            conn,
            "SELECT count(*) FROM t",
            max_scan_rows=settings.MAX_SCAN_ROWS,
            adapter=DuckDbAdapter(str(db_path)),
        )
    finally:
        conn.close()

    assert result.rows == [[100000]]
    assert result.scan_rows_estimated is False
    assert result.scan_rows == 0  # 实测口径拿不到算子行数（元数据快路径）


@pytest.mark.parametrize(
    "est",
    [
        # 用例 4：EMPTY_RESULT / 无白名单扫描算子（operator 为空）
        ScanPrecheck(rows=0, measurable=False, estimated=True, operator=""),
        # 用例 5：裸 `N rows`（无 `~`）解析结果即 rows=0
        ScanPrecheck(rows=0, measurable=False, estimated=True, operator="SEQ_SCAN"),
        # 用例 6：`~0 rows` 按不可信处理
        ScanPrecheck(rows=0, measurable=True, estimated=True, operator="SEQ_SCAN"),
        # 用例 7：未知算子 fail-closed（算子名不在白名单 → operator 为空）
        ScanPrecheck(rows=5, measurable=True, estimated=True, operator=""),
        # 非估算口径的返回也不许触发跳过
        ScanPrecheck(rows=5, measurable=True, estimated=False, operator="SEQ_SCAN"),
    ],
)
def test_unusable_estimate_falls_back_to_exact(monkeypatch, est):
    """用例 4-7：不可用的估算一律回退实测，绝不"拿不到就当 0 行放行"。"""
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 0.0)
    adapter = FakeAdapter(est=est, exact=(7, True))

    result = execute_sql(object(), "SELECT v FROM t", max_scan_rows=1_000_000, adapter=adapter)

    assert adapter.calls == ["estimate", "exact", "run"]
    assert result.scan_rows == 7
    assert result.scan_rows_estimated is False


def test_both_stages_unmeasurable_warns_and_skips_cache(monkeypatch, caplog):
    """用例 8：两段都不可计量 → measurable=False、不写缓存、告警一次。"""
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 0.0)
    adapter = FakeAdapter(
        est=ScanPrecheck(rows=0, measurable=False, estimated=True, operator=""),
        exact=(0, False),
    )

    with caplog.at_level(logging.WARNING, logger="exec.guards"):
        execute_sql(object(), "SELECT v FROM t", max_scan_rows=1_000_000, adapter=adapter)

    assert cached_scan_rows("SELECT v FROM t", scope="fake|") is None
    assert any("扫描行数预检不可计量" in r.message for r in caplog.records)


def test_first_stage_failure_does_not_consume_warning_quota(monkeypatch, caplog):
    """用例 9：第一段解析失败不得消耗"不可计量"告警额度。

    变异验证：若第一段也调 `_warn_unmeasurable_once`，第二条查询的 WARNING 就没了，
    本用例必挂——这正是 duckdb 计划格式变更时告警哑掉的复现路径。
    """
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 0.0)

    # 第一次：第一段不可用（EMPTY_RESULT 形态），第二段可计量 → 不该有任何告警
    ok_adapter = FakeAdapter(
        est=ScanPrecheck(rows=0, measurable=False, estimated=True, operator=""),
        exact=(10, True),
    )
    with caplog.at_level(logging.WARNING, logger="exec.guards"):
        execute_sql(
            object(), "SELECT v FROM t WHERE 1=1", max_scan_rows=1_000_000, adapter=ok_adapter
        )
    assert not [r for r in caplog.records if "不可计量" in r.message]

    # 第二次：真正不可计量 → 必须仍然 WARNING（额度没被第一条吃掉）
    bad_adapter = FakeAdapter(
        est=ScanPrecheck(rows=0, measurable=False, estimated=True, operator=""),
        exact=(0, False),
    )
    with caplog.at_level(logging.WARNING, logger="exec.guards"):
        execute_sql(
            object(), "SELECT v FROM t WHERE 2=2", max_scan_rows=1_000_000, adapter=bad_adapter
        )

    warnings = [r for r in caplog.records if "不可计量" in r.message]
    assert len(warnings) == 1, [r.message for r in caplog.records]


def test_cache_never_stores_estimate(monkeypatch):
    """用例 10：缓存只含实测值——连续两次跳过同一 SQL，第二次仍走第一段。"""
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 0.0)
    adapter = FakeAdapter(est=_usable(1000), exact=(999_999, True))

    for _ in range(2):
        result = execute_sql(object(), "SELECT v FROM t", max_scan_rows=1_000_000, adapter=adapter)
        assert result.scan_rows_estimated is True

    assert adapter.estimate_calls == 2  # 第二次没有因为缓存变成实测
    assert adapter.calls.count("exact") == 0
    assert cached_scan_rows("SELECT v FROM t", scope="fake|") is None


def test_legacy_adapter_without_estimate_uses_exact_path(monkeypatch):
    """未实现 `explain_scan_estimate` 的 adapter（CH/Hive/MySQL/PG）行为与今天一致。"""
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 1.0)
    adapter = LegacyAdapter(exact=(12, True))

    result = execute_sql(object(), "SELECT v FROM t", max_scan_rows=1_000_000, adapter=adapter)

    assert adapter.calls == ["exact", "run"]
    assert result.scan_rows == 12
    assert result.scan_rows_estimated is False
    assert cached_scan_rows("SELECT v FROM t", scope="fake|") == 12


def test_settings_defaults():
    """三项配置的默认值（`.env.example` / `settings.py` 必须一致）。"""
    assert settings.SCAN_PRECHECK_TWO_STAGE == 1
    assert settings.SCAN_PRECHECK_SAFETY_FACTOR == 32
    assert settings.SCAN_PRECHECK_AUDIT_RATE == pytest.approx(0.01)


# --------------------------------------------------------------------------- #
# T5：采样复核 + 开关回退（用例 11-12）
# --------------------------------------------------------------------------- #


def test_switch_off_restores_legacy_sequence(monkeypatch):
    """用例 11：`SCAN_PRECHECK_TWO_STAGE=0` → 不调新方法、`scan_rows_estimated` 恒 False。"""
    monkeypatch.setattr(settings, "SCAN_PRECHECK_TWO_STAGE", 0)
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 1.0)  # 采样也不该生效
    adapter = FakeAdapter(est=_usable(1000), exact=(12, True))

    result = execute_sql(object(), "SELECT v FROM t", max_scan_rows=1_000_000, adapter=adapter)

    assert adapter.calls == ["exact", "run"]
    assert adapter.estimate_calls == 0
    assert result.scan_rows == 12
    assert result.scan_rows_estimated is False


def test_sample_hit_forces_exact_and_fuses(monkeypatch, caplog):
    """用例 12：采样命中 → 强制实测、按实测熔断（不是"看一眼就算了"）。

    构造：估算 1000 本来会被放行（1000 × 32 < 1000 万），但实测 200 万超阈值。
    采样必须让熔断生效，否则采样窗口反而是熔断缺口。
    """
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 1.0)
    adapter = FakeAdapter(est=_usable(1000), exact=(2_000_000, True))

    with caplog.at_level(logging.INFO, logger="exec.guards"):
        with pytest.raises(MaxRowsScannedExceeded):
            execute_sql(object(), "SELECT v FROM t", max_scan_rows=1_000_000, adapter=adapter)

    assert adapter.calls == ["estimate", "exact"]
    drift = [r for r in caplog.records if "估算偏差" in r.message]
    assert drift and drift[0].levelno == logging.WARNING
    assert getattr(drift[0], "ratio", None) == pytest.approx(2000.0)


def test_sample_hit_within_factor_logs_info(monkeypatch, caplog):
    """采样命中但偏差在 SF 以内 → INFO（不误报 WARNING 淹没日志）。"""
    monkeypatch.setattr(settings, "SCAN_PRECHECK_AUDIT_RATE", 1.0)
    adapter = FakeAdapter(est=_usable(1000), exact=(2000, True))

    with caplog.at_level(logging.INFO, logger="exec.guards"):
        result = execute_sql(object(), "SELECT v FROM t", max_scan_rows=10_000_000, adapter=adapter)

    assert result.scan_rows == 2000
    assert result.scan_rows_estimated is False
    drift = [r for r in caplog.records if "估算偏差" in r.message]
    assert drift and drift[0].levelno == logging.INFO
