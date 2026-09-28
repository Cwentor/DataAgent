"""语义目录数据驱动（P0-2）单元测试：物理元数据 + 配置覆写构建目录。"""

from __future__ import annotations

import json

import pytest

from compiler.sql_compiler import CompileError, compile_sql
from config import settings
from semantic import catalog
from semantic.catalog_loader import (
    build_catalog,
    refresh_catalog,
    reset_defaults,
)
from semantic.dsl_schema import AggFunc, AggregateMetric, Dimension, QueryDSL


def _overlay(tmp_path, extra_fields=None, extra_join=None) -> str:
    """写一份覆写配置，可注入额外字段/连接声明。"""
    base = {
        "fact_table": "fact_orders",
        "fact_tables": ["fact_orders", "fact_refunds"],
        "aliases": {"fact_orders": "f", "dim_user": "u", "dim_product": "p"},
        "fields": {
            "order_id": {"table": "fact_orders", "column": "order_id", "dtype": "int"},
            "order_amount": {"table": "fact_orders", "column": "order_amount", "dtype": "float"},
            "order_time": {"table": "fact_orders", "column": "order_time", "dtype": "timestamp"},
            "province": {"table": "dim_user", "column": "province", "dtype": "str"},
            "category": {"table": "dim_product", "column": "category", "dtype": "str"},
        },
        "join_rules": {
            "dim_user": {"type": "inner", "on": [["user_id", "user_id"]]},
            "dim_product": {"type": "inner", "on": [["product_id", "product_id"]]},
        },
        "fact_join_rules": {"fact_refunds": {"type": "left", "on": [["order_id", "order_id"]]}},
    }
    if extra_fields:
        base["fields"].update(extra_fields)
    if extra_join:
        base["join_rules"].update(extra_join)
    p = tmp_path / "semantic.json"
    p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_build_catalog_from_conn_without_overlay(conn):
    """无覆写时：目录与库内物理列一致（默认字段全部存在于 information_schema）。"""
    cat = build_catalog(conn=conn)
    assert cat.fact_table == "fact_orders"
    assert "order_amount" in cat.columns
    assert cat.columns["order_amount"].dtype == "float"
    assert cat.columns["order_amount"].label == "订单金额"
    assert cat.join_rules["dim_user"].join_type == "inner"
    assert cat.fact_join_rules["fact_refunds"].join_type == "left"


def test_overlay_label_overrides_and_falls_back(conn, tmp_path):
    """中文标签：覆写显式 label 优先；缺省回退内置默认文案；新字段无内置默认为 None。"""
    overlay = _overlay(
        tmp_path,
        extra_fields={
            "province": {
                "table": "dim_user",
                "column": "province",
                "dtype": "str",
                "label": "收货省份",
            },
            "gross_amount": {"table": "fact_orders", "column": "order_amount", "dtype": "float"},
        },
    )
    cat = build_catalog(conn=conn, overlay_path=overlay)
    assert cat.columns["province"].label == "收货省份"
    assert cat.columns["order_amount"].label == "订单金额"
    assert cat.columns["gross_amount"].label is None


def test_overlay_adds_new_field_and_compiles(conn, tmp_path):
    """P0-2 核心：新增逻辑字段只需改配置（映射到库内既有列），即可被编译器引用。"""
    overlay = _overlay(
        tmp_path,
        extra_fields={
            "gross_amount": {"table": "fact_orders", "column": "order_amount", "dtype": "float"}
        },
    )
    refresh_catalog(conn=conn, overlay_path=overlay)
    try:
        assert "gross_amount" in catalog.COLUMNS
        dsl = QueryDSL(
            metrics=[AggregateMetric(field="gross_amount", agg=AggFunc.SUM, alias="gmv")],
        )
        sql = compile_sql(dsl)
        assert "SUM(f.order_amount)" in sql
    finally:
        reset_defaults()


