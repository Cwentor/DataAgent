r"""Hive 方言渲染器（二十五期 spec D6/D7）。

口径要点（计划期 apache/hive:4.0.0 容器实测，spec §0）：
- 标识符反引号（内部翻倍）；字符串字面量走反斜杠转义系（\ 翻倍、' 写作
  \'——Hive 默认反斜杠转义语义且无会话级 pin 配置面，与 CH 同口径）；
- Hive 无 date_trunc 函数（实测 Invalid function，Spark SQL 才有）：四档
  等价式 day=to_date / week=date_sub+pmod(datediff, '1970-01-05' 周一锚定) /
  month=trunc('MM') / quarter=trunc('Q')——trunc 接受 timestamp 输入、四档
  全部产出 DATE 类型（与 spine 桶值同型，join 相等比较成立）；
- date_add：DAY 走原生 date_add(date, int)；MONTH/YEAR 走 add_months
  （月末钳制语义实测 add_months('2025-01-31',1)=2025-02-28）；
- spine 无递归 CTE、无 generate_series（spec §12 预研成立），用
  posexplode(split(space(n),' ')) 行生成技巧（无 UDF、Hive 0.13+ 稳定）：
  N 由纯 Python 桶数计算（月历算术复用 CH 的 _bucket_count，单一语义来源），
  未知步长形态与负向窗口显式 CompileError（对齐 pg_interval_literal 先例）；
- HAVING 别名实测可用（同 MySQL/CH 路线）；DECIMAL 除法自带高精度，divide
  透传（同 PG 路线）。
"""

from __future__ import annotations

from datetime import date, datetime

from compiler.dialect.clickhouse import _bucket_count, parse_interval_step
from compiler.errors import CompileError

_TS_FMT = "%Y-%m-%d %H:%M:%S"


class HiveRenderer:
    """Hive 方言渲染器（DialectRenderer 协议实现）。"""

    name = "hive"

    def quote_ident(self, name: str) -> str:
        return "`" + str(name).replace("`", "``") + "`"

    def quote_column(self, alias: str, column: str) -> str:
        return f"`{alias}`.`{column}`"

    def timestamp_literal(self, value: datetime | date | str) -> str:
        if isinstance(value, datetime):
            s = value.strftime(_TS_FMT)
        else:
            s = str(value)
        return f"CAST('{s}' AS TIMESTAMP)"

    def date_trunc(self, granularity: str, expr: str) -> str:
        # Hive 无 date_trunc（spec §0 M1 实测）：四档等价式；week 的
        # '1970-01-05' 是纪元周锚（周一），pmod 产出 0(周一)..6(周日) 偏移
        mapping = {
            "day": f"to_date({expr})",
            "week": f"date_sub({expr}, pmod(datediff({expr}, '1970-01-05'), 7))",
            "month": f"trunc({expr}, 'MM')",
            "quarter": f"trunc({expr}, 'Q')",
        }
        if granularity not in mapping:
            raise CompileError(f"不支持的 granularity: {granularity!r}")
        return mapping[granularity]

    def date_add(self, expr: str, interval: str) -> str:
        # DAY 原生 date_add；MONTH/YEAR 走 add_months（Hive 无月/年平移的
        # INTERVAL 语法，月末钳制语义见 spec §0 实测）
        multiple, unit = parse_interval_step(interval)
        if unit == "DAY":
            return f"date_add({expr}, {multiple})"
        months = multiple * (12 if unit == "YEAR" else 1)
        return f"add_months({expr}, {months})"

    def divide(self, numerator: str, denominator: str) -> str:
        # Hive DECIMAL 除法自带高精度（spec §0 M6 实测 1/3 → 0.3333…），
        # 与 DuckDB 口径在 parity 容差内一致：透传（同 PG 路线）
        return f"{numerator} / NULLIF({denominator}, 0)"

    def having_metric_ref(self, alias: str, expr: str) -> str:
        # HAVING 引用输出别名实测可用（spec §0 M7，同 MySQL/CH 路线）
        return self.quote_ident(alias)

    def spine_cte_sql(self, alias: str, start: str, end: str, step: str) -> str:
        # posexplode(split(space(n-1),' ')) 行生成（spec §0 M3 实测）：CTE 体
        # 产出与 date_trunc 同型的 DATE 桶序列；N 纯 Python 桶数（月历算术
        # 复用 CH 实现，保证五方言同一口径）；start 取日期面作桶起点。
        # LATERAL VIEW 的表/列别名用普通名——Hive 裸标识符禁下划线开头
        # （__one 实测 ParseException，spec §0 M3）
        multiple, unit = parse_interval_step(step)
        n = _bucket_count(start, end, multiple, unit)
        anchor = start[:10]
        if unit == "DAY":
            shift = f"date_add('{anchor}', idx * {multiple})"
        else:
            months = multiple * (12 if unit == "YEAR" else 1)
            shift = f"add_months('{anchor}', idx * {months})"
        return (
            f"SELECT {shift} AS {self.quote_ident(alias)} "
            f"FROM (SELECT 1) one "
            f"LATERAL VIEW posexplode(split(space({n - 1}), ' ')) seq AS idx, val"
        )

    def internal_aliases(self) -> dict[str, str]:
        # 编译器内部别名（__spine/__agg/__ranked/__rn）：Hive 裸标识符禁下划线
        # 开头（spec §0 M3 实测），统一反引号包裹；其余方言保持裸名（编译器
        # getattr 分派缺省，产物逐字节不变）
        return {key: self.quote_ident("__" + key) for key in ("spine", "agg", "ranked", "rn")}

    def with_keyword(self) -> str:
        # spine 非递归（posexplode 集合产出），无需 RECURSIVE
        return "WITH"

    def escape_string_literal(self, value: str) -> str:
        # 反斜杠转义系：\ 翻倍 + ' 写作 \'（Hive 默认转义语义，spec D6）
        return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


__all__ = ["HiveRenderer", "parse_interval_step"]
