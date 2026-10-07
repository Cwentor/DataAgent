"""会话行为链模拟器：复刻原 jar 的 stage 责任链（UserStageChain + stage/*）。

一次会话 = 按 path.json 加权抽取的页面路径，逐 Stage 演算：
- 每个页面产出一条埋点日志事件（start / page + actions + displays + err）；
- 同时在 WarehouseState 上落业务行（购物车 / 收藏 / 领券 / 订单 / 支付 / 退款 / 评论），
  对应原 jar 在 MySQL 业务库与埋点日志两条数据线上的双写。

与原 jar 的有意差异（详见 docs/plans/2026-10-06-gmall-mock-reverse-spec.md §3.4）：
- 单线程顺序演算（原线程池并发不可复现）；
- good_detail 从上个页面的曝光选择商品时只过滤 sku 位（原实现会把活动位 id 误当 sku）；
- 下单读取购物车时跳过已下单条目（原实现在 if-order-rate=100 时会重复下单已购条目，疑似笔误）；
- 3102 折扣活动的减免按 (10 − benefit_discount)/10 比例计算（原实现直接乘 discount 值，疑似笔误）；
- 活动/优惠券有效期按「区间内随机锚日 ± 20 天」平移（原 jar 通过 updateRecentlyDate 手工刷新）。
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from mock.gmall import business, config, names

# 种子表列序（与 seed_data.json 行值一一对应，来源 upstream/gmall.sql 的 CREATE TABLE）
SEED_COLUMNS = {
    "base_province": [
        "province_id",
        "province_name",
        "region_id",
        "area_code",
        "iso_code",
        "iso_3166_2",
        "create_time",
        "operate_time",
    ],
    "base_region": ["region_id", "region_name", "create_time", "operate_time"],
    "base_category1": ["category1_id", "category1_name", "create_time", "operate_time"],
    "base_category2": [
        "category2_id",
        "category2_name",
        "category1_id",
        "create_time",
        "operate_time",
    ],
    "base_category3": [
        "category3_id",
        "category3_name",
        "category2_id",
        "create_time",
        "operate_time",
    ],
    "base_trademark": ["tm_id", "tm_name", "logo_url", "create_time", "operate_time"],
    "spu_info": [
        "spu_id",
        "spu_name",
        "description",
        "category3_id",
        "tm_id",
        "create_time",
        "operate_time",
    ],
    "sku_info": [
        "sku_id",
        "spu_id",
        "price",
        "sku_name",
        "sku_desc",
        "weight",
        "tm_id",
        "category3_id",
        "sku_default_img",
        "is_sale",
        "create_time",
        "operate_time",
    ],
    "coupon_info": [
        "coupon_id",
        "coupon_name",
        "coupon_type",
        "condition_amount",
        "condition_num",
        "activity_id",
        "benefit_amount",
        "benefit_discount",
        "create_time",
        "range_type",
        "limit_num",
        "taken_count",
        "start_time",
        "end_time",
        "operate_time",
        "expire_time",
        "range_desc",
    ],
    "coupon_range": [
        "id",
        "coupon_id",
        "range_type",
        "range_id",
        "create_time",
        "operate_time",
    ],
    "activity_info": [
        "activity_id",
        "activity_name",
        "activity_type",
        "activity_desc",
        "start_time",
        "end_time",
        "create_time",
        "operate_time",
    ],
    "activity_rule": [
        "rule_id",
        "activity_id",
        "activity_type",
        "condition_amount",
        "condition_num",
        "benefit_amount",
        "benefit_discount",
        "benefit_level",
        "create_time",
        "operate_time",
    ],
    "activity_sku": ["id", "activity_id", "sku_id", "create_time", "operate_time"],
}

# 设备与系统属性（原 AppCommon.build 的加权选项）
_DEVICES = [
    ("xiaomi 13", 10),
    ("xiaomi 13 Pro", 10),
    ("xiaomi 12 ultra", 20),
    ("iPhone 14", 30),
    ("iPhone 13", 20),
    ("vivo x90", 20),
    ("iPhone 14 Plus", 20),
    ("vivo IQOO Z6x", 10),
    ("OPPO Remo8", 10),
    ("Redmi k50", 20),
    ("SAMSUNG Galaxy s22", 5),
    ("realme Neo2", 20),
    ("OPPO Oneplus 10", 5),
    ("SAMSUNG Galaxy S21", 3),
]
_IOS_VERSIONS = [("13.3.1", 30), ("13.2.9", 10), ("13.2.3", 10), ("12.4.1", 5)]
_ANDROID_VERSIONS = [("13.0", 70), ("12.0", 20), ("11.0", 5), ("10.1", 5)]
_ANDROID_CHANNELS = [
    ("xiaomi", 30),
    ("wandoujia", 10),
    ("web", 10),
    ("oppo", 20),
    ("vivo", 5),
    ("360", 5),
]
_APP_VERSIONS = [("2.1.134", 70), ("2.1.132", 20), ("2.1.111", 5), ("2.0.1", 5)]
_START_ENTRIES = [("install", 5), ("icon", 75), ("notice", 20)]

_PAGE_IDS_WITH_ERROR = {"good_list", "good_detail", "cart", "order"}

_EPOCH = datetime(1970, 1, 1, tzinfo=ZoneInfo("UTC"))


def _ts_ms(dt: datetime) -> int:
    return int((dt.replace(tzinfo=ZoneInfo("UTC")) - _EPOCH).total_seconds() * 1000)


def _weighted(rng: random.Random, options: list[tuple[str, int]]) -> str:
    return rng.choices([o for o, _ in options], weights=[w for _, w in options], k=1)[0]


def _weighted_int(rng: random.Random, weights: list[int]) -> int:
    """按权重抽 1..len(weights) 的索引位（原 RandomBox/RandomNumBuilder 语义）。"""
    return rng.choices(range(1, len(weights) + 1), weights=weights, k=1)[0]


def _load_paths() -> list[tuple[list[str], int]]:
    path_file = Path(__file__).resolve().parent / "path.json"
    with open(path_file, encoding="utf-8") as f:
        raw = json.load(f)
    return [(item["path"], int(item["rate"])) for item in raw]


class SeedCatalog:
    """种子表结构化视图（省份 / 品牌 / SPU / SKU / 券 / 活动）。"""

    def __init__(self, seed_rows: dict[str, list[list]]):
        self.rows = seed_rows
        self.provinces: list[dict] = self._dicts("base_province")
        self.regions: list[dict] = self._dicts("base_region")
        self.category1: list[dict] = self._dicts("base_category1")
        self.category2: list[dict] = self._dicts("base_category2")
        self.category3: list[dict] = self._dicts("base_category3")
        self.trademarks: list[dict] = self._dicts("base_trademark")
        self.spus: list[dict] = self._dicts("spu_info")
        self.skus: list[dict] = self._dicts("sku_info")
        self.coupons: list[dict] = self._dicts("coupon_info")
        self.coupon_ranges: list[dict] = self._dicts("coupon_range")
        self.activities: list[dict] = self._dicts("activity_info")
        self.activity_rules: list[dict] = self._dicts("activity_rule")
        self.activity_skus: list[dict] = self._dicts("activity_sku")
        self.sku_by_id = {s["sku_id"]: s for s in self.skus}
        # 类目链查找表（category3 -> category2 -> category1）
        self.cat2_by_id = {c["category2_id"]: c for c in self.category2}
        self.cat1_by_id = {c["category1_id"]: c for c in self.category1}
        # 优惠券生效范围（原 CouponInfoServiceImpl.initCouponCache：range 表回填）
        for coupon in self.coupons:
            for r in self.coupon_ranges:
                if r["coupon_id"] == coupon["coupon_id"]:
                    coupon["range_type"] = r["range_type"]
                    coupon["range_id"] = r["range_id"]
        # 活动挂规则与 sku（原 ActivityInfoServiceImpl.loadCache）
        for act in self.activities:
            act["rules"] = [
                r for r in self.activity_rules if r["activity_id"] == act["activity_id"]
            ]
            act["sku_ids"] = [
                a["sku_id"] for a in self.activity_skus if a["activity_id"] == act["activity_id"]
            ]
        # SKU 冗余品牌与类目链名称（dwd 明细宽表惯例，供语义层免链式 join）
        tm_name_by_id = {t["tm_id"]: t["tm_name"] for t in self.trademarks}
        for sku in self.skus:
            sku["tm_name"] = tm_name_by_id.get(sku["tm_id"])
            c3 = next((c for c in self.category3 if c["category3_id"] == sku["category3_id"]), None)
            sku["category3_name"] = c3["category3_name"] if c3 else None
            c2 = self.cat2_by_id.get(c3["category2_id"]) if c3 else None
            sku["category2_id"] = c2["category2_id"] if c2 else None
            sku["category2_name"] = c2["category2_name"] if c2 else None
            c1 = self.cat1_by_id.get(c2["category1_id"]) if c2 else None
            sku["category1_id"] = c1["category1_id"] if c1 else None
            sku["category1_name"] = c1["category1_name"] if c1 else None

    def enrich_detail(self, detail: dict, sku: dict, user_id: int, province_id: int) -> None:
        """明细行宽表冗余（品牌/类目链/用户/省份），一次写入保证订单内一致。"""
        detail["user_id"] = user_id
        detail["province_id"] = province_id
        for key in (
            "tm_id",
            "tm_name",
            "category3_id",
            "category3_name",
            "category2_id",
            "category2_name",
            "category1_id",
            "category1_name",
        ):
            detail[key] = sku.get(key)

    def _dicts(self, table: str) -> list[dict]:
        cols = SEED_COLUMNS[table]
        return [dict(zip(cols, row, strict=False)) for row in self.rows[table]]


class WarehouseState:
    """业务行累计容器（对应原 jar 的 MySQL 业务库）。"""

    def __init__(self):
        self.users: list[dict] = []
        self.carts: list[dict] = []
        self.favors: list[dict] = []
        self.coupon_uses: list[dict] = []
        self.orders: list[dict] = []
        self.payments: list[dict] = []
        self.refunds: list[dict] = []
        self.refund_payments: list[dict] = []
        self.comments: list[dict] = []
        self.status_logs: list[dict] = []
        # 增量索引：会话演算是 O(会话数×查询) 的热路径，全量扫描会在
        # 数万会话规模下退化为 O(n²)，这里按 user/status 维护倒排
        self._carts_by_user: dict[int, list[dict]] = {}
        self._favors_by_user: dict[int, list[dict]] = {}
        self._coupon_uses_by_user: dict[int, list[dict]] = {}
        self._coupon_ids_by_user: dict[int, set[int]] = {}
        self._orders_by_user_status: dict[tuple[int, str], list[dict]] = {}
        self._refunds_by_user_status: dict[tuple[int, str], list[dict]] = {}
        self._payments_by_order: dict[int, dict] = {}
        self._cart_id = 0
        self._favor_id = 0
        self._coupon_use_id = 0
        self._order_id = 0
        self._payment_id = 0
        self._refund_id = 0
        self._refund_payment_id = 0
        self._comment_id = 0
        self._status_log_id = 0

    def next_id(self, attr: str) -> int:
        value = getattr(self, attr)
        setattr(self, attr, value + 1)
        return value

    def add_cart(self, cart: dict) -> None:
        self.carts.append(cart)
        self._carts_by_user.setdefault(cart["user_id"], []).append(cart)

    def remove_cart(self, cart: dict) -> None:
        self.carts.remove(cart)
        self._carts_by_user[cart["user_id"]].remove(cart)

    def carts_of(self, user_id: int) -> list[dict]:
        return [c for c in self._carts_by_user.get(user_id, []) if c["is_ordered"] == 0]

    def add_favor(self, favor: dict) -> None:
        self.favors.append(favor)
        self._favors_by_user.setdefault(favor["user_id"], []).append(favor)

    def favors_of(self, user_id: int) -> list[dict]:
        return [f for f in self._favors_by_user.get(user_id, []) if f["is_cancel"] == "0"]

    def add_coupon_use(self, row: dict) -> None:
        self.coupon_uses.append(row)
        self._coupon_uses_by_user.setdefault(row["user_id"], []).append(row)
        self._coupon_ids_by_user.setdefault(row["user_id"], set()).add(row["coupon_id"])

    def coupon_ids_of(self, user_id: int) -> set[int]:
        return self._coupon_ids_by_user.get(user_id, set())

    def coupon_uses_of(self, user_id: int) -> list[dict]:
        return self._coupon_uses_by_user.get(user_id, [])

    def add_order(self, order: dict) -> None:
        self.orders.append(order)
        key = (order["user_id"], order["order_status"])
        self._orders_by_user_status.setdefault(key, []).append(order)

    def set_order_status(self, order: dict, new_status: str) -> None:
        key = (order["user_id"], order["order_status"])
        bucket = self._orders_by_user_status.get(key)
        if bucket is not None and order in bucket:
            bucket.remove(order)
        order["order_status"] = new_status
        self._orders_by_user_status.setdefault((order["user_id"], new_status), []).append(order)

    def orders_of(self, user_id: int, status: str) -> list[dict]:
        return self._orders_by_user_status.get((user_id, status), [])

    def add_payment(self, payment: dict) -> None:
        self.payments.append(payment)
        self._payments_by_order[payment["order_id"]] = payment

    def payment_of_order(self, order_id: int) -> dict | None:
        return self._payments_by_order.get(order_id)

    def add_refund(self, refund: dict) -> None:
        self.refunds.append(refund)
        key = (refund["user_id"], refund["refund_status"])
        self._refunds_by_user_status.setdefault(key, []).append(refund)

    def set_refund_status(self, refund: dict, new_status: str) -> None:
        key = (refund["user_id"], refund["refund_status"])
        bucket = self._refunds_by_user_status.get(key)
        if bucket is not None and refund in bucket:
            bucket.remove(refund)
        refund["refund_status"] = new_status
        self._refunds_by_user_status.setdefault((refund["user_id"], new_status), []).append(refund)

    def refunds_of(self, user_id: int, status: str) -> list[dict]:
        return self._refunds_by_user_status.get((user_id, status), [])


class SessionSimulator:
    """单日会话演算器：run_day(day, n_sessions) 推进业务库与日志事件列表。"""

    def __init__(
        self,
        rng: random.Random,
        catalog: SeedCatalog,
        state: WarehouseState,
    ):
        self.rng = rng
        self.catalog = catalog
        self.state = state
        self.paths = _load_paths()
        self.hour_weights = business.parse_weight(config.START_TIME_WEIGHT)
        self.tm_male = business.parse_weight(config.TM_WEIGHT_MALE)
        self.tm_female = business.parse_weight(config.TM_WEIGHT_FEMALE)
        self.payment_weights = business.parse_weight(config.PAYMENT_TYPE_WEIGHT)
        self.search_keywords = list(config.SEARCH_KEYWORDS)
        self.events: list[dict] = []

    # ------------------------------------------------------------------ #
    # 种子日期平移：活动/券锚日随机、有效窗口 ±20 天（对应原 updateRecentlyDate）
    # ------------------------------------------------------------------ #
    def shift_seed_validity(self, day_start: datetime, day_end: datetime) -> None:
        rng = self.rng
        span_days = (day_end.date() - day_start.date()).days + 1
        for act in self.catalog.activities:
            anchor = day_start + timedelta(days=rng.randint(0, span_days - 1))
            act["start_time"] = anchor - timedelta(days=rng.randint(1, 20))
            act["end_time"] = anchor + timedelta(days=rng.randint(1, 20))
        for coupon in self.catalog.coupons:
            anchor = day_start + timedelta(days=rng.randint(0, span_days - 1))
            coupon["start_time"] = anchor - timedelta(days=rng.randint(1, 20))
            coupon["end_time"] = anchor + timedelta(days=rng.randint(1, 20))

    # ------------------------------------------------------------------ #
    # 会话级公共字段（原 AppCommon.build + initAppCommonInfo）
    # ------------------------------------------------------------------ #
    def _gen_common(self, now: datetime) -> dict:
        rng = self.rng
        device = _weighted(rng, _DEVICES)
        brand = device.split(" ")[0]
        if brand == "iPhone":
            channel, os_name = "Appstore", "iOS " + _weighted(rng, _IOS_VERSIONS)
        else:
            channel, os_name = _weighted(rng, _ANDROID_CHANNELS), "Android " + _weighted(
                rng, _ANDROID_VERSIONS
            )
        province = rng.choice(self.catalog.provinces)
        return {
            "mid": f"mid_{rng.randint(1, config.MAX_MID)}",
            "sid": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
            "vc": "v" + _weighted(rng, _APP_VERSIONS),
            "ch": channel,
            "os": os_name,
            "ar": str(province["province_id"]),
            "md": device,
            "ba": brand,
            "is_new": str(rng.randint(0, 1)),
        }

    def _pick_sku_weighted(self, gender: str | None, phone_tail: int | None) -> dict:
        """按性别品牌权重选 sku（原 AppDisplay.getSkuIdByUser + SkuInfoServiceImpl）。"""
        rng = self.rng
        if gender == "M":
            weights = self.tm_male
        elif gender == "F":
            weights = self.tm_female
        elif gender is None and phone_tail is not None:
            weights = self.tm_female if phone_tail % 2 == 0 else self.tm_male
        else:
            return self.catalog.skus[rng.randint(0, len(self.catalog.skus) - 1)]
        tm_id = _weighted_int(rng, weights)
        candidates = [s for s in self.catalog.skus if s["tm_id"] == tm_id]
        return rng.choice(candidates) if candidates else rng.choice(self.catalog.skus)

    # ------------------------------------------------------------------ #
    # 曝光生成（原 AppDisplay$AppDisplayBuilder.buildList）
    # ------------------------------------------------------------------ #
    def _gen_displays(
        self, ctx: dict, pos_id: int, max_size: int, activity_ids: list[int] | None = None
    ) -> list[dict]:
        displays: list[dict] = []
        if activity_ids:
            for aid in activity_ids:
                displays.append(
                    {"item_type": "activity_id", "item": str(aid), "pos_seq": 1, "pos_id": 1}
                )
        display_size = self.rng.randint(1, max_size)
        user = ctx["user"]
        for i in range(len(displays), display_size + 1):
            if user is None:
                sku = self._pick_sku_weighted(None, None)
            else:
                sku = self._pick_sku_weighted(user["gender"], int(user["phone_num"][-1]))
            displays.append(
                {"item_type": "sku_id", "item": str(sku["sku_id"]), "pos_seq": i, "pos_id": pos_id}
            )
        return displays

    # ------------------------------------------------------------------ #
    # 日志事件构造（原 AppMain + LogService.doLog）
    # ------------------------------------------------------------------ #
    def _emit(
        self,
        ctx: dict,
        *,
        start: dict | None = None,
        page: dict | None = None,
        displays: list[dict] | None = None,
        actions: list[dict] | None = None,
        with_error: bool = False,
    ) -> dict:
        event: dict = {"common": dict(ctx["common"]), "ts": _ts_ms(ctx["cur_time"])}
        if start is not None:
            event["start"] = start
        if page is not None:
            event["page"] = page
        if displays:
            event["displays"] = displays
        if actions:
            event["actions"] = actions
        if with_error and self.rng.random() * 100 < config.ERROR_RATE:
            event["err"] = {
                "error_code": self.rng.randint(1001, 4001),
                "msg": " Exception in thread \\  java.net.SocketTimeoutException",
            }
        self.events.append(event)
        return event

    def _advance(self, ctx: dict, during_ms: int) -> None:
        ctx["cur_time"] += timedelta(milliseconds=during_ms)

    def _during(self) -> int:
        return self.rng.randint(config.DURING_TIME_MIN_MS, config.DURING_TIME_MAX_MS)

    def _page(self, ctx: dict, page_id: str, **extra) -> dict:
        page = {"page_id": page_id, "during_time": extra.pop("during_time", self._during())}
        page.update(extra)
        return page

    def _action(self, ctx: dict, action_id: str, item_type: str, item: str) -> dict:
        return {
            "action_id": action_id,
            "item_type": item_type,
            "item": item,
            "ts": _ts_ms(ctx["cur_time"]),
        }

    # ------------------------------------------------------------------ #
    # 登录 / 注册（原 LoginStage.checkLogin + RegisterStage.register）
    # ------------------------------------------------------------------ #
    def _ensure_user(self, ctx: dict, event: dict | None) -> None:
        if ctx["user"] is not None:
            return
        if self.rng.random() * 100 >= config.IF_REGISTER_RATE:
            # 登录既有用户
            ctx["user"] = self.rng.choice(self.state.users)
        else:
            # 注册新用户（原 RegisterStage：先落当前页日志，再补一条 register 页）
            user = self._new_user(ctx["cur_time"].date())
            ctx["user"] = user
            if event is not None and "page" in event:
                page_id = event["page"]["page_id"]
                event["page"]["last_page_id"] = None
                self._advance(ctx, 15000)
                register_page = self._page(
                    ctx,
                    "register",
                    during_time=15000,
                    item_type="user_id",
                    item=str(user["user_id"]),
                )
                register_page["last_page_id"] = page_id
                self._emit(ctx, page=register_page)
        ctx["common"]["uid"] = str(ctx["user"]["user_id"])
        ctx["carts"] = self.state.carts_of(ctx["user"]["user_id"])
        ctx["favors"] = self.state.favors_of(ctx["user"]["user_id"])

    def _new_user(self, day) -> dict:
        user = business.gen_users(self.rng, 1, day)[0]
        user["user_id"] = len(self.state.users) + 1
        user["create_time"] = datetime(day.year, day.month, day.day)
        user["operate_time"] = None
        self.state.users.append(user)
        return user

    # ------------------------------------------------------------------ #
    # 领券（原 CouponInfoServiceImpl.takeRandomCoupon）
    # ------------------------------------------------------------------ #
    def _take_random_coupons(self, ctx: dict, event: dict, sku: dict | None) -> None:
        user = ctx["user"]
        if user is None:
            return
        owned = self.state.coupon_ids_of(user["user_id"])
        now = ctx["cur_time"]
        candidates = self.catalog.coupons
        if sku is not None:
            candidates = [
                c
                for c in candidates
                if (c["range_type"] == "3301" and c["range_id"] == sku["category3_id"])
                or (c["range_type"] == "3302" and c["range_id"] == sku["tm_id"])
                or (c["range_type"] == "3303" and c["range_id"] == sku["spu_id"])
            ]
        else:
            candidates = self.rng.sample(
                candidates, min(config.GET_COUPON_COUNT_HOME, len(candidates))
            )
        actions = event.setdefault("actions", [])
        for coupon in candidates:
            if coupon["coupon_id"] in owned:
                continue
            row = {
                "coupon_use_id": self.state.next_id("_coupon_use_id"),
                "coupon_id": coupon["coupon_id"],
                "user_id": user["user_id"],
                "order_id": None,
                "coupon_status": "1401",
                "get_time": now,
                "using_time": None,
                "used_time": None,
                "expire_time": coupon["end_time"],
                "create_time": now,
                "operate_time": None,
            }
            self.state.add_coupon_use(row)
            owned.add(coupon["coupon_id"])
            actions.append(self._action(ctx, "get_coupon", "coupon_id", str(coupon["coupon_id"])))

    # ------------------------------------------------------------------ #
    # 优惠券 / 活动择优（原 takeBestCouponForOrder / joinActivityForOrder）
    # ------------------------------------------------------------------ #
    def _best_coupon(self, ctx: dict, details: list[dict]) -> tuple[dict, float] | None:
        best: tuple[dict, float] | None = None
        for coupon in self.catalog.coupons:
            matched = [
                d
                for d in details
                if (
                    coupon["range_type"] == "3301"
                    and coupon["range_id"] == self.catalog.sku_by_id[d["sku_id"]]["category3_id"]
                )
                or (
                    coupon["range_type"] == "3302"
                    and coupon["range_id"] == self.catalog.sku_by_id[d["sku_id"]]["tm_id"]
                )
                or (
                    coupon["range_type"] == "3303"
                    and coupon["range_id"] == self.catalog.sku_by_id[d["sku_id"]]["spu_id"]
                )
            ]
            if not matched:
                continue
            amount_sum = round(sum(d["order_price"] * d["sku_num"] for d in matched), 2)
            num_sum = sum(d["sku_num"] for d in matched)
            benefit = 0.0
            if coupon["coupon_type"] == "3201" and amount_sum > (coupon["condition_amount"] or 0):
                benefit = float(coupon["benefit_amount"] or 0)
            elif coupon["coupon_type"] == "3202" and num_sum >= (coupon["condition_num"] or 0):
                benefit = round(amount_sum * (10 - float(coupon["benefit_discount"] or 0)) / 10, 2)
            elif coupon["coupon_type"] == "3203":
                benefit = float(coupon["benefit_amount"] or 0)
            if benefit > 0 and (best is None or benefit > best[1]):
                best = (coupon, benefit)
        return best

    def _best_activity(self, day: datetime, details: list[dict]) -> tuple[dict, float] | None:
        best: tuple[dict, float] | None = None
        for act in self.catalog.activities:
            if not (act["start_time"] <= day <= act["end_time"]):
                continue
            matched = [d for d in details if d["sku_id"] in act["sku_ids"]]
            if not matched:
                continue
            amount_sum = round(sum(d["order_price"] * d["sku_num"] for d in matched), 2)
            num_sum = sum(d["sku_num"] for d in matched)
            reduce_amount = 0.0
            for rule in act["rules"]:
                if rule["activity_type"] == "3101" and amount_sum >= (
                    rule["condition_amount"] or 0
                ):
                    reduce_amount = max(reduce_amount, float(rule["benefit_amount"] or 0))
                elif rule["activity_type"] == "3102" and num_sum >= (rule["condition_num"] or 0):
                    reduce_amount = max(
                        reduce_amount,
                        round(amount_sum * (10 - float(rule["benefit_discount"] or 0)) / 10, 2),
                    )
            if reduce_amount > 0 and (best is None or reduce_amount > best[1]):
                best = (act, reduce_amount)
        return best

    # ------------------------------------------------------------------ #
    # Stage 实现（对应原 jar 各 Stage 类）
    # ------------------------------------------------------------------ #
    def stage_start_app(self, ctx: dict) -> bool:
        self._advance(ctx, self._during())
        start = {
            "entry": _weighted(self.rng, _START_ENTRIES),
            "open_ad_id": self.rng.randint(1, 20),
            "open_ad_ms": self.rng.randint(1000, 10000),
            "open_ad_skip_ms": 0 if self.rng.random() < 0.5 else self.rng.randint(1000, 100000),
            "loading_time": self.rng.randint(1000, 20000),
        }
        self._emit(ctx, start=start)
        return True

    def stage_home(self, ctx: dict) -> bool:
        now = ctx["cur_time"]
        act_ids = [
            a["activity_id"]
            for a in self.catalog.activities
            if a["start_time"] <= now <= a["end_time"]
        ]
        displays = self._gen_displays(ctx, 1, 7, activity_ids=act_ids)
        displays += self._gen_displays(ctx, 2, 30)
        ctx["last_display"] = displays
        page = self._page(ctx, "home")
        self._advance(ctx, page["during_time"])
        event = self._emit(ctx, page=page, displays=displays)
        # 原 HomeStage.handleCoupon：仅已登录用户领券，不触发登录
        if ctx["user"] is not None and self.rng.random() * 100 < config.IF_GET_COUPON_RATE:
            self._take_random_coupons(ctx, event, None)
        ctx["last_page"] = "home"
        return True

    def stage_search(self, ctx: dict) -> bool:
        displays = self._gen_displays(ctx, 3, 10)
        ctx["last_display"] = displays
        page = self._page(ctx, "search")
        self._advance(ctx, page["during_time"])
        self._emit(ctx, page=page, displays=displays)
        ctx["last_page"] = "search"
        return True

    def stage_good_list(self, ctx: dict) -> bool:
        displays = self._gen_displays(ctx, 10, 20)
        ctx["last_display"] = displays
        keyword = self.rng.choice(self.search_keywords)
        page = self._page(
            ctx, "good_list", item_type="keyword", item=keyword, last_page_id=ctx["last_page"]
        )
        self._advance(ctx, page["during_time"])
        self._emit(ctx, page=page, displays=displays, with_error=True)
        ctx["last_page"] = "good_list"
        return True

    def stage_activity1111(self, ctx: dict) -> bool:
        displays = self._gen_displays(ctx, 8, 30) + self._gen_displays(ctx, 9, 6)
        ctx["last_display"] = displays
        page = self._page(ctx, "activity1111")
        self._advance(ctx, page["during_time"])
        self._emit(ctx, page=page, displays=displays)
        # 原 jar 此处把 last_page 固定回 home（按原实现保留）
        ctx["last_page"] = "home"
        return True

    def stage_good_detail(self, ctx: dict) -> bool:
        rng = self.rng
        user = ctx["user"]
        sku_displays = [d for d in ctx["last_display"] if d["item_type"] == "sku_id"]
        if sku_displays:
            chosen = rng.choice(sku_displays)
            from_pos_id, from_pos_seq = chosen["pos_id"], chosen["pos_seq"]
        else:
            from_pos_id = from_pos_seq = 999
        sku = (
            self.catalog.sku_by_id[int(chosen["item"])]
            if sku_displays
            else self._pick_sku_weighted(
                user["gender"] if user else None,
                int(user["phone_num"][-1]) if user else None,
            )
        )
        displays = self._gen_displays(ctx, 4, 9)
        ctx["last_display"] = displays
        page = self._page(
            ctx,
            "good_detail",
            item_type="sku_id",
            item=str(sku["sku_id"]),
            last_page_id=ctx["last_page"],
            from_pos_id=from_pos_id,
            from_pos_seq=from_pos_seq,
        )
        self._advance(ctx, page["during_time"])
        event = self._emit(ctx, page=page, displays=displays, with_error=True)
        self._handle_favor(ctx, event, sku)
        if ctx["user"] is not None and self.rng.random() * 100 < config.IF_GET_COUPON_RATE:
            self._take_random_coupons(ctx, event, sku)
        self._try_buy(ctx, event, sku)
        ctx["last_page"] = "good_detail"
        ctx["cur_sku"] = sku
        return True

    def _handle_favor(self, ctx: dict, event: dict, sku: dict) -> None:
        user = ctx["user"]
        existing = (
            next((f for f in ctx["favors"] if f["sku_id"] == sku["sku_id"]), None)
            if user is not None
            else None
        )
        rng = self.rng
        if existing is not None:
            if rng.random() * 100 < config.IF_FAVOR_CANCEL_RATE:
                self._advance(ctx, 2000)
                existing["is_cancel"] = "1"
                existing["operate_time"] = ctx["cur_time"]
                ctx["favors"].remove(existing)
                event.setdefault("actions", []).append(
                    self._action(ctx, "favor_canel", "sku_id", str(sku["sku_id"]))
                )
            return
        if rng.random() * 100 < config.IF_FAVOR_RATE:
            # 原 GoodDetailStage.handleFavor：收藏前 checkLogin（可能触发注册）
            self._ensure_user(ctx, event)
            user = ctx["user"]
            self._advance(ctx, 2000)
            favor = {
                "favor_id": self.state.next_id("_favor_id"),
                "user_id": ctx["user"]["user_id"],
                "sku_id": sku["sku_id"],
                "spu_id": sku["spu_id"],
                "is_cancel": "0",
                "create_time": ctx["cur_time"],
                "operate_time": None,
            }
            self.state.add_favor(favor)
            ctx["favors"].append(favor)
            event.setdefault("actions", []).append(
                self._action(ctx, "favor_add", "sku_id", str(sku["sku_id"]))
            )

    def _try_buy(self, ctx: dict, event: dict, sku: dict) -> None:
        if self.rng.random() * 100 >= config.IF_CART_RATE:
            return
        self._ensure_user(ctx, event)
        if ctx["next_stage"] == "order":
            ctx["buying_sku"] = sku
            return
        self._advance(ctx, 3000)
        user = ctx["user"]
        existing = next((c for c in ctx["carts"] if c["sku_id"] == sku["sku_id"]), None)
        now = ctx["cur_time"]
        if existing is not None:
            existing["sku_num"] += 1
            existing["cart_price"] = sku["price"]
            existing["operate_time"] = now
        else:
            self.state._cart_id += 1
            cart = {
                "cart_id": self.state._cart_id,
                "user_id": user["user_id"],
                "sku_id": sku["sku_id"],
                "cart_price": sku["price"],
                "sku_num": 1,
                "sku_name": sku["sku_name"],
                "is_ordered": 0,
                "create_time": now,
                "operate_time": now,
                "order_time": None,
            }
            self.state.add_cart(cart)
            ctx["carts"].append(cart)
        event.setdefault("actions", []).append(
            self._action(ctx, "cart_add", "sku_id", str(sku["sku_id"]))
        )

    def stage_cart(self, ctx: dict) -> bool:
        displays = self._gen_displays(ctx, 5, 20)
        ctx["last_display"] = displays
        page = self._page(ctx, "cart", last_page_id=ctx["last_page"])
        self._advance(ctx, page["during_time"])
        event = self._emit(ctx, page=page, displays=displays, with_error=True)
        self._ensure_user(ctx, event)
        rng = self.rng
        if ctx["carts"]:
            self._advance(ctx, 2000)
            if rng.random() * 100 < config.IF_CART_ADD_NUM_RATE:
                cart = rng.choice(ctx["carts"])
                cart["sku_num"] += 1
                cart["operate_time"] = ctx["cur_time"]
                event.setdefault("actions", []).append(
                    self._action(ctx, "cart_add_num", "sku_id", str(cart["sku_id"]))
                )
            elif rng.random() * 100 < config.IF_CART_RM_RATE:
                cart = rng.choice(ctx["carts"])
                self.state.remove_cart(cart)
                ctx["carts"].remove(cart)
                event.setdefault("actions", []).append(
                    self._action(ctx, "cart_remove", "sku_id", str(cart["sku_id"]))
                )
        ctx["last_page"] = "cart"
        return True

    def stage_order(self, ctx: dict) -> bool:
        buyable = (ctx["last_page"] == "good_detail" and ctx["buying_sku"] is not None) or ctx[
            "carts"
        ]
        if not buyable:
            return False
        self._ensure_user(ctx, None)
        order, details = self._gen_order(ctx)
        page = self._page(
            ctx,
            "order",
            item_type="sku_ids",
            item=",".join(str(d["sku_id"]) for d in details),
            last_page_id=ctx["last_page"],
        )
        self._advance(ctx, page["during_time"])
        event = self._emit(ctx, page=page, with_error=True)
        if order["coupon_reduce_amount"] > 0:
            event.setdefault("actions", []).append(
                self._action(ctx, "get_coupon", "coupon_id", str(order["coupon_id"]))
            )
        self._gen_payment(ctx, order)
        ctx["order"] = order
        ctx["last_page"] = "order"
        return True

    def _gen_order(self, ctx: dict) -> tuple[dict, list[dict]]:
        rng = self.rng
        user = ctx["user"]
        now = ctx["cur_time"]
        details: list[dict] = []
        if ctx["last_page"] == "good_detail" and ctx["buying_sku"] is not None:
            sku = ctx["buying_sku"]
            detail = {
                "sku_id": sku["sku_id"],
                "sku_name": sku["sku_name"],
                "order_price": float(sku["price"]),
                "sku_num": 1,
                "split_coupon_amount": 0.0,
                "split_activity_amount": 0.0,
            }
            self.catalog.enrich_detail(detail, sku, user["user_id"], int(ctx["common"]["ar"]))
            details.append(detail)
            ctx["buying_sku"] = None
        else:
            for cart in list(ctx["carts"]):
                if rng.random() * 100 >= config.IF_ORDER_RATE or cart["is_ordered"] == 1:
                    continue
                detail = {
                    "sku_id": cart["sku_id"],
                    "sku_name": cart["sku_name"],
                    "order_price": float(cart["cart_price"]),
                    "sku_num": cart["sku_num"],
                    "split_coupon_amount": 0.0,
                    "split_activity_amount": 0.0,
                }
                self.catalog.enrich_detail(
                    detail,
                    self.catalog.sku_by_id[cart["sku_id"]],
                    user["user_id"],
                    int(ctx["common"]["ar"]),
                )
                details.append(detail)
                cart["is_ordered"] = 1
                cart["order_time"] = now
                cart["operate_time"] = now
            if ctx["last_page"] == "cart":
                ctx["carts"] = [c for c in ctx["carts"] if c["is_ordered"] == 0]
        business.recalc_details(details)

        order = {
            "order_id": self.state.next_id("_order_id"),
            "out_trade_no": names.gen_digits(rng, 15),
            "user_id": user["user_id"],
            "province_id": int(ctx["common"]["ar"]),
            "consignee": user["name"],
            "consignee_tel": user["phone_num"],
            "trade_body": ",".join(d["sku_name"] for d in details),
            "feight_fee": None,
            "payment_way": "3501",
            "order_status": "1001",
            "create_time": now,
            "operate_time": None,
            "expire_time": None,
            "refundable_time": now + timedelta(days=7),
            "coupon_id": None,
            "details": details,
        }

        coupon_pick = self._best_coupon(ctx, details)
        activity_pick = self._best_activity(now, details)
        coupon_use_row = None
        if coupon_pick and (activity_pick is None or coupon_pick[1] > activity_pick[1]):
            coupon, benefit = coupon_pick
            business.split_reduce(details, benefit, "split_coupon_amount")
            order["coupon_id"] = coupon["coupon_id"]
            coupon_use_row = next(
                (
                    c
                    for c in self.state.coupon_uses_of(user["user_id"])
                    if c["coupon_id"] == coupon["coupon_id"] and c["coupon_status"] == "1401"
                ),
                None,
            )
            if coupon_use_row is None:
                coupon_use_row = {
                    "coupon_use_id": self.state.next_id("_coupon_use_id"),
                    "coupon_id": coupon["coupon_id"],
                    "user_id": user["user_id"],
                    "order_id": None,
                    "coupon_status": "1401",
                    "get_time": now,
                    "using_time": None,
                    "used_time": None,
                    "expire_time": coupon["end_time"],
                    "create_time": now,
                    "operate_time": None,
                }
                self.state.add_coupon_use(coupon_use_row)
            coupon_use_row.update(
                {
                    "order_id": order["order_id"],
                    "coupon_status": "1402",
                    "using_time": now,
                    "operate_time": now,
                }
            )
        elif activity_pick is not None:
            _, reduce_amount = activity_pick
            business.split_reduce(details, reduce_amount, "split_activity_amount")

        business.recalc_details(details)
        business.recalc_order(order)
        self.state.add_order(order)
        self._status_log(order, now)
        return order, details

    def _gen_payment(self, ctx: dict, order: dict) -> None:
        payment_type = _weighted_int(self.rng, self.payment_weights)
        payment = {
            "payment_id": self.state.next_id("_payment_id"),
            "out_trade_no": order["out_trade_no"],
            "order_id": order["order_id"],
            "user_id": order["user_id"],
            "total_amount": order["total_amount"],
            "subject": order["trade_body"],
            "payment_type": f"110{payment_type}",
            "payment_status": "1601",
            "create_time": ctx["cur_time"],
            "callback_time": None,
            "callback_content": None,
            "operate_time": None,
        }
        self.state.add_payment(payment)

    def stage_payment(self, ctx: dict) -> bool:
        order = ctx["order"]
        if order is None:
            return False
        page = self._page(
            ctx,
            "payment",
            item_type="order_id",
            item=str(order["order_id"]),
            last_page_id=ctx["last_page"],
        )
        self._advance(ctx, page["during_time"])
        self._emit(ctx, page=page)
        self._advance(ctx, 10000)
        now = ctx["cur_time"]
        payment = self.state.payment_of_order(order["order_id"])
        payment.update(
            {
                "payment_status": "1602",
                "callback_time": now,
                "callback_content": "callback xxxxxxx",
                "operate_time": now,
            }
        )
        self.state.set_order_status(order, "1002")
        order["operate_time"] = now
        self._status_log(order, now)
        coupon_use_row = next(
            (
                c
                for c in self.state.coupon_uses_of(order["user_id"])
                if c["order_id"] == order["order_id"] and c["coupon_status"] == "1402"
            ),
            None,
        )
        if coupon_use_row is not None:
            coupon_use_row.update(
                {
                    "coupon_status": "1403",
                    "used_time": now,
                    "operate_time": now,
                }
            )
        ctx["last_page"] = "payment"
        return True

    def stage_mine(self, ctx: dict) -> bool:
        displays = self._gen_displays(ctx, 6, 20)
        ctx["last_display"] = displays
        page = self._page(ctx, "mine", last_page_id=ctx["last_page"])
        self._advance(ctx, page["during_time"])
        event = self._emit(ctx, page=page, displays=displays)
        self._ensure_user(ctx, event)
        ctx["last_page"] = "mine"
        return True

    def stage_order_list(self, ctx: dict) -> bool:
        page = self._page(ctx, "order_list", last_page_id=ctx["last_page"])
        self._advance(ctx, page["during_time"])
        event = self._emit(ctx, page=page)
        self._ensure_user(ctx, event)
        user = ctx["user"]
        now = ctx["cur_time"]
        paid = self.state.orders_of(user["user_id"], "1002")
        rng = self.rng
        if rng.random() * 100 < config.IF_REFUND_RATE and paid:
            order = rng.choice(paid)
            self.state.set_order_status(order, "1005")
            order["operate_time"] = now
            self._status_log(order, now)
            detail = order["details"][0]
            refund_type = rng.choices(["1501", "1502"], weights=[30, 60], k=1)[0]
            reason = rng.choices(
                ["1301", "1304", "1303", "1305", "1302", "1306", "1307"],
                weights=[30, 10, 10, 11, 12, 16, 8],
                k=1,
            )[0]
            self.state.add_refund(
                {
                    "refund_id": self.state.next_id("_refund_id"),
                    "user_id": user["user_id"],
                    "order_id": order["order_id"],
                    "sku_id": detail["sku_id"],
                    "refund_type": refund_type,
                    "refund_num": detail["sku_num"],
                    "refund_amount": round(detail["order_price"] * detail["sku_num"], 2),
                    "refund_reason_type": reason,
                    "refund_reason_txt": f"退款原因具体：{names.gen_digits(rng, 10)}",
                    "refund_status": "0701",
                    "create_time": now,
                    "operate_time": None,
                }
            )
        elif paid:
            order = rng.choice(paid)
            self.state.set_order_status(order, "1004")
            order["operate_time"] = now
            self._status_log(order, now)
            appraise = rng.choices(
                ["1201", "1202", "1203", "1204"],
                weights=business.parse_weight(config.APPRAISE_WEIGHT),
                k=1,
            )[0]
            for detail in order["details"]:
                self.state.comments.append(
                    {
                        "comment_id": self.state.next_id("_comment_id"),
                        "user_id": user["user_id"],
                        "sku_id": detail["sku_id"],
                        "spu_id": self.catalog.sku_by_id[detail["sku_id"]]["spu_id"],
                        "order_id": order["order_id"],
                        "appraise": appraise,
                        "nick_name": user["nick_name"],
                        "head_img": None,
                        "comment_txt": f"评论内容：{names.gen_digits(rng, 50)}",
                        "create_time": now,
                        "operate_time": None,
                    }
                )
        ctx["last_page"] = "order_list"
        return True

    def stage_end(self, ctx: dict) -> bool:
        """原 EndStage：超时未付订单置 1003；退款单完成打款；退款中订单置 1006。"""
        user = ctx["user"]
        if user is None:
            return True
        now = ctx["cur_time"] + timedelta(hours=1)
        for order in self.state.orders_of(user["user_id"], "1001"):
            self.state.set_order_status(order, "1003")
            order["expire_time"] = order["create_time"] + timedelta(minutes=10)
            order["operate_time"] = now
            payment = self.state.payment_of_order(order["order_id"])
            if payment is not None:
                payment["payment_status"] = "1603"
                payment["operate_time"] = now
        for refund in list(self.state.refunds_of(user["user_id"], "0701")):
            self.state.set_refund_status(refund, "0702")
            refund["operate_time"] = now
            rp = {
                "refund_payment_id": self.state.next_id("_refund_payment_id"),
                "order_id": refund["order_id"],
                "sku_id": refund["sku_id"],
                "payment_type": "1101",
                "trade_no": names.gen_digits(self.rng, 15),
                "total_amount": refund["refund_amount"],
                "subject": "退款",
                "refund_status": "1601",
                "create_time": now,
                "callback_time": None,
                "callback_content": None,
                "operate_time": None,
            }
            self.state.refund_payments.append(rp)
            now += timedelta(seconds=5)
            rp["refund_status"] = "1602"
            rp["callback_time"] = now
            rp["callback_content"] = "xxxxxxxxxxxxxxx"
            self.state.set_refund_status(refund, "0705")
            refund["operate_time"] = now
        for order in list(self.state.orders_of(user["user_id"], "1005")):
            self.state.set_order_status(order, "1006")
            order["operate_time"] = now
            self._status_log(order, now)
        return True

    def _status_log(self, order: dict, now: datetime) -> None:
        self.state.status_logs.append(
            {
                "status_log_id": self.state.next_id("_status_log_id"),
                "order_id": order["order_id"],
                "order_status": order["order_status"],
                "create_time": now,
                "operate_time": None,
            }
        )

    # ------------------------------------------------------------------ #
    # 会话驱动（原 UserStageChain.run + handleStages）
    # ------------------------------------------------------------------ #
    _STAGES: ClassVar[dict[str, Any]] = {
        "start_app": stage_start_app,
        "home": stage_home,
        "search": stage_search,
        "good_list": stage_good_list,
        "good_detail": stage_good_detail,
        "activity1111": stage_activity1111,
        "cart": stage_cart,
        "order": stage_order,
        "payment": stage_payment,
        "mine": stage_mine,
        "order_list": stage_order_list,
        "end": stage_end,
    }

    def run_session(self, day: datetime) -> None:
        rng = self.rng
        paths, weights = zip(*self.paths, strict=True)
        path = rng.choices(paths, weights=weights, k=1)[0]
        hour = _weighted_int(rng, self.hour_weights) - 1
        minute = rng.randint(0, 30) if hour == 23 else rng.randint(0, 59)
        start_time = day.replace(hour=hour, minute=minute, second=rng.randint(0, 59))
        ctx: dict = {
            "common": self._gen_common(start_time),
            "user": None,
            "carts": [],
            "favors": [],
            "cur_time": start_time,
            "last_page": None,
            "last_display": [],
            "buying_sku": None,
            "order": None,
            "next_stage": None,
        }
        # 会话登录老用户（原 initUserInfo：if_nologin_rate=50）
        if rng.random() * 100 >= config.IF_LOGIN_RATE and self.state.users:
            ctx["user"] = rng.choice(self.state.users)
            ctx["common"]["uid"] = str(ctx["user"]["user_id"])
            ctx["carts"] = self.state.carts_of(ctx["user"]["user_id"])
            ctx["favors"] = self.state.favors_of(ctx["user"]["user_id"])
        for i, stage_name in enumerate(path):
            if i < len(path) - 1:
                ctx["next_stage"] = path[i + 1]
            else:
                ctx["next_stage"] = None
            handler = self._STAGES.get(stage_name)
            if handler is None:
                raise ValueError(f"path.json 引用了未实现的 stage: {stage_name}")
            if not handler(self, ctx):
                break
