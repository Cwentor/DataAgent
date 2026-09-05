"""语义 DSL 数据契约（Pydantic V2）。

本模块是 "自然语言 -> 结构化 DSL -> 确定性 SQL" 链路中的唯一事实来源
（Single Source of Truth）。Agent 只能产出符合本契约的 JSON，编译器只接受本契约
对象，从而在结构上杜绝 SQL 注入与任意 Join。

约束设计要点：
- 所有模型 extra="forbid"，非法/未知字段直接报错；
- 操作符、聚合函数、时间粒度、排序方向均为受限枚举；
- TimeFilter 支持相对/绝对时间跨度与同比/环比标记（comparison）；
- Metric 通过 discriminated union 区分聚合 / 比率 / 窗口指标；
- 顶层支持窗口分析（累计/移动平均）、日期连续补零、分组 Top-N。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# SQL 标识符白名单：alias / order_by.field 等直接拼入 SQL 标识符位置的自由字符串，
# 必须严格限制为 ASCII 字母数字下划线（<=64 字符），从 Pydantic 校验层杜绝标识符注入（P0-1）。
IDENTIFIER_PATTERN: str = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"

# 时间维度逻辑字段白名单（时间主轴 / 窗口函数 / 补零排序所依赖）。
# TimeFilter.time_field 只能取这些字段之一，防止把任意列当作时间轴（越权/语义错误）。
TIME_FIELDS: frozenset[str] = frozenset({"order_time", "refund_time", "register_time"})


# --------------------------------------------------------------------------- #
# 时间相关枚举
# --------------------------------------------------------------------------- #
class Granularity(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"


class Comparison(StrEnum):
    """同比/环比标记。"""

    NONE = "none"
    YOY = "yoy"  # 同比
    MOM = "mom"  # 环比


class TimeRangeType(StrEnum):
    RELATIVE = "relative"
    ABSOLUTE = "absolute"


class RelativeUnit(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class RelativeMode(StrEnum):
    """相对窗口语义。

    - trailing：相对锚点向前滚动 N 个时间单位，窗口为 [锚点-N单位, 锚点)。
    - calendar：自然日历周期，例如 "上个月" 为整个上一个自然月。
    - to_date：自然周期起点至今（MTD/QTD/YTD），窗口为 [周期起点, reference_date)，
      例如 "本月至今" 为 [当月1日, 锚点日期)。
    """

    TRAILING = "trailing"
    CALENDAR = "calendar"
    TO_DATE = "to_date"


class RelativeTime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0, description="时间跨度数值，必须为正整数")
    unit: RelativeUnit = RelativeUnit.DAY
    mode: RelativeMode = RelativeMode.TRAILING


class AbsoluteTime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def _check_order(self) -> AbsoluteTime:
        if self.start >= self.end:
            raise ValueError("absolute.start 必须严格早于 absolute.end")
        return self


class TimeFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    granularity: Granularity = Granularity.DAY
    range_type: TimeRangeType = TimeRangeType.RELATIVE
    relative: RelativeTime | None = None
    absolute: AbsoluteTime | None = None
    comparison: Comparison = Comparison.NONE
    reference_date: date | None = Field(
        default=None,
        description="相对时间窗口的锚点日期；为空时回退到 config.AS_OF_DATE",
    )
    time_field: str = Field(
        default="order_time",
        description=(
            "时间窗口锚定的时间字段（时间主轴），必须是 TIME_FIELDS 白名单中的逻辑字段；"
            "例如退款时序分析可声明 refund_time，解除 order_time 硬编码封锁"
        ),
    )

    @model_validator(mode="after")
    def _check_range(self) -> TimeFilter:
        if self.range_type == TimeRangeType.RELATIVE and self.relative is None:
            raise ValueError("range_type=relative 时必须提供 relative 对象")
        if self.range_type == TimeRangeType.ABSOLUTE and self.absolute is None:
            raise ValueError("range_type=absolute 时必须提供 absolute 对象")
        if self.time_field not in TIME_FIELDS:
            raise ValueError(f"time_field={self.time_field!r} 不在时间字段白名单 TIME_FIELDS 内")
        if (
            self.range_type == TimeRangeType.RELATIVE
            and self.relative is not None
            and self.relative.mode == RelativeMode.TO_DATE
        ):
            if self.relative.unit not in (
                RelativeUnit.MONTH,
                RelativeUnit.QUARTER,
                RelativeUnit.YEAR,
            ):
                raise ValueError("to_date 模式仅支持 month/quarter/year 单位（MTD/QTD/YTD）")
        return self


# --------------------------------------------------------------------------- #
# 指标相关
# --------------------------------------------------------------------------- #
class AggFunc(StrEnum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class AggregateMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["aggregate"] = "aggregate"
    field: str = Field(min_length=1, description="聚合字段（语义目录中的逻辑字段名）")
    agg: AggFunc = AggFunc.SUM
    alias: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN, description="输出列别名")


class RatioMetric(BaseModel):
    """比率指标，如 ARPU = 总GMV / 活跃用户数。

    numerator / denominator 必须是聚合指标，不允许嵌套比率，保证可确定性编译。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ratio"] = "ratio"
    numerator: AggregateMetric
    denominator: AggregateMetric
    alias: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)


