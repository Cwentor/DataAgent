"""P0-1 渗透验证脚本：手工矩阵测试 assert_read_only_sql 对危险 SQL（表函数 /
多语句 / 写操作）的拦截，并用裸 DuckDB 连接复现 read_csv 数据外泄风险
（Manual pen-test matrix for the read-only SQL guard）。
"""

import os
import tempfile

import duckdb

from exec.guards import UnsafeSqlError, assert_read_only_sql

tests = [
    ("read_csv", "SELECT * FROM read_csv('C:/windows/win.ini')"),
    ("read_csv_glob", "SELECT * FROM read_csv_glob('C:/windows/*.ini')"),
    ("read_json", "SELECT * FROM read_json('C:/windows/win.ini')"),
    ("read_parquet", "SELECT * FROM read_parquet('x.parquet')"),
    ("comment_delete", "/*x*/DELETE FROM t"),
    ("multi", "SELECT 1; SELECT 2"),
    ("normal_select", "SELECT 1 AS a"),
]
for name, sql in tests:
    try:
        assert_read_only_sql(sql)
        print(f"{name}: ACCEPT")
    except UnsafeSqlError as e:
        print(f"{name}: REJECT ({e})")

print()
with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
    f.write("secret_id,secret_value\n1,TOPSECRET-PWNED\n")
    path = f.name
print("written secret to", path)
conn = duckdb.connect()
try:
    # 渗透测试本意即验证任意路径读取可达性；路径经参数绑定传入，避免 SQL 文本拼接
    rows = conn.execute("SELECT * FROM read_csv(?)", [path]).fetchall()
    print("read_csv EXFILTRATED:", rows)
finally:
    conn.close()
    os.unlink(path)

# 也验证一下通过 web/service 入口是否可达（构造绕过 assert 但属读文件的 SQL）
print()
try:
    assert_read_only_sql("SELECT * FROM read_csv('C:/windows/win.ini')")
    print("CONCLUSION: P0-1 CONFIRMED - read_csv bypasses read-only check")
except UnsafeSqlError:
    print("CONCLUSION: P0-1 NOT REPRODUCED")
