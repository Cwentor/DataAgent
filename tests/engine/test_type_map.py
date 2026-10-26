"""物理类型归一的表驱动用例（二十四期 T1，spec D2/D8）。

清单来源（2026-10-26 实测，**建仓产物**而非文档猜测）：

- DuckDB：`analytics_sandbox.duckdb` 的
  `SELECT DISTINCT data_type FROM information_schema.columns`（7 种，带精度）；
- MySQL / PostgreSQL / ClickHouse：`mock/gmall/schema_{mysql,postgres,clickhouse}.py`
  的 DDL 转译产物列类型，按各自 `information_schema` / `system.columns` 的
  **报告形态**书写（MySQL 报基名不带精度；PG 报 `integer`/`numeric`/
  `timestamp without time zone`；CH 报 `Nullable(...)` 包裹形态）。

覆盖度是 Review Focus 1：清单缺项会被 `map_dtype` 打成 `ValueError`（启动失败，
可接受），但**降级为 str 不可接受**——故此处逐项断言具体 dtype。
"""

from __future__ import annotations

import pytest

from exec.engine.type_map import map_dtype, strip_type_wrappers

# DuckDB information_schema.columns.data_type（7 种，含精度）
DUCKDB_TYPES = {
    "BIGINT": "int",
    "INTEGER": "int",
    "VARCHAR": "str",
    "TIMESTAMP": "timestamp",
    "DATE": "timestamp",
    "DECIMAL(10,2)": "float",
    "DECIMAL(16,2)": "float",
}

# MySQL information_schema.columns.data_type（基名，不带精度）
MYSQL_TYPES = {
    "bigint": "int",
    "int": "int",
    "text": "str",
    "datetime": "timestamp",
    "date": "timestamp",
    "decimal": "float",
}

# PostgreSQL information_schema.columns.data_type（PG 自有别名）
POSTGRES_TYPES = {
    "bigint": "int",
    "integer": "int",
    "text": "str",
    "numeric": "float",
    "date": "timestamp",
    "timestamp without time zone": "timestamp",
}

# ClickHouse system.columns.type（Nullable 包裹 + 带空格精度）
CLICKHOUSE_TYPES = {
    "Nullable(Int32)": "int",
    "Nullable(Int64)": "int",
    "Nullable(String)": "str",
    "Nullable(DateTime)": "timestamp",
    "Nullable(Date32)": "timestamp",
    "Nullable(Decimal(10, 2))": "float",
    "Nullable(Decimal(16, 2))": "float",
}


@pytest.mark.parametrize(("raw", "expected"), sorted(DUCKDB_TYPES.items()))
def test_duckdb_types_covered(raw, expected):
    assert map_dtype(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), sorted(MYSQL_TYPES.items()))
def test_mysql_types_covered(raw, expected):
    assert map_dtype(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), sorted(POSTGRES_TYPES.items()))
def test_postgres_types_covered(raw, expected):
    assert map_dtype(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), sorted(CLICKHOUSE_TYPES.items()))
def test_clickhouse_types_covered(raw, expected):
    assert map_dtype(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 多层包裹（CH 低基数列可能同时带 Nullable）
        ("LowCardinality(Nullable(String))", "str"),
        ("LowCardinality(String)", "str"),
        # 带精度的字符串/时间（参数括号需剥离但保留后缀词）
        ("VARCHAR(64)", "str"),
        ("CHAR(8)", "str"),
        ("TIMESTAMP(3) WITHOUT TIME ZONE", "timestamp"),
        ("timestamp with time zone", "timestamp"),
        ("DateTime64(3)", "timestamp"),
        ("Decimal(16, 2)", "float"),
        # 大小写与空白归一
        ("  integer  ", "int"),
        ("DOUBLE PRECISION", "float"),
    ],
)
def test_wrapper_stripping(raw, expected):
    assert map_dtype(raw) == expected


@pytest.mark.parametrize("raw", ["Int8", "UInt256", "int128", "uint64"])
def test_integer_width_fallback(raw):
    """位宽形态兜底：`Int8..Int256` / `UInt8..UInt256` 不必逐项登记。"""
    assert map_dtype(raw) == "int"


@pytest.mark.parametrize(
    "raw",
    [
        "Array(String)",  # 容器类型：当前建仓不产出，出现即 schema 越界
        "Map(String, String)",
        "Tuple(Int32, String)",
        "geometry",
        "interval",
    ],
)
def test_unknown_type_fails_closed(raw):
    """未知类型必须抛错并指名原始形态（严禁静默降级为 str）。"""
    with pytest.raises(ValueError, match="未支持的数据库类型"):
        map_dtype(raw)


def test_strip_wrappers_keeps_suffix_words():
    assert strip_type_wrappers("timestamp(3) without time zone") == "TIMESTAMP WITHOUT TIME ZONE"
    assert strip_type_wrappers("Nullable(Decimal(16, 2))") == "DECIMAL"
    assert strip_type_wrappers("LowCardinality(Nullable(String))") == "STRING"
