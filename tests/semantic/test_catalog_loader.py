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


def _overlay(tmp_path, extra_fields=None, extra_join=None, drop_business=None) -> str:
    """写一份覆写配置，可注入额外字段/连接声明；缺省含最小合法业务事实节。

    drop_business：从最小合法业务节集合中删掉指定节（坏配置路径用例）。
    """
    base = {
        "fact_table": "order_detail",
        "fact_tables": ["order_detail", "order_refund_info"],
        "aliases": {"order_detail": "f", "user_info": "u", "sku_info": "sk", "base_province": "pr"},
        "fields": {
            "order_id": {"table": "order_detail", "column": "order_id", "dtype": "int"},
            "split_total_amount": {
                "table": "order_detail",
                "column": "split_total_amount",
                "dtype": "float",
            },
            "order_time": {"table": "order_detail", "column": "order_time", "dtype": "timestamp"},
            "province": {"table": "base_province", "column": "province_name", "dtype": "str"},
            "gender": {"table": "user_info", "column": "gender", "dtype": "str"},
        },
        "join_rules": {
            "base_province": {"type": "inner", "on": [["province_id", "province_id"]]},
            "user_info": {"type": "inner", "on": [["user_id", "user_id"]]},
            "sku_info": {"type": "inner", "on": [["sku_id", "sku_id"]]},
        },
        "fact_join_rules": {
            "order_refund_info": {"type": "left", "on": [["order_id", "order_id"]]}
        },
        # 最小合法业务事实节（六必要节齐全，可选节缺省走内置默认）
        "metrics": [],
        "paid_filter": {
            "field": "order_status",
            "value": "1002",
            "label": "成功支付",
            "anti_question_words": ["未支付", "已取消", "未成交"],
        },
        "default_window": {
            "start": "2024-05-01",
            "end": "2024-05-15",
            "two_period_midpoint": True,
            "description": "缺省分析窗口",
        },
        "reflector_concepts": {},
        "out_of_scope_concepts": [],
        "undefined_metrics": [],
    }
    if extra_fields:
        base["fields"].update(extra_fields)
    if extra_join:
        base["join_rules"].update(extra_join)
    if drop_business:
        for key in drop_business:
            base.pop(key, None)
    p = tmp_path / "semantic.json"
    p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_build_catalog_from_conn_without_overlay(conn):
    """无覆写时：目录与库内物理列一致（默认字段全部存在于 information_schema）。"""
    cat = build_catalog(conn=conn)
    assert cat.fact_table == "order_detail"
    assert "split_total_amount" in cat.columns
    assert cat.columns["split_total_amount"].dtype == "float"
    assert cat.columns["split_total_amount"].label == "实付金额"
    assert cat.join_rules["user_info"].join_type == "inner"
    assert cat.fact_join_rules["order_refund_info"].join_type == "left"


def test_overlay_label_overrides_and_falls_back(conn, tmp_path):
    """中文标签：覆写显式 label 优先；缺省回退内置默认文案；新字段无内置默认为 None。"""
    overlay = _overlay(
        tmp_path,
        extra_fields={
            "province": {
                "table": "base_province",
                "column": "province_name",
                "dtype": "str",
                "label": "收货省份",
            },
            "gross_amount": {
                "table": "order_detail",
                "column": "split_total_amount",
                "dtype": "float",
            },
        },
    )
    cat = build_catalog(conn=conn, overlay_path=overlay)
    assert cat.columns["province"].label == "收货省份"
    assert cat.columns["split_total_amount"].label == "实付金额"
    assert cat.columns["gross_amount"].label is None


def test_overlay_adds_new_field_and_compiles(conn, tmp_path):
    """P0-2 核心：新增逻辑字段只需改配置（映射到库内既有列），即可被编译器引用。"""
    overlay = _overlay(
        tmp_path,
        extra_fields={
            "gross_amount": {
                "table": "order_detail",
                "column": "split_total_amount",
                "dtype": "float",
            }
        },
    )
    refresh_catalog(conn=conn, overlay_path=overlay)
    try:
        assert "gross_amount" in catalog.COLUMNS
        dsl = QueryDSL(
            metrics=[AggregateMetric(field="gross_amount", agg=AggFunc.SUM, alias="gmv")],
        )
        sql = compile_sql(dsl)
        assert "SUM(f.split_total_amount)" in sql
    finally:
        reset_defaults()


