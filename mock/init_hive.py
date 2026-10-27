"""Hive 版 mock 数仓初始化（二十五期 spec §7/D9/D10）。

用法（在项目根目录执行）：
    python -m mock.init_hive

前置条件：
- .env 配置 HIVE_* 指向目标 HiveServer2 且**库已存在**（CREATE DATABASE 属部署
  动作；建仓账号需 CREATE/INSERT/DROP 权限，运行时查询另用只读账号，两账号
  分离见 spec §9）；
- 数仓 DDL 由 schema_hive 运行时转译（表/列与 DuckDB 版单一来源对齐）；
- staging：CSV 先写 HIVE_STAGING_CLIENT_DIR（本机路径），再经 LOAD DATA LOCAL
  INPATH 从 HIVE_STAGING_SERVER_DIR 加载——**LOCAL 语义是 hiveserver2 进程本地
  路径**，部署中两目录须为同一内容（CI/docker 卷映射；本地单机部署若 hiveserver2
  与脚本同机，两路径指向同一目录即可，spec D10）。

效果：幂等重建（DROP 全部既有表 -> 建表 -> 同一 build_gmall_tables 灌数（种子
42、AS_OF_DATE=2025-12-31，与 DuckDB 版逐行同源）-> 元数据表 -> 打印行数）。
Hive 无 CBO ANALYZE 概念，**无 ANALYZE 步骤**（与 init_postgres 差异点，spec D9）。

建仓通道显式注入 HiveAdapter（不依赖 DB_BACKEND 环境变量）：本入口只服务 Hive，
若按缺省后端取 adapter，误设 DB_BACKEND=duckdb 会把 DuckDB DDL 打到 Hive 连接上
——显式注入为 fail-closed 口径（与 init_clickhouse 同源）。
"""

from __future__ import annotations

from config import settings
from exec.engine.hive_adapter import HiveAdapter
from mock.gmall.build import build_gmall_tables
from mock.gmall.schema_hive import hive_table_names
from mock.metadata import FIELD_METADATA, TABLE_METADATA

SEED = 42  # 与 mock.init_duckdb 同源：保证评测可复现

# 元数据表 DDL（与 DuckDB/MySQL/PG 版语义同源；STRING 语义由 TEXTFILE 承载）。
# 表名反引号包裹：Hive 裸标识符禁下划线开头（__spine 同源坑，spec §0 M3）
_FIELD_METADATA_DDL = (
    "CREATE TABLE `_field_metadata` "
    "(table_name STRING, column_name STRING, comment STRING) "
    "ROW FORMAT DELIMITED FIELDS TERMINATED BY '\\001' "
    "NULL DEFINED AS '\\\\N' STORED AS TEXTFILE"
)
_TABLE_METADATA_DDL = (
    "CREATE TABLE `_table_metadata` (table_name STRING, comment STRING) "
    "ROW FORMAT DELIMITED FIELDS TERMINATED BY '\\001' "
    "NULL DEFINED AS '\\\\N' STORED AS TEXTFILE"
)


def _write_metadata(adapter: HiveAdapter, conn) -> None:
    """元数据表：DDL + insert_dataframe 通道灌入（与业务表同一条 staging 链路）。"""
    import pandas as pd

    cur = conn.cursor()
    try:
        cur.execute(_FIELD_METADATA_DDL)
        cur.execute(_TABLE_METADATA_DDL)
    finally:
        cur.close()
    field_rows = pd.DataFrame(
        [(t, c, k) for t, cols in FIELD_METADATA.items() for c, k in cols.items()],
        columns=["table_name", "column_name", "comment"],
    )
    table_rows = pd.DataFrame(list(TABLE_METADATA.items()), columns=["table_name", "comment"])
    adapter.insert_dataframe(conn, "_field_metadata", field_rows)
    adapter.insert_dataframe(conn, "_table_metadata", table_rows)


def main() -> None:
    """幂等重建 Hive 版 Gmall 数仓（结构转译 + 同源数据 + 行数对照打印）。"""
    adapter = HiveAdapter()
    from pyhive import hive

    # 建仓需要 CREATE/INSERT/DROP：直连驱动（不经 adapter.connect_readonly——
    # 建仓账号非只读授权，验证式加固会正确拒绝）
    conn = hive.connect(
        host=settings.HIVE_HOST,
        port=settings.HIVE_PORT,
        username=settings.HIVE_USER,
        password=settings.HIVE_PASSWORD or None,
        database=settings.HIVE_DATABASE,
        scheme="http",
    )
    try:
        # 幂等清场（含元数据表；建表全权交 create_hive_tables，严禁在此再显式
        # create 业务表——与 init_mysql 双重建表教训同源）
        cur = conn.cursor()
        try:
            for table in (*hive_table_names(), "_field_metadata", "_table_metadata"):
                cur.execute(f"DROP TABLE IF EXISTS `{table}`")
        finally:
            cur.close()
        # write_applog 默认 True：行为事实表由埋点日志解析入仓（与 DuckDB 版
        # 同链路；日志生成确定性，重复落盘幂等）
        counts = build_gmall_tables(conn, seed=SEED, adapter=adapter)
        _write_metadata(adapter, conn)
        print("[init_hive] 表创建完成:")
        for t, n in counts.items():
            print(f"  - {t}: {n} 行")
        print(
            f"[init_hive] 目标库: {settings.HIVE_DATABASE}@"
            f"{settings.HIVE_HOST}:{settings.HIVE_PORT}"
        )
        print("[init_hive] 行数应与 python -m mock.init_duckdb 输出一致（同源生成器）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