def test_overlay_join_rule_renders_structured(conn, tmp_path):
    """连接声明改为结构化（join type + 字段对），渲染出受控 JOIN。"""
    overlay = _overlay(tmp_path)
    refresh_catalog(conn=conn, overlay_path=overlay)
    try:
        dsl = QueryDSL(
            metrics=[AggregateMetric(field="order_amount", agg=AggFunc.SUM, alias="gmv")],
            dimensions=[Dimension(field="province")],
        )
        sql = compile_sql(dsl)
        assert "JOIN dim_user u ON u.user_id = f.user_id" in sql
    finally:
        reset_defaults()


def test_overlay_missing_column_rejected(conn, tmp_path):
    """覆写引用库中不存在的列 -> 拒绝（fail-closed，不静默丢弃）。"""
    overlay = _overlay(
        tmp_path,
        extra_fields={"ghost": {"table": "fact_orders", "column": "no_such_col", "dtype": "str"}},
    )
    with pytest.raises(ValueError, match="no_such_col"):
        build_catalog(conn=conn, overlay_path=overlay)


def test_overlay_missing_table_rejected(conn, tmp_path):
    overlay = _overlay(
        tmp_path,
        extra_fields={"x": {"table": "no_such_table", "column": "c", "dtype": "str"}},
    )
    with pytest.raises(ValueError, match="no_such_table"):
        build_catalog(conn=conn, overlay_path=overlay)


def test_refresh_mutates_globals_and_reset_restores(conn, tmp_path):
    """refresh 安装到 semantic.catalog 全局；reset_defaults 恢复内置默认（测试隔离）。"""
    before = set(catalog.COLUMNS)
    overlay = _overlay(tmp_path)  # 更小字段集
    refresh_catalog(conn=conn, overlay_path=overlay)
    assert set(catalog.COLUMNS) < before  # 覆写字段集更小
    reset_defaults()
    assert set(catalog.COLUMNS) == before


def test_unknown_field_still_rejected_after_refresh(conn, tmp_path):
    """刷新后未登记的字段依然被编译器拒绝（目录即白名单）。"""
    overlay = _overlay(tmp_path)
    refresh_catalog(conn=conn, overlay_path=overlay)
    try:
        dsl = QueryDSL(metrics=[AggregateMetric(field="nope", agg=AggFunc.SUM, alias="x")])
        with pytest.raises(CompileError, match="nope"):
            compile_sql(dsl)
    finally:
        reset_defaults()


# --------------------------------------------------------------------------- #
# 维度成员词汇表数据驱动（审计 §3.2-4）：dim 表 distinct 值 -> DIMENSION_MEMBERS
# --------------------------------------------------------------------------- #
def test_dimension_members_loaded_from_warehouse(conn):
    """build_catalog 从 dim 表 distinct 值加载维度成员，与库内数据一致。"""
    cat = build_catalog(conn=conn)
    members = cat.dimension_members
    # 省份成员与库内 dim_user.province 完全一致（数据驱动，非内置默认引用）
    db_provinces = [
        str(r[0])
        for r in conn.execute('SELECT DISTINCT "province" FROM "dim_user" ORDER BY 1').fetchall()
    ]
    assert members["province"] == tuple(db_provinces)
    db_categories = [
        str(r[0])
        for r in conn.execute('SELECT DISTINCT "category" FROM "dim_product" ORDER BY 1').fetchall()
    ]
    assert members["category"] == tuple(db_categories)
    # dim 表全部 str 字段均被覆盖（brand/gender 自动纳入，无需声明；
    # 默认覆写补齐 dim_shop/product_name 后，shop_name/product_name 同样自动纳入）
    assert set(members) == {
        "province",
        "gender",
        "category",
        "brand",
        "shop_name",
        "product_name",
    }


