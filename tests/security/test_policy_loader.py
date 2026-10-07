"""权限策略数据驱动（P0-3）单元测试：配置加载 / 参数化 RLS 解析 / 刷新与复位。"""

from __future__ import annotations

import json

import pytest

from security import policy as policy_mod
from security.errors import SecurityError
from security.guard import _resolve_row_filter, apply_policy
from security.policy_loader import (
    build_policies,
    refresh_policies,
    reset_default_policies,
)
from semantic.dsl_schema import AggFunc, AggregateMetric, QueryDSL


def _dsl(field: str = "split_total_amount") -> QueryDSL:
    return QueryDSL(metrics=[AggregateMetric(field=field, agg=AggFunc.SUM, alias="gmv")])


def test_default_policies_still_work():
    """无配置时内置默认策略可用，RLS 参数化模板按主体解析。"""
    out = apply_policy(_dsl(), "analyst")
    province_filters = [f for f in out.filters if f.field == "province"]
    assert len(province_filters) == 1
    assert province_filters[0].value == ["广东", "浙江", "江苏", "北京", "上海"]
    out2 = apply_policy(_dsl(), "restricted")
    pf = [f for f in out2.filters if f.field == "province"]
    assert pf[0].value == ["广东"]


def test_resolve_row_filter_param():
    rf = {"field": "province", "operator": "in", "param": "principal.provinces"}
    resolved = _resolve_row_filter(rf, "analyst")
    assert resolved["value"] == ["广东", "浙江", "江苏", "北京", "上海"]
    assert "param" not in resolved
    # 无 param 的普通谓词原样返回
    plain = {"field": "order_status", "operator": "eq", "value": "1002"}
    assert _resolve_row_filter(plain, "analyst") is plain


def test_resolve_row_filter_missing_attr_rejected():
    with pytest.raises(SecurityError, match="provinces"):
        _resolve_row_filter(
            {"field": "province", "operator": "in", "param": "principal.provinces"}, "admin"
        )


def test_build_policies_from_config(tmp_path):
    """P0-3 核心：策略来自配置（含主体属性表），改配置即改权限。"""
    cfg = {
        "principal_attrs": {"ops": {"provinces": ["上海"]}},
        "policies": {
            "ops": {
                "allowed_tables": ["order_detail", "user_info"],
                "forbidden_columns": ["split_coupon_amount"],
                "row_filters": [
                    {"field": "province", "operator": "in", "param": "principal.provinces"}
                ],
            }
        },
    }
    p = tmp_path / "policies.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    policies, attrs = build_policies(p)
    assert set(policies) == {"ops"}
    assert attrs["ops"]["provinces"] == ["上海"]
    # 安装后新主体可施加策略
    refresh_policies(p)
    try:
        out = apply_policy(_dsl(), "ops")
        pf = [f for f in out.filters if f.field == "province"]
        assert pf[0].value == ["上海"]
    finally:
        reset_default_policies()


def test_build_policies_rejects_empty_allowed_tables(tmp_path):
    cfg = {"policies": {"ghost": {"allowed_tables": []}}}
    p = tmp_path / "policies.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ValueError, match="allowed_tables"):
        build_policies(p)


def test_refresh_and_reset_globals():
    before = set(policy_mod.POLICIES)
    refresh_policies()
    assert set(policy_mod.POLICIES) == before  # 默认配置与内置一致
    reset_default_policies()
    assert set(policy_mod.POLICIES) == before
