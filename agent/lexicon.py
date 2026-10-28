"""问法层词表（Lexicon）：趋势/时间序列类提问的触发词单一事实源。

M-P1（2026-10）：memory 与 tool_agent 各自维护的 _TREND_KEYWORDS 已漂移
（18 vs 19 词），合并为本模块单一词表；消费方一律 import 本模块，
严禁再复制词表。问法词属"怎么问"而非"业务事实"，因此独立于
semantic.json（业务事实层），也不随数仓刷新变化。

2026-10-28（冗余审计 §3.2）：heuristic 里同一份 9 词时间序列元组写了两遍
（`_dimensions` / `_order_by`）又加一遍（退款场景判定），共 3 处复制。
提取为 `TIME_SERIES_KEYWORDS`——注意它是 `TREND_KEYWORDS` 的**严格子集**，
不是全部：`环比/同比/走势/变化` 这类是分析语气词，不代表要按时间出序列，
混用会把"GMV 环比"错误地变成时间序列查询。
"""

from __future__ import annotations

import re

# 趋势/时间序列语气词（并集：原 memory 18 词 ∪ 原 tool_agent 18 词）
TREND_KEYWORDS: tuple[str, ...] = (
    "按天",
    "每天",
    "每日",
    "逐日",
    "按周",
    "每周",
    "按月",
    "每月",
    "逐月",
    "趋势",
    "走势",
    "累计",
    "移动平均",
    "滑动平均",
    "环比",
    "同比",
    "补零",
    "补齐",
    "连续",
    "yoy",
    "mom",
    "变化",
)

# 时间序列主轴词：命中即"按时间分组 / 按时间升序"。
# 是 TREND_KEYWORDS 的严格子集，**故意**不含环比/同比/走势/变化/连续 等分析语气词
# （见模块 docstring）。新增词前先确认它真的要求"出时间序列"，否则用 TREND_KEYWORDS。
TIME_SERIES_KEYWORDS: tuple[str, ...] = (
    "每日",
    "按天",
    "每天",
    "趋势",
    "累计",
    "移动平均",
    "滑动平均",
    "补零",
    "补齐",
)

# 明确的时间窗口表达（绝对 / 相对）。
# **故意不含**"全部/所有/历史/累计/至今"——那 5 个是"用户已明确全量口径"的标记，
# 不是时间窗口。混进本正则会让"全部历史"被当成"已有时间窗口"而跳过澄清反问。
TIME_WINDOW_PATTERN: str = (
    r"\d{4}\s*年|\d{1,2}\s*月|\d{1,2}\s*[日号]|\d{4}-\d{2}(-\d{2})?"
    r"|上个月|上月|本月|这个月|今年|去年|今天|昨天|昨日|最近|过去|近\s*\d+|本周|上周"
)
TIME_WINDOW_RE: re.Pattern[str] = re.compile(TIME_WINDOW_PATTERN)

# 显式"全量历史"标记：出现即认为用户已明确时间口径，无需再反问。
ALL_TIME_MARKERS: tuple[str, ...] = ("全部", "所有", "历史", "累计", "至今")

# 宽松时间信号（窗口 ∪ 全量标记）：意图路由只关心"有没有时间线索"，不区分是
# 具体窗口还是全量口径，故用并集。澄清层则必须区分——两者不能共用一条正则。
TIME_SIGNAL_RE: re.Pattern[str] = re.compile(
    TIME_WINDOW_PATTERN + "|" + "|".join(re.escape(m) for m in ALL_TIME_MARKERS)
)

__all__ = [
    "ALL_TIME_MARKERS",
    "TIME_SERIES_KEYWORDS",
    "TIME_SIGNAL_RE",
    "TIME_WINDOW_PATTERN",
    "TIME_WINDOW_RE",
    "TREND_KEYWORDS",
]
