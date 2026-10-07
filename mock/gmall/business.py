"""业务数据生成（用户与订单金额组装），复刻原 jar 的 UserInfoServiceImpl / OrderInfo 逻辑。

全部随机消费来自调用方传入的 random.Random，保证种子确定性。
金额分摊规则（原 jar OrderInfo.sumOrderAmount / CouponUse.splitAmount /
ActivityOrder.splitAmount）：
- original_total_amount = Σ(明细单价×数量)；
- 优惠券/活动减免按明细金额占比分摊到 split_coupon_amount / split_activity_amount，
  最后一条明细兜差额（HALF_UP 2 位），保证 Σ分摊 = 减免总额；
- split_total_amount = 单价×数量 − 券分摊 − 活动分摊；
- total_amount = Σ split_total_amount。
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from mock.gmall import names


def _weighted(rng: random.Random, options: list[str], weights: list[int]) -> str:
    return rng.choices(options, weights=weights, k=1)[0]


def parse_weight(spec: str) -> list[int]:
    return [int(x) for x in spec.split(":")]


def gen_users(rng: random.Random, count: int, base_date: date) -> list[dict]:
    """复刻 UserInfoServiceImpl.initUserInfo：手机号决定性别（1/3 概率缺失）。"""
    users: list[dict] = []
    level_options, level_weights = ["1", "2", "3"], parse_weight("7:2:1")
    for _ in range(count):
        email = names.gen_email(rng)
        phone = "13" + names.gen_digits(rng, 9)
        birthday = base_date - timedelta(days=rng.randint(180, 660) * 30)
        level = _weighted(rng, level_options, level_weights)
        # 原 fixUserInfo：尾数奇偶定性别，尾数 %3==0 时 gender 字段置空（1/3）
        parity_gender = "F" if int(phone[-1]) % 2 == 0 else "M"
        gender = None if int(phone[-1]) % 3 == 0 else parity_gender
        last = names.inside_last_name(rng, parity_gender)
        users.append(
            {
                "login_name": email.split("@")[0],
                "nick_name": names.gen_nick_name(rng, parity_gender, last),
                "name": rng.choice(names.FAMILY_NAMES) + last,
                "phone_num": phone,
                "email": email,
                "user_level": level,
                "birthday": birthday,
                "gender": gender,
            }
        )
    return users


def update_users(rng: random.Random, users: list[dict], rate: int, asof: date) -> None:
    """复刻 updateUsers：按比例抽取存量用户做资料变更（operate_time 记为锚点日）。"""
    if rate <= 0:
        return
    count = max(1, len(users) * rate // 100)
    for user in rng.sample(users, count):
        rand = rng.randint(2, 7)
        parity_gender = "F" if int(user["phone_num"][-1]) % 2 == 0 else "M"
        if rand % 2 == 0:
            last = names.inside_last_name(rng, parity_gender)
            user["nick_name"] = names.gen_nick_name(rng, parity_gender, last)
        if rand % 3 == 0:
            user["user_level"] = _weighted(rng, ["1", "2", "3"], parse_weight("7:2:1"))
        if rand % 5 == 0:
            user["email"] = names.gen_email(rng)
        if rand % 7 == 0:
            user["phone_num"] = "13" + names.gen_digits(rng, 9)
        user["operate_time"] = asof


def split_reduce(details: list[dict], reduce_amount: float, split_key: str) -> None:
    """把减免总额按明细金额占比分摊（最后一条兜差额），复刻 jar 的 HALF_UP 2 位。"""
    if reduce_amount <= 0 or not details:
        return
    amounts = [round(d["order_price"] * d["sku_num"], 2) for d in details]
    total = sum(amounts)
    if total <= 0:
        return
    assigned = 0.0
    for i, d in enumerate(details):
        if i == len(details) - 1:
            share = round(reduce_amount - assigned, 2)
        else:
            share = round(reduce_amount * amounts[i] / total, 2)
            assigned = round(assigned + share, 2)
        d[split_key] = round(d[split_key] + share, 2)
    # 兜底：浮点残差收敛到最后一条明细
    diff = round(reduce_amount - sum(d[split_key] for d in details), 2)
    details[-1][split_key] = round(details[-1][split_key] + diff, 2)


def recalc_details(details: list[dict]) -> None:
    """split_total_amount = 单价×数量 − 券分摊 − 活动分摊（原 calcTotalAmount）。"""
    for d in details:
        gross = round(d["order_price"] * d["sku_num"], 2)
        d["split_total_amount"] = round(
            gross - d["split_coupon_amount"] - d["split_activity_amount"], 2
        )


def recalc_order(order: dict) -> None:
    """复刻 OrderInfo.sumOrderAmount：订单四项金额全部由明细汇总。"""
    details = order["details"]
    order["original_total_amount"] = round(sum(d["order_price"] * d["sku_num"] for d in details), 2)
    order["coupon_reduce_amount"] = round(sum(d["split_coupon_amount"] for d in details), 2)
    order["activity_reduce_amount"] = round(sum(d["split_activity_amount"] for d in details), 2)
    order["total_amount"] = round(sum(d["split_total_amount"] for d in details), 2)
