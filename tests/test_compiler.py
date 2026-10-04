"""SQL 编译器单元测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from compiler.sql_compiler import CompileError, compile_sql
from semantic.dsl_schema import QueryDSL


def test_single_metric_no_dimension(conn):
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
        }
    )
    sql = compile_sql(dsl)
    assert 'SUM(f.order_amount) AS "gmv"' in sql
    row = conn.execute(sql).fetchone()
    assert row[0] > 0


def test_dimension_triggers_group_by_and_join(conn):
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "category"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
        }
    )
    sql = compile_sql(dsl)
    assert "JOIN dim_product p" in sql
    assert "GROUP BY p.category" in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) > 0


def test_ratio_metric(conn):
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {
                    "kind": "ratio",
                    "numerator": {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "denominator": {
                        "kind": "aggregate",
                        "field": "user_id",
                        "agg": "count_distinct",
                        "alias": "active_users",
                    },
                    "alias": "arpu",
                }
            ],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
        }
    )
    sql = compile_sql(dsl)
    # 除零防护：分母统一包裹 NULLIF(..., 0)，分母为 0 时产出 NULL 而非 inf/NaN
    assert "COALESCE(" in sql and "NULLIF(" in sql
    assert conn.execute(sql).fetchone()[0] > 0


def test_unregistered_field_rejected():
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "hacked_column", "agg": "sum", "alias": "x"}
            ],
        }
    )
    with pytest.raises(CompileError):
        compile_sql(dsl)


def test_string_literal_escaped():
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "filters": [{"field": "province", "operator": "eq", "value": "O'Reilly"}],
        }
    )
    sql = compile_sql(dsl)
    assert "O''Reilly" in sql


def test_comparison_mom_compiles_cte(conn):
    """环比：应生成 cur/prev 双窗口并输出增长率列。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
                "comparison": "mom",
            },
        }
    )
    sql = compile_sql(dsl)
    assert "WITH cur AS (" in sql
    assert "gmv_prev" in sql
    assert "gmv_mom" in sql
    row = conn.execute(sql).fetchone()
    cur, prev, mom = row
    assert cur > 0 and prev > 0
    assert abs(mom - (cur - prev) / prev) < 1e-9


def test_comparison_yoy_uses_year_shift():
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
                "comparison": "yoy",
            },
        }
    )
    sql = compile_sql(dsl)
    assert "gmv_yoy" in sql
    assert "2023-06-01 00:00:00" in sql


def test_multi_fact_refund_join(conn):
    """多事实表：退款指标应触发 fact_orders LEFT JOIN fact_refunds。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {
                    "kind": "aggregate",
                    "field": "refund_amount",
                    "agg": "sum",
                    "alias": "refund_amount",
                }
            ],
            "dimensions": [{"field": "category"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
        }
    )
    sql = compile_sql(dsl)
    assert "LEFT JOIN fact_refunds r ON r.order_id = f.order_id" in sql
    assert 'SUM(r.refund_amount) AS "refund_amount"' in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) > 0


def test_window_cumsum(conn):
    """窗口累计：每日累计 GMV，需时间维度。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
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
            "dimensions": [{"field": "order_time"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-06-08"},
            },
            "order_by": [{"field": "order_time", "direction": "asc"}],
        }
    )
    sql = compile_sql(dsl)
    assert "SUM(SUM(f.order_amount)) OVER (ORDER BY date_trunc('day', f.order_time))" in sql
    rows = conn.execute(sql).fetchall()
    # 累计单调不减
    vals = [r[1] for r in rows]
    assert all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))


def test_window_moving_avg(conn):
    """窗口移动平均：ROWS BETWEEN N-1 PRECEDING AND CURRENT ROW。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {
                    "kind": "window",
                    "base": {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "func": "moving_avg",
                    "window_size": 7,
                    "alias": "ma7_gmv",
                }
            ],
            "dimensions": [{"field": "order_time"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-06-08"},
            },
        }
    )
    sql = compile_sql(dsl)
    assert "ROWS BETWEEN 6 PRECEDING AND CURRENT ROW" in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) == 7


def test_window_requires_time_dimension():
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
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
        }
    )
    with pytest.raises(CompileError):
        compile_sql(dsl)


def test_fill_gaps_zero_fill(conn):
    """日期补零：spine LEFT JOIN，无数据的日期填 0。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "order_time"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-06-08"},
            },
            "fill_gaps": True,
        }
    )
    sql = compile_sql(dsl)
    assert "generate_series" in sql
    assert 'COALESCE(a."gmv", 0) AS "gmv"' in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) == 7  # 7 个自然日，无缺失


