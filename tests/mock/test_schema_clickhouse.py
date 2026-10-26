"""ClickHouse 建仓 DDL 对拍（二十三期 spec D9）：转译 + 后处理确定性锁定。

schema.py 是表/列单一来源：本文件锁定 转译+Nullable 包裹+MergeTree 后处理 的
确定性产物（首表全文快照 + 全表清单/引擎/排序键性质断言），严禁手写第二份表结构。
"""

from __future__ import annotations

import re

import pytest

from mock.gmall.schema import DDL_STATEMENTS
from mock.gmall.schema_clickhouse import (
    _ORDER_BY_KEYS,
    clickhouse_ddl,
    clickhouse_table_names,
)

_TYPE_TOKEN_RE = re.compile(
    r"(Decimal\(\d+,\s*\d+\)|DateTime|Date|String|Float64|Int64|Int32)(?![A-Za-z0-9_])"
)


def _table_of(ddl: str) -> str:
    return re.search(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", ddl, re.IGNORECASE).group(1)


def test_table_names_match_source_order():
    assert clickhouse_table_names() == [
        re.search(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", d, re.IGNORECASE).group(1)
        for d in DDL_STATEMENTS
    ]


def test_every_table_has_merge_tree_engine():
    for ddl in clickhouse_ddl():
        assert "ENGINE = MergeTree" in ddl, ddl[:80]


def test_time_axis_tables_have_order_by_key():
    for table, col in _ORDER_BY_KEYS.items():
        ddl = next(d for d in clickhouse_ddl() if _table_of(d) == table)
        assert f"ORDER BY (`{col}`)" in ddl
        # Nullable 排序键需显式开启（MergeTree 默认拒绝 Nullable 排序键）
        assert "SETTINGS allow_nullable_key = 1" in ddl


def test_non_axis_tables_order_by_tuple():
    names = set(clickhouse_table_names()) - set(_ORDER_BY_KEYS)
    assert names, "应存在非时间主轴表"
    for ddl in clickhouse_ddl():
        if _table_of(ddl) not in _ORDER_BY_KEYS:
            assert "ORDER BY tuple()" in ddl


def test_all_column_types_wrapped_nullable():
    """全列 Nullable（spec D9）：类型 token 前必须是 Nullable(，且无双重包裹。"""
    for ddl in clickhouse_ddl():
        assert "Nullable(Nullable(" not in ddl
        body = ddl.split("ENGINE")[0]
        tokens = list(_TYPE_TOKEN_RE.finditer(body))
        assert tokens, ddl[:120]
        for m in tokens:
            assert body[: m.start()].endswith("Nullable("), (ddl[:80], m.group(0))


def test_primary_key_constraint_stripped():
    """源 DDL 的 PRIMARY KEY 列约束剥离（CH 主键语义由 ORDER BY 承载，spec D9）。"""
    for ddl in clickhouse_ddl():
        assert "PRIMARY KEY" not in ddl


def test_first_table_snapshot():
    """首表全文快照（确定性锚；转译器升级时此测试显式暴露漂移）。"""
    assert clickhouse_ddl()[0] == (
        "CREATE TABLE base_province (province_id Nullable(Int32), "
        "province_name Nullable(String), region_id Nullable(Int32), "
        "area_code Nullable(String), iso_code Nullable(String), "
        "iso_3166_2 Nullable(String), create_time Nullable(DateTime), "
        "operate_time Nullable(DateTime)) "
        "ENGINE = MergeTree ORDER BY tuple()"
    )


def test_date_type_canonicalized():
    """转译器输出大写 DATE -> 归一为 Date（CH 文档形态）。"""
    ddl = next(d for d in clickhouse_ddl() if _table_of(d) == "user_info")
    assert "Nullable(Date)" in ddl
    assert "Nullable(DATE)" not in ddl


@pytest.mark.parametrize("module_name", ["mock.init_clickhouse"])
def test_init_module_importable_without_side_effects(module_name):
    import importlib

    importlib.import_module(module_name)  # 无副作用（main 仅 __main__ 执行）
