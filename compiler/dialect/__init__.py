"""编译方言注册中心（二十一期，spec D4）：按 SQL_DIALECT/DB_BACKEND 提供 renderer。

每次调用现取（不缓存实例、动态读 settings）：测试 monkeypatch
settings.SQL_DIALECT / DB_BACKEND 需即时生效；renderer 无状态。
"""

from __future__ import annotations

from compiler.dialect.base import DialectRenderer

__all__ = ["DialectRenderer", "get_renderer", "resolve_dialect"]


def resolve_dialect() -> str:
    """SQL_DIALECT 显式配置优先，缺省跟随 DB_BACKEND（取值即 sqlglot 方言名）。"""
    from config import settings

    if settings.SQL_DIALECT:
        return settings.SQL_DIALECT
    return settings.DB_BACKEND


def get_renderer() -> DialectRenderer:
    """取当前方言渲染器（duckdb / mysql）。"""
    from compiler.errors import CompileError  # 惰性导入：errors 不反向依赖本包

    name = resolve_dialect()
    if name == "duckdb":
        from compiler.dialect.duckdb import DuckDbRenderer

        return DuckDbRenderer()
    if name == "mysql":
        from compiler.dialect.mysql import MySqlRenderer

        return MySqlRenderer()
    raise CompileError(f"未知 SQL_DIALECT: {name!r}（仅支持 duckdb / mysql）")
