"""SQL 执行层资源治理单元测试（P0/P1）。

覆盖：
- execute_sql 正常执行（columns/rows/scan_rows/duration）；
- statement_timeout：超时中断并抛 QueryTimeoutError，连接可复用；
- 扫描行数上限：EXPLAIN ANALYZE 预检熔断，抛 MaxRowsScannedExceeded；
- LIMIT 硬上限：返回行数超限熔断，抛 ResultLimitExceeded；
- parse_scan_rows：解析 DuckDB 分析计划中的算子行数。
"""

from __future__ import annotations

import duckdb
import pytest

from exec.guards import (
    MaxRowsScannedExceeded,
    QueryTimeoutError,
    ResultLimitExceeded,
    SqlExecutionError,
    UnsafeSqlError,
    assert_read_only_sql,
    execute_sql,
    parse_scan_rows,
)


@pytest.fixture
def big_conn() -> duckdb.DuckDBPyConnection:
    """带大表的独立内存连接（用于超时/扫描熔断测试）。"""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE big AS SELECT range AS id, (range % 7) AS grp FROM range(200000)")
    yield c
    c.close()


def test_unsafe_sql_rejects_non_select(big_conn):
    """P0-2：DDL/DML 语句被只读白名单硬拦截。"""
    for sql in (
        "DROP TABLE big",
        "DELETE FROM big",
        "INSERT INTO big VALUES (1)",
        "CREATE TABLE x AS SELECT 1",
        "ATTACH 'x.duckdb'",
        "PRAGMA version",
    ):
        with pytest.raises(UnsafeSqlError):
            execute_sql(big_conn, sql)


def test_unsafe_sql_rejects_multi_statement(big_conn):
    """P0-2：堆叠多语句被拒绝（DuckDB 实测可执行 SELECT 1; SELECT 2）。"""
    with pytest.raises(UnsafeSqlError):
        execute_sql(big_conn, "SELECT 1; SELECT 2")


def test_unsafe_sql_allows_comment_and_with(big_conn):
    """注释先剥离再校验；WITH 与 SELECT 正常放行。"""
    result = execute_sql(big_conn, "-- 注释\nSELECT grp, sum(id) AS s FROM big GROUP BY grp")
    assert result.columns == ["grp", "s"]
    result2 = execute_sql(big_conn, "WITH t AS (SELECT 1 AS x) SELECT x FROM t")
    assert result2.columns == ["x"]


def test_unsafe_sql_literal_with_semicolon_is_allowed(big_conn):
    """字符串字面量内的分号不应被误判为多语句。"""
    result = execute_sql(big_conn, "SELECT 'a;b' AS v, grp FROM big WHERE grp = 1")
    assert result.columns[0] == "v"


def test_execute_sql_normal(big_conn):
    result = execute_sql(big_conn, "SELECT grp, sum(id) AS s FROM big GROUP BY grp ORDER BY grp")
    assert result.columns == ["grp", "s"]
    assert len(result.rows) == 7
    assert result.scan_rows == 0  # 未启用扫描预算时不做预检
    assert result.duration_ms >= 0


def test_scan_rows_cap_trips(big_conn):
    """扫描行数超过上限 -> 熔断拒绝执行。"""
    with pytest.raises(MaxRowsScannedExceeded) as excinfo:
        execute_sql(
            big_conn,
            "SELECT grp, sum(id) AS s FROM big GROUP BY grp",
            max_scan_rows=50000,
        )
    assert "200000" in str(excinfo.value)


def test_scan_rows_within_budget(big_conn):
    result = execute_sql(
        big_conn,
        "SELECT grp, sum(id) AS s FROM big GROUP BY grp",
        max_scan_rows=500000,
    )
    assert len(result.rows) == 7
    assert result.scan_rows == 200000


def test_result_limit_trips(big_conn):
    with pytest.raises(ResultLimitExceeded) as excinfo:
        execute_sql(
            big_conn,
            "SELECT grp, sum(id) AS s FROM big GROUP BY grp",
            max_result_rows=3,
        )
    assert "7" in str(excinfo.value)


def test_statement_timeout_interrupts(big_conn):
    """病态大查询超过语句超时 -> 中断取消，连接可复用。"""
    with pytest.raises(QueryTimeoutError):
        execute_sql(
            big_conn,
            "SELECT count(*) FROM big a, big b, big c",
            statement_timeout_ms=300,
        )
    # 中断后连接仍可复用
    assert big_conn.execute("SELECT count(*) FROM big").fetchone()[0] == 200000


