"""守卫前移（P0）：LLM 生成前按主体过滤可用 Schema/字段/口径元数据。

核心断言：
- 越权字段根本不出现在注入 Agent 的元数据（Prompt 白名单 / RAG 口径）中；
- 启发式确定性路径在生成完成前拒绝越权字段（SecurityError）；
- 事后守卫 apply_policy 仍保留为纵深防御（第二道防线）。
"""

from __future__ import annotations

import pytest

from agent.glossary import scoped_glossary
from agent.heuristic import DeterministicNL2DSL
from agent.pipeline import run_pipeline
from agent.prompts import build_messages, build_system_prompt
from agent.rag import retrieve
from security.errors import SecurityError
from security.guard import apply_policy
from security.scope import (
    is_field_allowed,
    scoped_catalog,
    scoped_field_listing,
    scoped_fields,
    scoped_tables,
)
from semantic.catalog import COLUMNS

_SENSITIVE = {"refund_amount", "refund_id", "refund_status", "refund_time", "split_coupon_amount"}


# --------------------------------------------------------------------------- #
# 字段 / 表 / 目录作用域
# --------------------------------------------------------------------------- #
def test_scoped_fields_none_is_full_catalog():
    assert scoped_fields(None) == frozenset(COLUMNS.keys())


def test_scoped_fields_admin_full():
    assert scoped_fields("admin") == frozenset(COLUMNS.keys())


def test_scoped_fields_restricted_excludes_sensitive():
    allowed = scoped_fields("restricted")
    assert allowed & _SENSITIVE == frozenset()
    assert {"split_total_amount", "category1_name", "province", "user_id"} <= allowed


def test_scoped_tables_restricted():
    """Gmall 模型：restricted 允许表 = 明细锚点 + 订单头 + 商品/用户/省份维度。"""
    assert scoped_tables("restricted") == frozenset(
        {"order_detail", "order_info", "sku_info", "user_info", "base_province"}
    )


def test_scoped_catalog_subset():
    catalog = scoped_catalog("restricted")
    assert set(catalog) == scoped_fields("restricted")
    assert "refund_amount" not in catalog


def test_is_field_allowed():
    assert is_field_allowed("admin", "refund_amount")
    assert not is_field_allowed("restricted", "refund_amount")
    assert is_field_allowed("restricted", "split_total_amount")


def test_unknown_principal_raises():
    with pytest.raises(SecurityError):
        scoped_fields("ghost")


def test_scoped_field_listing_deterministic():
    listing = scoped_field_listing("restricted")
    assert "refund_amount" not in listing
    assert "split_coupon_amount" not in listing
    assert listing == ", ".join(sorted(scoped_fields("restricted")))


# --------------------------------------------------------------------------- #
# 口径文档 RAG 作用域
# --------------------------------------------------------------------------- #
def test_scoped_glossary_restricted_excludes_refund_docs():
    docs = {d.key for d in scoped_glossary("restricted")}
    assert "refund_rate" not in docs
    assert "refund_amount" not in docs
    assert {"gmv", "order_count", "active_users", "arpu", "avg_order_amount"} <= docs


def test_retrieve_scoped_for_restricted():
    docs = retrieve("退款率怎么算？", principal="restricted")
    assert all(d.key != "refund_rate" for d in docs)


def test_retrieve_unscoped_includes_refund():
    docs = retrieve("退款率怎么算？")
    assert docs and docs[0].key == "refund_rate"


# --------------------------------------------------------------------------- #
# Prompt 最小权限注入（守卫前移核心）
# --------------------------------------------------------------------------- #
def test_prompt_whitelist_is_scoped_for_restricted():
    prompt = build_system_prompt("restricted")
    assert "refund_amount" not in prompt
    assert "split_coupon_amount" not in prompt
    # 越权口径约定也不应被注入（不"教"模型用越权字段）
    assert "退款率" not in prompt


def test_prompt_whitelist_full_for_admin():
    prompt = build_system_prompt("admin")
    assert "refund_amount" in prompt
    assert "split_coupon_amount" in prompt


def test_build_messages_scoped_system_prompt():
    messages = build_messages("2024年6月GMV", principal="restricted")
    assert messages[0]["role"] == "system"
    assert "refund_amount" not in messages[0]["content"]
    assert "split_total_amount" in messages[0]["content"]


# --------------------------------------------------------------------------- #
# 启发式确定性路径：生成前拒绝越权字段
# --------------------------------------------------------------------------- #
def test_heuristic_scopes_out_forbidden_fields():
    h = DeterministicNL2DSL()
    with pytest.raises(SecurityError, match="无权"):
        h.run("2024年6月各品类退款金额？", principal="restricted")
    # 合法字段正常生成
    dsl = h.run("2024年6月各品类成功订单GMV？", principal="restricted")
    assert dsl.metrics[0].alias == "gmv"


def test_run_pipeline_scopes_before_generation():
    """run_pipeline(principal=restricted) 查询退款 -> 生成前即拒绝（SecurityError）。"""
    with pytest.raises(SecurityError, match="无权"):
        run_pipeline("各品类成功订单的退款金额是多少？", principal="restricted")


def test_apply_policy_still_defense_in_depth(conn):
    """事后守卫仍是纵深防御：直接构造越权 DSL 也会被 apply_policy 拒绝。"""
    from semantic.dsl_schema import QueryDSL

    dsl = QueryDSL.model_validate(
        {"metrics": [{"kind": "aggregate", "field": "refund_amount", "agg": "sum", "alias": "r"}]}
    )
    with pytest.raises(SecurityError, match="无权"):
        apply_policy(dsl, "restricted")
