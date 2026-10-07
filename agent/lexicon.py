"""问法层词表（Lexicon）：趋势/时间序列类提问的触发词单一事实源。

M-P1（2026-10）：memory 与 tool_agent 各自维护的 _TREND_KEYWORDS 已漂移
（18 vs 19 词），合并为本模块单一词表；消费方一律 import 本模块，
严禁再复制词表。问法词属"怎么问"而非"业务事实"，因此独立于
semantic.json（业务事实层），也不随数仓刷新变化。
"""

from __future__ import annotations

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

__all__ = ["TREND_KEYWORDS"]
