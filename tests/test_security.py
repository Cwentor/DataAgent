"""安全守卫单元测试：表级/列级/行级 RLS + 默认无策略。"""

from __future__ import annotations

import pytest

from agent.pipeline import run_pipeline
from compiler.sql_compiler import compile_sql
from security.guard import SecurityError, apply_policy
from semantic.dsl_schema import QueryDSL


def _dsl(**over):
    base = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
    }
    base.update(over)
    return QueryDSL.model_validate(base)


# --------------------------------------------------------------------------- #
# 表级权限
# --------------------------------------------------------------------------- #
def test_restricted_denies_refund_table():
    """受限主体引用退款表字段 -> SecurityError。"""
    dsl = _dsl(
        metrics=[
            {"kind": "aggregate", "field": "refund_amount", "agg": "sum", "alias": "refund_amount"}
        ],
    )
    with pytest.raises(SecurityError):
        apply_policy(dsl, "restricted")


def test_admin_allows_refund_table():
    dsl = _dsl(
        metrics=[
            {"kind": "aggregate", "field": "refund_amount", "agg": "sum", "alias": "refund_amount"}
        ],
    )
    out = apply_policy(dsl, "admin")
    assert out == dsl


# --------------------------------------------------------------------------- #
# 列级权限
# --------------------------------------------------------------------------- #
def test_restricted_denies_discount_column():
    dsl = _dsl(
        metrics=[
            {"kind": "aggregate", "field": "discount_amount", "agg": "sum", "alias": "discount"}
        ],
    )
    with pytest.raises(SecurityError):
        apply_policy(dsl, "restricted")


def test_analyst_allows_discount_column():
    dsl = _dsl(
        metrics=[
            {"kind": "aggregate", "field": "discount_amount", "agg": "sum", "alias": "discount"}
        ],
    )
    out = apply_policy(dsl, "analyst")
    assert out.metrics[0].alias == "discount"


# --------------------------------------------------------------------------- #
# 行级 RLS
# --------------------------------------------------------------------------- #
def test_rls_injects_province_filter(conn):
    """RLS：受限主体只能看广东，编译 SQL 应含 province IN 且结果只含广东。"""
    dsl = _dsl(
        dimensions=[{"field": "province"}],
    )
    guarded = apply_policy(dsl, "restricted")
    assert len(guarded.filters) == 1
    assert guarded.filters[0].field == "province"
    assert guarded.filters[0].value == ["广东"]

    sql = compile_sql(guarded)
    assert "u.province IN ('广东')" in sql
    rows = conn.execute(sql).fetchall()
    assert all(r[0] == "广东" for r in rows)


def test_rls_analyst_limits_five_provinces(conn):
    dsl = _dsl(dimensions=[{"field": "province"}])
    guarded = apply_policy(dsl, "analyst")
    sql = compile_sql(guarded)
    rows = conn.execute(sql).fetchall()
    provinces = {r[0] for r in rows}
    assert provinces <= {"广东", "浙江", "江苏", "北京", "上海"}


# --------------------------------------------------------------------------- #
# RLS 对抗用例矩阵（§4 项5）：受限主体 × 敏感指标/敏感列
# --------------------------------------------------------------------------- #
def test_rls_adversarial_matrix_restricted():
    """受限主体尝试各类敏感指标（退款列/折扣列/退款表）必须全部被拒。"""
    from security.scope import scoped_fields

    allowed = scoped_fields("restricted")
    sensitive = ["refund_amount", "refund_id", "refund_status", "refund_time", "discount_amount"]
    # 敏感字段根本不在作用域内（守卫前移：不进模型视野）
    for field in sensitive:
        assert field not in allowed, field

    # 强行构造引用敏感字段的 DSL -> apply_policy 纵深防御拒绝
    for field in sensitive:
        dsl = _dsl(
            metrics=[{"kind": "aggregate", "field": field, "agg": "sum", "alias": "sneaky"}],
        )
        with pytest.raises(SecurityError):
            apply_policy(dsl, "restricted")


def test_rls_adversarial_matrix_admin_allows():
    """同一批敏感字段对 admin 全部放行（对照基线）。"""
    for field in ["refund_amount", "discount_amount"]:
        dsl = _dsl(
            metrics=[{"kind": "aggregate", "field": field, "agg": "sum", "alias": "sneaky"}],
        )
        out = apply_policy(dsl, "admin")
        assert out.metrics[0].field == field


def test_rls_row_filter_always_injected_even_with_user_filter(conn):
    """用户自带其他过滤（如类目）时，RLS（广东）必须始终强制注入，不可被绕过。"""
    dsl = _dsl(
        dimensions=[{"field": "province"}],
        filters=[{"field": "category", "operator": "eq", "value": "数码"}],
    )
    guarded = apply_policy(dsl, "restricted")
    sql = compile_sql(guarded)
    assert "u.province IN ('广东')" in sql
    rows = conn.execute(sql).fetchall()
    assert rows, "RLS 合并后仍应有广东数据"
    assert all(r[0] == "广东" for r in rows)


def test_rls_adversarial_via_run_pipeline():
    """端到端对抗：受限主体用启发式可识别的敏感指标 -> 生成前 SecurityError 拒绝。

    "优惠金额"等未登记说法不进入启发式指标词典，会先以 PipelineError 拒绝
    （同样不可得数据）；这里只断言启发式能识别、从而走越权路径的敏感指标。
    """
    for query in (
        "2024年6月各省退款金额是多少？",
        "各品类的退款率是多少？",
    ):
        with pytest.raises(SecurityError):
            run_pipeline(query, principal="restricted")


# --------------------------------------------------------------------------- #
# 默认无策略 / 未登记主体
# --------------------------------------------------------------------------- #
def test_none_principal_is_noop():
    dsl = _dsl()
    assert apply_policy(dsl, None) is dsl


def test_unknown_principal_raises():
    dsl = _dsl()
    with pytest.raises(SecurityError):
        apply_policy(dsl, "ghost")


def test_run_pipeline_accepts_principal():
    """run_pipeline(query, principal=...) 端到端：受限主体查询退款 -> 拒绝。"""
    with pytest.raises(SecurityError):
        run_pipeline("各品类成功订单的退款金额是多少？", principal="restricted")


def test_expression_metric_guard_collects_underlying_fields():
    """十九期 M2：表达式指标的权限检查回溯到被引用聚合指标的字段——

    restricted 主体（禁 discount_amount/refund_amount）通过表达式引用
    discount_amount 聚合时必须被拒（Review Focus：禁列不得借表达式绕过）。
    """
    with pytest.raises(SecurityError):
        apply_policy(
            QueryDSL.model_validate(
                {
                    "metrics": [
                        {
                            "kind": "aggregate",
                            "field": "discount_amount",
                            "agg": "sum",
                            "alias": "discount",
                        },
                        {
                            "kind": "expression",
                            "alias": "discount_k",
                            "expr": {
                                "op": "round",
                                "args": [
                                    {"op": "div", "args": [{"ref": "discount"}, {"lit": 1000}]},
                                    {"lit": 2},
                                ],
                            },
                        },
                    ]
                }
            ),
            "restricted",
        )


def test_expression_metric_guard_allows_authorized_fields():
    """有权限字段的表达式指标正常通过守卫。"""
    apply_policy(
        QueryDSL.model_validate(
            {
                "metrics": [
                    {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"},
                    {
                        "kind": "expression",
                        "alias": "aov",
                        "expr": {"op": "div", "args": [{"ref": "gmv"}, {"lit": 100}]},
                    },
                ]
            }
        ),
        "analyst",
    )
