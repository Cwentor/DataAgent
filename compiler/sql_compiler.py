"""确定性 SQL 编译器：QueryDSL -> DuckDB SQL。

设计目标：编译器是完全确定性的纯函数，不接受任何自由 SQL 片段。它只做三件事：
1. 校验 DSL 引用的字段都在语义目录（semantic.catalog）中登记；
2. 按目录声明的连接规则组装 FROM / JOIN；
3. 按受限操作符、聚合函数与枚举生成 SQL，字面量严格转义。

任何未登记字段、非法操作符都会抛出 CompileError，从机制上杜绝 SQL 注入与任意 Join。

支持的语义能力：
- 聚合 / 比率指标、维度分组、过滤、排序、限行；
- 同比/环比（comparison，cur/prev 双窗口 CTE）；
- 窗口函数（累计求和 cumsum / 移动平均 moving_avg）；
- 日期连续补零（fill_gaps）；
- 分组 Top-N（top_n，ROW_NUMBER 分区过滤）。
"""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta
from datetime import time as dtime

from config import settings
from semantic import catalog
from semantic.catalog import JoinRule
from semantic.dsl_schema import (
    TIME_FIELDS,
    AggFunc,
    AggregateMetric,
    Comparison,
    Dimension,
    Filter,
    FilterOperator,
    Granularity,
    Metric,
    QueryDSL,
    RatioMetric,
    RelativeMode,
    RelativeTime,
    RelativeUnit,
    SortDirection,
    TimeFilter,
    TimeRangeType,
    TopN,
    WindowFunc,
    WindowMetric,
)


class CompileError(ValueError):
    """DSL 合法但无法编译为 SQL（例如引用了未登记字段）。"""


# 时间窗口锚定的时间列（由 TimeFilter.time_field 解析，白名单在 dsl_schema.TIME_FIELDS）
def _time_field_qualify(tf: TimeFilter) -> str:
    """把 time_filter.time_field 解析为限定 SQL 列（如 f.order_time / r.refund_time）。"""
    return _qualify(tf.time_field)


def _quote_ident(name: str) -> str:
    """把标识符（别名/排序字段）以双引号包裹并转义内部引号，杜绝标识符注入。

    alias / order_by.field 虽已在 DSL 层受 IDENTIFIER_PATTERN 约束，此处是
    独立的第二道防线：即使未来出现绕过 Pydantic 校验的路径，裸拼的标识符
    也只会被当作一个整体标识符，而无法改写 SQL 结构。
    """
    return '"' + str(name).replace('"', '""') + '"'


# --------------------------------------------------------------------------- #
# 时间窗口解析
# --------------------------------------------------------------------------- #
def _sub_months(d: date, n: int) -> date:
    """按月减法（保持日不变，超出月底则钳到月底）。"""
    total = d.year * 12 + (d.month - 1) - n
    y, m0 = divmod(total, 12)
    m = m0 + 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last_day))


def _sub_years(d: date, n: int) -> date:
    """按年减法（保持月日不变，闰日钳到 2 月 28）。"""
    y = d.year - n
    last_day = calendar.monthrange(y, d.month)[1]
    return date(y, d.month, min(d.day, last_day))


def _quarter_start(d: date) -> date:
    """给定日期所在季度的第一天。"""
    q = (d.month - 1) // 3  # 0, 1, 2, 3
    return date(d.year, q * 3 + 1, 1)


def _shift_window(
    start: datetime,
    end: datetime,
    comparison: Comparison,
    granularity: Granularity = Granularity.DAY,
) -> tuple[datetime, datetime]:
    """将当前窗口整体平移一个对比周期，得到基准窗口。

    - MOM：窗口整体前移一个月（granularity=quarter 时前移一个季度）；
    - YOY：窗口整体前移一年。
    平移后仍为半开区间 [new_start, new_end)。
    """
    shift_months = 1
    if comparison == Comparison.MOM and granularity == Granularity.QUARTER:
        shift_months = 3
    if comparison == Comparison.MOM:
        return (
            datetime.combine(_sub_months(start.date(), shift_months), start.time()),
            datetime.combine(_sub_months(end.date(), shift_months), end.time()),
        )
    if comparison == Comparison.YOY:
        return (
            datetime.combine(_sub_years(start.date(), 1), start.time()),
            datetime.combine(_sub_years(end.date(), 1), end.time()),
        )
    raise CompileError(f"不支持的 comparison: {comparison!r}")


