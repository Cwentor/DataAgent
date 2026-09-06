"""Agent 路由向后兼容转发层（双路由合并后的过渡层）。

历史定位：从旧 agent/router.py 迁移而来，曾持有 Action / RouteResult / route_query
旧三分类路由 API。双路由合并后，判定统一由 agent.router.intent_router 的五分类
判决中心承担，route_query 已无生产调用方并被移除；本文件仅保留个别常量与
既有导入路径的转发（web.service 等消费方无感迁移）。
"""

from __future__ import annotations

from agent.clarify import Clarification, detect_clarifications
from agent.clarify import undefined_metric_terms as undefined_metric_terms
from agent.glossary import GlossaryDoc
from agent.rag import retrieve

CHITCHAT_REPLY = "抱歉，我是数据分析助手，只能回答与业务数据相关的问题。"

# 向后兼容别名（web/service 历史导入路径）
_CHITCHAT_REPLY = CHITCHAT_REPLY

__all__ = [
    "CHITCHAT_REPLY",
    "_CHITCHAT_REPLY",
    "Clarification",
    "GlossaryDoc",
    "detect_clarifications",
    "retrieve",
    "undefined_metric_terms",
]
