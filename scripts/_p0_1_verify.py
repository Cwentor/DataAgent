"""P0-1 SQL 守卫矩阵验证：逐条断言危险 SQL（读文件表函数 / COPY / EXPORT /
扩展加载 / 多语句等）被拒绝、合法 SELECT / CTE / 字面量分号被放行，并实测
execute_sql 真实入口（Guard verification matrix）。
"""

import os
import tempfile

import duckdb

from exec.guards import UnsafeSqlError, assert_read_only_sql, execute_sql

tests = [
    ("read_csv", "SELECT * FROM read_csv('C:/windows/win.ini')"),
    ("read_csv_glob", "SELECT * FROM read_csv_glob('C:/windows/*.ini')"),
    ("read_json", "SELECT * FROM read_json('C:/windows/win.ini')"),
    ("read_parquet", "SELECT * FROM read_parquet('x.parquet')"),
    ("read_csv_auto", "SELECT * FROM read_csv_auto('x.csv')"),
    ("parquet_scan", "SELECT * FROM parquet_scan('x.parquet')"),
    ("read_duckdb", "SELECT * FROM read_duckdb('other.db')"),
    ("read_text", "SELECT * FROM read_text('C:/windows/win.ini')"),
    ("sqlite_scan", "SELECT * FROM sqlite_scan('x.db', 't')"),
    ("glob", "SELECT * FROM glob('*.parquet')"),
    ("query_func", "SELECT * FROM query('SELECT 1')"),
    ("query_table_func", "SELECT * FROM query_table('t')"),
    ("copy_to", "COPY (SELECT 1) TO 'x.csv'"),
    ("export", "EXPORT DATABASE 'dir'"),
    ("import", "IMPORT DATABASE 'dir'"),
    ("install", "INSTALL 'http_extension'"),
    ("load", "LOAD 'extension'"),
    ("call", "CALL pragma_version()"),
    ("from_string", "SELECT * FROM 'C:/windows/win.ini'"),
    ("from_string2", "SELECT * FROM 'data.csv'"),
    ("comment_delete", "/*x*/DELETE FROM t"),
    ("multi", "SELECT 1; SELECT 2"),
    ("normal_select", "SELECT 1 AS a"),
    ("with_cte", "WITH t AS (SELECT 1 AS x) SELECT x FROM t"),
    ("literal_semicolon", "SELECT 'a;b' AS v"),
    ("alias_read_csv_column", 'SELECT 1 AS "read_csv"'),
]
ok = 0
for name, sql in tests:
    try:
        assert_read_only_sql(sql)
        verdict = "ACCEPT"
    except UnsafeSqlError:
        verdict = "REJECT"
    print(f"{name:22s} {verdict}")
    if (name in ("normal_select", "with_cte", "literal_semicolon", "alias_read_csv_column")) == (
        verdict == "ACCEPT"
    ):
        ok += 1
    elif (
        name not in ("normal_select", "with_cte", "literal_semicolon", "alias_read_csv_column")
        and verdict == "REJECT"
    ):
        ok += 1
print("\nmatched expectations:", ok, "/", len(tests))

# 实测真实业务入口 execute_sql：读文件 SQL 必须被守卫拒绝
with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
    f.write("secret_id,secret_value\n1,TOPSECRET-PWNED\n")
    path = f.name
conn = duckdb.connect()
try:
    try:
        execute_sql(conn, f"SELECT * FROM read_csv('{path}')")
        print("EXFILTRATION STILL POSSIBLE: FAIL")
    except UnsafeSqlError:
        print("EXFILTRATION BLOCKED (guards layer via execute_sql): OK")
    except Exception as e:
        print("EXFILTRATION BLOCKED (other):", type(e).__name__)
finally:
    conn.close()
    os.unlink(path)