def test_engine_error_wrapped(big_conn):
    """DuckDB 引擎报错包装为 SqlExecutionError（可自愈）。"""
    with pytest.raises(SqlExecutionError) as excinfo:
        execute_sql(
            big_conn,
            "SELECT nonexistent_column FROM big",
            max_scan_rows=1000000,
        )
    assert "Binder Error" in str(excinfo.value)


def test_parse_scan_rows():
    txt = (
        "dummy\n"
        "│        100,000 rows       │\n"
        "│        7 rows             │\n"
        "│            0 rows         │"
    )
    assert parse_scan_rows(txt) == 100000
    assert parse_scan_rows("no rows here") == 0


def test_engine_error_wrapped_on_real_execution(big_conn):
    """真实执行阶段的 DuckDB 引擎报错同样包装为 SqlExecutionError（可自愈）。"""
    with pytest.raises(SqlExecutionError) as excinfo:
        execute_sql(big_conn, "SELECT nonexistent_column FROM big")
    assert "Binder Error" in str(excinfo.value)


def test_unsafe_sql_rejects_table_functions(big_conn):
    """P0-1：文件表函数（read_csv/read_json/read_parquet/read_duckdb/glob 等）应被只读白名单拒绝。"""
    for sql in (
        "SELECT * FROM read_csv('data.csv')",
        "SELECT * FROM read_csv_glob('*.csv')",
        "SELECT * FROM read_json('data.json')",
        "SELECT * FROM read_parquet('data.parquet')",
        "SELECT * FROM read_duckdb('other.db')",
        "SELECT * FROM glob('*.parquet')",
        "SELECT * FROM parquet_scan('data.parquet')",
        "SELECT * FROM read_text('data.txt')",
        "SELECT * FROM sqlite_scan('x.db', 't')",
        "SELECT * FROM query('SELECT 1')",
        "SELECT * FROM query_table('t')",
    ):
        with pytest.raises(UnsafeSqlError):
            execute_sql(big_conn, sql)


def test_unsafe_sql_rejects_copy_export_import(big_conn):
    """P0-1：COPY/EXPORT/IMPORT 等语句导致文件读写，应被拒绝。"""
    for sql in (
        "COPY (SELECT 1) TO 'x.csv'",
        "COPY fact_orders TO 'fact_orders.csv'",
        "EXPORT DATABASE 'dir'",
        "IMPORT DATABASE 'dir'",
        "INSTALL 'http://example.com/extension'",
        "LOAD 'extension'",
    ):
        with pytest.raises(UnsafeSqlError):
            execute_sql(big_conn, sql)


def test_unsafe_sql_rejects_string_table(big_conn):
    """P0-1：DuckDB 支持 FROM '<file>' 读取本地文件，应被拒绝。"""
    for sql in (
        "SELECT * FROM 'C:/windows/win.ini'",
        "SELECT * FROM 'data.csv'",
        "SELECT * FROM 'data.json'",
    ):
        with pytest.raises(UnsafeSqlError):
            execute_sql(big_conn, sql)


def test_unsafe_sql_rejects_metadata_and_query_functions(big_conn):
    """P0-1：parquet 元数据函数 / 外部库 query / 引号函数名 / SELECT INTO 等新绕过面拒绝。"""
    for sql in (
        "SELECT * FROM parquet_kv_metadata('x.parquet')",
        "SELECT * FROM parquet_schema('x.parquet')",
        "SELECT * FROM parquet_file_metadata('x.parquet')",
        "SELECT * FROM parquet_metadata('x.parquet')",
        "SELECT * FROM sqlite_query('x.db', 'SELECT 1')",
        "SELECT * FROM postgres_query('dbname=x', 'SELECT 1')",
        "SELECT * FROM mysql_query('dbname=x', 'SELECT 1')",
        "SELECT * FROM mongo_scan('mongodb://x', 'db', 'c')",
        "SELECT * FROM json_execute_serialized_sql('{\"statements\":[]}')",
        "SELECT * FROM write_log('evil')",
        "SELECT * FROM \"read_csv\"('C:/windows/win.ini')",
        "SELECT * FROM \"parquet_scan\"('x.parquet')",
        "SELECT 1 INTO t",
        "SELECT * FROM $$C:/windows/win.ini$$",
        "SELECT * FROM (SELECT * FROM read_csv('x')) AS t",
    ):
        with pytest.raises(UnsafeSqlError):
            execute_sql(big_conn, sql)


