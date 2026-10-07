"""Gmall 测试数仓构建编排：种子 -> 用户 -> 逐日会话演算 -> 日志解析入仓。

用法（在项目根目录执行）：
    python -m mock.init_duckdb     # 等价于 build_gmall_tables 全量幂等重建

确定性约束（与既有评测锚点同源）：
- 单一 random.Random(seed) 按固定顺序消费：用户生成 -> 活动有效期平移 ->
  用户资料变更 -> 逐日逐会话演算；
- 派生值（品牌 / 省名 / sku 名称等）全部查表，不消耗主 rng；
- 数据域收敛于 [mock.gmall.config.DOMAIN_START, AS_OF_DATE]（2021-01-01 ~ 2025-12-31）。
"""

from __future__ import annotations

import json
import random
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from config import settings
from mock.gmall import business, config, parser, schema
from mock.gmall.logs import write_day_log
from mock.gmall.simulate import SEED_COLUMNS, SeedCatalog, SessionSimulator, WarehouseState

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def _bulk_insert(conn: duckdb.DuckDBPyConnection, table: str, rows: list[tuple]) -> None:
    """pandas DataFrame 批量插入（executemany 在十万行级耗时分钟级，不可用）。

    含 NULL 的整数列需转为 pandas 可空 Int64，否则 object 列无法对齐
    DuckDB 的 INTEGER 类型；None 在 VARCHAR / TIMESTAMP 列由 DuckDB 落为 NULL。
    """
    if not rows:
        return
    df = pd.DataFrame(rows)
    for idx in df.columns:
        col = df[idx]
        if col.dtype == object and col.map(lambda v: v is None or isinstance(v, int)).all():
            df[idx] = col.astype("Int64")
    conn.execute(f"INSERT INTO {table} SELECT * FROM df")


def _load_seed_rows() -> dict[str, list[list]]:
    seed_file = Path(__file__).resolve().parent / "seed_data.json"
    with open(seed_file, encoding="utf-8") as f:
        return json.load(f)


def _convert_seed_value(column: str, value) -> object:
    """种子行字面值 -> DuckDB 值：时间字符串转 datetime，*_id 数值串转 int。"""
    if isinstance(value, str):
        if _TS_RE.match(value):
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        if column.endswith("_id") and value.isdigit():
            return int(value)
    return value


def _insert_seed(conn: duckdb.DuckDBPyConnection, seed_rows: dict[str, list[list]]) -> None:
    for table, rows in seed_rows.items():
        cols = SEED_COLUMNS[table]
        placeholders = ", ".join(["?"] * len(cols))
        converted = [
            tuple(_convert_seed_value(c, v) for c, v in zip(cols, row, strict=False))
            for row in rows
        ]
        conn.executemany(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", converted
        )


def _build_users(rng: random.Random, asof: date) -> list[dict]:
    """初始用户：注册时间在数据域内均匀分布（支撑注册趋势分析；原 jar 全部同日注册）。"""
    users = business.gen_users(rng, config.N_USERS, asof)
    start_day = config.DOMAIN_START
    domain_days = (asof - start_day).days + 1
    for i, user in enumerate(users):
        user["user_id"] = i + 1
        reg_day = start_day + timedelta(days=rng.randint(0, domain_days - 1))
        user["create_time"] = datetime.combine(
            reg_day, time(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))
        )
        user["operate_time"] = None
    return users


def _user_rows(users: list[dict]) -> list[tuple]:
    return [
        (
            u["user_id"],
            u["login_name"],
            u["nick_name"],
            u["name"],
            u["phone_num"],
            u["email"],
            u["user_level"],
            u["birthday"],
            u["gender"],
            u["create_time"],
            u["operate_time"],
        )
        for u in users
    ]


def _cart_rows(state: WarehouseState) -> list[tuple]:
    return [
        (
            c["cart_id"],
            c["user_id"],
            c["sku_id"],
            c["cart_price"],
            c["sku_num"],
            c["sku_name"],
            c["is_ordered"],
            c["create_time"],
            c["operate_time"],
            c["order_time"],
        )
        for c in state.carts
    ]


def _favor_rows(state: WarehouseState) -> list[tuple]:
    return [
        (
            f["favor_id"],
            f["user_id"],
            f["sku_id"],
            f["spu_id"],
            f["is_cancel"],
            f["create_time"],
            f["operate_time"],
        )
        for f in state.favors
    ]


def _coupon_use_rows(state: WarehouseState) -> list[tuple]:
    return [
        (
            c["coupon_use_id"],
            c["coupon_id"],
            c["user_id"],
            c["order_id"],
            c["coupon_status"],
            c["get_time"],
            c["using_time"],
            c["used_time"],
            c["expire_time"],
            c["create_time"],
            c["operate_time"],
        )
        for c in state.coupon_uses
    ]


def _order_rows(state: WarehouseState) -> list[tuple]:
    return [
        (
            o["order_id"],
            o["out_trade_no"],
            o["user_id"],
            o["province_id"],
            o["consignee"],
            o["consignee_tel"],
            o["trade_body"],
            o["original_total_amount"],
            o["coupon_reduce_amount"],
            o["activity_reduce_amount"],
            o["total_amount"],
            o["feight_fee"],
            o["payment_way"],
            o["order_status"],
            o["create_time"],
            o["operate_time"],
            o["expire_time"],
            o["refundable_time"],
        )
        for o in state.orders
    ]


