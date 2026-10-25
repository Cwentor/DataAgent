"""类型规范化测试（spec D13）：pymysql 返回类型 -> 全链路统一类型。"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from exec.engine.normalize import normalize_cell


def test_decimal_to_float():
    assert normalize_cell(Decimal("123.45")) == 123.45
    assert isinstance(normalize_cell(Decimal("1")), float)


def test_datetime_passthrough():
    value = datetime(2025, 1, 1, 12, 0, 0)
    assert normalize_cell(value) is value


def test_bytes_rejected_as_none(caplog):
    """二进制值拒绝外透：置 None 并 warning 留痕（不静默吞）。"""
    with caplog.at_level(logging.WARNING, logger="exec.normalize"):
        assert normalize_cell(b"\x00bin") is None
    assert any("二进制" in r.message or "bytes" in r.message for r in caplog.records)


def test_none_and_scalars_passthrough():
    assert normalize_cell(None) is None
    assert normalize_cell(3) == 3
    assert normalize_cell("x") == "x"
    assert normalize_cell(True) is True
