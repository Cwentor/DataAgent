"""Gmall mock 生成器参数（反向解析自原 jar 的 application.yml + AppConfig 静态默认值）。

对照关系见 docs/plans/2026-10-06-gmall-mock-reverse-spec.md §4。
与原 jar 的有意差异：
- mock.date 不再可配，锚定 settings.AS_OF_DATE；
- 逐日演算 [DOMAIN_START, AS_OF_DATE]（2021-01-01 ~ 2025-12-31）每一天（模拟课程每日跑批），
  每日 SESSIONS_PER_DAY 个会话（原单日 200）；
- 单线程演算保证种子确定性（原 jar 线程池并发顺序不可复现）。
"""

from __future__ import annotations

from datetime import date

# 会话与设备规模
SESSIONS_PER_DAY = 6  # 原 mock.user-session.count=200/日，缩减为小而确定
MAX_MID = 1000  # 原 mock.max.mid=1000000
N_USERS = 300  # 用户规模（原 mock.new.user 数千）
USER_UPDATE_RATE = 20  # 原 mock.user.update-rate：存量用户资料变更比例

# 数据域（确定性）：[DOMAIN_START, AS_OF_DATE] 逐日演算（含两端）。
# 2021-01-01 ~ 2025-12-31 共 1826 天，覆盖 5 个完整自然年，同比/环比/跨年均有全量历史。
DOMAIN_START = date(2021, 1, 1)

# 行为概率（原 mock.if-*-rate 与 AppConfig 静态默认）
IF_LOGIN_RATE = 50  # 原 if_nologin_rate=50：会话登录老用户的概率
IF_REGISTER_RATE = 50  # 原 if_noregister_when_nologin_rate=50：未登录时注册新用户的概率
IF_FAVOR_RATE = 70  # 原 mock.if-favor-rate
IF_FAVOR_CANCEL_RATE = 10  # AppConfig 默认
IF_CART_RATE = 100  # 原 mock.if-cart-rate
IF_CART_ADD_NUM_RATE = 10  # AppConfig 默认
IF_CART_RM_RATE = 10  # AppConfig 默认
IF_ORDER_RATE = 100  # 原 mock.if-order-rate
IF_REFUND_RATE = 50  # 原 mock.if-refund-rate
IF_GET_COUPON_RATE = 50  # AppConfig if_get_coupon_rate
GET_COUPON_COUNT_HOME = 3  # AppConfig get_coupon_count_home
IF_ADD_ADDRESS_RATE = 15  # AppConfig if_add_address（trade_add_address 动作）
ERROR_RATE = 3  # 原 mock.error.rate（百分比）

# 页面停留时长（毫秒）
DURING_TIME_MIN_MS = 5000
DURING_TIME_MAX_MS = 20000  # 原 mock.page.during-time-ms

# 权重串（原样保留自 application.yml）
START_TIME_WEIGHT = "10:5:0:0:0:0:5:5:5:10:10:15:20:10:10:10:10:10:20:25:30:35:30:20"
PAYMENT_TYPE_WEIGHT = "40:50:10"  # 1101 支付宝 / 1102 微信 / 1103 银联
TM_WEIGHT_MALE = "3:2:5:5:5:1:1:1:1:1:1"  # 11 品牌男性浏览权重
TM_WEIGHT_FEMALE = "1:5:1:1:2:2:2:5:5:5:5"  # 11 品牌女性浏览权重
REFER_WEIGHT = "10:2:3:4:5"
DETAIL_SOURCE_TYPE_RATE = "40:25:15:20"  # 查询/推广/推荐/促销
APPRAISE_WEIGHT = "80:10:4:1"  # 1201 好评 / 1202 中评 / 1203 差评 / 1204 追评

SEARCH_KEYWORDS = "轻薄本,拯救者,联想,小米,iPhone14,扫地机器人,衬衫,心相印纸抽,匡威".split(",")

# 埋点 JSON 日志落盘目录（相对项目根）
APPLOG_DIR = "logs/gmall_applog"
