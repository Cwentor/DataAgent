"""DuckDB 方言渲染器（二十一期）：与迁移前 sql_compiler 内联渲染逐字节一致。

此实现是 golden 锚点的守护者——任何输出变化都会打穿评测与快照，
修改前必须同步重建 golden 并在提交说明中论证语义等价。
"""

from __future__ import annotations

from datetime import date, datetime


class DuckDbRenderer:
    """DuckDB 方言渲染器（DialectRenderer 协议实现）。"""

    name = "duckdb"

    def quote_ident(self, name: str) -> str:
        # 双引号包裹 + 内部双引号翻倍，杜绝标识符注入（DSL 层白名单外的第二道防线）
        return '"' + str(name).replace('"', '""') + '"'

    def quote_column(self, alias: str, column: str) -> str:
        # DuckDB 裸限定列（物理列名经 catalog 白名单登记，无注入面）
        return f"{alias}.{column}"

    def timestamp_literal(self, value: datetime | date | str) -> str:
        if isinstance(value, datetime):
            return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
        return f"TIMESTAMP '{value}'"

    def date_trunc(self, granularity: str, expr: str) -> str:
        return f"date_trunc('{granularity}', {expr})"

    def date_add(self, expr: str, interval: str) -> str:
        # DuckDB 原生 date_add(timestamp, interval)——与迁移前内联渲染逐字节一致（golden 锚点）
        return f"date_add({expr}, {interval})"

    def spine_cte_sql(self, alias: str, start: str, end: str, step: str) -> str:
        return (
            f"SELECT UNNEST(generate_series(TIMESTAMP '{start}', TIMESTAMP '{end}', {step})) "
            f"AS {self.quote_ident(alias)}"
        )

    def with_keyword(self) -> str:
        return "WITH"

    def escape_string_literal(self, value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"


__all__ = ["DuckDbRenderer"]
