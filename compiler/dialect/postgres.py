"""PostgreSQL 方言渲染器（二十二期 spec D7/D8/D17）。

口径要点：
- 标识符双引号（PG 标准）；时间字面量 TIMESTAMP '...'（PG 原生）；
- date_trunc 四档全原生（week 周一锚定，与 DuckDB date_trunc / MySQL WEEKDAY
  等价式口径一致）；
- spine 用 generate_series 原生集合函数——无递归深度问题（MySQL 递归 CTE +
  cte_max_recursion_depth 的负担在 PG 不存在）；
- 字符串转义仅单引号翻倍、不翻倍反斜杠：PG 在 standard_conforming_strings=on
  （9.1+ 默认，adapter.harden 显式 pin）下反斜杠即字面量，翻倍反而引入多余
  字符（spec D7）；
- INTERVAL 字面量转译：编译器产出的 INTERVAL 1 DAY 形态（DuckDB/MySQL 兼容）
  在 PG 非法，统一转 INTERVAL '1 day'——spine 步长与 comparison date_add
  对齐共用同一转译点，未知形态显式 CompileError（spec D17）。
"""

from __future__ import annotations

import re
from datetime import date, datetime

from compiler.errors import CompileError

# 编译器侧 INTERVAL 字面量形态（sql_compiler 硬编码：spine 四处步长 +
# comparison 对齐两类）：INTERVAL <n> <UNIT>；PG 语法要求 INTERVAL '<n> <unit>'
_INTERVAL_RE = re.compile(r"INTERVAL\s+(\d+)\s+(DAY|WEEK|MONTH|YEAR)", re.IGNORECASE)


def pg_interval_literal(step: str) -> str:
    """编译器 INTERVAL 字面量 -> PG 字面量（'INTERVAL 1 DAY' -> "INTERVAL '1 day'"）。"""
    m = _INTERVAL_RE.fullmatch(step.strip())
    if m is None:
        raise CompileError(f"不支持的 INTERVAL 字面量形态: {step!r}")
    return f"INTERVAL '{m.group(1)} {m.group(2).lower()}'"


class PostgresRenderer:
    """PostgreSQL 方言渲染器（DialectRenderer 协议实现）。"""

    name = "postgres"

    def quote_ident(self, name: str) -> str:
        # 双引号包裹 + 内部双引号翻倍，杜绝标识符注入（与 DuckDB 同 rationale）
        return '"' + str(name).replace('"', '""') + '"'

    def quote_column(self, alias: str, column: str) -> str:
        # 裸限定列：别名来自编译器内部生成、物理列名经 catalog 白名单登记，无注入面
        return f"{alias}.{column}"

    def timestamp_literal(self, value: datetime | date | str) -> str:
        if isinstance(value, datetime):
            return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
        return f"TIMESTAMP '{value}'"

    def date_trunc(self, granularity: str, expr: str) -> str:
        # PG 原生 date_trunc（week 周一锚定）；granularity 来自契约枚举四档
        return f"date_trunc('{granularity}', {expr})"

    def spine_cte_sql(self, alias: str, start: str, end: str, step: str) -> str:
        # generate_series 原生集合函数：端点闭区间口径与 DuckDB generate_series
        # 一致（sql_compiler 侧 end_incl 已按粒度回退到末桶起点）
        a = self.quote_ident(alias)
        return (
            f"SELECT gs AS {a} FROM generate_series(TIMESTAMP '{start}', TIMESTAMP '{end}', "
            f"{pg_interval_literal(step)}) AS gs"
        )

    def with_keyword(self) -> str:
        # spine 非递归（generate_series 集合函数），无需 RECURSIVE
        return "WITH"

    def escape_string_literal(self, value: str) -> str:
        # 仅单引号翻倍；反斜杠在 standard_conforming_strings=on（harden 显式 pin）
        # 下是字面量，翻倍会引入多余字符——语义确定性由会话加固保证（spec D7）
        return "'" + str(value).replace("'", "''") + "'"

    def date_add(self, expr: str, interval: str) -> str:
        # PG 无 date_add 函数（spec D17）：加法等价式 + INTERVAL 字面量转译
        return f"{expr} + {pg_interval_literal(interval)}"

    def divide(self, numerator: str, denominator: str) -> str:
        # 与二十二期产物逐字节一致：PG numeric 除法保留高 scale，精度足够
        return f"{numerator} / NULLIF({denominator}, 0)"

    def having_metric_ref(self, alias: str, expr: str) -> str:
        # PG 语义禁止 HAVING 引用输出别名（UndefinedColumn 实锤，CI 首跑）：
        # 重放底层聚合表达式（alias_expr 由编译器两遍渲染机制提供）
        return expr


__all__ = ["PostgresRenderer", "pg_interval_literal"]
