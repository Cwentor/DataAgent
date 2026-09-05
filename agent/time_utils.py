"""共享的时间工具函数"""

from __future__ import annotations

from config import settings
from semantic.dsl_schema import Comparison, Granularity, TimeFilter, TimeRangeType


def default_compare_window(
    comparison: Comparison | None, granularity: Granularity = Granularity.MONTH
) -> TimeFilter:
    """无窗口时的默认趋势窗口（确定性，锚定 AS_OF_DATE）。

    此函数供 heuristic.py 和 trend_analysis_tool.py 共享使用，
    避免默认窗口常量的重复实现。
    """
    if comparison == Comparison.YOY:
        amount, unit = 12, "month"
    elif comparison == Comparison.MOM:
        amount, unit = 6, "month"
    elif granularity == Granularity.QUARTER:
        amount, unit = 4, "quarter"
    elif granularity == Granularity.MONTH:
        amount, unit = 6, "month"
    elif granularity == Granularity.WEEK:
        amount, unit = 12, "week"
    else:
        amount, unit = 30, "day"
    return TimeFilter.model_validate(
        {
            "granularity": granularity.value,
            "range_type": TimeRangeType.RELATIVE.value,
            "relative": {"amount": amount, "unit": unit, "mode": "trailing"},
            "comparison": comparison.value if comparison else Comparison.NONE.value,
            "reference_date": settings.AS_OF_DATE.isoformat(),
        }
    )