def test_refresh_catalog_syncs_dimension_members_globals(conn):
    """refresh_catalog 把成员词汇表安装到 semantic.catalog 全局，reset 恢复默认。"""
    default_provinces = dict(catalog.DIMENSION_MEMBERS)["province"]
    refresh_catalog(conn=conn)
    try:
        # mock 库与内置默认同源；库内成员经 ORDER BY 稳定排序，故按集合比较
        assert set(catalog.DIMENSION_MEMBERS["province"]) == set(default_provinces)
        # 库中新增省份后重新刷新 -> 词汇表跟随（新增成员离线路径不失明的核心断言）
        conn.execute("INSERT INTO dim_user VALUES (99999, '西藏', 'M', '2023-01-01')")
        refresh_catalog(conn=conn)
        assert "西藏" in catalog.DIMENSION_MEMBERS["province"]
    finally:
        reset_defaults()
        conn.execute("DELETE FROM dim_user WHERE user_id = 99999")


def test_build_catalog_without_db_falls_back_to_defaults(tmp_path, monkeypatch):
    """库不可用时回退内置默认词汇表（离线可运行），不报错。

    隔离真实数仓文件（settings.DB_PATH 指向不存在的路径），确保走回退分支。
    """
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "missing.duckdb")
    cat = build_catalog(db_path=tmp_path / "missing.duckdb")
    assert set(cat.dimension_members["province"]) == {
        "广东",
        "浙江",
        "江苏",
        "北京",
        "上海",
        "四川",
        "湖北",
        "山东",
    }
    assert set(cat.dimension_members["category"]) == {
        "数码",
        "家电",
        "服饰",
        "美妆",
        "食品",
        "家居",
    }


def test_loader_aliases_fallback_and_override(conn, tmp_path):
    """aliases：无覆写键回退内置默认；有覆写键则采用 json 值（二期 RF#1）。"""
    from semantic.catalog import COLUMNS

    assert COLUMNS["order_amount"].aliases  # 前置：内置默认已登记

    # 无 aliases 覆写键 => 回退内置默认（向后兼容旧 semantic.json）
    cat = build_catalog(conn=conn, overlay_path=_overlay(tmp_path))
    assert cat.columns["order_amount"].aliases == COLUMNS["order_amount"].aliases

    # 显式覆写 aliases => 采用 json 值
    p = _overlay(
        tmp_path,
        extra_fields={
            "order_amount": {
                "table": "fact_orders",
                "column": "order_amount",
                "dtype": "float",
                "label": "订单金额",
                "aliases": ["gmv", "自定义别名"],
            }
        },
    )
    cat2 = build_catalog(conn=conn, overlay_path=p)
    assert cat2.columns["order_amount"].aliases == ("gmv", "自定义别名")


def test_project_default_overlay_keeps_shop_visible(conn):
    """项目默认覆写（config/semantic.json）必须与内置目录口径一致：dim_shop 全链路可用。

    回归背景：overlay 是整体替换而非合并，配置曾遗漏 dim_shop 导致服务启动后
    店铺字段/意图词在 web 链路消失（内置目录口径与 web 口径分裂、无测试覆盖）。
    """
    # 不传 overlay_path => 走默认 config/semantic.json（与 web 启动 refresh_catalog 同源）
    cat = build_catalog(conn=conn)
    for field in ("shop_id", "shop_name", "product_name"):
        assert field in cat.columns, f"默认覆写遗漏逻辑字段 {field}"
    assert cat.aliases.get("dim_shop") == "s"
    assert cat.join_rules["dim_shop"].join_type == "inner"
    assert cat.columns["shop_name"].label == "门店名称"
    assert "店铺" in cat.columns["shop_name"].aliases

    refresh_catalog(conn=conn)
    try:
        dsl = QueryDSL(
            metrics=[AggregateMetric(field="order_amount", agg=AggFunc.SUM, alias="gmv")],
            dimensions=[Dimension(field="shop_name")],
        )
        sql = compile_sql(dsl)
        assert "JOIN dim_shop s ON s.shop_id = f.shop_id" in sql
        assert "GROUP BY s.shop_name" in sql
    finally:
        reset_defaults()