def _resolve_window(tf: TimeFilter) -> tuple[datetime, datetime]:
    """将 TimeFilter 解析为半开时间区间 [start, end)。"""
    if tf.range_type == TimeRangeType.ABSOLUTE:
        start = datetime.combine(tf.absolute.start, dtime.min)
        end = datetime.combine(tf.absolute.end, dtime.min)
        return start, end

    rel: RelativeTime = tf.relative
    ref_date: date = tf.reference_date or settings.AS_OF_DATE
    ref = datetime.combine(ref_date, dtime.min)

    # to_date 模式（MTD/QTD/YTD）：窗口为 [周期起点, reference_date)
    if rel.mode == RelativeMode.TO_DATE:
        if rel.unit == RelativeUnit.MONTH:
            start = ref_date.replace(day=1)
        elif rel.unit == RelativeUnit.QUARTER:
            start = _quarter_start(ref_date)
        elif rel.unit == RelativeUnit.YEAR:
            start = date(ref_date.year, 1, 1)
        else:
            raise CompileError(f"to_date 模式不支持 unit={rel.unit!r}")
        return datetime.combine(start, dtime.min), ref

    if rel.mode == RelativeMode.CALENDAR:
        if rel.unit == RelativeUnit.MONTH:
            start_month = _sub_months(ref_date.replace(day=1), rel.amount)
            end_month = _sub_months(ref_date.replace(day=1), rel.amount - 1)
            return (
                datetime.combine(start_month, dtime.min),
                datetime.combine(end_month, dtime.min),
            )
        if rel.unit == RelativeUnit.QUARTER:
            # calendar quarter：过去 N 个完整季度（不含当前季度的部分）
            cur_q_start = _quarter_start(ref_date)
            start = _sub_months(cur_q_start, rel.amount * 3)
            end = _sub_months(cur_q_start, (rel.amount - 1) * 3)
            return datetime.combine(start, dtime.min), datetime.combine(end, dtime.min)
        if rel.unit == RelativeUnit.YEAR:
            start = date(ref_date.year - rel.amount, 1, 1)
            end = date(ref_date.year - rel.amount + 1, 1, 1)
            return datetime.combine(start, dtime.min), datetime.combine(end, dtime.min)
        raise CompileError(f"calendar 模式不支持 unit={rel.unit!r}")

    # trailing 模式
    if rel.unit == RelativeUnit.DAY:
        start = ref - timedelta(days=rel.amount)
    elif rel.unit == RelativeUnit.WEEK:
        start = ref - timedelta(days=rel.amount * 7)
    elif rel.unit == RelativeUnit.MONTH:
        start = datetime.combine(_sub_months(ref_date, rel.amount), dtime.min)
    elif rel.unit == RelativeUnit.QUARTER:
        start = datetime.combine(_sub_months(ref_date, rel.amount * 3), dtime.min)
    elif rel.unit == RelativeUnit.YEAR:
        start = datetime.combine(
            date(ref_date.year - rel.amount, ref_date.month, ref_date.day), dtime.min
        )
    else:
        raise CompileError(f"不支持的相对时间单位: {rel.unit!r}")
    return start, ref


# --------------------------------------------------------------------------- #
# 字段与字面量
# --------------------------------------------------------------------------- #
def _table_for_field(field: str) -> str:
    meta = catalog.COLUMNS.get(field)
    if meta is None:
        raise CompileError(f"未登记的字段: {field!r}，只能引用语义目录中的逻辑字段")
    return meta.table


def _qualify(field: str) -> str:
    meta = catalog.COLUMNS.get(field)
    if meta is None:
        raise CompileError(f"未登记的字段: {field!r}")
    return f"{catalog.ALIASES[meta.table]}.{meta.column}"


def _check_scalar_type(value, dtype: str) -> None:
    if dtype == "str" and not isinstance(value, str):
        raise CompileError(f"字段要求字符串，收到 {value!r}")
    if dtype == "int" and (isinstance(value, bool) or not isinstance(value, int)):
        raise CompileError(f"字段要求整数，收到 {value!r}")
    if dtype == "float" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise CompileError(f"字段要求数值，收到 {value!r}")
    if dtype == "bool" and not isinstance(value, bool):
        raise CompileError(f"字段要求布尔值，收到 {value!r}")
    if dtype == "timestamp" and not isinstance(value, (str, date, datetime)):
        raise CompileError(f"字段要求日期/时间，收到 {value!r}")


