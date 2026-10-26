"""MySQL 方言渲染器（二十一期 spec D5/D6/D7/D8）。

口径要点：
- 标识符反引号（不依赖 ANSI_QUOTES sql_mode，两种 mode 下均正确）；
- date_trunc 用纯日期运算等价式，保持返回 DATE 类型（不用 DATE_FORMAT
  字符串，避免 fill_gaps spine 对齐类型不一致）；week 用 WEEKDAY 对齐
  周一（与 DuckDB date_trunc('week') 口径一致，禁用 WEEK()/YEARWEEK
  ——其默认 mode 周日开头）；
- 时间字面量 CAST AS DATETIME（TIMESTAMP 'str' 在 MySQL 有时区转换
  语义，与 DATETIME 列比较有隐式坑；DDL 侧时间列统一 DATETIME）；
- 字符串转义：单引号翻倍之外反斜杠亦翻倍——与连接级
  NO_BACKSLASH_ESCAPES 构成两道独立防线（spec D8）。
"""

from __future__ import annotations

from datetime import date, datetime


class MySqlRenderer:
    """MySQL 方言渲染器（DialectRenderer 协议实现）。"""

    name = "mysql"

    def quote_ident(self, name: str) -> str:
        return "`" + str(name).replace("`", "``") + "`"

    def quote_column(self, alias: str, column: str) -> str:
        return f"`{alias}`.`{column}`"

    def timestamp_literal(self, value: datetime | date | str) -> str:
        if isinstance(value, datetime):
            s = value.strftime("%Y-%m-%d %H:%M:%S")
        else:
            s = str(value)
        return f"CAST('{s}' AS DATETIME)"

    def date_trunc(self, granularity: str, expr: str) -> str:
        if granularity == "day":
            return f"DATE({expr})"
        if granularity == "week":
            # WEEKDAY: 0=周一；DATE(e) 回退 n 个 WEEKDAY 天 = 本周一（含当天）
            return f"DATE_SUB(DATE({expr}), INTERVAL WEEKDAY(DATE({expr})) DAY)"
        if granularity == "month":
            # 当月 1 号：减去 (day-1) 天
            return f"DATE_SUB(DATE({expr}), INTERVAL DAYOFMONTH(DATE({expr})) - 1 DAY)"
        if granularity == "quarter":
            # 年初 1 月 1 日 + (季度-1)*3 个月
            return f"MAKEDATE(YEAR({expr}), 1) + INTERVAL (QUARTER({expr}) - 1) * 3 MONTH"
        from compiler.errors import CompileError

        raise CompileError(f"不支持的 granularity: {granularity!r}")

    def date_add(self, expr: str, interval: str) -> str:
        # MySQL DATE_ADD 同名兼容；小写渲染与迁移前内联产物逐字节一致（golden 锚点）
        return f"date_add({expr}, {interval})"

    def divide(self, numerator: str, denominator: str) -> str:
        # 与迁移前内联渲染逐字节一致（golden 锚点）：MySQL DECIMAL 除法结果
        # scale = S1 + div_precision_increment（默认 4），精度足够无需转译
        return f"{numerator} / NULLIF({denominator}, 0)"

    def having_metric_ref(self, alias: str, expr: str) -> str:
        # MySQL 允许 HAVING 引用输出别名——与迁移前内联渲染逐字节一致（golden 锚点）
        return self.quote_ident(alias)

    def spine_cte_sql(self, alias: str, start: str, end: str, step: str) -> str:
        # 递归 CTE：锚点行 UNION ALL 自引用递增，终点闭区间（与 DuckDB
        # generate_series 端点口径一致）；深度由连接加固 cte_max_recursion_depth 承载
        a = self.quote_ident(alias)
        s, e = self.timestamp_literal(start), self.timestamp_literal(end)
        return f"SELECT {s} AS {a} UNION ALL SELECT {a} + {step} FROM __spine WHERE {a} + {step} <= {e}"

    def with_keyword(self) -> str:
        return "WITH RECURSIVE"

    def escape_string_literal(self, value: str) -> str:
        # 反斜杠翻倍：即使 NO_BACKSLASH_ESCAPES 被部署环境覆盖，
        # 翻倍后的 \\ 在两种 sql_mode 下语义一致（spec D8 双保险）
        return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


__all__ = ["MySqlRenderer"]
