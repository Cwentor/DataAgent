"""PostgreSQL 版建仓 DDL 对拍（二十二期 spec D13）：与 schema.py 单一来源对齐。

不连服务（CI postgres-parity job 承担服务级验证）：转译产物结构断言 +
表名提取 + 与 MySQL 版表集合一致性（同一 schema.py 源）。
"""

from __future__ import annotations

import sqlglot

from mock.gmall.schema import DDL_STATEMENTS
from mock.gmall.schema_mysql import mysql_table_names
from mock.gmall.schema_postgres import postgres_ddl, postgres_table_names


def test_table_names_match_source_and_mysql():
    """表名提取与源 DDL/MySQL 版三方一致（同一 schema.py 单一来源）。"""
    assert postgres_table_names() == mysql_table_names()
    assert len(postgres_table_names()) == len(DDL_STATEMENTS)


def test_ddl_transpiled_statement_count_matches():
    """每条源 DDL 转译产出一组语句，且 CREATE TABLE 数与源 DDL 条数一致。"""
    out = postgres_ddl()
    creates = [s for s in out if s.upper().startswith("CREATE TABLE")]
    assert len(creates) == len(DDL_STATEMENTS)


def test_ddl_reparse_roundtrip():
    """转译产物逐条可被 sqlglot 以 postgres 方言再次解析（语法合法性锁定）。"""
    for ddl in postgres_ddl():
        sqlglot.parse_one(ddl, read="postgres")


def test_ddl_no_mysql_dialect_leakage():
    """PG DDL 不得残留 MySQL 方言痕迹（反引号 / ENGINE 子句）。"""
    out = "\n".join(postgres_ddl())
    assert "`" not in out, "PG DDL 不得残留反引号（MySQL 方言泄漏）"
    assert "ENGINE" not in out.upper()


def test_core_types_preserved():
    """类型口径：时间列与文本列在转译后仍可识别（sqlglot 版本升级即在此暴露）。"""
    out = "\n".join(postgres_ddl()).upper()
    assert "TIMESTAMP" in out
    assert "TEXT" in out or "VARCHAR" in out
