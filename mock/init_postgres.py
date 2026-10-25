"""PostgreSQL 版 mock 数仓初始化（二十二期 spec D13/§7）。

用法（在项目根目录执行）：
    python -m mock.init_postgres

前置条件：
- .env 配置 POSTGRES_* 指向目标实例且**库已存在**（CREATE DATABASE 属部署
  动作；建仓账号需 CREATE/INSERT/DROP 权限，运行时查询另用只读账号，
  两账号分离见 spec D11 部署要求）；
- 数仓 DDL 由 schema_postgres 运行时转译（表/列与 DuckDB 版单一来源对齐）。

效果：
- 幂等重建：DROP 全部既有表 -> 建表 -> 同一生成器灌数（种子 42、
  AS_OF_DATE=2025-12-31、单 rng 顺序消费，与 DuckDB 版逐行同源）->
  元数据表 -> 显式 ANALYZE -> 打印行数。

与 DuckDB 版（mock.init_duckdb）数据一致性的保证：两侧消费同一
build_gmall_tables 的同一 DataFrame / rows 序列，仅入库通道不同。

显式 ANALYZE（spec D13，PG 特有步骤）：PG CBO 依赖统计信息，新灌数据不
收集则 EXPLAIN 计划与扫描熔断口径失真。
"""

from __future__ import annotations

import psycopg

from config import settings
from exec.engine.postgres_adapter import PostgresAdapter
from mock.gmall.build import build_gmall_tables
from mock.gmall.schema_postgres import postgres_table_names
from mock.metadata import FIELD_METADATA, TABLE_METADATA

SEED = 42  # 与 mock.init_duckdb 同源：保证评测可复现


def _writable_conn():
    # 建仓需要 CREATE/INSERT/DROP：复用 POSTGRES_* 配置（要求该账号可写）；
    # autocommit 与运行时口径一致（DDL/INSERT 逐条提交）；不带只读 options
    return psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DATABASE,
        autocommit=True,
    )


def _write_metadata(conn) -> None:
    with conn.cursor() as cur:
        # "comment" 引号包裹：PG 保留字作列名的防御性写法（MySQL 版未引号，
        # PG 侧统一加引号规避版本演进风险）
        cur.execute(
            "CREATE TABLE _field_metadata ("
            'table_name VARCHAR(256), column_name VARCHAR(256), "comment" VARCHAR(1024))'
        )
        cur.executemany(
            "INSERT INTO _field_metadata VALUES (%s, %s, %s)",
            [(t, c, k) for t, cols in FIELD_METADATA.items() for c, k in cols.items()],
        )
        cur.execute(
            'CREATE TABLE _table_metadata (table_name VARCHAR(256), "comment" VARCHAR(1024))'
        )
        cur.executemany("INSERT INTO _table_metadata VALUES (%s, %s)", list(TABLE_METADATA.items()))


def main() -> None:
    """幂等重建 PostgreSQL 版 Gmall 数仓（结构转译 + 同源数据 + 行数对照打印）。"""
    conn = _writable_conn()
    try:
        with conn.cursor() as cur:
            # 幂等清场（含元数据表；建表全权交 build_gmall_tables 的 postgres
            # 分支，严禁在此再显式 create——与 init_mysql 双重建表教训同源）
            for table in (*postgres_table_names(), "_field_metadata", "_table_metadata"):
                cur.execute(f'DROP TABLE IF EXISTS "{table}"')
        # write_applog 默认 True：行为事实表由埋点日志解析入仓（与 DuckDB 版
        # 同链路；日志生成确定性，重复落盘幂等）
        counts = build_gmall_tables(conn, seed=SEED, adapter=PostgresAdapter())
        _write_metadata(conn)
        with conn.cursor() as cur:
            cur.execute("ANALYZE")  # PG CBO 统计收集（spec D13，PG 特有步骤）
        print("[init_postgres] 表创建完成:")
        for t, n in counts.items():
            print(f"  - {t}: {n} 行")
        print(
            f"[init_postgres] 目标库: {settings.POSTGRES_DATABASE}@"
            f"{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}"
        )
        print("[init_postgres] 行数应与 python -m mock.init_duckdb 输出一致（同源生成器）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