def test_fill_gaps_requires_time_filter():
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "order_time"}],
            "fill_gaps": True,
        }
    )
    with pytest.raises(CompileError):
        compile_sql(dsl)


def test_top_n_partition(conn):
    """分组 Top-N：每省 Top 3 品类。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "province"}, {"field": "category"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "top_n": {
                "n": 3,
                "partition_by": ["province"],
                "order_by": [{"field": "gmv", "direction": "desc"}],
            },
        }
    )
    sql = compile_sql(dsl)
    assert "ROW_NUMBER() OVER (PARTITION BY u.province" in sql
    assert "__rn <= 3" in sql
    rows = conn.execute(sql).fetchall()
    # 每省最多 3 行
    from collections import Counter

    counts = Counter(r[0] for r in rows)
    assert all(n <= 3 for n in counts.values())


def test_multi_metric_comparison(conn):
    """多指标同环比：GMV 与订单数各自输出 prev 与增长率。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"},
                {"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "order_count"},
            ],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
                "comparison": "mom",
            },
        }
    )
    sql = compile_sql(dsl)
    for alias in ("gmv", "order_count"):
        assert f"{alias}_prev" in sql
        assert f"{alias}_mom" in sql
    row = conn.execute(sql).fetchone()
    assert len(row) == 6


def test_multi_fact_ratio_refund_rate(conn):
    """跨事实表比率：退款率 = SUM(refund_amount)/SUM(order_amount)。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {
                    "kind": "ratio",
                    "numerator": {
                        "kind": "aggregate",
                        "field": "refund_amount",
                        "agg": "sum",
                        "alias": "refund_amount",
                    },
                    "denominator": {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "alias": "refund_rate",
                }
            ],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
        }
    )
    sql = compile_sql(dsl)
    assert "LEFT JOIN fact_refunds r" in sql
    rate = conn.execute(sql).fetchone()[0]
    assert 0 <= rate <= 1


def test_comparison_with_dimension_groups_and_pairs(conn):
    """同环比 + 分组维度：cur/prev 按维度分组配对，输出 维度+当前+基准+增长率。

    回归修复：comparison 路径此前静默丢弃 dimensions 的正确性缺陷。
    """
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "category"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
                "comparison": "yoy",
            },
            "order_by": [{"field": "gmv_yoy", "direction": "desc"}],
        }
    )
    sql = compile_sql(dsl)
    assert "GROUP BY p.category" in sql
    assert 'LEFT JOIN prev USING ("category")' in sql
    assert 'cur."category" AS "category"' in sql
    assert 'ORDER BY "gmv_yoy" DESC' in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) > 0
    # 每行校验增长率 = (cur - prev) / prev
    for _category, cur, prev, yoy in rows:
        if prev:
            assert abs(yoy - (cur - prev) / prev) < 1e-6
        else:
            assert yoy is None


def test_comparison_with_time_dimension_aligned_pairing(conn):
    """时间维度 + 环比：按位配对（prev 时间列 date_add 对齐后 JOIN），不再抛错。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "order_time"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
                "comparison": "mom",
            },
            "order_by": [{"field": "order_time", "direction": "asc"}],
        }
    )
    sql = compile_sql(dsl)
    # prev CTE 时间列经 date_add 平移到当前窗口（按位配对）
    assert "date_add(date_trunc('day', f.order_time), INTERVAL 1 MONTH) AS \"order_time\"" in sql
    assert 'LEFT JOIN prev USING ("order_time")' in sql
    assert 'cur."order_time" AS "order_time"' in sql
    assert 'ORDER BY "order_time" ASC' in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) > 0
    for _t, cur, prev, mom in rows:
        if prev:
            assert abs(mom - (cur - prev) / prev) < 1e-6
        else:
            assert mom is None


