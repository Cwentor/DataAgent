"""执行引擎抽象：EngineAdapter 协议（二十一期，spec D1）。

护栏框架（exec.guards.execute_sql）引擎无关；连接创建、执行游标、
超时取消、计划解析、异常归一、连接加固、建仓灌数等引擎差异全部
由各 adapter 实现（duckdb / mysql / postgres）。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from exec.errors import SqlExecutionError


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


__all__ = ["EngineAdapter"]
