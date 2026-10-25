"""MySQL 版 Gmall 建仓 DDL（二十一期 spec D12）：DuckDB DDL 运行时转译。

转换由 sqlglot（read=duckdb, write=mysql）承担，类型口径：
- INTEGER -> INT、BIGINT -> BIGINT、DECIMAL(p,s) 保真；
- TIMESTAMP -> DATETIME（无时区语义、无 2038 问题，与 spec D8 字面量 CAST
  AS DATETIME 口径配套）；
- VARCHAR -> TEXT（sqlglot 缺省口径，长文本/普通文本均安全）。

表/列与 schema.py 单一来源对齐（tests/mock/test_schema_mysql.py 对拍锁定），
严禁在本文件手写第二份表结构（漂移风险）。二级索引为 MySQL 侧增量：
MySQL 无 zoneMap，事实表时间列与常用过滤列必须显式索引，否则 EXPLAIN
ANALYZE 预检本身即拖垮建仓验证。
"""

from __future__ import annotations

import re

import sqlglot

from mock.gmall.schema import DDL_STATEMENTS

# 表名正则提取（版本无关）：sqlglot 的 Create.this 是 Schema 节点（.name 恒为
# 空串；表名在其内层 Table），依赖 .this.name 会产出空表名 -> DROP TABLE
# IF EXISTS `` 报 1103（CI mysql-parity 首跑实锤，2026-10-25）；源 DDL 文本
# 规整（CREATE TABLE name (），正则绝对稳定。
_TABLE_NAME_RE = re.compile(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", re.IGNORECASE)


def mysql_ddl() -> list[str]:
    """DuckDB DDL -> MySQL DDL（sqlglot 转译，幂等、确定性）。"""
    return [
        out
        for ddl in DDL_STATEMENTS
        for out in sqlglot.transpile(ddl, read="duckdb", write="mysql")
    ]


def mysql_table_names() -> list[str]:
    """源 DDL 的表名清单（按声明序；供 DROP 重建与对拍，正则提取版本无关）。"""
    names: list[str] = []
    for ddl in DDL_STATEMENTS:
        m = _TABLE_NAME_RE.search(ddl)
        if m is None:
            raise RuntimeError(f"DDL 表名提取失败: {ddl.strip()[:80]!r}")
        names.append(m.group(1))
    return names


# 二级索引（spec D12：MySQL 无 zoneMap，时间主轴 + 常用过滤列显式索引）
MYSQL_INDEXES: tuple[str, ...] = (
    "CREATE INDEX idx_order_detail_time ON order_detail (order_time)",
    "CREATE INDEX idx_order_detail_order ON order_detail (order_id)",
    "CREATE INDEX idx_order_detail_user ON order_detail (user_id)",
    "CREATE INDEX idx_order_info_time ON order_info (create_time)",
    "CREATE INDEX idx_payment_info_time ON payment_info (create_time)",
    "CREATE INDEX idx_refund_time ON order_refund_info (create_time)",
    "CREATE INDEX idx_user_info_time ON user_info (create_time)",
    "CREATE INDEX idx_fact_start_ts ON fact_start (ts)",
    "CREATE INDEX idx_fact_page_view_ts ON fact_page_view (ts)",
    "CREATE INDEX idx_fact_action_ts ON fact_action (ts)",
    "CREATE INDEX idx_fact_display_ts ON fact_display (ts)",
)


def create_mysql_tables(conn) -> None:
    """在给定 MySQL 连接上按序执行全部 DDL 与二级索引（假定空库）。"""
    for ddl in mysql_ddl():
        conn.cursor().execute(ddl)
    for idx in MYSQL_INDEXES:
        conn.cursor().execute(idx)


__all__ = ["MYSQL_INDEXES", "create_mysql_tables", "mysql_ddl", "mysql_table_names"]
