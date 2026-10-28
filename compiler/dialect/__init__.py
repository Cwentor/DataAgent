"""编译方言注册中心（二十一期 spec D4；二十二期注册表化）：按 SQL_DIALECT/DB_BACKEND 提供 renderer。

**每次调用仍现读 settings**（测试 monkeypatch SQL_DIALECT / DB_BACKEND 需即时
生效），但渲染器实例按方言名缓存复用：renderer 全部无实例状态（五个实现都没有
`__init__`、没有 `self.x =` 赋值），原实现每次调用都走惰性工厂
（`importlib.import_module` + 实例化，单次 2.55 µs），而一次编译要取 27+ 次
（2026-10-28 冗余审计 §4.1，约 70 µs/次编译，且多数在循环内）。
缓存键是方言名，故切换方言仍即时生效；新增方言只需在注册表登记。
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


# 实例缓存（方言名 -> renderer）。渲染器无状态，复用安全；键为方言名，
# 因此 settings 变更（切换后端/方言）仍然即时生效，只是省掉重复实例化。
_RENDERER_INSTANCES: dict[str, Any] = {}


def get_renderer() -> DialectRenderer:
    """取当前方言渲染器（duckdb / mysql / postgres / clickhouse / hive）。"""
    from compiler.errors import CompileError  # 惰性导入：errors 不反向依赖本包

    name = resolve_dialect()
    cached = _RENDERER_INSTANCES.get(name)
    if cached is not None:
        return cached
    factory = _RENDERER_FACTORIES.get(name)
    if factory is None:
        raise CompileError(f"未知 SQL_DIALECT: {name!r}（仅支持 {', '.join(_RENDERER_FACTORIES)}）")
    renderer = factory()
    _RENDERER_INSTANCES[name] = renderer
    return renderer
