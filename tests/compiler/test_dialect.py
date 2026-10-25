"""方言层测试：DuckDB 渲染与重构前逐字节一致（golden 锚点保护）+ 注册中心行为。"""

from __future__ import annotations

from datetime import datetime

from compiler.dialect import get_renderer
from compiler.dialect.duckdb import DuckDbRenderer


def test_duckdb_renderer_snippets():
    """DuckDB 各原语输出与迁移前内联渲染逐字节一致。"""
    r = DuckDbRenderer()
    assert r.quote_ident("gmv") == '"gmv"'
    assert r.quote_ident('a"b') == '"a""b"'
    assert r.quote_column("f", "order_id") == "f.order_id"
    assert r.timestamp_literal(datetime(2025, 1, 1, 0, 0, 0)) == "TIMESTAMP '2025-01-01 00:00:00'"
    assert r.timestamp_literal("2025-01-01") == "TIMESTAMP '2025-01-01'"
    assert r.date_trunc("month", "f.order_time") == "date_trunc('month', f.order_time)"
    assert r.escape_string_literal("a'b") == "'a''b'"
    assert r.with_keyword() == "WITH"
    spine = r.spine_cte_sql("day", "2025-01-01 00:00:00", "2025-01-03 00:00:00", "INTERVAL 1 DAY")
    assert spine == (
        "SELECT UNNEST(generate_series(TIMESTAMP '2025-01-01 00:00:00', "
        "TIMESTAMP '2025-01-03 00:00:00', INTERVAL 1 DAY)) AS \"day\""
    )


def test_resolve_dialect_explicit_overrides_backend(monkeypatch):
    """SQL_DIALECT 显式配置优先；缺省跟随 DB_BACKEND（只测解析，不实例化 mysql）。"""
    import config.settings as settings
    from compiler.dialect import resolve_dialect

    monkeypatch.setattr(settings, "SQL_DIALECT", "")
    monkeypatch.setattr(settings, "DB_BACKEND", "duckdb")
    assert resolve_dialect() == "duckdb"
    monkeypatch.setattr(settings, "SQL_DIALECT", "mysql")
    assert resolve_dialect() == "mysql"
    monkeypatch.setattr(settings, "SQL_DIALECT", "")
    monkeypatch.setattr(settings, "DB_BACKEND", "mysql")
    assert resolve_dialect() == "mysql"


def test_get_renderer_rejects_unknown(monkeypatch):
    """未知方言 fail-fast（CompileError），不静默回退默认。"""
    import pytest

    import config.settings as settings
    from compiler.errors import CompileError

    monkeypatch.setattr(settings, "SQL_DIALECT", "oracle")
    with pytest.raises(CompileError, match="oracle"):
        get_renderer()
