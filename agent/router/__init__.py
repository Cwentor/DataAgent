"""Agent 路由包：意图分类与调度分发。

本模块提供生产级意图识别与路由决策中心，将系统从"线性固定执行管线"升级为
"具备自主路径决策能力的 Agent"。

双路由合并（审计 §3.1-1 修复）：意图判定只有单一来源——判决中心
IntentRouter（Fast-Path -> LLM 语义分类 -> 规则兜底）；旧三分类
classify_intent / route_query 已移除。legacy 模块仅保留个别常量与
既有导入路径的转发。

意图体系在原五分类基础上增加 ``UNSAFE_ACTION``（破坏性指令 / 越界敏感实体
的安全拦截意图，审计修复 D2：杜绝 DROP/DELETE 类请求落入 system_action
虚假确认或 data_query 浪费自愈预算）。
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# 意图体系（含 UNSAFE_ACTION 安全拦截意图）：唯一判定来源
# --------------------------------------------------------------------------- #
from agent.router.intent_router import (
    BLOCKED_DESTRUCTIVE,
    BLOCKED_DESTRUCTIVE_REPLY,
    BLOCKED_SENSITIVE,
    BLOCKED_SENSITIVE_REPLY,
    CHITCHAT,
    CLARIFY,
    DATA_QUERY,
    GLOSSARY_EXPLAIN,
    INTENT_TYPE_VALUES,
    ROUTING_LATENCY_MS,
    SYSTEM_ACTION,
    UNSAFE_ACTION,
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
    "BLOCKED_DESTRUCTIVE",
    "BLOCKED_DESTRUCTIVE_REPLY",
    "BLOCKED_SENSITIVE",
    "BLOCKED_SENSITIVE_REPLY",
    "CHITCHAT",
    "CHITCHAT_REPLY",
    "CLARIFY",
    "DATA_QUERY",
    "GLOSSARY_EXPLAIN",
    "INTENT_TYPE_VALUES",
    "ROUTING_LATENCY_MS",
    "SYSTEM_ACTION",
    "UNSAFE_ACTION",
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
