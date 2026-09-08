"""本地 Mock 数仓初始化脚本。

用法（在项目根目录执行）：
    python -m mock.init_duckdb

效果：
- 在项目根目录创建 analytics_sandbox.duckdb；
- 写入 3 张业务表：dim_user / dim_product / fact_orders；
- 写入 _field_metadata 元数据清单表；
- 使用固定随机种子与锚点日期，保证数据可复现（任意机器结果一致）。
"""

from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path

import duckdb

from config import settings
from mock.metadata import FIELD_METADATA, TABLE_METADATA

# 固定随机种子：保证评测可复现
SEED = 42

PROVINCES = ["广东", "浙江", "江苏", "北京", "上海", "四川", "湖北", "山东"]
GENDERS = ["M", "F"]
CATEGORIES = {
    "数码": ["华为", "小米", "苹果", "联想"],
    "家电": ["美的", "格力", "海尔", "TCL"],
    "服饰": ["优衣库", "耐克", "阿迪达斯", "李宁"],
    "美妆": ["兰蔻", "雅诗兰黛", "欧莱雅", "自然堂"],
    "食品": ["三只松鼠", "良品铺子", "蒙牛", "伊利"],
    "家居": ["宜家", "顾家", "全友", "林氏木业"],
}

N_USERS = 200
N_PRODUCTS = 60
N_ORDERS = 3000
N_SHOPS = 20


def generate(seed: int = SEED) -> tuple[list, list, list, list, list]:
    """生成五张表的行数据，返回 (users, products, orders, refunds, shops)。

    注意：refunds 的随机数在 orders 之后消费，因此不改变既有订单数据；
    shops 使用独立随机序列（seed+1），product_name / shop_id 使用确定性派生，
    均不消耗主 rng 序列，保证既有订单/退款数据完全可复现。
    """
    rng = random.Random(seed)
    asof = settings.AS_OF_DATE

    # 店铺维度表：门店名确定性生成，不消耗主 rng 序列（保证既有订单/退款数据可复现）
    shops: list[tuple] = [(sid, f"店铺{sid:02d}") for sid in range(1, N_SHOPS + 1)]

    users: list[tuple] = []
    for uid in range(1, N_USERS + 1):
        province = rng.choice(PROVINCES)
        gender = rng.choice(GENDERS)
        register_time = asof - timedelta(days=rng.randint(0, 730))
        users.append((uid, province, gender, register_time))

    products: list[tuple] = []
    price_map: dict[int, float] = {}
    for pid in range(1, N_PRODUCTS + 1):
        category = rng.choice(list(CATEGORIES.keys()))
        brand = rng.choice(CATEGORIES[category])
        unit_price = round(rng.uniform(9.9, 9999.0), 2)
        # product_name 确定性派生（不消耗 rng），保证既有订单/退款随机序列不变
        product_name = f"{brand}-{category}#{pid:03d}"
        products.append((pid, category, brand, unit_price, product_name))
        price_map[pid] = unit_price

    orders: list[tuple] = []
    refund_candidates: list[tuple] = []
    for oid in range(1, N_ORDERS + 1):
        uid = rng.randint(1, N_USERS)
        pid = rng.randint(1, N_PRODUCTS)
        qty = rng.randint(1, 5)
        gross = round(price_map[pid] * qty, 2)
        discount = round(gross * rng.uniform(0.0, 0.30), 2)
        amount = round(gross - discount, 2)
        status = "SUCCESS" if rng.random() < 0.88 else "CANCELLED"
        order_time = asof - timedelta(
            # 覆盖最近约 400 天（>1 年），使同比(yoy)/环比(mom)计算都有历史数据
            days=rng.randint(0, 400),
            seconds=rng.randint(0, 86399),
        )
        # shop_id 确定性派生（不消耗 rng），保持既有订单/退款随机序列不变
        shop_id = ((uid + pid) % N_SHOPS) + 1
        orders.append((oid, uid, pid, shop_id, amount, discount, status, order_time))
        if status == "SUCCESS":
            refund_candidates.append((oid, amount, order_time))

    # 退款事实表：每个成功订单约 12% 概率退款，与订单 1:1（无扇出放大）
    refunds: list[tuple] = []
    rid = 0
    for oid, amount, order_time in refund_candidates:
        if rng.random() < 0.12:
            rid += 1
            refund_amount = round(amount * rng.uniform(0.3, 1.0), 2)
            refund_status = "SUCCESS" if rng.random() < 0.9 else "PENDING"
            refund_time = order_time + timedelta(days=rng.randint(0, 7))
            refunds.append((rid, oid, refund_amount, refund_time, refund_status))

    return users, products, orders, refunds, shops


def _ddl(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE dim_user (
            user_id       INTEGER PRIMARY KEY,
            province      VARCHAR,
            gender        VARCHAR,
            register_time TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE dim_product (
            product_id   INTEGER PRIMARY KEY,
            category     VARCHAR,
            brand        VARCHAR,
            unit_price   DOUBLE,
            product_name VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE dim_shop (
            shop_id   INTEGER PRIMARY KEY,
            shop_name VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE fact_orders (
            order_id        INTEGER PRIMARY KEY,
            user_id         INTEGER,
            product_id      INTEGER,
            shop_id         INTEGER,
            order_amount    DOUBLE,
            discount_amount DOUBLE,
            pay_status      VARCHAR,
            order_time      TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE fact_refunds (
            refund_id     INTEGER PRIMARY KEY,
            order_id      INTEGER,
            refund_amount DOUBLE,
            refund_time   TIMESTAMP,
            refund_status VARCHAR
        )
    """)


def build_tables(conn: duckdb.DuckDBPyConnection, seed: int = SEED) -> None:
    """在给定连接上建表并灌数据（内存连接或文件连接均可）。"""
    _ddl(conn)
    users, products, orders, refunds, shops = generate(seed)

    conn.executemany("INSERT INTO dim_user VALUES (?, ?, ?, ?)", users)
    conn.executemany("INSERT INTO dim_product VALUES (?, ?, ?, ?, ?)", products)
    conn.executemany("INSERT INTO dim_shop VALUES (?, ?)", shops)
    conn.executemany("INSERT INTO fact_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)", orders)
    conn.executemany("INSERT INTO fact_refunds VALUES (?, ?, ?, ?, ?)", refunds)

    _write_metadata(conn)


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
    # 表级注释也一并入库
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
        build_tables(conn)
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("dim_user", "dim_product", "dim_shop", "fact_orders", "fact_refunds")
        }
        print("[init_duckdb] 表创建完成:")
        for t, n in counts.items():
            print(f"  - {t}: {n} 行")
        print(f"[init_duckdb] 数据库文件: {db_path}")
        print("[init_duckdb] 字段业务注释（_field_metadata）:")
        for row in conn.execute(
            "SELECT table_name, column_name, comment FROM _field_metadata ORDER BY table_name"
        ).fetchall():
            print(f"  - {row[0]}.{row[1]}: {row[2]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
