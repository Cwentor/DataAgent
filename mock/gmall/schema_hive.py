"""Hive 版 Gmall 建仓 DDL（二十五期 spec D9）：DuckDB DDL 运行时转译。

转换由 sqlglot（read=duckdb, write=hive）承担 + 两步确定性后处理：
1. 剥离 PRIMARY KEY 列约束（Hive 无主键语义，唯一性由确定性生成器保证，
   与 CH 同理）；
2. 追加表级存储子句（sqlglot 不覆盖 ROW FORMAT/STORED AS）：分隔符 \\001
   为 LazySimpleSerDe 原生默认——规避文本值逗号/引号歧义（spec D10，与
   HiveAdapter.CSV_FIELD_SEP 同源）；null 标记 **SQL 字面量双反斜杠 '\\N'**：
   Hive 字面量解析会吃单反斜杠（'\\N' 落库 serialization.null.format='N'，
   CSV null 标记失配——spec §0 M4 实测坑），双反斜杠才解析为两字符 \\N，与
   HiveAdapter.CSV_NULL_MARK 对应；STORED AS TEXTFILE。

表/列与 schema.py 单一来源对齐（tests/mock/test_schema_hive.py 对拍锁定），
严禁在本文件手写第二份表结构（漂移风险）。Hive 无 CBO ANALYZE 概念，建仓
无 ANALYZE 步骤（与 init_postgres 差异点，spec D9）。
"""

from __future__ import annotations

import re

import sqlglot

from mock.gmall.schema import DDL_STATEMENTS

# 表名正则提取（与 schema_mysql/schema_clickhouse 同源口径：正则绝对稳定）
_TABLE_NAME_RE = re.compile(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", re.IGNORECASE)

# 表级存储子句（spec D9/D10）：Python 层 '\\001' 的值为四字符 \001（Hive 解析
# 八进制转义为 chr(1)）；'\\\\N' 的值为三字符 \\N（Hive 字面量解析后为两字符 \N）
_ROW_FORMAT_CLAUSE = (
    "ROW FORMAT DELIMITED FIELDS TERMINATED BY '\\001' "
    "NULL DEFINED AS '\\\\N' STORED AS TEXTFILE"
)


def _post_process(ddl: str) -> str:
    body = ddl.strip().rstrip(";")
    # 主键无承载语义（spec D9）；正则连前置空白一并剥离，避免残留 ") ,"
    body = re.sub(r"\s*PRIMARY\s+KEY", "", body, flags=re.IGNORECASE)
    body = re.sub(r"\s{2,}", " ", body)  # 约束剥离后的连续空格归一
    return f"{body} {_ROW_FORMAT_CLAUSE}"


def hive_ddl() -> list[str]:
    """DuckDB DDL -> Hive DDL（sqlglot 转译 + 确定性后处理，幂等）。"""
    return [
        _post_process(out)
        for ddl in DDL_STATEMENTS
        for out in sqlglot.transpile(ddl, read="duckdb", write="hive")
    ]


def hive_table_names() -> list[str]:
    """源 DDL 的表名清单（按声明序；供 DROP 重建与对拍，正则提取版本无关）。"""
    names: list[str] = []
    for ddl in DDL_STATEMENTS:
        m = _TABLE_NAME_RE.search(ddl)
        if m is None:
            raise RuntimeError(f"DDL 表名提取失败: {ddl.strip()[:80]!r}")
        names.append(m.group(1))
    return names


def create_hive_tables(conn) -> None:
    """在给定 PyHive 连接上按序执行全部 DDL（假定空库）。"""
    cur = conn.cursor()
    try:
        for ddl in hive_ddl():
            cur.execute(ddl)
    finally:
        cur.close()


__all__ = ["create_hive_tables", "hive_ddl", "hive_table_names"]
