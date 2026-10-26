"""ClickHouse 版 mock 数仓初始化（二十三期 spec §6/D10）。

用法（在项目根目录执行）：
    python -m mock.init_clickhouse

前置条件：
- .env 配置 CLICKHOUSE_* 指向目标实例且**库已存在**（CREATE DATABASE 属部署
  动作；建仓账号需 CREATE/INSERT/DROP 权限，运行时查询另用 readonly profile
  账号，两账号分离见 spec §9）；
- 数仓 DDL 由 schema_clickhouse 运行时转译（表/列与 DuckDB 版单一来源对齐）。

效果：幂等重建（DROP 全部既有表 -> 建表 -> 同一 build_gmall_tables 灌数（种子
42、AS_OF_DATE=2025-12-31，与 DuckDB 版逐行同源）-> 元数据表 -> 打印行数）。
CH 无 CBO 统计概念，**无 ANALYZE 步骤**（与 init_postgres 差异点，spec D9）。

建仓通道显式注入 ClickHouseAdapter（不依赖 DB_BACKEND 环境变量）：本入口只服务
ClickHouse，若按缺省后端取 adapter，误设 DB_BACKEND=duckdb 会把 DuckDB DDL 打到
CH 客户端上——显式注入为 fail-closed 口径。
"""

from __future__ import annotations

import clickhouse_connect
import pandas as pd

from config import settings
from exec.engine.clickhouse_adapter import ClickHouseAdapter
from mock.gmall.build import build_gmall_tables
from mock.gmall.schema_clickhouse import clickhouse_table_names
from mock.metadata import FIELD_METADATA, TABLE_METADATA

SEED = 42  # 与 mock.init_duckdb 同源：保证评测可复现


def _writable_client():
    # 建仓需要 CREATE/INSERT/DROP：直连驱动（不经 adapter.connect_readonly——
    # 建仓账号非 readonly profile，验证式加固会正确拒绝）
    return clickhouse_connect.get_client(
        host=settings.CLICKHOUSE_HOST,
        port=settings.CLICKHOUSE_PORT,
        username=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD,
        database=settings.CLICKHOUSE_DATABASE,
        connect_timeout=5,
    )


def _write_metadata(client) -> None:
    client.command(
        "CREATE TABLE _field_metadata "
        "(table_name String, column_name String, comment String) "
        "ENGINE = MergeTree ORDER BY tuple()"
    )
    client.insert_df(
        "_field_metadata",
        pd.DataFrame(
            [(t, c, k) for t, cols in FIELD_METADATA.items() for c, k in cols.items()],
            columns=["table_name", "column_name", "comment"],
        ),
    )
    client.command(
        "CREATE TABLE _table_metadata (table_name String, comment String) "
        "ENGINE = MergeTree ORDER BY tuple()"
    )
    client.insert_df(
        "_table_metadata",
        pd.DataFrame(list(TABLE_METADATA.items()), columns=["table_name", "comment"]),
    )


def main() -> None:
    """幂等重建 ClickHouse 版 Gmall 数仓（结构转译 + 同源数据 + 行数对照打印）。"""
    client = _writable_client()
    try:
        # 幂等清场（含元数据表；建表全权交 create_clickhouse_tables，严禁在此
        # 再显式 create——与 init_mysql 双重建表教训同源）
        for table in (*clickhouse_table_names(), "_field_metadata", "_table_metadata"):
            client.command(f"DROP TABLE IF EXISTS `{table}`")
        # write_applog 默认 True：行为事实表由埋点日志解析入仓（与 DuckDB 版
        # 同链路；日志生成确定性，重复落盘幂等）
        counts = build_gmall_tables(client, seed=SEED, adapter=ClickHouseAdapter())
        _write_metadata(client)
        print("[init_clickhouse] 表创建完成:")
        for t, n in counts.items():
            print(f"  - {t}: {n} 行")
        print(
            f"[init_clickhouse] 目标库: {settings.CLICKHOUSE_DATABASE}@"
            f"{settings.CLICKHOUSE_HOST}:{settings.CLICKHOUSE_PORT}"
        )
        print("[init_clickhouse] 行数应与 python -m mock.init_duckdb 输出一致（同源生成器）")
    finally:
        client.close()


if __name__ == "__main__":
    main()
