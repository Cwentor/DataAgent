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

    def insert(self, table: str, data, column_names=None, column_oriented=False, **kw):
        assert column_oriented, "建仓灌数走列式原生通道"
        # dtype=object 保真：否则 pandas 会把含 None 的时间列推断为 datetime64
        # 并把 None 变成 NaT，掩盖适配器的空值清洗契约
        df = pd.DataFrame(
            {n: pd.Series(list(c), dtype=object) for n, c in zip(column_names, data, strict=False)}
        )
        self.inserted.append((table, df))
        return len(df)

    def insert_df(self, table: str, df, **kw):  # pragma: no cover - 兜底通道
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
    """具名 DataFrame 列名即物理列名（DESCRIBE 仅用于取列类型，不改列名）。"""
    adapter = ClickHouseAdapter()
    client = FakeClient(_ok_script())
    df = pd.DataFrame({"a": [1, 2], "b": [None, "x"]})
    adapter.insert_dataframe(client, "t1", df)
    assert list(client.inserted[0][1].columns) == ["a", "b"]


def _typed_script(describe_rows) -> dict[str, FakeResult]:
    script = _ok_script()
    script["DESCRIBE TABLE `t1`"] = FakeResult(describe_rows, ["name", "type", "default_type"])
    return script


def test_insert_dataframe_coerces_date_strings():
    """Date 列字符串 -> date 实例（驱动 Date 写入不接受字符串，CI 建仓实锤）。"""
    from datetime import date

    adapter = ClickHouseAdapter()
    client = FakeClient(_typed_script([("birthday", "Nullable(Date)", "")]))
    df = pd.DataFrame({"birthday": ["1990-01-02", None, "2000-12-31"]})
    adapter.insert_dataframe(client, "t1", df)
    out = client.inserted[0][1]["birthday"].tolist()
    assert out == [date(1990, 1, 2), None, date(2000, 12, 31)]


def test_insert_dataframe_coerces_datetime_strings():
    """DateTime 列字符串 -> datetime 实例（驱动 DateTime 写入调 .timestamp()）。"""
    from datetime import datetime

    adapter = ClickHouseAdapter()
    client = FakeClient(_typed_script([("ts", "Nullable(DateTime)", "")]))
    df = pd.DataFrame({"ts": ["2025-01-02 03:04:05", "2025-06-07"]})
    adapter.insert_dataframe(client, "t1", df)
    out = client.inserted[0][1]["ts"].tolist()
    assert out == [datetime(2025, 1, 2, 3, 4, 5), datetime(2025, 6, 7)]


def test_insert_dataframe_keeps_unparsable_and_other_types():
    """解析失败与非时间列保持原值：不猜测、由驱动诚实报错。"""
    adapter = ClickHouseAdapter()
    client = FakeClient(
        _typed_script([("d", "Date", ""), ("amount", "Nullable(Decimal(16, 2))", "")])
    )
    df = pd.DataFrame({"d": ["not-a-date"], "amount": [12.5]})
    adapter.insert_dataframe(client, "t1", df)
    out = client.inserted[0][1]
    assert out["d"].tolist() == ["not-a-date"]
    assert out["amount"].tolist() == [12.5]


def test_insert_dataframe_keeps_none_in_datetime_column():
    """赋列不得让 pandas 重新推断成 datetime64：None 必须保持 None 而非 NaT。

    回归：CI pandas 3.1.0 下 `df[col] = [datetime, None]` 重新推断为
    datetime64，None 变 NaT；驱动 `if x else 0` 判 NaT 为真后调 .timestamp()
    抛 `ValueError: NaTType does not support timestamp`（coupon_info.expire_time 实锤）。
    """
    from datetime import datetime

    adapter = ClickHouseAdapter()
    client = FakeClient(_typed_script([("expire_time", "Nullable(DateTime)", "")]))
    df = pd.DataFrame({"expire_time": [datetime(2023, 12, 13, 1, 15, 58), None]})
    adapter.insert_dataframe(client, "t1", df)
    out = client.inserted[0][1]["expire_time"].tolist()
    assert out[0] == datetime(2023, 12, 13, 1, 15, 58)
    assert out[1] is None


def test_is_null_covers_pandas_singletons():
    from exec.engine.clickhouse_adapter import _is_null

    assert _is_null(None) is True
    assert _is_null(float("nan")) is True
    assert _is_null(pd.NaT) is True
    assert _is_null(pd.NA) is True
    assert _is_null(datetime(2025, 1, 1)) is False
    assert _is_null("") is False


def test_base_ch_type_strips_wrappers():
    from exec.engine.clickhouse_adapter import _base_ch_type

    assert _base_ch_type("Nullable(Date)") == "Date"
    assert _base_ch_type("LowCardinality(Nullable(String))") == "String"
    assert _base_ch_type("Nullable(Decimal(16, 2))") == "Decimal(16, 2)"
    assert _base_ch_type("") == ""


def test_registry_clickhouse():
    from exec.engine import get_adapter
    from exec.engine.clickhouse_adapter import ClickHouseAdapter as Adapter

    assert isinstance(get_adapter("clickhouse"), Adapter)


def test_timeout_and_scan_limit_constants():
    assert CH_TIMEOUT_ERROR_CODE == 159
    assert CH_SCAN_LIMIT_ERROR_CODE == 131