def test_comparison_yoy_time_dimension_pairing():
    """时间维度 + 同比：prev 时间列 date_add +1 year 对齐（6月每日 GMV 同比场景）。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "order_time"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
                "comparison": "yoy",
            },
        }
    )
    sql = compile_sql(dsl)
    assert "date_add(date_trunc('day', f.order_time), INTERVAL 1 YEAR) AS \"order_time\"" in sql
    assert 'LEFT JOIN prev USING ("order_time")' in sql
    assert 'AS "gmv_yoy"' in sql


def test_projection_dsl_valid_and_invalid_shapes():
    """十九期 M1：metrics 可空的纯维度投影契约（形态越界契约层即拒）。"""
    # 合法：纯维度投影（无指标）
    dsl = QueryDSL.model_validate({"dimensions": [{"field": "brand"}]})
    assert dsl.metrics == []

    # 非法：指标与维度同时为空（无查询目标）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate({})

    # 非法：投影 + 分组 Top-N（无指标可排序）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "dimensions": [{"field": "province"}],
                "top_n": {
                    "n": 3,
                    "partition_by": ["province"],
                    "order_by": [{"field": "province", "direction": "asc"}],
                },
            }
        )

    # 非法：投影 + 日期补零（无指标可填充）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate({"dimensions": [{"field": "brand"}], "fill_gaps": True})

    # 非法：投影 + 同比/环比（无指标可对比）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "dimensions": [{"field": "brand"}],
                "time_filter": {
                    "range_type": "absolute",
                    "absolute": {"start": "2024-05-01", "end": "2024-06-01"},
                    "comparison": "mom",
                },
            }
        )


def test_projection_compiles_distinct_and_orders(conn):
    """十九期 M1：纯维度投影编译 SELECT DISTINCT（无 GROUP BY），执行返回真实取值。"""
    dsl = QueryDSL.model_validate(
        {
            "dimensions": [{"field": "brand"}],
            "order_by": [{"field": "brand", "direction": "asc"}],
            "limit": 100,
        }
    )
    sql = compile_sql(dsl)
    assert "SELECT DISTINCT" in sql
    assert "GROUP BY" not in sql
    assert "ORDER BY" in sql
    assert "LIMIT 100" in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) > 0
    # dim_product 经 fact_orders JOIN 语义：返回的是订单事实中出现过的品牌
    # （mock 数仓订单仅覆盖部分 DIMENSION_MEMBERS 品牌，小米必在）
    values = {r[0] for r in rows}
    assert "小米" in values


def test_projection_respects_limit_cap(conn):
    """十九期 M1 Review Focus #5：投影无界输出由 LIMIT 硬上限兜底。"""
    dsl = QueryDSL.model_validate({"dimensions": [{"field": "brand"}], "limit": 3})
    sql = compile_sql(dsl)
    assert "LIMIT 3" in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) <= 3


def test_having_filters_after_group_by(conn):
    """十九期 M2：HAVING 聚合后过滤——按品类分组后过滤 GMV。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "category"}],
            "having": [{"field": "gmv", "operator": "gt", "value": 0}],
            "order_by": [{"field": "gmv", "direction": "desc"}],
        }
    )
    sql = compile_sql(dsl)
    assert "HAVING" in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) > 0
    # 全部行的 gmv 均 > 0
    assert all(r[1] > 0 for r in rows)


def test_having_field_must_be_metric_alias():
    """HAVING 字段仅限本 DSL 指标别名（非列名/维度名）。"""
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "metrics": [
                    {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                ],
                "dimensions": [{"field": "category"}],
                "having": [{"field": "category", "operator": "gt", "value": 0}],
            }
        )


def test_having_requires_grouping_and_metrics():
    """HAVING 语义前提：必须带分组维度且指标非空；纯投影/标量拒绝。"""
    base_metrics = [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}]
    # 无分组维度（全局标量）带 HAVING
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {"metrics": base_metrics, "having": [{"field": "gmv", "operator": "gt", "value": 0}]}
        )
    # 纯维度投影带 HAVING（无指标别名可过滤）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "dimensions": [{"field": "brand"}],
                "having": [{"field": "gmv", "operator": "gt", "value": 0}],
            }
        )


def test_expression_metric_div_compiles_and_executes(conn):
    """十九期 M2：表达式指标——客单价 = GMV / 订单量（结构化 AST，除零 NULLIF）。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"},
                {"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "orders"},
                {
                    "kind": "expression",
                    "alias": "aov",
                    "expr": {"op": "div", "args": [{"ref": "gmv"}, {"ref": "orders"}]},
                },
            ],
            "dimensions": [{"field": "category"}],
        }
    )
    sql = compile_sql(dsl)
    assert 'NULLIF("orders", 0)' in sql or "NULLIF" in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) > 0
    # 数学正确性：aov = gmv / orders（有订单的品类）
    gmv_idx, orders_idx, aov_idx = 1, 2, 3
    for r in rows:
        if r[orders_idx] and r[orders_idx] > 0:
            assert abs(r[aov_idx] - r[gmv_idx] / r[orders_idx]) < 1e-6


