"""确定性边界守卫：原始 SQL 网关（"LLM 永不产出裸 SQL"的强制执行点）。

架构铁律（AGENTS.md）：任何数据查询必须经过 semantic DSL 契约 ->
compiler 确定性编译。本模块在**网关层**再拦一道：

1. 字符串形态的 payload 直接拒绝（DSL 必须是契约对象 / 结构化 dict）；
2. 结构化 payload 内出现 ``sql`` 键或 SQL 语句形态文本立即拒绝
   （防 Planner/LLM 把 SQL 塞进任何自由字段）；
3. dict 进入 DSL 契约前强制 ``QueryDSL.model_validate``（extra="forbid"，
   未知字段即拒绝，结构上封死夹带通道）。

命中即抛 ``GuardrailViolation``（网关层拒绝，不静默降级）。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from semantic.dsl_schema import QueryDSL


class GuardrailViolation(Exception):
    """网关层守卫违规：尝试绕过 DSL 契约（裸 SQL / 非法形态 / 越权字段）。"""


# SQL 语句形态启发式：DML/DDL/DCL 关键字开头或出现的组合特征
_SQL_STATEMENT_RE = re.compile(
    r"\b(select\s.+\sfrom\s|insert\s+into|update\s+\w+\s+set|delete\s+from|"
    r"drop\s+(table|view|schema)|alter\s+table|create\s+(table|view|schema)|"
    r"truncate\s+table|grant\s|revoke\s|attach\s+database|copy\s+.+\s+to\b|"
    r"read_csv\s*\(|read_parquet\s*\(|pragma\s+)\b",
    re.IGNORECASE | re.DOTALL,
)

# dict payload 中不允许出现的键（DSL 契约内的合法键由 extra=forbid 兜底，
# 此处显式列出高语义风险键做**前置报错**，给出更准确的错误信息）
_FORBIDDEN_KEYS = frozenset({"sql", "raw_sql", "query_sql", "statement"})


def looks_like_sql(text: str) -> bool:
    """判断自由文本是否呈 SQL 语句形态（保守启发式，宁可误拒不放行）。"""
    if not text:
        return False
    return bool(_SQL_STATEMENT_RE.search(text))


def reject_raw_sql_text(text: str, *, where: str) -> None:
    """自由文本中出现 SQL 形态即抛 GuardrailViolation。"""
    if looks_like_sql(text):
        raise GuardrailViolation(
            f"[{where}] 检测到裸 SQL 形态文本。所有查询必须经 DSL 契约编译，"
            "禁止直接产出/传递 SQL。"
        )


def _walk_strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    """深度遍历结构化 payload，收集所有字符串叶子及其路径。"""
    found: list[tuple[str, str]] = []
    if isinstance(node, str):
        found.append((path, node))
    elif isinstance(node, dict):
        for key, value in node.items():
            found.extend(_walk_strings(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, (list, tuple)):
        for i, item in enumerate(node):
            found.extend(_walk_strings(item, f"{path}[{i}]"))
    return found


def validate_dsl_payload(dsl_payload: Any, *, where: str = "gateway") -> QueryDSL:
    """网关层 DSL 载荷校验：结构化、无 SQL 键、无 SQL 形态文本、强契约重建。

    通过后返回经 ``QueryDSL.model_validate`` 重建的强契约对象（调用方应只
    使用该返回值，不得使用原始 payload）。
    """
    if isinstance(dsl_payload, QueryDSL):
        payload_dict = dsl_payload.model_dump(mode="json")
    elif isinstance(dsl_payload, dict):
        payload_dict = dsl_payload
    elif isinstance(dsl_payload, str):
        raise GuardrailViolation(
            f"[{where}] DSL 载荷为字符串形态（疑似裸 SQL/自由文本）。"
            "必须提供符合 semantic.dsl_schema.QueryDSL 的结构化对象。"
        )
    else:
        raise GuardrailViolation(f"[{where}] 不支持的 DSL 载荷类型: {type(dsl_payload).__name__}")

    for key in _FORBIDDEN_KEYS:
        if key in payload_dict:
            raise GuardrailViolation(
                f"[{where}] DSL 载荷包含被禁止的键 {key!r}："
                "查询必须经 DSL 契约 -> 确定性编译器，禁止夹带 SQL。"
            )

    for path, text in _walk_strings(payload_dict):
        if not path.endswith(("alias", "field", "metric", "dimension", "time_field", "by", "name")):
            reject_raw_sql_text(text, where=f"{where}:{path}")

    try:
        return QueryDSL.model_validate(payload_dict)
    except ValidationError as exc:
        raise GuardrailViolation(f"[{where}] DSL 载荷不符合契约: {exc}") from exc


__all__ = ["GuardrailViolation", "looks_like_sql", "reject_raw_sql_text", "validate_dsl_payload"]
