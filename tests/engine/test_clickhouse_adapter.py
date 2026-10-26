"""ClickHouse adapter 纯逻辑单测（二十三期 spec D2-D5）。

驱动已实测（clickhouse-connect 0.7+）：Client 无 cancel 方法（cancel 恒空操作
+ 服务端 max_execution_time 自限）；异常无结构化 code 属性（错误码嵌在报错
文本 "code: N"，正则提取归一）。本文件不依赖真实 CH 服务——harden/归一/灌数
清洗用 Fake client 锁行为；服务级由 CI clickhouse-parity 承担。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from exec.engine.clickhouse_adapter import (
    CH_SCAN_LIMIT_ERROR_CODE,
    CH_TIMEOUT_ERROR_CODE,
    HARDEN_REQUIRED_SETTINGS,
    ClickHouseAdapter,
    parse_ch_error_code,
)


class FakeChError(Exception):
    """伪造 CH 驱动异常基类（模拟 clickhouse_connect.driver.exceptions.Error）。"""


def _err(message: str) -> Exception:
    return FakeChError(message)


class FakeResult:
    def __init__(self, rows, columns):
        self.result_rows = rows
        self.column_names = columns


class FakeClient:
    """按脚本回放 query 结果的假客户端（harden/DESCRIBE 校验用）。"""

    def __init__(self, script: dict[str, FakeResult]):
        self._script = script
        self.inserted: list[tuple[str, pd.DataFrame]] = []

    def query(self, sql: str, *a, **kw):
        return self._script[sql]

    def insert_df(self, table: str, df, **kw):
        self.inserted.append((table, df))
        return len(df)

    def close(self) -> None:  # pragma: no cover - 接口占位
        pass


_HARDEN_SQL = (
    "SELECT name, value FROM system.settings "
    "WHERE name IN ('readonly', 'max_execution_time', 'max_rows_to_read')"
)


def _ok_script(readonly="1", met="30", mrr="10000000") -> dict[str, FakeResult]:
    return {
        _HARDEN_SQL: FakeResult(
            [
                ("readonly", readonly),
                ("max_execution_time", met),
                ("max_rows_to_read", mrr),
            ],
            ["name", "value"],
        ),
        "DESCRIBE TABLE `t1`": FakeResult(
            [("a", ""), ("b", ""), ("c", "")], ["name", "type", "default_type"]
        ),
    }


def test_parse_ch_error_code():
    assert parse_ch_error_code("Received ClickHouse exception, code: 159, message: x") == 159
    assert parse_ch_error_code("no code here") is None


def test_engine_error_timeout_159_normalized(monkeypatch):
    from exec.errors import QueryTimeoutError

    monkeypatch.setattr("exec.engine.clickhouse_adapter._CH_ERROR_BASE", FakeChError)
    adapter = ClickHouseAdapter()
    normalized = adapter.engine_error(
        _err("Received ClickHouse exception, code: 159, message: timeout")
    )
    assert isinstance(normalized, QueryTimeoutError)


def test_engine_error_scan_limit_131_normalized(monkeypatch):
    from exec.errors import SqlExecutionError

    monkeypatch.setattr("exec.engine.clickhouse_adapter._CH_ERROR_BASE", FakeChError)
    adapter = ClickHouseAdapter()
    normalized = adapter.engine_error(
        _err(f"Received ClickHouse exception, code: {CH_SCAN_LIMIT_ERROR_CODE}, message: limit")
    )
    assert isinstance(normalized, SqlExecutionError)
    assert "max_rows_to_read" in str(normalized)


def test_engine_error_without_code_stays_generic(monkeypatch):
    """错误码文本缺失/格式漂移 → 普通错误（Review Focus #1，严禁误归一）。"""
    from exec.errors import SqlExecutionError

    monkeypatch.setattr("exec.engine.clickhouse_adapter._CH_ERROR_BASE", FakeChError)
    adapter = ClickHouseAdapter()
    normalized = adapter.engine_error(_err("some drift message without code"))
    assert type(normalized) is SqlExecutionError