class WindowFunc(StrEnum):
    """窗口函数种类（在时间维度上滚动计算）。"""

    CUMSUM = "cumsum"  # 累计求和
    MOVING_AVG = "moving_avg"  # 移动平均


class WindowMetric(BaseModel):
    """窗口指标：先按时间分组聚合，再在时间维度上做滚动窗口计算。

    - cumsum：累计求和（如每日累计 GMV）；
    - moving_avg：移动平均（如近 7 日移动平均 GMV），必须提供 window_size。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["window"] = "window"
    base: AggregateMetric
    func: WindowFunc
    window_size: int | None = Field(
        default=None, ge=1, description="移动平均窗口大小（仅 moving_avg 需要）"
    )
    alias: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)

    @model_validator(mode="after")
    def _check_size(self) -> WindowMetric:
        if self.func == WindowFunc.MOVING_AVG and self.window_size is None:
            raise ValueError("moving_avg 指标必须提供 window_size")
        return self


Metric = Annotated[AggregateMetric | RatioMetric | WindowMetric, Field(discriminator="kind")]


class Dimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    alias: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)


# --------------------------------------------------------------------------- #
# 过滤 / 排序
# --------------------------------------------------------------------------- #
class FilterOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    IN = "in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    BETWEEN = "between"


FilterValue = str | int | float | bool | list[str | int | float | bool]


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    operator: FilterOperator
    value: FilterValue

    @model_validator(mode="after")
    def _check_value(self) -> Filter:
        if self.operator in (FilterOperator.IN, FilterOperator.BETWEEN):
            if not isinstance(self.value, list) or len(self.value) == 0:
                raise ValueError(f"{self.operator.value} 操作要求 value 为非空列表")
            if self.operator == FilterOperator.BETWEEN and len(self.value) != 2:
                raise ValueError("between 操作要求 value 为两个元素的列表 [low, high]")
        else:
            if isinstance(self.value, list):
                raise ValueError(f"{self.operator.value} 操作要求 value 为标量")
        return self


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class OrderBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    direction: SortDirection = SortDirection.DESC


# --------------------------------------------------------------------------- #
# 分组 Top-N
# --------------------------------------------------------------------------- #
class TopN(BaseModel):
    """分组 Top-N：在每个分区内按排序字段取前 N 条。

    例如 "每省 GMV Top 3 品类"：
    - partition_by = ["province"]（分区维度）；
    - order_by = [{field:"gmv", direction:"desc"}]（分区内排序）。
    编译器生成 ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...) 再过滤序号 <= n。
    """

    model_config = ConfigDict(extra="forbid")

    n: int = Field(ge=1, description="每分区保留的前 N 条")
    partition_by: list[str] = Field(min_length=1, description="分区维度（逻辑字段名）")
    order_by: list[OrderBy] = Field(min_length=1, description="分区内排序（指标别名或维度名）")


# --------------------------------------------------------------------------- #
# 顶层 DSL
# --------------------------------------------------------------------------- #
class QueryDSL(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[Metric] = Field(min_length=1)
    dimensions: list[Dimension] = Field(default_factory=list)
    time_filter: TimeFilter | None = None
    filters: list[Filter] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=10000)
    fill_gaps: bool = Field(
        default=False,
        description="日期连续补零：按时间维度补齐缺失日期并用 0 填充指标",
    )
    top_n: TopN | None = Field(default=None, description="分组 Top-N（如每省 Top 3 品类）")