def test_overlay_join_rule_renders_structured(conn, tmp_path):
    """连接声明改为结构化（join type + 字段对），渲染出受控 JOIN。"""
    overlay = _overlay(tmp_path)
    refresh_catalog(conn=conn, overlay_path=overlay)
    try:
        dsl = QueryDSL(
            metrics=[AggregateMetric(field="split_total_amount", agg=AggFunc.SUM, alias="gmv")],
            dimensions=[Dimension(field="province")],
        )
        sql = compile_sql(dsl)
        assert "JOIN base_province pr ON pr.province_id = f.province_id" in sql
    finally:
        reset_defaults()


def test_overlay_missing_column_rejected(conn, tmp_path):
    """覆写引用库中不存在的列 -> 拒绝（fail-closed，不静默丢弃）。"""
    overlay = _overlay(
        tmp_path,
        extra_fields={"ghost": {"table": "order_detail", "column": "no_such_col", "dtype": "str"}},
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


def test_reset_defaults_rereads_json():
    """reset_defaults 重读 semantic.json（不回放 import 快照）：运行中改配置即时生效。"""
    mutated = catalog._load_builtin()
    mutated["default_window"] = {
        "start": "2023-01-01",
        "end": "2023-03-31",
        "two_period_midpoint": True,
        "description": "评审收口测试",
    }
    try:
        catalog.apply_builtin(mutated)
        assert catalog.DEFAULT_WINDOW["end"] == "2023-03-31"
    finally:
        reset_defaults()
    # 磁盘上 json 未变 => reset 后恢复锚点窗口（golden 确定性锚）
    assert catalog.DEFAULT_WINDOW["start"] == "2024-05-01"
    assert catalog.DEFAULT_WINDOW["end"] == "2024-05-15"


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
# 维度成员词汇表数据驱动（审计 §3.2-4）：白名单字段 distinct 值 -> DIMENSION_MEMBERS
# --------------------------------------------------------------------------- #
def test_dimension_members_loaded_from_warehouse(conn):
    """build_catalog 按 dimension_member_fields 白名单从库内 distinct 值加载成员。"""
    cat = build_catalog(conn=conn)
    members = cat.dimension_members
    # 省份成员与库内 base_province.province_name 完全一致（数据驱动，非内置默认引用）
    db_provinces = [
        str(r[0])
        for r in conn.execute(
            'SELECT DISTINCT "province_name" FROM "base_province" '
            'WHERE "province_name" IS NOT NULL ORDER BY 1'
        ).fetchall()
    ]
    assert members["province"] == tuple(db_provinces)
    # 一级类目冗余在 order_detail 宽表：成员与明细表 distinct 值一致
    db_categories = [
        str(r[0])
        for r in conn.execute(
            'SELECT DISTINCT "category1_name" FROM "order_detail" '
            'WHERE "category1_name" IS NOT NULL ORDER BY 1'
        ).fetchall()
    ]
    assert members["category1_name"] == tuple(db_categories)
    # 白名单内全部 str 字段均被覆盖（交易域 + 流量域：tm_name/page_id/action_id/
    # channel/entry 等自动纳入，无需额外声明）
    assert set(members) == {
        "province",
        "gender",
        "tm_name",
        "category1_name",
        "category3_name",
        "order_status",
        "user_level",
        "page_id",
        "action_id",
        "channel",
        "entry",
    }


def test_refresh_catalog_syncs_dimension_members_globals(conn):
    """refresh_catalog 把成员词汇表安装到 semantic.catalog 全局，reset 恢复默认。"""
    default_provinces = dict(catalog.DIMENSION_MEMBERS)["province"]
    refresh_catalog(conn=conn)
    try:
        # mock 库与内置默认同源；库内成员经 ORDER BY 稳定排序，故按集合比较
        assert set(catalog.DIMENSION_MEMBERS["province"]) == set(default_provinces)
        # 库中新增省份后重新刷新 -> 词汇表跟随（新增成员离线路径不失明的核心断言）
        conn.execute(
            "INSERT INTO base_province VALUES "
            "(99999, '凌州', 1, '100000', 'CN-99', 'CN-99', '2023-01-01', NULL)"
        )
        refresh_catalog(conn=conn)
        assert "凌州" in catalog.DIMENSION_MEMBERS["province"]
    finally:
        reset_defaults()
        conn.execute("DELETE FROM base_province WHERE province_id = 99999")


def test_build_catalog_without_db_falls_back_to_defaults(tmp_path, monkeypatch):
    """库不可用时回退内置默认词汇表（离线可运行），不报错。

    隔离真实数仓文件（settings.DB_PATH 指向不存在的路径），确保走回退分支。
    """
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "missing.duckdb")
    cat = build_catalog(db_path=tmp_path / "missing.duckdb")
    assert set(cat.dimension_members["province"]) == {
        "北京",
        "天津",
        "山西",
        "内蒙古",
        "河北",
        "上海",
        "江苏",
        "浙江",
        "安徽",
        "福建",
        "江西",
        "山东",
        "重庆",
        "台湾",
        "黑龙江",
        "吉林",
        "辽宁",
        "陕西",
        "甘肃",
        "青海",
        "宁夏",
        "新疆",
        "河南",
        "湖北",
        "湖南",
        "广东",
        "广西",
        "海南",
        "香港",
        "澳门",
        "四川",
        "贵州",
        "云南",
        "西藏",
    }
    assert set(cat.dimension_members["category1_name"]) == {
        "个护化妆",
        "家用电器",
        "手机",
        "电脑办公",
        "食品饮料、保健食品",
    }


