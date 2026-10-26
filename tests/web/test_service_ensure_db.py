"""`ensure_db()` 后端分派（二十四期 T7，spec D5）。

红线：只有 duckdb 后端会在启动期建仓；其他后端启动只刷目录/策略，
绝不触碰 `settings.DB_PATH`（否则会在 PG/CH 部署里凭空建出一个无关的
DuckDB 文件，且目录会指向错误的库）。
"""

from __future__ import annotations

import pytest

import config.settings as settings_module
from exec.errors import SqlExecutionError


@pytest.fixture()
def _isolate(monkeypatch):
    """把 init_db / refresh_catalog / refresh_policies 全替换为记录桩。"""
    import mock.init_duckdb as init_module
    import security.policy_loader as policy_module
    import semantic.catalog_loader as loader_module

    calls: dict[str, list] = {"init_db": [], "refresh_catalog": [], "refresh_policies": []}

    monkeypatch.setattr(init_module, "main", lambda: calls["init_db"].append(True))

    def _refresh_catalog(*args, **kwargs):
        calls["refresh_catalog"].append((args, kwargs))

    monkeypatch.setattr(loader_module, "refresh_catalog", _refresh_catalog)
    monkeypatch.setattr(
        policy_module, "refresh_policies", lambda: calls["refresh_policies"].append(True)
    )
    return calls


def test_ensure_db_duckdb_initializes_when_missing(tmp_path, monkeypatch, _isolate):
    """duckdb 且库文件缺失 → 建仓一次，并按该文件刷新目录。"""
    from web.service import ensure_db

    missing = tmp_path / "missing.duckdb"
    monkeypatch.setattr(settings_module, "DB_BACKEND", "duckdb")
    monkeypatch.setattr(settings_module, "DB_PATH", missing)

    ensure_db()

    assert _isolate["init_db"] == [True]
    assert _isolate["refresh_catalog"] == [((), {"db_path": missing})]
    assert _isolate["refresh_policies"] == [True]


def test_ensure_db_duckdb_skips_init_when_present(tmp_path, monkeypatch, _isolate):
    """duckdb 且库文件已存在 → 不重建（幂等建仓只在缺失时触发）。"""
    from web.service import ensure_db

    present = tmp_path / "present.duckdb"
    present.write_bytes(b"")
    monkeypatch.setattr(settings_module, "DB_BACKEND", "duckdb")
    monkeypatch.setattr(settings_module, "DB_PATH", present)

    ensure_db()

    assert _isolate["init_db"] == []
    assert _isolate["refresh_catalog"] == [((), {"db_path": present})]


def test_ensure_db_non_duckdb_never_touches_duckdb_file(tmp_path, monkeypatch, _isolate):
    """postgres/clickhouse/mysql：不建仓、不给 refresh_catalog 传 db_path。"""
    from web.service import ensure_db

    monkeypatch.setattr(settings_module, "DB_BACKEND", "postgres")
    monkeypatch.setattr(settings_module, "DB_PATH", tmp_path / "absent.duckdb")

    ensure_db()

    assert _isolate["init_db"] == []  # 关键红线：不建 DuckDB 文件
    assert _isolate["refresh_catalog"] == [((), {})]  # 无 db_path
    assert _isolate["refresh_policies"] == [True]


@pytest.mark.parametrize("backend", ["mysql", "clickhouse"])
def test_ensure_db_other_backends_follow_same_branch(backend, tmp_path, monkeypatch, _isolate):
    """三分支归一：mysql / clickhouse 与 postgres 走同一 else 分支。"""
    from web.service import ensure_db

    monkeypatch.setattr(settings_module, "DB_BACKEND", backend)
    monkeypatch.setattr(settings_module, "DB_PATH", tmp_path / "absent.duckdb")

    ensure_db()

    assert _isolate["init_db"] == []
    assert _isolate["refresh_catalog"] == [((), {})]


def test_ensure_db_propagates_backend_failure(monkeypatch, _isolate):
    """后端不可用时目录刷新抛错 → ensure_db 原样抛出，严禁吞异常降级。"""
    import semantic.catalog_loader as loader_module
    from web.service import ensure_db

    def _boom(*args, **kwargs):
        raise SqlExecutionError("connection refused")

    monkeypatch.setattr(loader_module, "refresh_catalog", _boom)
    monkeypatch.setattr(settings_module, "DB_BACKEND", "clickhouse")

    with pytest.raises(SqlExecutionError, match="connection refused"):
        ensure_db()
    assert _isolate["refresh_policies"] == []  # 失败即中断，不继续刷策略
