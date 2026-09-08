"""DSL 数据契约单元测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from semantic.dsl_schema import (
    QueryDSL,
    TimeFilter,
)


def _base_dsl(**overrides) -> dict:
    dsl = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
        "filters": [],
        "order_by": [],
        "limit": 100,
    }
    dsl.update(overrides)
    return dsl


def test_minimal_dsl_parses():
    dsl = QueryDSL.model_validate(_base_dsl())
    assert dsl.limit == 100
    assert dsl.metrics[0].alias == "gmv"


def test_scalar_order_by_stripped_when_no_dimension():
    """自洽性校验（模块 B）：无分组维度时的排序无意义，模型层强制抹除 order_by。"""
    dsl = QueryDSL.model_validate(_base_dsl(order_by=[{"field": "gmv", "direction": "desc"}]))
    assert dsl.dimensions == []
    assert dsl.order_by == []


def test_order_by_preserved_when_dimension_present():
    """有分组维度时保留 order_by（非标量，排序有语义）。"""
    dsl = QueryDSL.model_validate(
        _base_dsl(
            dimensions=[{"field": "product_name"}],
            order_by=[{"field": "gmv", "direction": "desc"}],
        )
    )
    assert [(o.field, o.direction.value) for o in dsl.order_by] == [("gmv", "desc")]


def test_default_limit_is_100():
    dsl = QueryDSL.model_validate(
        {"metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}]}
    )
    assert dsl.limit == 100


def test_alias_rejects_sql_injection_payload():
    """P0-1：alias 必须匹配标识符白名单，注入负载直接校验失败。"""
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(
                metrics=[
                    {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "gmv FROM dim_user u JOIN fact_orders f2 ON 1=1 WHERE 1=1 --",
                    }
                ]
            )
        )


def test_order_by_field_rejects_injection():
    """P0-1：order_by.field 同样受标识符白名单约束。"""
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(order_by=[{"field": "gmv; DROP TABLE x", "direction": "desc"}])
        )


def test_dimension_alias_rejects_injection():
    """P0-1：Dimension.alias 受同一白名单约束。"""
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(dimensions=[{"field": "category", "alias": "cat, gmv FROM x"}])
        )


def test_extra_field_is_forbidden():
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(_base_dsl(sneaky="injection"))


def test_between_requires_two_values():
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(filters=[{"field": "order_amount", "operator": "between", "value": [1]}])
        )


def test_in_requires_nonempty_list():
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(filters=[{"field": "province", "operator": "in", "value": []}])
        )


def test_relative_time_requires_relative_object():
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(time_filter={"granularity": "day", "range_type": "relative"})
        )


def test_comparison_marker_supported_in_schema():
    tf = TimeFilter.model_validate(
        {
            "granularity": "month",
            "range_type": "absolute",
            "absolute": {"start": "2024-05-01", "end": "2024-06-01"},
            "comparison": "mom",
        }
    )
    assert tf.comparison.value == "mom"


def test_window_metric_cumsum_parses():
    dsl = QueryDSL.model_validate(
        _base_dsl(
            metrics=[
                {
                    "kind": "window",
                    "base": {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "func": "cumsum",
                    "alias": "cum_gmv",
                }
            ],
            dimensions=[{"field": "order_time"}],
        )
    )
    assert dsl.metrics[0].func.value == "cumsum"


def test_moving_avg_requires_window_size():
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(
                metrics=[
                    {
                        "kind": "window",
                        "base": {
                            "kind": "aggregate",
                            "field": "order_amount",
                            "agg": "sum",
                            "alias": "gmv",
                        },
                        "func": "moving_avg",
                        "alias": "ma_gmv",
                    }
                ],
            )
        )


def test_top_n_parses():
    dsl = QueryDSL.model_validate(
        _base_dsl(
            dimensions=[{"field": "province"}, {"field": "category"}],
            top_n={
                "n": 3,
                "partition_by": ["province"],
                "order_by": [{"field": "gmv", "direction": "desc"}],
            },
        )
    )
    assert dsl.top_n.n == 3
    assert dsl.top_n.partition_by == ["province"]


def test_fill_gaps_default_false():
    dsl = QueryDSL.model_validate(_base_dsl())
    assert dsl.fill_gaps is False


def test_top_n_rejects_empty_partition():
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(
                top_n={
                    "n": 3,
                    "partition_by": [],
                    "order_by": [{"field": "gmv", "direction": "desc"}],
                }
            )
        )
