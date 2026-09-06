"""意图路由：旧三分类（已废弃，双路由合并的过渡兼容层）。

历史定位：显式三分类（TEXT2SQL / RAG / CHITCHAT），曾作为 LLM 之前的第一道闸门。
双路由合并后，生产判定已统一由 agent.router.intent_router 的五分类判决中心承担，
本模块仅保留 Intent 枚举与 classify_intent 以维持既有导入路径不变，
classify_intent 已无生产调用方（仅测试引用）。

路由关键词（_RAG_PATTERNS / _CHITCHAT_PATTERNS）的单一来源已迁至五分类路由器。
"""

from __future__ import annotations

from enum import StrEnum

from agent.router.intent_router import _CHITCHAT_PATTERNS, _RAG_PATTERNS


class Intent(StrEnum):
    TEXT2SQL = "text2sql"
    RAG = "rag"
    CHITCHAT = "chitchat"
    DIRECT_ANSWER = "direct_answer"
    CLARIFY = "clarify"


def classify_intent(query: str) -> Intent:
    """把 query 分类为 TEXT2SQL / RAG / CHITCHAT 之一。"""
    q = query.strip().lower()
    if any(p in q for p in _RAG_PATTERNS):
        return Intent.RAG
    if any(p in q for p in _CHITCHAT_PATTERNS):
        return Intent.CHITCHAT
    return Intent.TEXT2SQL
