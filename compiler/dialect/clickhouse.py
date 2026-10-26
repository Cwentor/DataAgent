"""ClickHouse 方言渲染器（二十三期 spec D6/D7）。

口径要点：
- 标识符反引号（内部翻倍）；字符串字面量走反斜杠转义系（\\ 翻倍、' 写作
  \\'——CH 字面量以反斜杠转义为标准语义，与 MySQL 的 '' 口径不同，不使用
  引号翻倍）；
- date_trunc 四档全原生（week 用 toMonday 周一锚定，规避 toStartOfWeek 的
  周起始 mode 歧义，与 DuckDB date_trunc('week') / MySQL WEEKDAY 口径一致）；
- date_add 透传拼接：编译器 INTERVAL 1 DAY 形态在 CH 合法（无需 PG 式转译）；
- spine 用 numbers(N) + addDays/addMonths/addYears 函数族（远古可用、签名稳定；
  拒绝 generateSeries 版本赌注与实验态递归 CTE，spec D7）：N 由渲染器纯
  Python 桶数计算——day/week 按整日差（WEEK 归一 7 天）、month/quarter（MONTH
  形态）与 year（YEAR 形态）手写月历算术（不引入 dateutil），未知步长形态与
  负向窗口显式 CompileError（对齐 pg_interval_literal 先例）。
"""

from __future__ import annotations

import re
from datetime import date, datetime

from compiler.errors import CompileError

# 编译器侧 INTERVAL 字面量形态（与 postgres 转译同源约束）：
# spine 四处步长 + comparison 对齐两类：INTERVAL <n> <UNIT>
_INTERVAL_RE = re.compile(r"INTERVAL\s+(\d+)\s+(DAY|WEEK|MONTH|YEAR)", re.IGNORECASE)

# CH 时间平移函数族（按归一后的单位分发；WEEK 归一为 DAY*7，QUARTER 编译器
# 侧即 MONTH*3 形态）
_UNIT_FUNCS = {"DAY": "addDays", "MONTH": "addMonths", "YEAR": "addYears"}

_FMT = "%Y-%m-%d %H:%M:%S"


def parse_interval_step(step: str) -> tuple[int, str]:
    """编译器 INTERVAL 字面量 -> (步长倍数, 归一单位)。

    'INTERVAL 7 DAY' -> (7, 'DAY')；'INTERVAL 1 WEEK' -> (7, 'DAY')（周步长
    归一天步长）；未知形态显式 CompileError（严禁静默拼接非法 SQL）。
    """
    m = _INTERVAL_RE.fullmatch(step.strip())
    if m is None:
        raise CompileError(f"不支持的 INTERVAL 字面量形态: {step!r}")
    multiple, unit = int(m.group(1)), m.group(2).upper()
    if unit == "WEEK":
        return multiple * 7, "DAY"
    return multiple, unit


def _bucket_count(start: str, end: str, multiple: int, unit: str) -> int:
    """fill_gaps 桶数 N = floor((end - start) / step) + 1。

    end 已由编译器 end_incl 回退到末桶起点，闭区间口径与 DuckDB
    generate_series 一致；start <= end 前提之外（负向窗口）fail-closed 报错。
    month/year 按"锚点日/时未满整步差一个月/年"的月历算术（闰年与月末由
    datetime 比较天然承载）。
    """
    s = datetime.strptime(start, _FMT)
    e = datetime.strptime(end, _FMT)
    if unit == "DAY":
        total = (e - s).days
    elif unit == "MONTH":
        total = (e.year - s.year) * 12 + (e.month - s.month)
        if (e.day, e.hour, e.minute, e.second) < (s.day, s.hour, s.minute, s.second):
            total -= 1
    else:  # YEAR
        total = e.year - s.year
        if (e.month, e.day, e.hour, e.minute, e.second) < (
            s.month,
            s.day,
            s.hour,
            s.minute,
            s.second,
        ):
            total -= 1
    if total < 0:
        raise CompileError(f"spine 窗口负向: start={start!r} end={end!r} unit={unit}")
    return total // multiple + 1


class ClickHouseRenderer:
    """ClickHouse 方言渲染器（DialectRenderer 协议实现）。"""

    name = "clickhouse"

    def quote_ident(self, name: str) -> str:
        return "`" + str(name).replace("`", "``") + "`"

    def quote_column(self, alias: str, column: str) -> str:
        return f"`{alias}`.`{column}`"

    def timestamp_literal(self, value: datetime | date | str) -> str:
        if isinstance(value, datetime):
            s = value.strftime(_FMT)
        else:
            s = str(value)
        return f"toDateTime('{s}')"

    def date_trunc(self, granularity: str, expr: str) -> str:
        mapping = {
            "day": f"toStartOfDay({expr})",
            "week": f"toMonday({expr})",
            "month": f"toStartOfMonth({expr})",
            "quarter": f"toStartOfQuarter({expr})",
        }
        if granularity not in mapping:
            raise CompileError(f"不支持的 granularity: {granularity!r}")
        return mapping[granularity]

    def date_add(self, expr: str, interval: str) -> str:
        # 编译器 INTERVAL 1 DAY 形态在 CH 合法，透传拼接（spec D6）
        return f"{expr} + {interval}"

    def having_metric_ref(self, alias: str, expr: str) -> str:
        # CH 支持别名复用（同 MySQL 路线，spec D6）
        return self.quote_ident(alias)

    def spine_cte_sql(self, alias: str, start: str, end: str, step: str) -> str:
        # numbers(N) + addDays/addMonths/addYears：N 纯 Python 计算（spec D7）；
        # number 为 numbers() 表的 UInt64 列，按步长倍数平移
        multiple, unit = parse_interval_step(step)
        n = _bucket_count(start, end, multiple, unit)
        m = "" if multiple == 1 else f" * {multiple}"
        func = _UNIT_FUNCS[unit]
        return (
            f"SELECT {func}(toDateTime('{start}'), number{m}) AS {self.quote_ident(alias)} "
            f"FROM numbers({n})"
        )

    def with_keyword(self) -> str:
        # spine 非递归（numbers 集合产出），无需 RECURSIVE
        return "WITH"

    def escape_string_literal(self, value: str) -> str:
        # 反斜杠转义系：\\ 翻倍 + ' 写作 \'（CH 标准转义语义，spec D6）
        return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


__all__ = ["ClickHouseRenderer", "parse_interval_step"]
