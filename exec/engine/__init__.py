"""执行引擎注册中心（二十一期 spec D1；二十二期注册表化）：按 DB_BACKEND 提供 adapter。

护栏框架（exec.guards）与连接池（exec.pool）经 get_adapter 取引擎实现；
mysql / postgres adapter 惰性 import——未装对应驱动的环境在 duckdb 后端下
零影响。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from config import settings
from exec.engine.base import EngineAdapter
from exec.engine.duckdb_adapter import DuckDbAdapter

__all__ = ["DuckDbAdapter", "EngineAdapter", "get_adapter"]


def _lazy_factory(module: str, attr: str) -> Callable[[], Any]:
    """惰性工厂：调用时才 import 驱动模块并实例化（隔离未装驱动环境）。"""

    def _make() -> Any:
        import importlib

        return getattr(importlib.import_module(module), attr)()

    return _make


# 后端注册表（二十二期）：新增后端 = 在此登记一行；报错文案由 keys 动态生成
_ADAPTER_FACTORIES: dict[str, Callable[[], Any]] = {
    "duckdb": DuckDbAdapter,
    "mysql": _lazy_factory("exec.engine.mysql_adapter", "MySqlAdapter"),
    "postgres": _lazy_factory("exec.engine.postgres_adapter", "PostgresAdapter"),
}


def get_adapter(backend: str | None = None) -> EngineAdapter:
    """按后端名取 adapter（缺省读 settings.DB_BACKEND）。

    每次调用现取（不缓存实例）：测试 monkeypatch settings.DB_BACKEND
    需即时生效；adapter 自身无状态，构造开销可忽略。
    """
    name = backend or settings.DB_BACKEND
    factory = _ADAPTER_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"未知 DB_BACKEND: {name!r}（仅支持 {', '.join(_ADAPTER_FACTORIES)}）")
    return factory()
