"""Hive 建仓 DDL 对拍（二十五期 spec D9）：转译 + 存储子句后处理确定性锁定。

schema.py 是表/列单一来源：本文件锁定 转译 + PRIMARY KEY 剥离 + ROW FORMAT
追加 的确定性产物（首表全文快照 + 全表清单/存储子句性质断言），严禁手写第二份
表结构。null 标记的 SQL 字面量形态（双反斜杠）为 RF-1 转义链路的对拍锚点。
"""

from __future__ import annotations

import re

import pytest

from mock.gmall.schema import DDL_STATEMENTS
from mock.gmall.schema_hive import hive_ddl, hive_table_names


def _table_of(ddl: str) -> str:
    return re.search(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", ddl, re.IGNORECASE).group(1)


def test_table_names_match_source_order():
    assert hive_table_names() == [
        re.search(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", d, re.IGNORECASE).group(1)
        for d in DDL_STATEMENTS
    ]


def test_first_table_ddl_snapshot():
    """首表 DDL 全文快照（转译器/后处理行为漂移即失败）。"""
    assert hive_ddl()[0] == (
        "CREATE TABLE base_province (province_id INT, province_name STRING, "
        "region_id INT, area_code STRING, iso_code STRING, iso_3166_2 STRING, "
        "create_time TIMESTAMP, operate_time TIMESTAMP) "
        "ROW FORMAT DELIMITED FIELDS TERMINATED BY '\\001' "
        "NULL DEFINED AS '\\\\N' STORED AS TEXTFILE"
    )


def test_every_table_has_row_format_and_textfile():
    for ddl in hive_ddl():
        assert "STORED AS TEXTFILE" in ddl, ddl[:80]
        # 分隔符 \001（SQL 八进制转义字面量四字符）与 null 标记双反斜杠
        assert "FIELDS TERMINATED BY '\\001'" in ddl, ddl[:80]
        assert "NULL DEFINED AS '\\\\N'" in ddl, ddl[:80]


def test_no_primary_key_residue():
    # Hive 无主键语义（spec D9）：PRIMARY KEY 剥离后不留残迹
    for ddl in hive_ddl():
        assert not re.search(r"PRIMARY\s+KEY", ddl, re.IGNORECASE), ddl[:80]


def test_type_transpilation_hive_names():
    # sqlglot duckdb->hive：INTEGER→INT、VARCHAR→STRING（计划期实测）
    ddl = hive_ddl()[0]
    assert "province_id INT" in ddl and "province_name STRING" in ddl


def test_ddl_hive_dialect_reparseable():
    # 产物可被 sqlglot hive 方言重解析（语法合法性，形态成立）
    import sqlglot

    for ddl in hive_ddl():
        assert sqlglot.parse_one(ddl, read="hive") is not None


def test_no_side_effect_on_import():
    """import 无副作用：hive_ddl() 纯函数（重复调用结果一致）。"""
    assert hive_ddl() == hive_ddl()


@pytest.mark.parametrize("index", [0, len(DDL_STATEMENTS) // 2, len(DDL_STATEMENTS) - 1])
def test_column_count_preserved(index):
    # 列数与源 DDL 一致（转译不增删列；AST 对拍摆脱类型枚举脆弱性）
    import sqlglot
    from sqlglot import exp

    def _cols(ddl: str) -> list[str]:
        schema = sqlglot.parse_one(ddl, read="hive").find(exp.Schema)
        return [c.name for c in schema.expressions if isinstance(c, exp.ColumnDef)]

    src_names = _cols(DDL_STATEMENTS[index])
    out_names = _cols(hive_ddl()[index])
    assert out_names == src_names