def test_loader_aliases_fallback_and_override(conn, tmp_path):
    """aliases：无覆写键回退内置默认；有覆写键则采用 json 值（二期 RF#1）。"""
    from semantic.catalog import COLUMNS

    assert COLUMNS["split_total_amount"].aliases  # 前置：内置默认已登记

    # 无 aliases 覆写键 => 回退内置默认（向后兼容旧 semantic.json）
    cat = build_catalog(conn=conn, overlay_path=_overlay(tmp_path))
    assert cat.columns["split_total_amount"].aliases == COLUMNS["split_total_amount"].aliases

    # 显式覆写 aliases => 采用 json 值
    p = _overlay(
        tmp_path,
        extra_fields={
            "split_total_amount": {
                "table": "order_detail",
                "column": "split_total_amount",
                "dtype": "float",
                "label": "订单金额",
                "aliases": ["gmv", "自定义别名"],
            }
        },
    )
    cat2 = build_catalog(conn=conn, overlay_path=p)
    assert cat2.columns["split_total_amount"].aliases == ("gmv", "自定义别名")


def test_project_default_overlay_consistent_with_builtin_catalog(conn):
    """项目默认覆写（config/semantic.json）必须与内置目录口径一致：核心字段全链路可用。

    回归背景：overlay 是整体替换而非合并，配置曾遗漏维度表导致服务启动后
    字段/意图词在 web 链路消失（内置目录口径与 web 口径分裂、无测试覆盖）。
    店铺域已随 Gmall 模型删除，改以省份/品牌/流量域时间字段为锚点断言。
    """
    # 不传 overlay_path => 走默认 config/semantic.json（与 web 启动 refresh_catalog 同源）
    cat = build_catalog(conn=conn)
    for field in ("tm_name", "category1_name", "sku_name", "page_view_time", "province"):
        assert field in cat.columns, f"默认覆写遗漏逻辑字段 {field}"
    assert cat.aliases.get("base_province") == "pr"
    assert cat.aliases.get("sku_info") == "sk"
    assert cat.join_rules["base_province"].join_type == "inner"
    assert cat.join_rules["sku_info"].join_type == "inner"
    assert cat.columns["tm_name"].label == "品牌"
    assert "品牌" in cat.columns["tm_name"].aliases

    refresh_catalog(conn=conn)
    try:
        dsl = QueryDSL(
            metrics=[AggregateMetric(field="split_total_amount", agg=AggFunc.SUM, alias="gmv")],
            dimensions=[Dimension(field="province")],
        )
        sql = compile_sql(dsl)
        assert "JOIN base_province pr ON pr.province_id = f.province_id" in sql
        assert "GROUP BY pr.province_name" in sql
    finally:
        reset_defaults()


