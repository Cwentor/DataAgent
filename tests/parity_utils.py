"""双后端一致性测试共享工具（二十二期从 test_dual_backend_parity.py 提取）。

比对口径（与二十一期逐字一致，勿改）：
- 数值按 rel=1e-5 + abs=1e-6 双口径容差（DuckDB float vs 各后端 DECIMAL 经
  normalize_cell 统一 float 后的精度差，二十一期 Review Focus #4）；
- 行序无关（排序后逐行比对）：排序键 repr(row)——各引擎对同值行的
  tie-break 顺序不构成契约，行集合一致才是语义等价；
- datetime 统一取日期面（date_trunc 返回 TIMESTAMP(00:00:00) 与各后端
  截断语义等价）。

导入方式：tests/ 为包（含 __init__.py），用 ``from tests.parity_utils import ...``
包内绝对导入。
"""

from __future__ import annotations


def norm_cell(v):
    """跨引擎类型归一（比对口径，非行为差异）。"""
    from datetime import datetime as datetime_cls
    from decimal import Decimal

    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime_cls):
        # datetime 是 date 的子类：date 实例不会进本分支
        # （isinstance(date_obj, datetime) 为 False），无需额外排除
        return v.date()
    return v


def assert_rows_equal(duck_rows, other_rows):
    """行集合一致断言（行序无关 + 数值 rel/abs 双口径容差）。"""
    import pytest

    assert len(duck_rows) == len(other_rows), "行数不一致"
    duck_sorted = sorted(([norm_cell(v) for v in r] for r in duck_rows), key=repr)
    other_sorted = sorted(([norm_cell(v) for v in r] for r in other_rows), key=repr)
    for rd, rm in zip(duck_sorted, other_sorted, strict=True):
        assert len(rd) == len(rm)
        for vd, vm in zip(rd, rm, strict=True):
            if vd is None or vm is None:
                assert vd is vm, f"空值不一致: {vd!r} vs {vm!r}"
                continue
            if isinstance(vd, (int, float)) and isinstance(vm, (int, float)):
                # 容差口径（二十一期 spec §9 Decimal 精度差异）：DECIMAL 除法
                # 舍入差为**绝对量级**，abs 兜底小值、rel 覆盖大值（CI 实测校准）
                assert vm == pytest.approx(vd, rel=1e-5, abs=1e-6), f"数值漂移: {vd!r} vs {vm!r}"
            else:
                assert vm == vd, f"值不一致: {vd!r} vs {vm!r}"


def compile_dsl(dsl, dialect: str) -> str:
    """按方言编译（dict 先过契约校验；临时切换 SQL_DIALECT 用完即还原）。"""
    import config.settings as settings
    from compiler.sql_compiler import compile_sql
    from semantic.dsl_schema import QueryDSL

    model = dsl if isinstance(dsl, QueryDSL) else QueryDSL.model_validate(dsl)
    old = settings.SQL_DIALECT
    settings.SQL_DIALECT = dialect
    try:
        return compile_sql(model)
    finally:
        settings.SQL_DIALECT = old


def abs_window_dsl(granularity: str, start: str, end: str, fill_gaps: bool = False) -> dict:
    """绝对窗口 GMV 按时间分组 DSL（形态取自 golden_dataset；limit 给足以
    覆盖 5 年 day 粒度 1826 行——契约默认 limit=100 会截断补零序列）。"""
    dsl = {
        "metrics": [
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
        ],
        "dimensions": [{"field": "order_time", "alias": None}],
        "time_filter": {
            "granularity": granularity,
            "range_type": "absolute",
            "relative": None,
            "absolute": {"start": start, "end": end},
            "comparison": "none",
            "reference_date": None,
            "time_field": "order_time",
        },
        "filters": [],
        "limit": 2000,
    }
    if fill_gaps:
        dsl["fill_gaps"] = True
    return dsl


__all__ = ["abs_window_dsl", "assert_rows_equal", "compile_dsl", "norm_cell"]
