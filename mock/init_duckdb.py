"""本地 Mock 数仓初始化脚本（Gmall 电商数仓版）。

用法（在项目根目录执行）：
    python -m mock.init_duckdb

效果：
- 幂等重建项目根目录的 analytics_sandbox.duckdb；
- 灌入 Gmall 模式数仓：种子表（省市区/类目/品牌/SPU/SKU/券/活动）+
  生成表（用户/购物车/收藏/领券/订单/支付/退款/评论/状态日志）+
  行为事实表（启动/页面浏览/点击/曝光，由埋点 JSON 日志解析入仓）；
- 同步落盘埋点 JSON 日志 logs/gmall_applog/<日期>.json（file 通道复刻）；
- 固定随机种子 42 与锚点 AS_OF_DATE=2024-06-30，保证数据可复现。

生成器实现见 mock/gmall/ 包，反向解析规格见
docs/plans/2026-10-06-gmall-mock-reverse-spec.md。
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from config import settings
from mock.gmall.build import build_gmall_tables
from mock.metadata import FIELD_METADATA, TABLE_METADATA

SEED = 42  # 固定随机种子：保证评测可复现


def build_tables(conn: duckdb.DuckDBPyConnection, seed: int = SEED) -> dict[str, int]:
    """在给定连接上建表并灌数据（内存连接或文件连接均可），返回各表行数。

    tests/conftest.py 通过该入口把 mock 数仓注入内存库。
    """
    return build_gmall_tables(conn, seed=seed)


def _write_metadata(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE _field_metadata (
            table_name  VARCHAR,
            column_name VARCHAR,
            comment     VARCHAR
        )
    """)
    rows = [
        (table, column, comment)
        for table, cols in FIELD_METADATA.items()
        for column, comment in cols.items()
    ]
    conn.executemany("INSERT INTO _field_metadata VALUES (?, ?, ?)", rows)
    conn.execute("""
        CREATE TABLE _table_metadata (
            table_name VARCHAR,
            comment    VARCHAR
        )
    """)
    conn.executemany("INSERT INTO _table_metadata VALUES (?, ?)", list(TABLE_METADATA.items()))


def main() -> None:
    """幂等重建本地数仓：删除旧 DuckDB 文件后全量写入 mock 数据与元数据。"""
    db_path = Path(settings.DB_PATH)
    db_path.unlink(missing_ok=True)  # 幂等重建
    conn = duckdb.connect(str(db_path))
    try:
        counts = build_gmall_tables(conn, seed=SEED)
        _write_metadata(conn)
        print("[init_duckdb] 表创建完成:")
        for t, n in counts.items():
            print(f"  - {t}: {n} 行")
        print(f"[init_duckdb] 数据库文件: {db_path}")
        print(f"[init_duckdb] 埋点日志目录: {settings.PROJECT_ROOT / 'logs' / 'gmall_applog'}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