# --------------------------------------------------------------------------- #
# v2 业务事实节（M-P1 收编）：坏配置路径 + 逐节加载一致性
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "missing",
    [
        "metrics",
        "paid_filter",
        "default_window",
        "reflector_concepts",
        "out_of_scope_concepts",
        "undefined_metrics",
    ],
)
def test_overlay_missing_required_business_sections_rejected(conn, tmp_path, missing):
    """坏配置路径（Review Focus 5）：缺任一必要业务节即报错，严禁静默回退内置快照。"""
    overlay = _overlay(tmp_path, drop_business=[missing])
    with pytest.raises(ValueError, match=missing):
        build_catalog(conn=conn, overlay_path=overlay)


def test_overlay_optional_business_sections_fall_back_to_builtin(conn, tmp_path):
    """可选业务节缺失时回退内置默认（逐节容错），六必要节齐全仍可构建。"""
    cat = build_catalog(conn=conn, overlay_path=_overlay(tmp_path))
    assert cat.metrics == ()
    assert cat.paid_filter == {
        "field": "order_status",
        "value": "1002",
        "label": "成功支付",
        "anti_question_words": ["未支付", "已取消", "未成交"],
    }
    assert cat.default_window["start"] == "2024-05-01"
    assert cat.out_of_scope_concepts == ()
    # 可选节走内置默认（同源快照）
    assert cat.table_labels == catalog.TABLE_LABELS
    assert cat.drilldown_dim_fields == catalog.DRILLDOWN_DIM_FIELDS
    assert cat.region_province_mapping == catalog.REGION_PROVINCE_MAPPING
    assert cat.dimension_members_seed == {
        k: tuple(v) for k, v in catalog.DIMENSION_MEMBERS_SEED.items()
    }
    assert cat.metric_aliases == catalog.METRIC_ALIASES
    assert cat.value_labels == catalog.VALUE_LABELS


def test_project_default_overlay_business_sections_consistent(conn):
    """逐节加载一致性：项目默认 semantic.json 的 13 个业务节逐节载入 Catalog，
    且与内置默认（同源快照）口径一致——Web 启动 refresh_catalog 与离线路径同源。"""
    cat = build_catalog(conn=conn)  # 默认走 config/semantic.json
    assert cat.metrics == tuple(catalog.METRICS)
    assert cat.count_entities == tuple(catalog.COUNT_ENTITIES)
    assert cat.paid_filter == catalog.PAID_FILTER
    assert cat.default_window == catalog.DEFAULT_WINDOW
    assert cat.reflector_concepts == catalog.REFLECTOR_CONCEPTS
    assert cat.out_of_scope_concepts == tuple(catalog.OUT_OF_SCOPE_CONCEPTS)
    assert cat.undefined_metrics == tuple(catalog.UNDEFINED_METRICS)
    assert cat.value_labels == catalog.VALUE_LABELS
    assert cat.table_labels == catalog.TABLE_LABELS
    assert cat.drilldown_dim_fields == catalog.DRILLDOWN_DIM_FIELDS
    assert cat.region_province_mapping == catalog.REGION_PROVINCE_MAPPING
    assert cat.dimension_members_seed == {
        k: tuple(v) for k, v in catalog.DIMENSION_MEMBERS_SEED.items()
    }
    assert cat.metric_aliases == catalog.METRIC_ALIASES
    # refresh 同步覆写全局：table_labels 跟随 json
    refresh_catalog(conn=conn)
    try:
        assert catalog.TABLE_LABELS == cat.table_labels
    finally:
        reset_defaults()


def test_builtin_catalog_missing_section_fails_fast(tmp_path):
    """M-P2：semantic.json 缺必要节 => import 时快速失败（严禁静默半目录）。"""
    import json as _json

    from semantic import catalog as _catalog

    bad = tmp_path / "semantic.json"
    data = {"fact_table": "order_detail"}  # 缺 fields/join_rules 等必要节
    bad.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="缺少必要节"):
        _catalog._load_builtin(bad)