def _literal(value, dtype: str) -> str:
    """将 Python 值安全转义为 SQL 字面量。"""
    _check_scalar_type(value, dtype)
    if dtype == "str":
        return "'" + str(value).replace("'", "''") + "'"
    if dtype == "int":
        return str(int(value))
    if dtype == "float":
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            raise CompileError("浮点字面量不能为 NaN/Inf")
        return repr(v)
    if dtype == "bool":
        return "TRUE" if value else "FALSE"
    if dtype == "timestamp":
        if isinstance(value, datetime):
            return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
        return f"TIMESTAMP '{value}'"
    raise CompileError(f"未知字面量类型: {dtype!r}")


def _filter_sql(f: Filter) -> str:
    meta = catalog.COLUMNS.get(f.field)
    if meta is None:
        raise CompileError(f"未登记的过滤字段: {f.field!r}")
    col = f"{catalog.ALIASES[meta.table]}.{meta.column}"
    dtype = meta.dtype

    if f.operator == FilterOperator.IN:
        vals = ", ".join(_literal(v, dtype) for v in f.value)
        return f"{col} IN ({vals})"
    if f.operator == FilterOperator.BETWEEN:
        lo, hi = f.value
        return f"{col} BETWEEN {_literal(lo, dtype)} AND {_literal(hi, dtype)}"

    op_map = {
        FilterOperator.EQ: "=",
        FilterOperator.NE: "<>",
        FilterOperator.GT: ">",
        FilterOperator.GTE: ">=",
        FilterOperator.LT: "<",
        FilterOperator.LTE: "<=",
    }
    return f"{col} {op_map[f.operator]} {_literal(f.value, dtype)}"


def _time_window_sql(tf: TimeFilter) -> str:
    start, end = _resolve_window(tf)
    col = _time_field_qualify(tf)
    s = start.strftime("%Y-%m-%d %H:%M:%S")
    e = end.strftime("%Y-%m-%d %H:%M:%S")
    return f"{col} >= TIMESTAMP '{s}' AND {col} < TIMESTAMP '{e}'"


def resolve_time_window(tf: TimeFilter) -> tuple[datetime, datetime]:
    """解析 TimeFilter 为半开时间区间 [start, end)（公开入口）。

    供空结果归因（审计修复 D3：判断时间范围是否超出数仓数据域）等上层
    场景复用，避免各处重复实现相对时间语义。
    """
    return _resolve_window(tf)


# --------------------------------------------------------------------------- #
# 指标 / 维度表达式
# --------------------------------------------------------------------------- #
def _aggregate_metrics(m: Metric) -> list[AggregateMetric]:
    if isinstance(m, RatioMetric):
        return [m.numerator, m.denominator]
    if isinstance(m, WindowMetric):
        return [m.base]
    return [m]


def _aggregate_expr(am: AggregateMetric) -> str:
    col = _qualify(am.field)
    if am.agg == AggFunc.SUM:
        return f"SUM({col})"
    if am.agg == AggFunc.COUNT:
        return f"COUNT({col})"
    if am.agg == AggFunc.COUNT_DISTINCT:
        return f"COUNT(DISTINCT {col})"
    if am.agg == AggFunc.AVG:
        return f"AVG({col})"
    if am.agg == AggFunc.MIN:
        return f"MIN({col})"
    if am.agg == AggFunc.MAX:
        return f"MAX({col})"
    raise CompileError(f"不支持的聚合函数: {am.agg!r}")


def _metric_expr(m: Metric) -> tuple[str, str]:
    """聚合/比率指标 -> (表达式, 别名)。窗口指标由 _window_expr 另行处理。"""
    if isinstance(m, WindowMetric):
        raise CompileError("窗口指标不能在此上下文直接展开")
    if isinstance(m, RatioMetric):
        num = _aggregate_expr(m.numerator)
        den = _aggregate_expr(m.denominator)
        # 除零防护：分母为 0 时产出 NULL 而非 inf/NaN，与 comparison 列的 NULLIF 口径对齐；
        # 分子 NULL 语义（审计修复 R4a）：无退款记录的品类 SUM(refund_amount) 为 NULL，
        # 比率应呈现 0（退款率 0.00%）而非 NULL——分子 COALESCE(·, 0)。
        return f"COALESCE({num}, 0) / NULLIF({den}, 0)", m.alias
    return _aggregate_expr(m), m.alias


