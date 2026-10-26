"""ClickHouse 版 Gmall 建仓 DDL（二十三期 spec D9）：DuckDB DDL 运行时转译。

转换由 sqlglot（read=duckdb, write=clickhouse）承担 + 三步确定性后处理：
1. 剥离 PRIMARY KEY 列约束——CH 主键语义由 MergeTree ORDER BY 承载（二者
   不一致会报 primary key 前缀错误）；唯一性由确定性生成器保证；
2. 列类型统一包 Nullable(...)（对齐 DuckDB/MySQL/PG 全列可空语义；埋点解析
   链路存在 NULL datetime 值——MySQL CI 实锤先例；本数仓规模开销可忽略）。
   **转译器已自行包裹 Nullable**（计划期实测 sqlglot 25.x：`INTEGER` ->
   `Nullable(Int32)`、`DATE` -> `Nullable(DATE)`），故本步实现为"先剥壳再统一
   包裹"的幂等归一，同时把大写 `DATE` 归一为 `Date32`（CH 的 Date 只覆盖
   1970..2149，本数仓 birthday 含 1967 年数据，Date 会负天数越界）；
3. 追加 ENGINE = MergeTree 子句（sqlglot 不覆盖表引擎）：时间主轴表按
   _ORDER_BY_KEYS 设 ORDER BY (主时间列)——CH 主键序存储 + 稀疏索引对齐
   MySQL 二级索引动机（spec D9）；其余表 ORDER BY tuple()。全列 Nullable 与
   Nullable 排序键并存需显式 `SETTINGS allow_nullable_key = 1`（MergeTree
   默认 0 会直接拒绝建表）。

表/列与 schema.py 单一来源对齐（tests/mock/test_schema_clickhouse.py 对拍锁定），
严禁在本文件手写第二份表结构（漂移风险）。CH 无 CBO 统计概念，建仓无 ANALYZE
步骤（与 init_postgres 差异点，spec D9）。
"""

from __future__ import annotations

import re

import sqlglot

from mock.gmall.schema import DDL_STATEMENTS

# 表名正则提取（与 schema_mysql 同源口径：正则绝对稳定）
_TABLE_NAME_RE = re.compile(r"CREATE\s+TABLE\s+`?([a-zA-Z0-9_]+)`?", re.IGNORECASE)

# 时间主轴表 -> ORDER BY 主时间列（与 MySQL 二级索引同一张表清单，spec D9）
_ORDER_BY_KEYS: dict[str, str] = {
    "order_detail": "order_time",
    "order_info": "create_time",
    "payment_info": "create_time",
    "order_refund_info": "create_time",
    "user_info": "create_time",
    "fact_start": "ts",
    "fact_page_view": "ts",
    "fact_action": "ts",
    "fact_display": "ts",
}

# 转译产物类型 token（计划期实测：DateTime/String/Int32/Int64/Decimal(P, S)/DATE）。
# 尾部用 (?![A-Za-z0-9_]) 而非 \b：Decimal(P, S) 以右括号结尾，\b 在其后不成立。
# Date32 须排在 Date 之前（同一位置优先匹配更长的形态）。
_TYPE_RE = re.compile(
    r"\b(Decimal\(\d+,\s*\d+\)|DateTime|Date32|Date|String|Float64|Float32"
    r"|UInt64|UInt32|UInt16|UInt8|Int64|Int32|Int16|Int8)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

# 已包裹的 Nullable(X) 剥壳正则（X 允许一层内嵌括号，覆盖 Decimal(16, 2)）
_NULLABLE_SHELL_RE = re.compile(r"Nullable\(([^()]*(?:\([^()]*\))?[^()]*)\)")

# 类型名归一（转译器输出大写 DATE）：Date -> Date32。
# CH 的 Date 为 uint16 天数，仅覆盖 1970-01-01 ~ 2149-06-06；本数仓 birthday
# 最早为 1967-02-21（3083 用户中 79 条早于 1970），用 Date 会负天数越界、
# 驱动报 `struct.error: 'H' format requires 0 <= number <= 65535`（CI 实锤）。
# Date32 为 4 字节，覆盖 1900-01-01 ~ 2299-12-31，语义一致、开销可忽略。
_TYPE_CANON = {"date": "Date32"}


def _canon_type(token: str) -> str:
    return _TYPE_CANON.get(token.lower(), token)


def _wrap_nullable(ddl: str) -> str:
    """全列 Nullable 幂等归一：先剥已有 Nullable 壳，再统一包裹。"""
    stripped = _NULLABLE_SHELL_RE.sub(lambda m: m.group(1), ddl)
    return _TYPE_RE.sub(lambda m: f"Nullable({_canon_type(m.group(1))})", stripped)


def _post_process(ddl: str) -> str:
    name = _TABLE_NAME_RE.search(ddl).group(1)
    body = ddl.strip().rstrip(";")
    # 主键语义由 ORDER BY 承载（spec D9）；正则连前置空白一并剥离，避免残留 ") ,"
    body = re.sub(r"\s*PRIMARY\s+KEY", "", body, flags=re.IGNORECASE)
    body = re.sub(r"\s{2,}", " ", body)  # 剥离约束后的连续空格归一
    body = _wrap_nullable(body)
    key = _ORDER_BY_KEYS.get(name)
    if key is None:
        return f"{body} ENGINE = MergeTree ORDER BY tuple()"
    # 全列 Nullable 与"时间列作排序键"并存需显式开启 allow_nullable_key
    # （MergeTree 默认 0，Nullable 排序键会直接拒绝建表——建仓期实测裁决）
    return f"{body} ENGINE = MergeTree ORDER BY (`{key}`) SETTINGS allow_nullable_key = 1"


def clickhouse_ddl() -> list[str]:
    """DuckDB DDL -> ClickHouse DDL（sqlglot 转译 + 确定性后处理，幂等）。"""
    return [
        _post_process(out)
        for ddl in DDL_STATEMENTS
        for out in sqlglot.transpile(ddl, read="duckdb", write="clickhouse")
    ]


def clickhouse_table_names() -> list[str]:
    """源 DDL 的表名清单（按声明序；供 DROP 重建与对拍，正则提取版本无关）。"""
    names: list[str] = []
    for ddl in DDL_STATEMENTS:
        m = _TABLE_NAME_RE.search(ddl)
        if m is None:
            raise RuntimeError(f"DDL 表名提取失败: {ddl.strip()[:80]!r}")
        names.append(m.group(1))
    return names


def create_clickhouse_tables(conn) -> None:
    """在给定 ClickHouse client 上按序执行全部 DDL（假定空库）。"""
    for ddl in clickhouse_ddl():
        conn.command(ddl)


__all__ = ["clickhouse_ddl", "clickhouse_table_names", "create_clickhouse_tables"]
