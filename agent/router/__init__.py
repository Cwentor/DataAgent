"""Agent 路由包：意图分类与调度分发。

本模块提供生产级意图识别与路由决策中心，将系统从"线性固定执行管线"升级为
"具备自主路径决策能力的 Agent"。

双路由合并（审计 §3.1-1 修复）：意图判定只有单一来源——五分类判决中心
IntentRouter（Fast-Path -> LLM 语义分类 -> 规则兜底）；旧三分类
classify_intent / route_query 已移除。legacy 模块仅保留个别常量与
既有导入路径的转发。
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# 五分类意图体系：唯一判定来源
# --------------------------------------------------------------------------- #
from agent.router.intent_router import (
    CHITCHAT,
    CLARIFY,
    DATA_QUERY,
    GLOSSARY_EXPLAIN,
    INTENT_TYPE_VALUES,
    ROUTING_LATENCY_MS,
    SYSTEM_ACTION,
    IntentRouter,
    IntentType,
    RouteDecision,
    route_decision,
)

# --------------------------------------------------------------------------- #
# 向后兼容转发（保持既有导入路径不变，仅常量与工具函数，无路由判定）
# --------------------------------------------------------------------------- #
from agent.router.legacy import (
    CHITCHAT_REPLY,
    Clarification,
    GlossaryDoc,
    detect_clarifications,
    retrieve,
    undefined_metric_terms,
)

__all__ = [
    "CHITCHAT",
    "CHITCHAT_REPLY",
    "CLARIFY",
    "DATA_QUERY",
    "GLOSSARY_EXPLAIN",
    "INTENT_TYPE_VALUES",
    "ROUTING_LATENCY_MS",
    "SYSTEM_ACTION",
    "Clarification",
    "GlossaryDoc",
    "IntentRouter",
    "IntentType",
    "RouteDecision",
    "detect_clarifications",
    "retrieve",
    "route_decision",
    "undefined_metric_terms",
]