def _window_expr(wm: WindowMetric, order_expr: str) -> str:
    """窗口指标 -> 窗口函数表达式（在时间维度上滚动计算）。"""
    base = _aggregate_expr(wm.base)
    if wm.func == WindowFunc.CUMSUM:
        return f"SUM({base}) OVER (ORDER BY {order_expr})"
    if wm.func == WindowFunc.MOVING_AVG:
        n = wm.window_size
        return (
            f"AVG({base}) OVER (ORDER BY {order_expr} "
            f"ROWS BETWEEN {n - 1} PRECEDING AND CURRENT ROW)"
        )
    raise CompileError(f"不支持的窗口函数: {wm.func!r}")


def _dimension_expr(d: Dimension, granularity: Granularity) -> tuple[str, str]:
    meta = catalog.COLUMNS.get(d.field)
    if meta is None:
        raise CompileError(f"未登记的维度字段: {d.field!r}")
    alias = d.alias or d.field
    qual = f"{catalog.ALIASES[meta.table]}.{meta.column}"
    if d.field in TIME_FIELDS:
        expr = f"date_trunc('{granularity.value}', {qual})"
    else:
        expr = qual
    return expr, alias


def _time_dimension_expr(dsl: QueryDSL, granularity: Granularity) -> tuple[str, str]:
    """找出 DSL 中的唯一时间维度，返回 (表达式, 别名)；无/多时间维度则报错。"""
    time_dims = [d for d in dsl.dimensions if d.field in TIME_FIELDS]
    if not time_dims:
        raise CompileError("窗口/补零查询需要时间维度（order_time/refund_time/register_time）")
    if len(time_dims) > 1:
        raise CompileError("一次查询最多一个时间维度")
    return _dimension_expr(time_dims[0], granularity)


# --------------------------------------------------------------------------- #
# 表集合与 FROM/JOIN
# --------------------------------------------------------------------------- #
def _collect_tables(dsl: QueryDSL) -> set[str]:
    """收集 DSL 引用到的所有表（用于决定 JOIN 哪些维度表）。"""
    tables: set[str] = set()
    for d in dsl.dimensions:
        tables.add(_table_for_field(d.field))
    for f in dsl.filters:
        tables.add(_table_for_field(f.field))
    for m in dsl.metrics:
        for am in _aggregate_metrics(m):
            tables.add(_table_for_field(am.field))
    return tables


def _join_sql(table: str, rule: JoinRule) -> str:
    """把受控连接声明渲染为 SQL（P0-2：目录只声明 join type + 字段对，不再裸拼 SQL）。

    on 的每个字段对是 (joined_table_col, fact_table_col)，
    渲染为 `{joined_alias}.{joined_col} = {fact_alias}.{fact_col}`。
    """
    alias = catalog.ALIASES.get(table, table)
    fact_alias = catalog.ALIASES.get(catalog.FACT_TABLE, catalog.FACT_TABLE)
    conditions = " AND ".join(f"{alias}.{jcol} = {fact_alias}.{fcol}" for jcol, fcol in rule.on)
    keyword = "LEFT JOIN" if rule.join_type == "left" else "JOIN"
    return f"{keyword} {table} {alias} ON {conditions}"


def _from_clause(dsl: QueryDSL) -> str:
    tables = _collect_tables(dsl)
    sql = f"FROM {catalog.FACT_TABLE} f"
    joins: list[str] = []
    # 维度表受控连接
    for dim, rule in catalog.JOIN_RULES.items():
        if dim in tables:
            joins.append(_join_sql(dim, rule))
    # 第二事实表受控连接（1:1 LEFT JOIN，无扇出放大）
    for fact, rule in catalog.FACT_JOIN_RULES.items():
        if fact in tables:
            joins.append(_join_sql(fact, rule))
    if joins:
        sql += "\n" + "\n".join(joins)
    return sql


