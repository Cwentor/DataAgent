"""编译方言抽象：DialectRenderer 协议（二十一期，spec D4）。

编译器的 SQL 结构组装（FROM/JOIN/GROUP BY/窗口/CTE）引擎无关，只有
"方言原语"（标识符引号、date_trunc、时间字面量、补零 spine、字符串
转义、时间平移）经 renderer 渲染。DuckDB 实现必须与迁移前输出逐字节一致
（golden 锚点锁定）；MySQL 实现见 mysql.py；PostgreSQL 实现见 postgres.py。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class DialectRenderer(Protocol):
    """SQL 方言渲染器协议（9 个原语方法）。"""

    name: str  # 方言名（与 sqlglot 方言名一致）

    def quote_ident(self, name: str) -> str:
        """标识符包裹与内部引号转义（杜绝标识符注入）。"""
        ...

    def quote_column(self, alias: str, column: str) -> str:
        """限定列渲染（别名.列名）。"""
        ...

    def timestamp_literal(self, value: datetime | date | str) -> str:
        """时间戳字面量（datetime 走完整格式化；date/str 原样字符串化）。"""
        ...

    def date_trunc(self, granularity: str, expr: str) -> str:
        """时间维度截断（granularity: day/week/month/quarter）。"""
        ...

    def date_add(self, expr: str, interval: str) -> str:
        """时间列平移（comparison 配对对齐；interval 为编译器 INTERVAL 字面量）。

        spec D17：DuckDB/MySQL 有同名 date_add 函数；PostgreSQL 无，以
        `{expr} + INTERVAL` 加法等价式实现。
        """
        ...

    def having_metric_ref(self, alias: str, expr: str) -> str:
        """HAVING 子句中指标别名的引用形态（spec D18）。

        DuckDB/MySQL 语义允许 HAVING 引用输出别名，返回别名引用（golden
        锚点逐字节不变）；PostgreSQL 禁止（HAVING 仅可用聚合表达式与
        GROUP BY 列），返回底层聚合表达式重放。
        """
        ...

    def spine_cte_sql(self, alias: str, start: str, end: str, step: str) -> str:
        """fill_gaps 日期序列 spine 的 CTE 体（start/end 为 'YYYY-MM-DD HH:MM:SS'）。

        DuckDB 用 generate_series + UNNEST 表达式；MySQL 用递归 CTE
        （自引用 __spine），二者均以本方法产出完整 SELECT 体。
        """
        ...

    def with_keyword(self) -> str:
        """CTE 前缀关键字（WITH / WITH RECURSIVE）。"""
        ...

    def escape_string_literal(self, value: str) -> str:
        """字符串字面量转义（单引号翻倍；MySQL 另做反斜杠双保险）。"""
        ...


__all__ = ["DialectRenderer"]