def test_expression_metric_nested_ops(conn):
    """十九期 M2：嵌套 op 形态合法——round(div(ref, lit))。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"},
                {
                    "kind": "expression",
                    "alias": "gmv_k",
                    "expr": {
                        "op": "round",
                        "args": [
                            {"op": "div", "args": [{"ref": "gmv"}, {"lit": 10000}]},
                            {"lit": 2},
                        ],
                    },
                },
            ],
            "dimensions": [],
        }
    )
    sql = compile_sql(dsl)
    assert "ROUND" in sql
    row = conn.execute(sql).fetchone()
    assert row is not None and row[1] is not None


def test_expression_metric_contract_rejections():
    """Review Focus #1：表达式契约层拒绝——op 白名单外 / ref 未声明 / ref 非聚合。"""
    # op 白名单外（结构化 AST 不接受任意函数名）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "metrics": [
                    {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"},
                    {
                        "kind": "expression",
                        "alias": "x",
                        "expr": {"op": "exec", "args": [{"ref": "gmv"}]},
                    },
                ]
            }
        )
    # ref 未在本 DSL 声明
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "metrics": [
                    {
                        "kind": "expression",
                        "alias": "x",
                        "expr": {"op": "abs", "args": [{"ref": "ghost"}]},
                    }
                ]
            }
        )
    # ref 指向非聚合指标（ratio）——单层引用结构性禁环
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "metrics": [
                    {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"},
                    {"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "orders"},
                    {
                        "kind": "ratio",
                        "alias": "rate",
                        "numerator": {"kind": "aggregate", "field": "order_amount", "agg": "sum"},
                        "denominator": {"kind": "aggregate", "field": "order_id", "agg": "count"},
                    },
                    {
                        "kind": "expression",
                        "alias": "x",
                        "expr": {"op": "abs", "args": [{"ref": "rate"}]},
                    },
                ]
            }
        )
    # lit 字面量 NaN/Inf（json.loads 默认接受非标浮点，契约层直接拒绝——
    # 评审收口：堵住 render_literal NaN/Inf 防线的绕行）
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "metrics": [
                    {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"},
                    {
                        "kind": "expression",
                        "alias": "x",
                        "expr": {"op": "div", "args": [{"ref": "gmv"}, {"lit": float("inf")}]},
                    },
                ]
            }
        )


@pytest.mark.parametrize(
    "payload, match",
    [
        # HAVING × 纯投影（无指标别名可过滤）
        (
            {
                "dimensions": [{"field": "brand"}],
                "having": [{"field": "gmv", "operator": "gt", "value": 0}],
            },
            "纯维度投影",
        ),
        # HAVING × 无分组维度（全局标量）
        (
            {
                "metrics": [
                    {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                ],
                "having": [{"field": "gmv", "operator": "gt", "value": 0}],
            },
            "分组维度",
        ),
        # HAVING × 窗口指标
        (
            {
                "metrics": [
                    {
                        "kind": "window",
                        "base": {"field": "order_amount", "agg": "sum", "alias": "gmv_base"},
                        "func": "cumsum",
                        "alias": "gmv_cum",
                    },
                    {"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "orders"},
                ],
                "dimensions": [{"field": "order_time"}],
                "having": [{"field": "orders", "operator": "gt", "value": 0}],
            },
            "窗口",
        ),
        # 表达式 ref 未声明别名
        (
            {
                "metrics": [
                    {
                        "kind": "expression",
                        "alias": "x",
                        "expr": {"op": "abs", "args": [{"ref": "ghost"}]},
                    }
                ]
            },
            "聚合指标别名",
        ),
    ],
)
def test_m2_mutual_exclusion_matrix(payload, match):
    """十九期 M2 验收：互斥组合在契约层显式拒绝（CompileError/ValidationError 矩阵）。"""
    with pytest.raises(ValidationError, match=match):
        QueryDSL.model_validate(payload)