# --------------------------------------------------------------------------- #
# 对比（同比/环比）编译
# --------------------------------------------------------------------------- #
def _compile_with_comparison(dsl: QueryDSL) -> str:
    """编译带 comparison 的 DSL：当前窗口 vs 基准窗口，输出增长率。

    输出约定（以指标别名为 gmv、comparison=YOY 为例）：
        gmv      当前周期值
        gmv_prev 基准周期值
        gmv_yoy  增长率 = (cur - prev) / NULLIF(prev, 0)
    对 MOM 同理生成 {alias}_mom。多指标时逐个指标生成三列。

    支持非时间维度（品类/品牌/省份等）：cur/prev 两个 CTE 各自按维度分组，
    外层以维度列 LEFT JOIN 配对，输出 维度列 + 当前值 + 基准值 + 增长率。

    支持时间维度（order_time/refund_time/register_time）的**按位配对**：
    prev CTE 的时间列经 date_add(时间列, 间隔) 对齐到当前窗口（MOM +1 month、
    YOY +1 year），外层以对齐后的时间列 JOIN —— 使"6月每日 GMV 同比"这类
    最常见问法可编译，而非抛 CompileError（历史缺陷修复）。
    """
    tf = dsl.time_filter
    assert tf is not None and tf.comparison != Comparison.NONE
    granularity = tf.granularity
    time_col = _time_field_qualify(tf)

    cur_start, cur_end = _resolve_window(tf)
    prev_start, prev_end = _shift_window(cur_start, cur_end, tf.comparison, granularity)
    cmp_suffix = "_mom" if tf.comparison == Comparison.MOM else "_yoy"

    # 时间维度（至多一个）与普通维度分离
    time_dim: Dimension | None = None
    plain_dims: list[Dimension] = []
    for d in dsl.dimensions:
        if d.field in TIME_FIELDS:
            time_dim = d
        else:
            plain_dims.append(d)
    if any(d.field in TIME_FIELDS for d in dsl.dimensions) and time_dim is None:
        time_dim = next(d for d in dsl.dimensions if d.field in TIME_FIELDS)

    # 对齐间隔：prev 时间列平移到当前窗口所需步长
    if tf.comparison == Comparison.MOM:
        align_interval = (
            "INTERVAL 3 MONTH" if granularity == Granularity.QUARTER else "INTERVAL 1 MONTH"
        )
    else:
        align_interval = "INTERVAL 1 YEAR"

    time_alias: str | None = time_dim.alias or time_dim.field if time_dim else None
    if time_dim is not None:
        trunc_expr = _dimension_expr(time_dim, granularity)[0]  # date_trunc(g, col)
        time_expr_cur = trunc_expr
        time_expr_prev = f"date_add({trunc_expr}, {align_interval})"

    # 维度表达式与别名（cur/prev 两 CTE 共用，保证可配对）
    dim_exprs: list[str] = []
    dim_aliases: list[str] = []
    for d in plain_dims:
        expr, alias = _dimension_expr(d, granularity)
        dim_exprs.append(expr)
        dim_aliases.append(alias)

    def _window_block(
        label: str,
        start: datetime,
        end: datetime,
        time_expr: str | None = None,
    ) -> str:
        select_items: list[str] = []
        for expr, alias in zip(dim_exprs, dim_aliases, strict=False):
            select_items.append(f"{expr} AS {_quote_ident(alias)}")
        if time_expr is not None:
            select_items.append(f"{time_expr} AS {_quote_ident(time_alias)}")
        for m in dsl.metrics:
            expr, alias = _metric_expr(m)
            select_items.append(f"{expr} AS {_quote_ident(alias)}")
        where = [_filter_sql(f) for f in dsl.filters]
        s = start.strftime("%Y-%m-%d %H:%M:%S")
        e = end.strftime("%Y-%m-%d %H:%M:%S")
        where.append(f"{time_col} >= TIMESTAMP '{s}' AND {time_col} < TIMESTAMP '{e}'")
        block = (
            f"{label} AS (\n"
            f"  SELECT {', '.join(select_items)}\n"
            f"  {_from_clause(dsl)}\n"
            "  WHERE " + " AND ".join(where) + "\n"
        )
        group_exprs = list(dim_exprs)
        if time_expr is not None:
            group_exprs.append(time_expr)
        if group_exprs:
            block += "  GROUP BY " + ", ".join(group_exprs) + "\n"
        block += ")"
        return block

    selects: list[str] = []
    for alias in dim_aliases:
        q = _quote_ident(alias)
        selects.append(f"cur.{q} AS {q}")
    if time_alias is not None:
        q = _quote_ident(time_alias)
        selects.append(f"cur.{q} AS {q}")
    for m in dsl.metrics:
        alias = m.alias
        q = _quote_ident(alias)
        q_prev = _quote_ident(alias + "_prev")
        q_cmp = _quote_ident(alias + cmp_suffix)
        selects.append(f"cur.{q} AS {q}")
        selects.append(f"prev.{q} AS {q_prev}")
        selects.append(f"(cur.{q} - prev.{q}) / NULLIF(prev.{q}, 0) AS {q_cmp}")

    sql = "WITH " + _window_block("cur", cur_start, cur_end, time_expr_cur if time_dim else None)
    sql += ",\n" + _window_block("prev", prev_start, prev_end, time_expr_prev if time_dim else None)
    sql += "\nSELECT " + ", ".join(selects)
    join_keys = list(dim_aliases) + ([time_alias] if time_alias is not None else [])
    if join_keys:
        sql += (
            "\nFROM cur LEFT JOIN prev USING ("
            + ", ".join(_quote_ident(a) for a in join_keys)
            + ")"
        )
    else:
        sql += "\nFROM cur, prev"

    # 排序字段：允许引用维度列 / 时间列 / 当前值 / 基准值 / 增长率列
    allowed = set(dim_aliases)
    if time_alias is not None:
        allowed.add(time_alias)
    for m in dsl.metrics:
        allowed.add(m.alias)
        allowed.add(m.alias + "_prev")
        allowed.add(m.alias + cmp_suffix)
    if dsl.order_by:
        parts: list[str] = []
        for o in dsl.order_by:
            if o.field not in allowed:
                raise CompileError(f"order_by 字段 {o.field!r} 不是维度/指标别名或对比列")
            direction = "ASC" if o.direction == SortDirection.ASC else "DESC"
            parts.append(f"{_quote_ident(o.field)} {direction}")
        sql += "\nORDER BY " + ", ".join(parts)

    sql += f"\nLIMIT {int(dsl.limit)}"
    return sql


