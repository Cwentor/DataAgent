"""SQL -> DSL 提升闸门（十九期 M4）：LLM 产出的 SQL 永不直接执行。

架构铁律（修订版）：LLM 产出的 SQL 只作**输入表面语法**——本模块用 sqlglot
（duckdb 方言）解析后逐项提升为 DSL 契约，执行一律由确定性编译器重新生成。

保守性优先（spec §5.2）：解析失败 / 歧义 / 白名单外构造一律拒升——拒升产出
结构化精确清单（子句 + 构造 + 原因）喂回 LLM 自愈改写（宁拒升不错译）。
拒升 ≠ 拒答：拒升的查询交由编排层回落确定性兜底 / 探索层（M6）。

可提升形态（M4 白名单）：
- FROM fact_orders + 语义目录受控连接（JOIN_RULES / FACT_JOIN_RULES，含
  连接类型与连接条件逐项核对）；
- 纯引用 CTE 内联（CTE 必须是"单表全量 SELECT"，含计算的 CTE 拒升）；
- SELECT：聚合指标（SUM/COUNT/AVG/MIN/MAX/COUNT DISTINCT，须带别名）与
  纯维度投影（DISTINCT）；
- WHERE：AND 叶子上的 eq/ne/gt/gte/lt/lte/in/between（字面量类型须与字段
  dtype 匹配）；时间主轴的 >=/< 对提升为 time_filter 绝对窗口；
- GROUP BY / HAVING（限指标别名）/ ORDER BY（限指标别名或维度）/ LIMIT。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from semantic import catalog
from semantic.dsl_schema import IDENTIFIER_PATTERN, TIME_FIELDS

# 允许提升的 FROM 主表（DSL 编译器恒以主事实表锚定）
_MAIN_TABLE = catalog.FACT_TABLE

_MAX_LIMIT = 10000  # DSL 契约 limit 上限
_MAX_SQL_LENGTH = 10_000  # 输入表面语法长度上限（先于解析挡住资源炸弹）
_PARSE_TIMEOUT_S = 5.0  # 解析超时（spec §3.3：解析失败/超时/歧义一律拒升）


@dataclass(frozen=True)
class LiftRejection:
    """一条拒升发现：子句 + 构造 + 精确原因（结构化喂回 LLM 自愈）。"""

    clause: str
    construct: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"clause": self.clause, "construct": self.construct, "reason": self.reason}


@dataclass
class LiftResult:
    """提升结果：ok=True 时 dsl 为契约 dict（交编译器重编译执行）。"""

    ok: bool
    dsl: dict | None = None
    rejections: list[LiftRejection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 物理列 <-> 逻辑字段 反查
# --------------------------------------------------------------------------- #
def _build_column_index() -> dict[tuple[str, str], str]:
    """(物理表, 物理列) -> 逻辑字段名（语义目录单一事实源）。"""
    index: dict[tuple[str, str], str] = {}
    for name, meta in catalog.COLUMNS.items():
        index[(meta.table, meta.column)] = name
    return index


_COLUMN_INDEX = _build_column_index()


def _resolve_column(node: exp.Column) -> tuple[str, str] | None:
    """解析列引用为 (逻辑字段, 所属物理表)；未登记/歧义返回 None。

    优先按限定名（table.col）反查；未限定列按物理列名全目录查找，命中多表
    视为歧义拒绝（保守性优先）。
    """
    col_name = node.name
    table_name = node.table
    if table_name:
        # 限定名可能是编译器别名（f/p/s/u/r）或物理表名——先别名还原
        physical = _physical_table(table_name)
        if (physical, col_name) in _COLUMN_INDEX:
            return _COLUMN_INDEX[(physical, col_name)], physical
        return None
    hits = [(tbl, fld) for (tbl, col), fld in _COLUMN_INDEX.items() if col == col_name]
    if len(hits) == 1:
        return hits[0][1], hits[0][0]
    return None


def _physical_table(name: str) -> str:
    """编译器别名（f/p/s/u/r）或物理表名 -> 物理表名。"""
    for tbl, alias in catalog.ALIASES.items():
        if alias == name:
            return tbl
    return name


def _reject(rejections: list[LiftRejection], notes: list[str] | None = None) -> LiftResult:
    return LiftResult(ok=False, dsl=None, rejections=rejections, notes=notes or [])


def _one(rejection: LiftRejection) -> LiftResult:
    return _reject([rejection])


# --------------------------------------------------------------------------- #
# 字面量
# --------------------------------------------------------------------------- #
def _literal_value(node: exp.Expression, dtype: str) -> object | None:
    """提取字面量并按字段 dtype 校验类型（类型不符拒升，严禁静默强转）。"""
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this if dtype == "str" else None
        raw = node.this
        try:
            value = int(raw) if dtype == "int" else float(raw)
        except (TypeError, ValueError):
            return None
        return value
    if isinstance(node, exp.Boolean):
        return bool(node.this) if dtype == "bool" else None
    if isinstance(node, exp.Cast):
        # TIMESTAMP '...'：内层字符串字面量按原样返回（时间窗口口径）
        if dtype == "timestamp" and isinstance(node.this, exp.Literal) and node.this.is_string:
            return str(node.this).strip("'")
        return _literal_value(node.this, dtype)
    if dtype == "timestamp" and isinstance(node, exp.Literal) and node.is_string:
        return str(node.this).strip("'")
    return None


def _field_dtype(field: str) -> str:
    meta = catalog.COLUMNS.get(field)
    return meta.dtype if meta is not None else ""


# --------------------------------------------------------------------------- #
# 谓词叶子提升
# --------------------------------------------------------------------------- #
_OP_MAP = {
    exp.EQ: "eq",
    exp.NEQ: "ne",
    exp.GT: "gt",
    exp.GTE: "gte",
    exp.LT: "lt",
    exp.LTE: "lte",
}


def _lift_comparison(node: exp.Expression, alias_fields: set[str] | None) -> dict | None:
    """提升单个比较叶子为 Filter dict；无法提升返回 None。

    ``alias_fields`` 非空表示 HAVING 语境：左值须为指标别名（此时不做列解析）。
    """
    op = _OP_MAP.get(type(node))
    if op is None:
        return None
    left, right = node.this, node.expression
    if alias_fields is not None:
        if not isinstance(left, exp.Column) or left.name not in alias_fields:
            return None
        value = _literal_value(right, "float")
        if value is None:
            return None
        return {"field": left.name, "operator": op, "value": value}
    if not isinstance(left, exp.Column):
        return None
    resolved = _resolve_column(left)
    if resolved is None:
        return None
    field_name, _table = resolved
    dtype = _field_dtype(field_name)
    if dtype not in ("str", "int", "float", "bool", "timestamp"):
        return None
    value = _literal_value(right, dtype)
    if value is None:
        return None
    return {"field": field_name, "operator": op, "value": value}


def _flatten_and(node: exp.Expression) -> list[exp.Expression] | None:
    """AND 树展平为叶子列表；出现 OR / NOT 即 None（DSL 无对应形态，拒升）。"""
    if isinstance(node, exp.And):
        left = _flatten_and(node.this)
        right = _flatten_and(node.expression)
        if left is None or right is None:
            return None
        return left + right
    if isinstance(node, (exp.Or, exp.Not)):
        return None
    return [node]


def _lift_predicates(
    where: exp.Expression | None,
    alias_fields: set[str] | None = None,
    agg_alias: dict[tuple[str, str], str] | None = None,
) -> tuple[list[dict], list[dict], list[LiftRejection]]:
    """提升 WHERE/HAVING 谓词为 (普通过滤, 时间窗口原始对, 拒升清单)。"""
    rejections: list[LiftRejection] = []
    filters: list[dict] = []
    time_pairs: list[tuple[str, str, dict]] = []  # (field, op, value)
    if where is None:
        return filters, time_pairs, rejections
    root = where.this if isinstance(where, (exp.Where, exp.Having)) else where
    leaves = _flatten_and(root)
    if leaves is None:
        rejections.append(
            LiftRejection("where", "OR/NOT", "DSL 谓词仅支持 AND 连接，OR/NOT 无法提升")
        )
        return filters, time_pairs, rejections
    for leaf in leaves:
        if isinstance(leaf, exp.In):
            if alias_fields is not None:
                rejections.append(LiftRejection("having", "IN", "HAVING 仅支持标量比较"))
                continue
            column = leaf.this
            resolved = _resolve_column(column) if isinstance(column, exp.Column) else None
            if resolved is None:
                rejections.append(LiftRejection("where", "IN 非列", "IN 左值必须是语义目录登记列"))
                continue
            field_name, _ = resolved
            dtype = _field_dtype(field_name)
            values: list[object] = []
            ok = True
            for item in leaf.expressions:
                value = _literal_value(item, dtype)
                if value is None:
                    ok = False
                    break
                values.append(value)
            if not ok or not values:
                rejections.append(
                    LiftRejection("where", "IN 字面量", "IN 列表字面量类型与字段不符或为空")
                )
                continue
            filters.append({"field": field_name, "operator": "in", "value": values})
            continue
        if isinstance(leaf, exp.Between):
            if alias_fields is not None:
                rejections.append(LiftRejection("having", "BETWEEN", "HAVING 仅支持标量比较"))
                continue
            column = leaf.this
            resolved = _resolve_column(column) if isinstance(column, exp.Column) else None
            if resolved is None:
                rejections.append(
                    LiftRejection("where", "BETWEEN 非列", "BETWEEN 左值必须是语义目录登记列")
                )
                continue
            field_name, _ = resolved
            dtype = _field_dtype(field_name)
            low = _literal_value(leaf.args.get("low"), dtype)
            high = _literal_value(leaf.args.get("high"), dtype)
            if low is None or high is None:
                rejections.append(
                    LiftRejection("where", "BETWEEN 字面量", "BETWEEN 边界字面量类型与字段不符")
                )
                continue
            filters.append({"field": field_name, "operator": "between", "value": [low, high]})
            continue
        if (
            alias_fields is not None
            and isinstance(leaf, exp.Binary)
            and type(leaf.this) in _AGG_CLASSES
        ):
            # HAVING SUM(col) op lit 形态：回查聚合指标别名（等价别名形态）
            agg_kind = _AGG_CLASSES[type(leaf.this)]
            agg_column = leaf.this.this
            resolved = _resolve_column(agg_column) if isinstance(agg_column, exp.Column) else None
            alias = (agg_alias or {}).get((agg_kind, resolved[0] if resolved else None))
            value = _literal_value(leaf.expression, "float") if alias else None
            if alias is None or value is None:
                rejections.append(
                    LiftRejection(
                        "having",
                        type(leaf.this).__name__,
                        "HAVING 聚合必须与本查询已声明的指标完全一致（或直接使用指标别名）",
                    )
                )
                continue
            filters.append(
                {
                    "field": alias,
                    "operator": _OP_MAP[type(leaf)],
                    "value": value,
                }
            )
            continue
        comparison = _lift_comparison(leaf, alias_fields)
        if comparison is None:
            rejections.append(
                LiftRejection(
                    "where" if alias_fields is None else "having",
                    type(leaf).__name__,
                    "仅支持 列/指标别名 与字面量的比较（eq/ne/gt/gte/lt/lte）",
                )
            )
            continue
        # 时间主轴 >= / < 对提取为 time_filter（半开区间窗口口径）
        if alias_fields is None and comparison["field"] in TIME_FIELDS:
            time_pairs.append((comparison["field"], comparison["operator"], comparison["value"]))
            continue
        filters.append(comparison)
    return filters, time_pairs, rejections


# --------------------------------------------------------------------------- #
# CTE 内联（纯引用）
# --------------------------------------------------------------------------- #
def _inline_pure_ctes(tree: exp.Expression, rejections: list[LiftRejection]) -> exp.Expression:
    """纯引用 CTE 内联：CTE 必须是"单表全量 SELECT"；含计算/过滤/嵌套 CTE 拒升。"""
    with_node = tree.args.get("with_")
    if with_node is None:
        return tree
    for cte in with_node.expressions:
        name = cte.alias_or_name
        inner = cte.this
        pure = (
            isinstance(inner, exp.Select)
            and not inner.args.get("distinct")
            and not inner.args.get("where")
            and not inner.args.get("group")
            and not inner.args.get("having")
            and not inner.args.get("limit")
            and not inner.args.get("order")
            and not inner.args.get("joins")
            and isinstance(inner.args.get("from_"), exp.From)
            and isinstance(inner.args["from_"].this, exp.Table)
            and all(isinstance(p, exp.Star) for p in inner.expressions)
        )
        if not pure:
            rejections.append(
                LiftRejection(
                    "with",
                    f"CTE {name!r}（非纯引用）",
                    "仅支持单表全量 SELECT 的纯引用 CTE（含计算/过滤/连接的 CTE 无法提升）",
                )
            )
            continue
        base_table = inner.args["from_"].this.name
        if base_table != _MAIN_TABLE and base_table not in {t for (t, _c) in _COLUMN_INDEX}:
            rejections.append(
                LiftRejection("with", f"CTE {name!r} 基表 {base_table!r}", "未登记的表")
            )
            continue
        for column in tree.find_all(exp.Column):
            if column.table == name:
                column.set("table", exp.to_identifier(base_table))
        for table in tree.find_all(exp.Table):
            if table.name == name and table is not cte.this:
                table.set("this", exp.to_identifier(base_table))
    tree.set("with", None)
    return tree


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
_AGG_CLASSES = {exp.Sum: "sum", exp.Count: "count", exp.Avg: "avg", exp.Min: "min", exp.Max: "max"}


class _ParseTimeout(Exception):
    """sqlglot 解析超时（内部信号；spec §3.3：超时一律拒升）。"""


def _parse_with_timeout(sql: str) -> exp.Expression:
    """带超时上限的解析，防恶意构造的解析资源炸弹。

    sqlglot 解析是纯 CPU 循环，线程无法强杀——超时后放弃等待（守护线程随
    进程回收），调用方按拒升处理；输入长度上限先行挡住大部分资源炸弹。
    """
    outcome: list[object] = []

    def _run() -> None:
        try:
            outcome.append(sqlglot.parse_one(sql, dialect="duckdb"))
        except Exception as exc:
            outcome.append(exc)

    worker = threading.Thread(target=_run, daemon=True, name="sqlglot-parse-guarded")
    worker.start()
    worker.join(_PARSE_TIMEOUT_S)
    if worker.is_alive() or not outcome:
        raise _ParseTimeout()
    item = outcome[0]
    if isinstance(item, Exception):
        raise item
    return item  # type: ignore[no-any-return]


def lift_sql(sql: str) -> LiftResult:
    """把 LLM 产出的 SQL 提升为 DSL 契约 dict（拒升返回结构化清单）。

    产物只交 ``compiler.compile_sql`` 重编译执行，绝不原样执行输入 SQL。
    """
    rejections: list[LiftRejection] = []
    notes: list[str] = []
    sql = (sql or "").strip()
    if not sql:
        return _one(LiftRejection("parse", "empty", "空 SQL 无法提升"))
    if len(sql) > _MAX_SQL_LENGTH:
        return _one(
            LiftRejection(
                "parse",
                f"oversized({len(sql)} chars)",
                f"SQL 长度超出提升闸门上限（{_MAX_SQL_LENGTH} 字符），拒升",
            )
        )
    try:
        tree = _parse_with_timeout(sql)
    except _ParseTimeout:
        return _one(
            LiftRejection(
                "parse", "timeout", f"SQL 解析超时（>{_PARSE_TIMEOUT_S:.0f}s），按保守性原则拒升"
            )
        )
    except Exception as exc:  # 解析失败：语法非法（保守拒升，不猜意图）
        return _one(LiftRejection("parse", "unparseable", f"SQL 解析失败: {exc}"))

    if not isinstance(tree, exp.Select):
        return _one(
            LiftRejection(
                "statement",
                type(tree).__name__,
                "仅支持单条 SELECT 查询（UNION/子查询根/DDL 等无法提升）",
            )
        )

    tree = _inline_pure_ctes(tree, rejections)
    if rejections:
        return _reject(rejections, notes)

    # ---- FROM / JOIN ---- #
    from_node = tree.args.get("from_")
    if from_node is None or not isinstance(from_node.this, exp.Table):
        return _one(LiftRejection("from", "缺失或非表", "必须有 FROM 主事实表"))
    main_table = _physical_table(from_node.this.name)
    if main_table != _MAIN_TABLE:
        return _one(
            LiftRejection(
                "from",
                f"主表 {main_table!r}",
                f"FROM 主表必须为 {_MAIN_TABLE!r}（DSL 编译器恒以主事实表锚定）",
            )
        )
    # 受控连接期望：表 -> (连接类型, {(物理表, 列)} 集合)；JoinRule.on 为
    # (joined_col, fact_col) 对（semantic/catalog.py JoinRule 契约）
    expected_joins: dict[str, tuple[str, set[tuple[str, str]]]] = {}
    for table, rule in {**catalog.JOIN_RULES, **catalog.FACT_JOIN_RULES}.items():
        cols: set[tuple[str, str]] = set()
        for joined_col, fact_col in rule.on:
            cols.add((table, joined_col))
            cols.add((_MAIN_TABLE, fact_col))
        expected_joins[table] = (rule.join_type, cols)
    for join in tree.args.get("joins") or []:
        joined = join.this
        if not isinstance(joined, exp.Table):
            return _one(LiftRejection("join", "非表连接", "JOIN 目标必须是物理表"))
        table = _physical_table(joined.name)
        expected = expected_joins.get(table)
        if expected is None:
            return _one(
                LiftRejection("join", f"表 {table!r}", "未在语义目录登记受控连接（JOIN_RULES）")
            )
        join_type, expected_cols = expected
        side = (join.side or "").upper()
        actual_type = "left" if side == "LEFT" else "inner"
        if actual_type != join_type:
            return _one(
                LiftRejection(
                    "join",
                    f"表 {table!r} 连接类型 {actual_type!r}",
                    f"语义目录要求 {join_type!r} 连接",
                )
            )
        on = join.args.get("on")
        if on is None:
            return _one(LiftRejection("join", f"表 {table!r} 无 ON", "无条件连接即笛卡尔积，拒升"))
        conjuncts = _flatten_and(on)
        if conjuncts is None:
            return _one(
                LiftRejection(
                    "join", f"表 {table!r} ON 含 OR/NOT", "连接条件必须为等值条件的 AND 组合"
                )
            )
        non_eq = next((c for c in conjuncts if not isinstance(c, exp.EQ)), None)
        if non_eq is not None:
            # 非 EQ 谓词（如 ON ... AND f.dt > '...'）在受控连接中无处安放，
            # 静默丢弃会扩大结果集——宁拒升不错译
            return _one(
                LiftRejection(
                    "join",
                    f"表 {table!r} ON 含 {type(non_eq).__name__}",
                    "受控连接仅支持等值条件（非等值谓词无法提升）",
                )
            )
        actual_cols: set[tuple[str, str]] = set()
        for eq in conjuncts:
            for column in eq.find_all(exp.Column):
                physical = _physical_table(column.table or "")
                actual_cols.add((physical, column.name))
        if actual_cols != expected_cols:
            return _one(
                LiftRejection(
                    "join",
                    f"表 {table!r} 连接条件",
                    f"连接条件必须与语义目录规则一致（{sorted(expected_cols)}）",
                )
            )

    # ---- SELECT ---- #
    is_projection = bool(tree.args.get("distinct"))
    metrics: list[dict] = []
    dims: list[str] = []
    metric_aliases: set[str] = set()
    for projection in tree.expressions:
        if isinstance(projection, exp.Star):
            return _one(LiftRejection("select", "SELECT *", "明细行投影不支持提升（请走探索层）"))
        if not isinstance(projection, exp.Alias):
            return _one(
                LiftRejection(
                    "select",
                    type(projection).__name__,
                    "每个投影必须带显式别名（AS <英文标识符>）",
                )
            )
        alias = projection.alias
        if not re.fullmatch(IDENTIFIER_PATTERN, alias):
            return _one(LiftRejection("select", f"别名 {alias!r}", "别名必须是英文标识符"))
        inner = projection.this
        agg_kind = _AGG_CLASSES.get(type(inner))
        if agg_kind is not None:
            if is_projection:
                return _one(
                    LiftRejection("select", "投影含聚合", "纯维度投影（DISTINCT）不得含聚合")
                )
            count_arg = inner.this
            if isinstance(inner, exp.Count) and isinstance(count_arg, exp.Star):
                return _one(
                    LiftRejection("select", "COUNT(*)", "聚合必须有语义字段（COUNT(*) 无法归因）")
                )
            distinct_arg = isinstance(count_arg, exp.Distinct)
            column = count_arg.expressions[0] if distinct_arg else count_arg
            if not isinstance(column, exp.Column):
                return _one(
                    LiftRejection("select", f"{type(inner).__name__} 非列", "聚合对象必须是列")
                )
            resolved = _resolve_column(column)
            if resolved is None:
                return _one(
                    LiftRejection(
                        "select", f"列 {column.sql()!r}", "未在语义目录登记（或跨表歧义）"
                    )
                )
            metrics.append(
                {
                    "kind": "aggregate",
                    "field": resolved[0],
                    "agg": "count_distinct" if distinct_arg else agg_kind,
                    "alias": alias,
                }
            )
            metric_aliases.add(alias)
            continue
        if isinstance(inner, exp.Column):
            resolved = _resolve_column(inner)
            if resolved is None:
                return _one(
                    LiftRejection("select", f"列 {inner.sql()!r}", "未在语义目录登记（或跨表歧义）")
                )
            dims.append(resolved[0])
            continue
        return _one(
            LiftRejection(
                "select",
                type(inner).__name__,
                "仅支持聚合指标或纯列投影（CASE/函数/算术等构造无法提升）",
            )
        )
    if not metrics and not dims:
        return _one(LiftRejection("select", "空投影", "至少需要一个聚合指标或维度投影"))
    if metrics and not dims and is_projection:
        return _one(LiftRejection("select", "DISTINCT 聚合", "纯维度投影不得含聚合"))

    # ---- WHERE（普通过滤 + 时间窗口对）---- #
    where_node = tree.args.get("where")
    raw_filters, time_pairs, where_rejections = _lift_predicates(where_node)
    rejections.extend(where_rejections)
    if rejections:
        return _reject(rejections, notes)

    time_filter: dict | None = None
    starts: dict[str, object] = {}
    ends: dict[str, object] = {}
    # 多字段/重复窗口显式拒升：DSL time_filter 为单时间字段契约，dict 覆盖
    # 会静默丢窗口扩大结果集（宁拒升不错译）
    pair_fields = [f for f, _op, _v in time_pairs]
    if len(set(pair_fields)) > 1:
        rejections.append(
            LiftRejection(
                "where",
                "多时间字段窗口",
                "DSL time_filter 仅支持单时间字段，多字段窗口无法表达，拒升",
            )
        )
    elif len(time_pairs) > 2 or len({(f, op) for f, op, _v in time_pairs}) != len(time_pairs):
        rejections.append(
            LiftRejection(
                "where",
                "重复时间窗口",
                "同字段重复/多余的区间条件无法取交集语义，拒升",
            )
        )
    for field_name, op, value in time_pairs:
        if op == "gte":
            starts[field_name] = value
        elif op == "lt":
            ends[field_name] = value
        else:
            rejections.append(
                LiftRejection(
                    "where",
                    f"时间列 {field_name} 的 {op}",
                    "时间过滤仅支持 >= 起点与 < 终点的半开区间对（提升为 time_filter）",
                )
            )
    for field_name, start in starts.items():
        end = ends.get(field_name)
        if end is None:
            rejections.append(
                LiftRejection(
                    "where", f"时间列 {field_name}", "时间窗口必须同时给出 >= 起点与 < 终点"
                )
            )
            continue
        time_filter = {
            "range_type": "absolute",
            "absolute": {"start": str(start), "end": str(end)},
            "time_field": field_name,
        }
    if rejections:
        return _reject(rejections, notes)

    # ---- GROUP BY / 投影一致性 ---- #
    group_node = tree.args.get("group")
    group_fields: list[str] = []
    if group_node is not None:
        for column in group_node.expressions:
            if not isinstance(column, exp.Column):
                return _one(LiftRejection("group by", type(column).__name__, "GROUP BY 必须是列"))
            resolved = _resolve_column(column)
            if resolved is None:
                return _one(LiftRejection("group by", f"列 {column.sql()!r}", "未在语义目录登记"))
            group_fields.append(resolved[0])
    if metrics and not group_fields and dims:
        return _one(
            LiftRejection(
                "group by",
                "投影含非聚合列但无 GROUP BY",
                "聚合查询的投影必须与 GROUP BY 一致",
            )
        )
    if not metrics and not is_projection:
        return _one(
            LiftRejection(
                "select",
                "明细行查询",
                "无聚合且无 DISTINCT 的列投影属明细行查询，DSL 无法表达（请走探索层）",
            )
        )
    if is_projection and group_fields:
        return _one(LiftRejection("group by", "DISTINCT + GROUP BY", "纯维度投影无需分组"))
    final_dims = group_fields or dims

    # ---- HAVING ---- #
    having_node = tree.args.get("having")
    agg_alias_map = {(m["agg"], m["field"]): m["alias"] for m in metrics}
    having_filters, _tp, having_rejections = _lift_predicates(
        having_node, alias_fields=metric_aliases, agg_alias=agg_alias_map
    )
    if having_rejections:
        return _reject(having_rejections, notes)

    # ---- ORDER BY ---- #
    order_by: list[dict] = []
    order_node = tree.args.get("order")
    if order_node is not None:
        for ordered in order_node.expressions:
            column = ordered.this
            if not isinstance(column, exp.Column):
                return _one(
                    LiftRejection("order by", type(column).__name__, "ORDER BY 必须是列或别名")
                )
            name = column.name
            if name not in metric_aliases and name not in final_dims:
                return _one(
                    LiftRejection(
                        "order by",
                        f"引用 {name!r}",
                        "ORDER BY 只能引用输出指标别名或分组维度",
                    )
                )
            direction = "desc" if bool(ordered.args.get("desc")) else "asc"
            order_by.append({"field": name, "direction": direction})

    # ---- LIMIT ---- #
    limit = 100
    limit_node = tree.args.get("limit")
    if limit_node is not None:
        try:
            limit = int(limit_node.expression.this)
        except (AttributeError, TypeError, ValueError):
            return _one(LiftRejection("limit", "非整数", "LIMIT 必须是整数字面量"))
        if limit < 1 or limit > _MAX_LIMIT:
            return _one(
                LiftRejection("limit", f"LIMIT {limit}", f"超出 DSL 契约上限（1~{_MAX_LIMIT}）")
            )
    else:
        notes.append("原 SQL 无 LIMIT，提升后应用 DSL 默认上限 100（语义收窄，探索层无此限制）")

    dsl: dict = {
        "metrics": metrics,
        "dimensions": [{"field": f} for f in final_dims],
        "filters": raw_filters,
        "order_by": order_by,
        "limit": limit,
    }
    if time_filter is not None:
        dsl["time_filter"] = time_filter
    if having_filters:
        dsl["having"] = having_filters
    return LiftResult(ok=True, dsl=dsl, rejections=[], notes=notes)
