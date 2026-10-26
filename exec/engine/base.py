"""执行引擎抽象：EngineAdapter 协议（二十一期，spec D1）。

护栏框架（exec.guards.execute_sql）引擎无关；连接创建、执行游标、
超时取消、计划解析、异常归一、连接加固、建仓灌数、物理元数据读取等引擎
差异全部由各 adapter 实现（duckdb / mysql / postgres / clickhouse）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from exec.errors import SqlExecutionError


def physical_from_rows(rows: Iterable[tuple[Any, ...]]) -> dict[str, dict[str, str]]:
    """元数据三元组 `(表, 列, 原始类型)` → `{表: {列: dtype}}`（四 adapter 共用）。

    - 下划线前缀的元数据表（`_field_metadata` 等）跳过（保持二十期前的目录语义）；
    - dtype 经 `exec.engine.type_map.map_dtype` 归一（未知类型 fail-closed）；
    - 保持元数据源的 `ORDER BY` 顺序（dict 保序，下游不依赖但便于排查）。
    """
    from exec.engine.type_map import map_dtype  # 惰性导入：本模块不反向依赖具体实现

    out: dict[str, dict[str, str]] = {}
    for table, column, raw in rows:
        if str(table).startswith("_"):
            continue
        out.setdefault(str(table), {})[str(column)] = map_dtype(str(raw))
    return out


@runtime_checkable
class EngineAdapter(Protocol):
    """查询执行引擎适配器协议（duck-typing：连接对象不入协议签名）。"""

    name: str  # 引擎名（DB_BACKEND 取值）
    dialect: str  # sqlglot 方言名（只读白名单解析 / 探索层回写共用）

    def connect_readonly(self) -> Any:
        """创建只读连接（含引擎侧加固；失败即抛，严禁带弱配置返回）。"""
        ...

    def run_query(self, conn: Any, sql: str) -> tuple[list[str], list[list[Any]]]:
        """执行单条 SELECT，返回 (columns, rows)；结果单元格须完成类型规范化。"""
        ...

    def cancel(self, conn: Any) -> None:
        """尽力取消在途查询（超时看门狗回调；引擎无此能力时为空操作）。"""
        ...

    def explain_scan_rows(self, conn: Any, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
        """EXPLAIN ANALYZE 预检扫描行数，返回 (扫描行数, 是否可计量)。

        不可计量（计划格式变更等）返回 (0, False)，由上层 warning 留痕，
        严禁静默当成 0 行放行。
        """
        ...

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        """引擎异常归一；非本引擎异常返回 None（调用方原样重抛）。"""
        ...

    def harden(self, conn: Any) -> None:
        """连接级加固（fail-closed：失败抛异常，严禁弱配置连接进管道）。"""
        ...

    def insert_dataframe(self, conn: Any, table: str, df: Any) -> int:
        """DataFrame 批量入库（仅 mock 建仓链路使用），返回插入行数。"""
        ...

    def physical_columns(self, conn: Any | None = None) -> dict[str, dict[str, str]]:
        """读取物理元数据，返回 `{表: {列: 目录 dtype}}`（二十四期，spec D1）。

        三条契约：

        - `conn=None` 时自建只读连接并负责关闭（服务启动路径）；传入连接时
          复用且**不**关闭（调用方持有生命周期，测试/已持连接场景）；
        - dtype 已归一为 `int|float|str|timestamp|bool`（见
          `exec.engine.type_map`），未知类型 fail-closed 抛 `ValueError`；
        - 元数据源按引擎自选：DuckDB/MySQL/PostgreSQL 走
          `information_schema.columns`，ClickHouse 走 `system.columns`；
          库名/schema 名一律驱动原生参数绑定（占位符方言各异，见 spec D3）。
        """
        ...


__all__ = ["EngineAdapter", "physical_from_rows"]