# --------------------------------------------------------------------------- #
# 分组 Top-N 编译
# --------------------------------------------------------------------------- #
def _compile_with_top_n(dsl: QueryDSL) -> str:
    """编译分组 Top-N：内层聚合 + ROW_NUMBER 分区排序，外层过滤序号 <= n。

    例 "每省 GMV Top 3 品类"：
        dimensions = [province, category]，top_n.n=3，partition_by=[province]，
        order_by=[{gmv desc}]。
    """
    top: TopN = dsl.top_n
    granularity = dsl.time_filter.granularity if dsl.time_filter else Granularity.DAY

    dim_expr_by_field: dict[str, str] = {}
    dim_alias_by_field: dict[str, str] = {}
    for d in dsl.dimensions:
        expr, alias = _dimension_expr(d, granularity)
        dim_expr_by_field[d.field] = expr
        dim_alias_by_field[d.field] = alias

    metric_expr_by_alias: dict[str, str] = {}
    for m in dsl.metrics:
        expr, alias = _metric_expr(m)
        metric_expr_by_alias[alias] = expr

    # 分区字段：必须是维度字段
    partition_exprs: list[str] = []
    partition_aliases: list[str] = []
    for field in top.partition_by:
        if field not in dim_expr_by_field:
            raise CompileError(f"top_n.partition_by 字段 {field!r} 不是维度字段")
        partition_exprs.append(dim_expr_by_field[field])
        partition_aliases.append(dim_alias_by_field[field])

    # 排序字段：指标别名或维度字段
    order_exprs: list[str] = []
    for o in top.order_by:
        if o.field in metric_expr_by_alias:
            expr = metric_expr_by_alias[o.field]
        elif o.field in dim_expr_by_field:
            expr = dim_expr_by_field[o.field]
        else:
            raise CompileError(f"top_n.order_by 字段 {o.field!r} 不是指标别名或维度字段")
        direction = "ASC" if o.direction == SortDirection.ASC else "DESC"
        order_exprs.append(f"{expr} {direction}")

    inner_selects: list[str] = []
    inner_group: list[str] = []
    for d in dsl.dimensions:
        expr, alias = _dimension_expr(d, granularity)
        inner_selects.append(f"{expr} AS {_quote_ident(alias)}")
        inner_group.append(expr)
    for m in dsl.metrics:
        expr, alias = _metric_expr(m)
        inner_selects.append(f"{expr} AS {_quote_ident(alias)}")

    where = [_filter_sql(f) for f in dsl.filters]
    if dsl.time_filter is not None:
        where.append(_time_window_sql(dsl.time_filter))

    inner_sql = "SELECT " + ", ".join(inner_selects)
    inner_sql += ",\n         ROW_NUMBER() OVER (PARTITION BY " + ", ".join(partition_exprs)
    inner_sql += " ORDER BY " + ", ".join(order_exprs) + ") AS __rn"
    inner_sql += "\n" + _from_clause(dsl)
    if where:
        inner_sql += "\nWHERE " + " AND ".join(where)
    inner_sql += "\nGROUP BY " + ", ".join(inner_group)

    # 外层：去掉 __rn，过滤序号
    outer_selects = [_quote_ident(dim_alias_by_field[d.field]) for d in dsl.dimensions]
    outer_selects += [_quote_ident(m.alias) for m in dsl.metrics]

    outer_order = [f"{_quote_ident(alias)} ASC" for alias in partition_aliases]
    outer_order += [
        (
            f"{_quote_ident(o.field)} ASC"
            if o.direction == SortDirection.ASC
            else f"{_quote_ident(o.field)} DESC"
        )
        for o in top.order_by
    ]

    sql = "SELECT " + ", ".join(outer_selects)
    sql += "\nFROM (\n" + inner_sql + "\n) AS __ranked"
    sql += f"\nWHERE __rn <= {int(top.n)}"
    sql += "\nORDER BY " + ", ".join(outer_order)
    sql += f"\nLIMIT {int(dsl.limit)}"
    return sql


