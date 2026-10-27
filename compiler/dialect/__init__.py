"""编译方言注册中心（二十一期 spec D4；二十二期注册表化）：按 SQL_DIALECT/DB_BACKEND 提供 renderer。

每次调用现取（不缓存实例、动态读 settings）：测试 monkeypatch
settings.SQL_DIALECT / DB_BACKEND 需即时生效；renderer 无状态。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from compiler.dialect.base import DialectRenderer

__all__ = ["DialectRenderer", "get_renderer", "resolve_dialect"]


def resolve_dialect() -> str:
    """SQL_DIALECT 显式配置优先，缺省跟随 DB_BACKEND（取值即 sqlglot 方言名）。"""
    from config import settings

    if settings.SQL_DIALECT:
        return settings.SQL_DIALECT
    return settings.DB_BACKEND


def _lazy_factory(module: str, attr: str) -> Callable[[], Any]:
    """惰性工厂：调用时才 import 模块并实例化（保持包间导入次序与既有行为）。"""

    def _make() -> Any:
        import importlib

        return getattr(importlib.import_module(module), attr)()

    return _make


# 方言注册表（二十二期）：新增方言 = 在此登记一行；报错文案由 keys 动态生成
_RENDERER_FACTORIES: dict[str, Callable[[], Any]] = {
    "duckdb": _lazy_factory("compiler.dialect.duckdb", "DuckDbRenderer"),
    "mysql": _lazy_factory("compiler.dialect.mysql", "MySqlRenderer"),
    "postgres": _lazy_factory("compiler.dialect.postgres", "PostgresRenderer"),
    "clickhouse": _lazy_factory("compiler.dialect.clickhouse", "ClickHouseRenderer"),
    "hive": _lazy_factory("compiler.dialect.hive", "HiveRenderer"),
}


def get_renderer() -> DialectRenderer:
    """取当前方言渲染器（duckdb / mysql / postgres / clickhouse）。"""
    from compiler.errors import CompileError  # 惰性导入：errors 不反向依赖本包

    name = resolve_dialect()
    factory = _RENDERER_FACTORIES.get(name)
    if factory is None:
        raise CompileError(f"未知 SQL_DIALECT: {name!r}（仅支持 {', '.join(_RENDERER_FACTORIES)}）")
    return factory()
