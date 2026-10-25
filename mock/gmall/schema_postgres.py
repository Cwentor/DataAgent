"""PostgreSQL 版 Gmall 建仓 DDL（二十二期 spec D13）：DuckDB DDL 运行时转译。

转换由 sqlglot（read=duckdb, write=postgres）承担，转译口径（sqlglot 30.x
实测）：INTEGER -> INT、VARCHAR -> TEXT、DECIMAL(p,s) 保真（PG 同义别名
NUMERIC）、TIMESTAMP 保真、无 ENGINE/反引号残留（tests/mock/
test_schema_postgres.py 锁定）。

表/列与 schema.py 单一来源对齐，严禁在本文件手写第二份表结构（漂移风险）。
PG 不强制 MySQL 侧那套二级索引：parity 断言结果集不依赖索引；统计信息由
建仓末尾显式 ANALYZE 收集（spec D13，PG CBO 依赖统计，缺收则 EXPLAIN
计划与扫描熔断口径失真）。
"""

from __future__ import annotations

import re

import sqlglot

from mock.gmall.schema import DDL_STATEMENTS

# 表名正则提取（与 schema_mysql 同 rationale）：sqlglot 的 Create.this 是
# Schema 节点（.name 恒为空串；表名在其内层 Table），源 DDL 文本规整，
# 正则绝对稳定。
_TABLE_NAME_RE = re.compile(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", re.IGNORECASE)


def postgres_ddl() -> list[str]:
    """DuckDB DDL -> PostgreSQL DDL（sqlglot 转译，幂等、确定性）。"""
    return [
        out
        for ddl in DDL_STATEMENTS
        for out in sqlglot.transpile(ddl, read="duckdb", write="postgres")
    ]


def postgres_table_names() -> list[str]:
    """源 DDL 的表名清单（按声明序；供 DROP 重建与对拍，正则提取版本无关）。"""
    names: list[str] = []
    for ddl in DDL_STATEMENTS:
        m = _TABLE_NAME_RE.search(ddl)
        if m is None:
            raise RuntimeError(f"DDL 表名提取失败: {ddl.strip()[:80]!r}")
        names.append(m.group(1))
    return names


def create_postgres_tables(conn) -> None:
    """在给定 PostgreSQL 连接上按序执行全部 DDL（假定空库；不建二级索引）。"""
    for ddl in postgres_ddl():
        with conn.cursor() as cur:
            cur.execute(ddl)


__all__ = ["create_postgres_tables", "postgres_ddl", "postgres_table_names"]
