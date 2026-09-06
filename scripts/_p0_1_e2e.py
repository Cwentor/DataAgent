"""P0-1 端到端安全验证：真实业务入口 execute_sql 必须拒绝 read_csv 表函数的
任意文件读取路径，正常查询照常放行（Pen-test e2e for the SQL guard）。
"""

import os
import tempfile

import duckdb

from exec.guards import UnsafeSqlError, execute_sql

with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
    f.write("secret_id,secret_value\n1,TOPSECRET-PWNED\n")
    path = f.name
conn = duckdb.connect()
try:
    # 真实业务入口 execute_sql 必须拦截 read_csv 表函数
    try:
        execute_sql(conn, f"SELECT * FROM read_csv('{path}')")
        print("UNSAFE: execute_sql accepted read_csv -- FAIL")
    except UnsafeSqlError as e:
        print("PROTECTED: execute_sql rejected read_csv ->", e)
    # 正常查询仍然放行
    r = execute_sql(conn, "SELECT 1 AS a")
    print("normal query ok:", r.columns, r.rows)
finally:
    conn.close()
    os.unlink(path)