# --------------------------------------------------------------------------- #
# 日期连续补零编译
# --------------------------------------------------------------------------- #
def _compile_with_fill_gaps(dsl: QueryDSL) -> str:
    """编译日期补零：时间序列 spine LEFT JOIN 聚合结果，缺值填 0。

    要求：唯一时间维度 + 明确时间窗口（absolute 或可解析的 relative）。
    支持 day / month 粒度；week 粒度暂不支持补零。
    """
    if dsl.time_filter is None:
        raise CompileError("fill_gaps 需要明确的时间窗口（time_filter）")
    tf = dsl.time_filter
    granularity = tf.granularity
    start, end = _resolve_window(tf)

    time_expr, time_alias = _time_dimension_expr(dsl, granularity)

    # spine 步长与闭区间终点
    if granularity == Granularity.DAY:
        step = "INTERVAL 1 DAY"
        end_incl = end - timedelta(days=1)
    elif granularity == Granularity.WEEK:
        step = "INTERVAL 1 WEEK"
        end_incl = end - timedelta(days=7)
    elif granularity == Granularity.MONTH:
        step = "INTERVAL 1 MONTH"
        end_incl = datetime.combine(_sub_months(end.date(), 1), end.time())
    elif granularity == Granularity.QUARTER:
        step = "INTERVAL 3 MONTH"
        end_incl = datetime.combine(_sub_months(end.date(), 3), end.time())
    else:
        raise CompileError(
            f"fill_gaps 暂不支持 granularity={granularity.value}（仅 day/week/month/quarter）"
        )

    # 内层聚合：时间维度 + 指标（聚合/比率均可）
    inner_selects: list[str] = [f"{time_expr} AS {_quote_ident(time_alias)}"]
    inner_group: list[str] = [time_expr]
    metric_aliases: list[str] = []
    for m in dsl.metrics:
        expr, alias = _metric_expr(m)
        inner_selects.append(f"{expr} AS {_quote_ident(alias)}")
        metric_aliases.append(alias)

    where = [_filter_sql(f) for f in dsl.filters]
    where.append(_time_window_sql(tf))

    start_s = start.strftime("%Y-%m-%d %H:%M:%S")
    end_incl_s = end_incl.strftime("%Y-%m-%d %H:%M:%S")

    spine = (
        f"__spine AS (\n"
        f"  SELECT UNNEST(generate_series(TIMESTAMP '{start_s}', TIMESTAMP '{end_incl_s}', {step})) "
        f"AS {_quote_ident(time_alias)}\n"
        ")"
    )
    agg = (
        "__agg AS (\n"
        "  SELECT " + ", ".join(inner_selects) + "\n"
        f"  {_from_clause(dsl)}\n"
        "  WHERE " + " AND ".join(where) + "\n"
        "  GROUP BY " + ", ".join(inner_group) + "\n"
        ")"
    )

    # 外层：spine LEFT JOIN agg，指标 COALESCE 为 0
    outer_selects: list[str] = [f"s.{_quote_ident(time_alias)} AS {_quote_ident(time_alias)}"]
    for alias in metric_aliases:
        q = _quote_ident(alias)
        outer_selects.append(f"COALESCE(a.{q}, 0) AS {q}")

    sql = "WITH " + spine + ",\n" + agg
    sql += "\nSELECT " + ", ".join(outer_selects)
    qt = _quote_ident(time_alias)
    sql += f"\nFROM __spine s\nLEFT JOIN __agg a ON a.{qt} = s.{qt}"
    sql += f"\nORDER BY s.{qt} ASC"
    sql += f"\nLIMIT {int(dsl.limit)}"
    return sql


