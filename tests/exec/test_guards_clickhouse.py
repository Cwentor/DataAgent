"""ClickHouse 方言护栏测试（二十三期 spec D4）：增量黑名单与 AST 结构拒绝。

通用黑名单与四层 fail-closed 结构由 tests/exec/test_exec.py 锁定（引擎无关）；
本文件只锁 clickhouse 增量面（SYSTEM/KILL/TRUNCATE/OPTIMIZE/SETTINGS/OUTFILE
+ 外部源表函数族）。
"""

from __future__ import annotations

import pytest

from exec.guards import UnsafeSqlError, assert_read_only_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SYSTEM SHUTDOWN",  # 管理语句族（最危险面）
        "SYSTEM RELOAD CONFIG",
        "KILL QUERY WHERE query_id='x'",
        "TRUNCATE TABLE order_detail",
        "OPTIMIZE TABLE order_detail FINAL",
        "SELECT 1 SETTINGS max_rows_to_read=0",  # 探索 SQL 篡改预算面（Review Focus #3）
        "SELECT * FROM order_detail INTO OUTFILE 'x.csv'",
    ],
)
def test_clickhouse_extra_keywords_rejected(sql):
    with pytest.raises(UnsafeSqlError):
        assert_read_only_sql(sql, dialect="clickhouse")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM url('http://evil')",
        "SELECT * FROM s3('http://evil')",
        "SELECT * FROM hdfs('x')",
        "SELECT * FROM file('/etc/passwd', 'CSV')",
        "SELECT * FROM jdbc('x')",
        "SELECT * FROM odbc('x')",
        "SELECT * FROM mysql('host:3306', 'db', 't')",
        "SELECT * FROM postgresql('host', 'db', 't')",
        "SELECT * FROM remote('host', 'db.t')",
        "SELECT * FROM clusterAllReplicas('c', 'db.t')",
        "SELECT * FROM input('x')",
        "SELECT * FROM generateRandom('x')",
        "SELECT sleep(10)",
        "SELECT sleepEachRow(1, 10)",
    ],
)
def test_clickhouse_extra_functions_rejected(sql):
    with pytest.raises(UnsafeSqlError):
        assert_read_only_sql(sql, dialect="clickhouse")


def test_clickhouse_normal_select_allowed():
    """正常只读查询不误伤（黑名单只增不破）。"""
    assert_read_only_sql(
        "SELECT count() FROM order_detail WHERE dt = '2025-12-31'", dialect="clickhouse"
    )


def test_clickhouse_settings_keyword_case_insensitive():
    """SETTINGS 子句大小写变体同样拒绝（关键词层 uppercase 归一）。"""
    with pytest.raises(UnsafeSqlError):
        assert_read_only_sql("select 1 settings readonly=0", dialect="clickhouse")


def test_other_dialects_not_affected_by_clickhouse_keywords():
    """增量黑名单按方言隔离：duckdb 方言下 SETTINGS 不属黑名单（零误伤）。"""
    assert_read_only_sql("SELECT 1 AS settings_flag", dialect="duckdb")
