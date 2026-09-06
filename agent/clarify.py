"""语义澄清反问：识别缺失时间窗口与未定义业务指标。

P0 要求：对"缺失时间窗口"与"未定义业务指标（如 高活用户）"主动反问，
禁止静默回退默认值（例如默认全量历史、或把未定义指标近似映射为已有指标）。

本模块是确定性规则层，在生成 DSL 之前运行，保证澄清判定离线可复现、可单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent.glossary import METRIC_TERMS


@dataclass(frozen=True)
class Clarification:
    """一次澄清反问：槽位类型 + 缺失术语 + 面向用户的问题文本。"""

    kind: str  # missing_time_window | undefined_metric
    term: str | None
    question: str

    def to_dict(self) -> dict[str, str | None]:
        """序列化为 API 可传输的字典（Serialize to dict）。"""
        return {"kind": self.kind, "term": self.term, "question": self.question}


# 未定义、需要用户补充口径的非"用户分群"类指标（后续可从语义目录自动推导）。
_UNDEFINED_METRICS: dict[str, str] = {
    "复购率": "复购率",
    "留存率": "留存率",
    "转化率": "转化率",
    "日活": "日活用户(DAU)",
    "月活": "月活用户(MAU)",
    "dau": "DAU(日活用户)",
    "mau": "MAU(月活用户)",
    "新客": "新客",
    "老客": "老客",
}

# 用户分群类未定义指标：形如「<修饰>用户」，如 高活用户 / 沉默用户 / 流失用户。
# 注意：活跃用户 / 去重用户 已在口径词典中定义，不在此列。
_USER_SEGMENT_RE = re.compile(
    r"(高活跃|高活|低活|中活|沉默|沉睡|流失|复购|付费|忠实|核心|新增|潜在|活跃度)(用户)"
)

# 明确的时间窗口表达（绝对 / 相对）。
_TIME_RE = re.compile(
    r"\d{4}\s*年|\d{1,2}\s*月|\d{1,2}\s*[日号]|\d{4}-\d{2}(-\d{2})?"
    r"|上个月|上月|本月|这个月|今年|去年|今天|昨天|昨日|最近|过去|近\s*\d+|本周|上周"
)

# 显式"全量历史"标记：出现即认为用户已明确时间口径，无需再反问。
_ALL_TIME_MARKERS = ("全部", "所有", "历史", "累计", "至今")

# 分组/分布/排行标记：出现则查询是"分布/排行"型，天然覆盖全量历史，不强制时间窗口。
# 注意：不含"每日/每天/趋势/按月/按天"等时间维度词——时间维度查询反而更需要明确窗口。
_GROUPING_MARKERS = (
    "各",
    "分布",
    "排名",
    "排行",
    "前",
    "top",
    "最高",
    "最多",
    "最少",
    "品类",
    "品牌",
    "省份",
    "每省",
    "每品牌",
    "每品类",
    "按品类",
    "按品牌",
    "按省",
    "分品类",
    "支付状态",
)


def _has_time_expression(query: str) -> bool:
    return bool(_TIME_RE.search(query))


def _has_all_time_marker(query: str) -> bool:
    return any(m in query for m in _ALL_TIME_MARKERS)


def _has_grouping_marker(query: str) -> bool:
    q = query.lower()
    return any(m in q for m in _GROUPING_MARKERS)


def _contains_defined_metric(query: str) -> bool:
    q = query.lower()
    return any(term in q for term in METRIC_TERMS)


def _undefined_metric_clarifications(query: str) -> list[Clarification]:
    q = query.lower()
    seen: set[str] = set()
    out: list[Clarification] = []

    # 1) 显式登记的未定义指标
    for raw, label in _UNDEFINED_METRICS.items():
        if raw.lower() in q and raw not in seen:
            seen.add(raw)
            out.append(
                Clarification(
                    kind="undefined_metric",
                    term=label,
                    question=(
                        f"“{label}”尚未定义业务口径，请补充其定义"
                        "（例如：时间窗口、活跃阈值、过滤条件、计算公式）。"
                    ),
                )
            )

    # 2) 「<修饰>用户」分群指标泛化识别（排除已定义指标）
    for m in _USER_SEGMENT_RE.finditer(query):
        term = m.group(0)
        if term in seen:
            continue
        if term.lower() in METRIC_TERMS:
            continue
        seen.add(term)
        out.append(
            Clarification(
                kind="undefined_metric",
                term=term,
                question=(
                    f"“{term}”尚未定义业务口径，请补充其定义"
                    "（例如：活跃阈值、时间窗口、过滤条件）。"
                ),
            )
        )
    return out


def _missing_time_clarification(query: str) -> Clarification | None:
    if _has_time_expression(query):
        return None
    if _has_all_time_marker(query):
        return None
    if _has_grouping_marker(query):
        return None
    if not _contains_defined_metric(query):
        return None
    return Clarification(
        kind="missing_time_window",
        term=None,
        question=(
            "请补充查询的时间范围（例如：2024年6月 / 上个月 / 最近30天 / 全部历史），"
            "避免默认使用全量历史数据。"
        ),
    )


def detect_clarifications(query: str) -> list[Clarification]:
    """返回需要用户澄清的问题列表；为空表示可直接进入 Text2SQL。"""
    out = _undefined_metric_clarifications(query)
    if not out:
        missing_time = _missing_time_clarification(query)
        if missing_time is not None:
            out.append(missing_time)
    return out


def undefined_metric_terms(query: str) -> list[str]:
    """返回 query 中检测到的未定义业务指标术语（非空即为歧义指标）。

    供 NL2DSL 层（agent.heuristic）使用：宁可拒绝，也不把未定义指标
    （如"高活跃用户"）静默近似映射为已定义指标（如"活跃用户"）。
    """
    return [c.term for c in _undefined_metric_clarifications(query) if c.term]