# --------------------------------------------------------------------------- #
# 主编译入口
# --------------------------------------------------------------------------- #
def compile_sql(dsl: QueryDSL) -> str:
    """将 QueryDSL 编译为 DuckDB SQL 字符串（确定性、无注入）。"""
    tf = dsl.time_filter
    has_window = any(isinstance(m, WindowMetric) for m in dsl.metrics)

    # 互斥校验：comparison / top_n / fill_gaps / window 属于不同查询形态
    comparison = tf.comparison if tf is not None else Comparison.NONE
    if comparison != Comparison.NONE:
        if has_window or dsl.fill_gaps or dsl.top_n is not None:
            raise CompileError("comparison 不能与窗口指标/补零/分组 Top-N 同时使用")
        return _compile_with_comparison(dsl)

    if dsl.top_n is not None:
        if has_window or dsl.fill_gaps:
            raise CompileError("分组 Top-N 不能与窗口指标/补零同时使用")
        return _compile_with_top_n(dsl)

    if dsl.fill_gaps:
        if has_window:
            raise CompileError("日期补零不能与窗口指标同时使用")
        return _compile_with_fill_gaps(dsl)

    # ---- 普通路径（聚合/比率/窗口指标） ----
    from_clause = _from_clause(dsl)
    granularity = tf.granularity if tf else Granularity.DAY

    selects: list[str] = []
    dim_exprs: list[str] = []
    dim_aliases: set[str] = set()
    dim_alias_by_field: dict[str, str] = {}
    time_dim_expr: str | None = None
    for d in dsl.dimensions:
        expr, alias = _dimension_expr(d, granularity)
        selects.append(f"{expr} AS {_quote_ident(alias)}")
        dim_exprs.append(expr)
        dim_aliases.add(alias)
        dim_alias_by_field[d.field] = alias
        if d.field in TIME_FIELDS:
            time_dim_expr = expr

    metric_aliases: set[str] = set()
    for m in dsl.metrics:
        if isinstance(m, WindowMetric):
            if time_dim_expr is None:
                raise CompileError("窗口指标需要时间维度（order_time/refund_time/register_time）")
            expr = _window_expr(m, time_dim_expr)
            alias = m.alias
        else:
            expr, alias = _metric_expr(m)
        selects.append(f"{expr} AS {_quote_ident(alias)}")
        metric_aliases.add(alias)

    where: list[str] = []
    for f in dsl.filters:
        where.append(_filter_sql(f))
    if tf is not None:
        where.append(_time_window_sql(tf))

    sql = "SELECT " + ", ".join(selects)
    sql += "\n" + from_clause
    if where:
        sql += "\nWHERE " + " AND ".join(where)
    if dim_exprs:
        sql += "\nGROUP BY " + ", ".join(dim_exprs)

    if dsl.order_by and dim_exprs:
        parts: list[str] = []
        for o in dsl.order_by:
            if o.field in metric_aliases or o.field in dim_aliases:
                ref = o.field
            elif o.field in catalog.COLUMNS:
                # 别名回溯（审计修复 T01）：order_by 引用原始逻辑列名（如 order_time
                # 未显式注册维度别名）时自动映射到该维度的输出列别名；仅当该列
                # 确实出现在分组维度中才可回溯，否则仍是非法排序引用。
                mapped = dim_alias_by_field.get(o.field)
                if mapped is None:
                    raise CompileError(
                        f"order_by 字段 {o.field!r} 不是指标别名或维度别名，"
                        f"且未出现在分组维度中"
                    )
                ref = mapped
            else:
                raise CompileError(f"order_by 字段 {o.field!r} 不是指标别名或维度别名")
            direction = "ASC" if o.direction == SortDirection.ASC else "DESC"
            parts.append(f"{_quote_ident(ref)} {direction}")
        sql += "\nORDER BY " + ", ".join(parts)

    # 纯全局标量（无维度、无排序）：只返回单行聚合，LIMIT 冗余，抹除以免误导
    if dim_exprs or dsl.order_by:
        sql += f"\nLIMIT {int(dsl.limit)}"
    return sql
