"""执行引擎注册中心（二十一期，spec D1）：按 DB_BACKEND 提供 adapter。

护栏框架（exec.guards）与连接池（exec.pool）经 get_adapter 取引擎实现；
mysql adapter 惰性 import——未装 pymysql 的环境在 duckdb 后端下零影响。
"""

from __future__ import annotations

from config import settings
from exec.engine.base import EngineAdapter
from exec.engine.duckdb_adapter import DuckDbAdapter

__all__ = ["DuckDbAdapter", "EngineAdapter", "get_adapter"]


def get_adapter(backend: str | None = None) -> EngineAdapter:
    """按后端名取 adapter（缺省读 settings.DB_BACKEND）。

    每次调用现取（不缓存实例）：测试 monkeypatch settings.DB_BACKEND
    需即时生效；adapter 自身无状态，构造开销可忽略。
    """
    name = backend or settings.DB_BACKEND
    if name == "duckdb":
        return DuckDbAdapter()
    if name == "mysql":
        from exec.engine.mysql_adapter import MySqlAdapter  # 惰性：隔离 pymysql 依赖

        return MySqlAdapter()
    raise ValueError(f"未知 DB_BACKEND: {name!r}（仅支持 duckdb / mysql）")
