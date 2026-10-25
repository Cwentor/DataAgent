"""MySQL 建仓 DDL 对拍测试（二十一期 spec D12）：与 DuckDB 版同表同列。

转换由 sqlglot 运行时承担（schema.mysql_ddl），对拍锁定：表集合一致、
每表列名序一致（类型允许方言差异）；类型口径抽查关键映射。
不依赖 MySQL 服务。

表名提取用正则（版本无关）：sqlglot 的 Create.this 是 Schema 节点
（.name 恒为空；表名在内层 Table），依赖 .this.name 会让两侧同构塌缩成
空名、对拍假绿（CI mysql-parity 首跑实锤，2026-10-25）。
"""

from __future__ import annotations

import re

import sqlglot

_TABLE_NAME_RE = re.compile(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", re.IGNORECASE)


def _tables(ddls: list[str], dialect: str) -> dict[str, list[str]]:
    parsed = {}
    for ddl in ddls:
        m = _TABLE_NAME_RE.search(ddl)
        assert m is not None, f"表名提取失败: {ddl.strip()[:80]!r}"
        cols = [
            c.name.lower()
            for c in sqlglot.parse_one(ddl, read=dialect).find_all(sqlglot.exp.ColumnDef)
        ]
        parsed[m.group(1).lower()] = cols
    return parsed


def test_mysql_schema_matches_duckdb_tables_and_columns():
    from mock.gmall.schema import DDL_STATEMENTS
    from mock.gmall.schema_mysql import mysql_ddl

    duck = _tables(DDL_STATEMENTS, "duckdb")
    my = _tables(mysql_ddl(), "mysql")
    assert set(duck) == set(my), f"表集合不一致: {set(duck) ^ set(my)}"
    for t, cols in duck.items():
        assert cols == my[t], f"表 {t} 列不一致: {cols} vs {my[t]}"


def test_mysql_type_mapping_baseline():
    """类型口径抽查：TIMESTAMP->DATETIME / DECIMAL 保真 / 整型收敛。"""
    from mock.gmall.schema_mysql import mysql_ddl

    joined = " ".join(mysql_ddl()).upper()
    assert "TIMESTAMP" not in joined  # 全部转 DATETIME（无时区语义）
    assert "DATETIME" in joined
    assert "DECIMAL(16, 2)" in joined  # 金额精度保真
    assert "INT" in joined and "BIGINT" in joined


def test_mysql_indexes_cover_fact_time_axes():
    """二级索引覆盖全部事实表时间轴（MySQL 无 zoneMap 的补偿，spec D12）。"""
    from mock.gmall.schema_mysql import MYSQL_INDEXES

    joined = " ".join(MYSQL_INDEXES).lower()
    for table in ("order_detail", "fact_start", "fact_page_view", "fact_action", "fact_display"):
        assert table in joined


def test_mysql_ddl_deterministic():
    """转译确定性：两次调用输出一致（幂等建仓前提）。"""
    from mock.gmall.schema_mysql import mysql_ddl

    assert mysql_ddl() == mysql_ddl()