def test_engine_error_non_ch_exception_returns_none(monkeypatch):
    monkeypatch.setattr("exec.engine.clickhouse_adapter._CH_ERROR_BASE", FakeChError)
    assert ClickHouseAdapter().engine_error(ValueError("x")) is None


def test_harden_pass_when_profile_matches_budget():
    adapter = ClickHouseAdapter()
    adapter.harden(FakeClient(_ok_script()))  # 不抛即通过


@pytest.mark.parametrize(
    "script",
    [
        # 缺设置项
        {
            _HARDEN_SQL: FakeResult([("readonly", "1")], ["name", "value"]),
        },
        # readonly < 1
        _ok_script(readonly="0"),
        # 预算不一致（部署漂移）
        _ok_script(mrr="12345"),
    ],
)
def test_harden_fail_closed_matrix(script):
    from security.errors import SecurityError

    adapter = ClickHouseAdapter()
    with pytest.raises(SecurityError):
        adapter.harden(FakeClient(script))


def test_harden_budget_consistent_with_settings():
    """profile 期望值必须与 settings 预算同源（防单测与部署双向漂移）。"""
    from config import settings

    expected = HARDEN_REQUIRED_SETTINGS()
    assert expected["max_rows_to_read"] == settings.MAX_SCAN_ROWS
    assert expected["max_execution_time"] == settings.QUERY_TIMEOUT_MS // 1000
    assert expected["readonly"] == 1


def test_explain_scan_rows_not_measurable():
    """CH 无 EXPLAIN ANALYZE：诚实返回 (0, False)，warning 留痕由护栏框架承接。"""
    assert ClickHouseAdapter().explain_scan_rows(object(), "SELECT 1", 1000) == (0, False)


def test_cancel_is_noop():
    """驱动实测无 cancel 方法：空操作 + 服务端 max_execution_time 自限（spec D5）。"""
    assert ClickHouseAdapter().cancel(object()) is None


def test_insert_dataframe_unnamed_columns_and_null_cleaning():
    """无列名 DataFrame 经 DESCRIBE 补物理列名；NaN/NaT/None 统一清洗（Review Focus #4）。"""
    adapter = ClickHouseAdapter()
    client = FakeClient(_ok_script())
    df = pd.DataFrame(
        [(1, None, datetime(2025, 1, 1)), (2, float("nan"), pd.NaT)],
    )
    adapter.insert_dataframe(client, "t1", df)
    table, out = client.inserted[0]
    assert table == "t1"
    assert list(out.columns) == ["a", "b", "c"]  # DESCRIBE 补名（位置对应）
    # 空值统一清洗为 None（NaN/NaT/None 三类全覆盖）
    assert out.iloc[0, 1] is None
    assert out.iloc[1, 1] is None
    assert out.iloc[1, 2] is None
    assert int(out.isna().sum().sum()) == 3


def test_insert_dataframe_named_columns_untouched():
    """具名 DataFrame 不做 DESCRIBE（列名即物理列名，建仓灌数快路径）。"""
    adapter = ClickHouseAdapter()
    client = FakeClient(_ok_script())
    df = pd.DataFrame({"a": [1, 2], "b": [None, "x"]})
    adapter.insert_dataframe(client, "t1", df)
    assert list(client.inserted[0][1].columns) == ["a", "b"]


def test_registry_clickhouse():
    from exec.engine import get_adapter
    from exec.engine.clickhouse_adapter import ClickHouseAdapter as Adapter

    assert isinstance(get_adapter("clickhouse"), Adapter)


def test_timeout_and_scan_limit_constants():
    assert CH_TIMEOUT_ERROR_CODE == 159
    assert CH_SCAN_LIMIT_ERROR_CODE == 131