def test_unsafe_sql_allows_union_and_legit_functions(big_conn):
    """只读 UNION 与编译器合法函数（date_trunc/generate_series 等）不应误伤。"""
    assert_read_only_sql("SELECT 1 AS a UNION ALL SELECT 2")
    assert_read_only_sql("SELECT date_trunc('day', order_time) AS d FROM t GROUP BY 1")
    assert_read_only_sql(
        "SELECT UNNEST(generate_series(TIMESTAMP '2024-01-01', TIMESTAMP '2024-01-03', "
        "INTERVAL 1 DAY)) AS d"
    )
    result = execute_sql(big_conn, "SELECT 1 AS a UNION ALL SELECT 2")
    assert len(result.rows) == 2


def test_unsafe_sql_allows_normal_statements(big_conn):
    """正常 SELECT/WITH、字面量分号、别名等应继续放行。"""
    for sql in (
        "SELECT 1 AS a",
        "SELECT 'a;b' AS v",  # 字面量内的分号不应触发多语句检测
        "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
        'SELECT 1 AS "read_csv"',  # 别名与关键词同名不应误伤
    ):
        result = execute_sql(big_conn, sql)
        # 简单检查：列名匹配
        cols = result.columns
        if "AS a" in sql:
            assert cols == ["a"]
        elif "'a;b'" in sql:
            assert cols == ["v"]
        elif "WITH t" in sql:
            assert cols == ["x"]
        elif 'AS "read_csv"' in sql:
            assert cols == ["read_csv"]
        assert len(result.rows) == 1


# --------------------------------------------------------------------------- #
# EXPLAIN 预检缓存（整改指令3-3）：同一 SQL 自愈重试不重复预检
# --------------------------------------------------------------------------- #
def test_scan_cache_key_normalizes_whitespace():
    """SQL 规范化哈希：空白折叠 + 小写，等价 SQL 命中同一缓存键。"""
    from exec.guards import _scan_cache_key

    sql_a = "SELECT grp, sum(id) AS s FROM big GROUP BY grp"
    sql_b = "  select   grp, SUM(id) as s\nfrom big group by grp  "
    assert _scan_cache_key(sql_a) == _scan_cache_key(sql_b)
    assert _scan_cache_key("SELECT 1") != _scan_cache_key("SELECT 2")


def test_execute_sql_reuses_scan_cache(big_conn, monkeypatch):
    """命中缓存后不再重复 EXPLAIN ANALYZE（预检降本），scan_rows 仍正确回填。"""
    from exec.guards import _run_with_timeout, cache_scan_rows, cached_scan_rows

    sql = "SELECT grp, sum(id) AS s FROM big GROUP BY grp"
    # 预填缓存（等价于首次预检的结果）
    cache_scan_rows(sql, 200000)
    assert cached_scan_rows(sql) == 200000

    # 若再次出现 EXPLAIN ANALYZE 预检（缓存未命中）则视为回归缺陷；真实执行不受影响
    real_run = _run_with_timeout

    def guarded_run(conn, s, timeout):
        if s.startswith("EXPLAIN ANALYZE"):
            raise AssertionError("预检缓存未命中，重复执行了 EXPLAIN ANALYZE")
        return real_run(conn, s, timeout)

    monkeypatch.setattr("exec.guards._run_with_timeout", guarded_run)
    result = execute_sql(big_conn, sql, max_scan_rows=500000)
    assert result.scan_rows == 200000  # 缓存值回填
    assert len(result.rows) == 7


def test_scan_cache_capacity_from_settings(monkeypatch):
    """容量上限读自 settings.MAX_SCAN_CACHE_SIZE（原硬编码 512 配置化）。

    写入条数超过容量即触发"清空防膨胀"，且容量阈值在运行时可由配置调整。
    """
    from config import settings
    from exec.guards import _SCAN_CACHE, cache_scan_rows, cached_scan_rows

    _SCAN_CACHE.clear()  # 隔离其他用例预填的全局缓存，保证容量计数从零开始
    monkeypatch.setattr(settings, "MAX_SCAN_CACHE_SIZE", 3)
    for i in range(3):
        cache_scan_rows(f"SELECT {i}", i)
    assert cached_scan_rows("SELECT 0") == 0  # 未超限时正常命中

    cache_scan_rows("SELECT 3", 3)  # 第 4 条写入超出容量 3，触发清空
    for i in range(4):
        assert cached_scan_rows(f"SELECT {i}") is None