def _order_detail_rows(state: WarehouseState) -> list[tuple]:
    rows: list[tuple] = []
    detail_id = 0
    for o in state.orders:
        for d in o["details"]:
            detail_id += 1
            rows.append(
                (
                    detail_id,
                    o["order_id"],
                    o["order_status"],
                    d["user_id"],
                    d["province_id"],
                    d["sku_id"],
                    d["sku_name"],
                    d["order_price"],
                    d["sku_num"],
                    d["split_total_amount"],
                    d["split_coupon_amount"],
                    d["split_activity_amount"],
                    o["create_time"],
                    d["tm_id"],
                    d["tm_name"],
                    d["category3_id"],
                    d["category3_name"],
                    d["category2_id"],
                    d["category2_name"],
                    d["category1_id"],
                    d["category1_name"],
                )
            )
    return rows


def _payment_rows(state: WarehouseState) -> list[tuple]:
    return [
        (
            p["payment_id"],
            p["out_trade_no"],
            p["order_id"],
            p["user_id"],
            p["total_amount"],
            p["subject"],
            p["payment_type"],
            p["payment_status"],
            p["create_time"],
            p["callback_time"],
            p["callback_content"],
            p["operate_time"],
        )
        for p in state.payments
    ]


def _refund_rows(state: WarehouseState) -> list[tuple]:
    return [
        (
            r["refund_id"],
            r["user_id"],
            r["order_id"],
            r["sku_id"],
            r["refund_type"],
            r["refund_num"],
            r["refund_amount"],
            r["refund_reason_type"],
            r["refund_reason_txt"],
            r["refund_status"],
            r["create_time"],
            r["operate_time"],
        )
        for r in state.refunds
    ]


def _refund_payment_rows(state: WarehouseState) -> list[tuple]:
    return [
        (
            r["refund_payment_id"],
            r["order_id"],
            r["sku_id"],
            r["payment_type"],
            r["trade_no"],
            r["total_amount"],
            r["subject"],
            r["refund_status"],
            r["create_time"],
            r["callback_time"],
            r["callback_content"],
            r["operate_time"],
        )
        for r in state.refund_payments
    ]


def _comment_rows(state: WarehouseState) -> list[tuple]:
    return [
        (
            c["comment_id"],
            c["user_id"],
            c["sku_id"],
            c["spu_id"],
            c["order_id"],
            c["appraise"],
            c["nick_name"],
            c["head_img"],
            c["comment_txt"],
            c["create_time"],
            c["operate_time"],
        )
        for c in state.comments
    ]


def _status_log_rows(state: WarehouseState) -> list[tuple]:
    return [
        (s["status_log_id"], s["order_id"], s["order_status"], s["create_time"], s["operate_time"])
        for s in state.status_logs
    ]


def build_gmall_tables(
    conn: duckdb.DuckDBPyConnection,
    seed: int = 42,
    applog_dir: Path | None = None,
    write_applog: bool = True,
) -> dict[str, int]:
    """在给定连接上全量构建 Gmall 测试数仓，返回各表行数。"""
    rng = random.Random(seed)
    asof = settings.AS_OF_DATE
    start_day = config.DOMAIN_START
    domain_days = (asof - start_day).days + 1
    day_start = datetime.combine(start_day, time(0, 0, 0))
    day_end = datetime.combine(asof, time(23, 59, 59))

    schema.create_tables(conn)
    seed_rows = _load_seed_rows()
    _insert_seed(conn, seed_rows)

    catalog = SeedCatalog(seed_rows)
    state = WarehouseState()
    users = _build_users(rng, asof)
    state.users = users
    business.update_users(rng, users, config.USER_UPDATE_RATE, asof)

    sim = SessionSimulator(rng, catalog, state)
    sim.shift_seed_validity(day_start, day_end)

    # 逐日逐会话演算：业务行随会话落 state，埋点日志按天写 JSONL
    if applog_dir is None:
        applog_dir = Path(settings.APPLOG_DIR)
    if write_applog:
        applog_dir.mkdir(parents=True, exist_ok=True)  # 运行时自动创建日志目录
    total_events = 0
    for offset in range(domain_days):
        day = datetime.combine(start_day + timedelta(days=offset), time(0, 0, 0))
        sim.events.clear()
        for _ in range(config.SESSIONS_PER_DAY):
            sim.run_session(day)
        if write_applog:
            total_events += write_day_log(applog_dir, day, sim.events)

    # 埋点日志解析入仓（真实读取 JSONL，走解析链路）
    if write_applog:
        starts, views, actions, displays = parser.parse_applog_dir(applog_dir)
        _bulk_insert(conn, "fact_start", starts)
        _bulk_insert(conn, "fact_page_view", views)
        _bulk_insert(conn, "fact_action", actions)
        _bulk_insert(conn, "fact_display", displays)

    # 业务行入仓
    _bulk_insert(conn, "user_info", _user_rows(users))
    _bulk_insert(conn, "cart_info", _cart_rows(state))
    _bulk_insert(conn, "favor_info", _favor_rows(state))
    _bulk_insert(conn, "coupon_use", _coupon_use_rows(state))
    _bulk_insert(conn, "order_info", _order_rows(state))
    _bulk_insert(conn, "order_detail", _order_detail_rows(state))
    _bulk_insert(conn, "payment_info", _payment_rows(state))
    _bulk_insert(conn, "order_refund_info", _refund_rows(state))
    _bulk_insert(conn, "refund_payment", _refund_payment_rows(state))
    _bulk_insert(conn, "comment_info", _comment_rows(state))
    _bulk_insert(conn, "order_status_log", _status_log_rows(state))

    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "base_province",
            "base_category1",
            "base_category2",
            "base_category3",
            "base_trademark",
            "spu_info",
            "sku_info",
            "coupon_info",
            "activity_info",
            "user_info",
            "cart_info",
            "favor_info",
            "coupon_use",
            "order_info",
            "order_detail",
            "payment_info",
            "order_refund_info",
            "refund_payment",
            "comment_info",
            "order_status_log",
            "fact_start",
            "fact_page_view",
            "fact_action",
            "fact_display",
        )
    }
