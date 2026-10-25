"""MySQL 版 mock 数仓初始化（二十一期 spec D12/§7）。

用法（在项目根目录执行）：
    python -m mock.init_mysql

前置条件：
- .env 配置 MYSQL_* 指向目标实例且**库已存在**（CREATE DATABASE 属部署
  动作；建仓账号需 CREATE/INSERT/DROP 权限，运行时查询另用只读账号，
  两账号分离见 spec D10 部署要求）；
- 数仓 DDL 由 schema_mysql 运行时转译（表/列与 DuckDB 版单一来源对齐）。

效果：
- 幂等重建：DROP 全部既有表 -> 建表与二级索引 -> 同一生成器灌数
  （种子 42、AS_OF_DATE=2025-12-31、单 rng 顺序消费，与 DuckDB 版
  逐行同源）-> 元数据表 -> 打印行数。

与 DuckDB 版（mock.init_duckdb）数据一致性的保证：两侧消费同一
build_gmall_tables 的同一 DataFrame / rows 序列，仅入库通道不同
（DuckDB pandas scan / pymysql executemany）。
"""

from __future__ import annotations

import pymysql

from config import settings
from exec.engine.mysql_adapter import MySqlAdapter
from mock.gmall.build import build_gmall_tables
from mock.gmall.schema_mysql import create_mysql_tables, mysql_table_names
from mock.metadata import FIELD_METADATA, TABLE_METADATA

SEED = 42  # 与 mock.init_duckdb 同源：保证评测可复现


def _writable_conn() -> pymysql.connections.Connection:
    # 建仓需要 CREATE/INSERT/DROP：复用 MYSQL_* 配置（要求该账号可写）；
    # autocommit + utf8mb4 与运行时口径一致
    return pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DATABASE,
        charset="utf8mb4",
        autocommit=True,
    )


def _write_metadata(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE _field_metadata ("
            "table_name VARCHAR(256), column_name VARCHAR(256), comment VARCHAR(1024))"
        )
        cur.executemany(
            "INSERT INTO _field_metadata VALUES (%s, %s, %s)",
            [(t, c, k) for t, cols in FIELD_METADATA.items() for c, k in cols.items()],
        )
        cur.execute("CREATE TABLE _table_metadata (table_name VARCHAR(256), comment VARCHAR(1024))")
        cur.executemany("INSERT INTO _table_metadata VALUES (%s, %s)", list(TABLE_METADATA.items()))


def main() -> None:
    """幂等重建 MySQL 版 Gmall 数仓（结构转译 + 同源数据 + 行数对照打印）。"""
    conn = _writable_conn()
    try:
        with conn.cursor() as cur:
            for table in mysql_table_names():
                cur.execute(f"DROP TABLE IF EXISTS `{table}`")
        create_mysql_tables(conn)
        # write_applog 默认 True：行为事实表由埋点日志解析入仓（与 DuckDB 版
        # 同链路；日志生成确定性，重复落盘幂等）
        counts = build_gmall_tables(conn, seed=SEED, adapter=MySqlAdapter())
        _write_metadata(conn)
        print("[init_mysql] 表创建完成:")
        for t, n in counts.items():
            print(f"  - {t}: {n} 行")
        print(
            f"[init_mysql] 目标库: {settings.MYSQL_DATABASE}@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}"
        )
        print("[init_mysql] 行数应与 python -m mock.init_duckdb 输出一致（同源生成器）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
