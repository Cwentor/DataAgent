"""Hive adapter 纯逻辑单测（二十五期 spec D2-D5/D10/D11）。

驱动已实测（PyHive 0.7.0 + thrift 0.25.0，apache/hive:4.0.0 容器）：同步执行
模型无可靠取消通道（cancel 恒空操作）；EXPLAIN 无 actual rows（扫描预检诚实
不可计量）；异常无结构化错误码（TStatus 文本）。本文件不依赖真实 Hive 服务
——归一/harden/DESCRIBE 解析/CSV 字节用 Fake 连接锁定；服务级由 CI
hive-parity 承担。
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from exec.engine import hive_adapter
from exec.engine.hive_adapter import (
    CSV_FIELD_SEP,
    CSV_NULL_MARK,
    HIVE_TIMEOUT_EXTRA_SECONDS,
    HiveAdapter,
    cell_to_csv_text,
    parse_describe_rows,
)


class FakeHiveError(Exception):
    """伪造 PyHive 异常基类（模拟 pyhive.exc.Error）。"""


class FakeCursor:
    """按脚本回放 execute 结果的假游标（记录语句供 LOAD 断言）。"""

    def __init__(self, script: dict[str, list[tuple]]):
        self._script = script
        self.executed: list[str] = []

    def execute(self, sql: str, *a, **kw) -> None:
        self.executed.append(sql)

    def fetchall(self):
        return self._script.get(self.executed[-1], [])

    @property
    def description(self):
        return [("col",) for _ in (self._script.get(self.executed[-1], []) or [1])][:1]

    def close(self) -> None:  # pragma: no cover - 接口占位
        pass


class FakeConn:
    def __init__(self, script: dict[str, list[tuple]]):
        self.cursor_obj = FakeCursor(script)
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        self.cursors.append(self.cursor_obj)
        return self.cursor_obj


@pytest.fixture(autouse=True)
def _fake_error_base(monkeypatch):
    monkeypatch.setattr(hive_adapter, "_HIVE_ERROR_BASE", FakeHiveError)


def _adapter(tmp_path, server="/opt/dataagent_staging") -> HiveAdapter:
    return HiveAdapter(
        dsn={
            "host": "127.0.0.1",
            "port": 10000,
            "user": "dataagent_ro",
            "password": "",
            "database": "gmall",
        },
        staging_client=str(tmp_path / "staging"),
        staging_server=server,
    )


# --------------------------------------------------------------------------- #
# 模块级硬编码清单（RF-1 转义链路的单测锁定）
# --------------------------------------------------------------------------- #
def test_module_constants():
    assert HIVE_TIMEOUT_EXTRA_SECONDS == 5.0
    assert CSV_FIELD_SEP == "\x01"  # LazySimpleSerDe 原生默认分隔符
    assert CSV_NULL_MARK == "\\N"  # 两字符 \N（与 DDL 的 '\\N' SQL 双反斜杠对应）


# --------------------------------------------------------------------------- #
# cell_to_csv_text（D10 灌数文本面）
# --------------------------------------------------------------------------- #
def test_cell_to_csv_text_none_writes_null_mark():
    assert cell_to_csv_text(None) == "\\N"


def test_cell_to_csv_text_scalars():
    assert cell_to_csv_text(True) == "true"
    assert cell_to_csv_text(3) == "3"
    assert cell_to_csv_text(0.5) == "0.5"
    assert cell_to_csv_text(datetime(2025, 6, 15, 10, 30, 0)) == "2025-06-15 10:30:00"
    assert cell_to_csv_text(date(2025, 6, 15)) == "2025-06-15"
    assert cell_to_csv_text("alpha") == "alpha"


def test_cell_to_csv_text_rejects_separator_and_newline():
    # 文本值含分隔符/换行 fail-closed（LazySimpleSerDe 无引号语义，静默写入
    # 会切错列——RF-1 同源的确定性防线）
    with pytest.raises(ValueError):
        cell_to_csv_text("a\x01b")
    with pytest.raises(ValueError):
        cell_to_csv_text("a\nb")
    with pytest.raises(ValueError):
        cell_to_csv_text("a\rb")


# --------------------------------------------------------------------------- #
# parse_describe_rows（D11 元数据解析器，M5 形态锁定）
# --------------------------------------------------------------------------- #
def test_parse_describe_rows_basic_three_columns():
    rows = [("id", "int", ""), ("name", "string", ""), ("ts", "timestamp", "")]
    assert parse_describe_rows(rows) == [("id", "int"), ("name", "string"), ("ts", "timestamp")]


def test_parse_describe_rows_skips_headers_and_blanks():
    rows = [
        ("# col_name", "data_type", "comment"),
        ("", "", ""),
        ("id", "int", ""),
        ("", None, None),
        ("name", "string", ""),
    ]
    assert parse_describe_rows(rows) == [("id", "int"), ("name", "string")]


def test_parse_describe_rows_partition_section_fail_closed():
    # 数仓单一来源无分区表：出现分区段即 schema 漂移，fail-closed 拒绝
    rows = [("id", "int", ""), ("# Partition Information", "", ""), ("dt", "date", "")]
    with pytest.raises(ValueError, match="分区"):
        parse_describe_rows(rows)


# --------------------------------------------------------------------------- #
# engine_error（D5 归一：文本路线，同 CH 口径）
# --------------------------------------------------------------------------- #
def test_engine_error_timeout_error_normalized(tmp_path):
    adapter = _adapter(tmp_path)
    err = adapter.engine_error(TimeoutError("read timed out"))
    from exec.errors import QueryTimeoutError

    assert isinstance(err, QueryTimeoutError)


def test_engine_error_pyhive_timeout_text(tmp_path):
    adapter = _adapter(tmp_path)
    err = adapter.engine_error(FakeHiveError("Error while compiling statement: Timeout"))
    from exec.errors import QueryTimeoutError

    assert isinstance(err, QueryTimeoutError)


def test_engine_error_generic_to_sql_execution_error(tmp_path):
    adapter = _adapter(tmp_path)
    err = adapter.engine_error(
        FakeHiveError("Error while compiling statement: FAILED: SemanticException x")
    )
    assert err is not None and "SemanticException" in str(err)


def test_engine_error_foreign_exception_passthrough(tmp_path):
    adapter = _adapter(tmp_path)
    assert adapter.engine_error(ValueError("not mine")) is None


# --------------------------------------------------------------------------- #
# harden（D4 验证式：身份回读 fail-closed）
# --------------------------------------------------------------------------- #
def test_harden_identity_match_passes(tmp_path):
    conn = FakeConn({"SELECT current_user()": [("dataagent_ro",)]})
    _adapter(tmp_path).harden(conn)


def test_harden_identity_mismatch_rejected(tmp_path):
    from security.errors import SecurityError

    conn = FakeConn({"SELECT current_user()": [("anonymous",)]})
    with pytest.raises(SecurityError, match="身份回读不一致"):
        _adapter(tmp_path).harden(conn)


def test_harden_query_failure_rejected(tmp_path):
    class BoomConn:
        def cursor(self):
            raise RuntimeError("connection broken")

    from security.errors import SecurityError

    with pytest.raises(SecurityError, match="加固失败"):
        _adapter(tmp_path).harden(BoomConn())


# --------------------------------------------------------------------------- #
# cancel / explain_scan_rows（D3/D5 诚实退化口径）
# --------------------------------------------------------------------------- #
def test_cancel_is_noop(tmp_path):
    assert _adapter(tmp_path).cancel(FakeConn({})) is None


def test_explain_scan_rows_honestly_unmeasurable(tmp_path):
    # Hive EXPLAIN 无 actual rows（spec D3）：常量 (0, False)，warning 留痕
    # 由护栏框架承接，严禁伪装计量
    assert _adapter(tmp_path).explain_scan_rows(FakeConn({}), "SELECT 1", 1000) == (0, False)


# --------------------------------------------------------------------------- #
# insert_dataframe（D10：CSV staging 字节 + LOAD 语句）
# --------------------------------------------------------------------------- #
def test_insert_dataframe_named_columns_writes_csv_and_load(tmp_path):
    adapter = _adapter(tmp_path)
    df = pd.DataFrame(
        {
            "id": pd.Series([1, 2, 3], dtype=object),
            "name": pd.Series(["alpha", None, "gamma"], dtype=object),
        }
    )
    conn = FakeConn({})
    n = adapter.insert_dataframe(conn, "t_load", df)
    assert n == 3
    csv_path = tmp_path / "staging" / "data_t_load.csv"
    content = csv_path.read_text(encoding="utf-8")
    # 字节级锁定：\x01 分隔 + \N null 标记（RF-1 转义链路）
    assert content == "1\x01alpha\n2\x01\\N\n3\x01gamma\n"
    load_stmts = [s for s in conn.cursor_obj.executed if s.startswith("LOAD DATA")]
    assert load_stmts == [
        "LOAD DATA LOCAL INPATH '/opt/dataagent_staging/data_t_load.csv' INTO TABLE `t_load`"
    ]


def test_insert_dataframe_unnamed_columns_from_describe(tmp_path):
    adapter = _adapter(tmp_path)
    describe = FakeConn({"DESCRIBE `t_load`": [("id", "int", ""), ("name", "string", "")]})
    df = pd.DataFrame([(1, "alpha"), (2, "beta")])
    assert adapter.insert_dataframe(describe, "t_load", df) == 2
    content = (tmp_path / "staging" / "data_t_load.csv").read_text(encoding="utf-8")
    assert content == "1\x01alpha\n2\x01beta\n"


# --------------------------------------------------------------------------- #
# 标识符 fail-closed（SHOW/DESCRIBE 无绑定参数的替代面）
# --------------------------------------------------------------------------- #
def test_safe_name_rejects_injection(tmp_path):
    with pytest.raises(ValueError, match="非法字符"):
        _adapter(tmp_path)._safe_name("t; DROP TABLE x")


def test_load_path_rejects_quote(tmp_path):
    with pytest.raises(ValueError, match="单引号"):
        _adapter(tmp_path, server="/opt/a'b")._load_path()


def test_quote_ident_escapes_backtick(tmp_path):
    assert _adapter(tmp_path).quote_ident("a`b") == "`a``b`"


# --------------------------------------------------------------------------- #
# 协议属性与注册表
# --------------------------------------------------------------------------- #
def test_adapter_name_and_dialect():
    assert HiveAdapter().name == "hive"
    assert HiveAdapter().dialect == "hive"


def test_registry_lazy_factory(monkeypatch):
    from exec.engine import _ADAPTER_FACTORIES, get_adapter

    monkeypatch.setattr("config.settings.DB_BACKEND", "hive")
    adapter = get_adapter()
    assert adapter.name == "hive"
    assert "hive" in _ADAPTER_FACTORIES
