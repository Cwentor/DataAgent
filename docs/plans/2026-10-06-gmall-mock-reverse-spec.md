# 设计规格｜Gmall Mock 数据生成器反向解析（2026-10）

> 蓝本：尚硅谷 Gmall 数仓 2023 remake 业务+埋点 mock 生成器 jar（`gmall-remake-mock-2023-05-15-3.jar`，反向解析完成后已清理）。
> 反编译产物：CFR 0.152 反编译结果（仅解析期本地参考，已完成使命后清理）。
> 辅助蓝本：[PyRSA/gmall2022-mock](https://github.com/PyRSA/gmall2022-mock)（逻辑等价的 2022 重写版）。
> 目标：Python 重实现（`mock/gmall/`），全量替换现有 5 表 mock 数仓，产出 DuckDB 测试数仓 + 埋点 JSON 日志。

## 1. 原 jar 架构（Spring Boot，`com.atguigu.gmallre.mock`）

```
入口 GmallRemakeMockApplication
├─ 无参数（主模式）→ UserMockTask.mainTask()
│   ├─ mock.clear.busi=1 → ClearService 清空业务表
│   ├─ mock.new.user=N → UserInfoService.genUserInfos(N, clear?)
│   └─ UserStageChainFactory.produce() → user-session.count 个会话链（线程池并发）
│       每条链：initSessionId → initUserInfo(50% 登录老用户) → 随机省份(ar)
│       → 按 path.json 加权抽路径 → 逐 Stage 演算（页面日志 + 业务写入）
└─ "test" 参数 → TestMockTask.mock(N, dt)：只造 N 组启动+good_detail 日志（不写业务库）
```

**种子数据全部来自预载的 MySQL gmall 库（现归档于 `mock/gmall/upstream/gmall.sql`）**，jar 只生成：
用户、加购、收藏、领券/用券、订单头/明细、支付、退款/退款支付、评论、订单状态日志、埋点日志。

### 1.1 path.json 加权路径抽样

`[{"path":[页面序列], "rate":权重}]`，按 rate 加权抽一条路径；页面名 `lower_underscore` → Stage 类名
（如 `good_detail` → `GoodDetailStage`）。原 jar 的 path.json 共 12 条路径（含 `activity1111` 双11活动页），
现归档为 `mock/gmall/path.json`。

### 1.2 24 小时分布

`mock.start-time-weight`（24 个整数）加权抽小时，分钟/秒均匀随机（23 点分钟 0-30）。
会话内时间推进：每个页面加 `during_time`（5000~20000ms 均匀），动作再加 2000~3000ms。

## 2. 埋点日志 JSON 结构（埋点数据线）

每行一个 JSON（fastjson 序列化 `AppMain`，null 字段不输出）：

```
common: { mid, uid?, sid, vc, ch, os, ar, md, ba, is_new }
start:  { entry, open_ad_id, open_ad_ms, open_ad_skip_ms, loading_time }   # 启动日志（与 page 互斥）
page:   { last_page_id?, page_id, item_type?, item?, from_pos_id?, from_pos_seq?,
          during_time, refer_id?, extend1?, extend2? }
actions?:  [ { action_id, item_type, item, extend1?, extend2?, ts } ]
displays?: [ { item_type, item, pos_seq, pos_id } ]
err?:   { error_code, msg }
ts:     毫秒时间戳
```

- `common`：mid=`mid_1..max_mid`；ar=省 id（1..34）；uid=登录用户 id（未登录为 null）；
  md 设备 14 选 1（含 iPhone/vivo/OPPO/小米…），ba=md 首词；iPhone→ch=Appstore/os=iOS 13.x，
  否则 ch 六选一 / os=Android 13.0(70%)…；vc 四选一（2.1.134 占 70%）；is_new 0/1。
- `start`：entry install(5)/icon(75)/notice(20)；open_ad_id 1-20；open_ad_ms 1000-10000；
  open_ad_skip_ms 50% 为 0 否则 1000-100000；loading_time 1000-20000。
- `page.during_time`：显式传 5000~20000ms，未传时默认同区间。
- `displays`：页面曝光位。构建规则（`AppDisplay$AppDisplayBuilder.buildList`）：
  - 先铺 activityIds（pos_id=1, pos_seq=1, item_type=activity_id，首页双11活动位）；
  - 再生成 1~max_size 个 sku 曝光：item_type=sku_id，item=按用户性别加权选 sku
    （`mock.tm-weight.male/female` 11 品牌权重 → sku 级权重=品牌权重）；
    未登录用户 50% 用 skuRandomBuilderF/M（按 phone 尾数奇偶），uid=null 时 1..35 均匀。
  - 各页面曝光位 pos_id：home=1(活动)+2(信息流 max30)、search=3、good_list=10(max20)、
    good_detail=4(max9)、cart=5(max20)、mine=6(max20)、discovery=7、activity1111=8(max30)+9(banner max6)。
- `actions`（ActionId 枚举）：favor_add / favor_canel / cart_add / cart_remove /
  cart_add_num / cart_minus_num / trade_add_address / get_coupon。
- `page.page_id`（PageId 枚举 26 个）：home/category/discovery/top_n/favor/search/good_list/
  good_detail/good_spec/comment/comment_done/comment_list/cart/order/payment/payment_done/
  order_list/orders_unpaid/orders_undelivered/orders_unreceipted/orders_wait_comment/
  mine/activity1111/login/register。
- 错误日志：good_list/good_detail/cart/order 等页面 `.checkError()` 按 `mock.error.rate`(3%)
  附加 err（error_code 1001~4001）。
- 输出通道：file（logback，按天滚）/ http / kafka；`mock.log.db.enable=1` 时另写 z_log 表。
  本项目复刻 file 通道：`logs/gmall_applog/<YYYY-MM-DD>.json`。

### 2.1 Stage 业务行为（责任链，2023 版核心差异）

| Stage | 页面日志 | 业务写入 |
|---|---|---|
| StartApp | 启动日志 | — |
| Home | displays=活动位+信息流 | 登录用户按 `if_get_coupon_rate`(50%) 领 3 张随机券（coupon_use 1401） |
| Search | 曝光位 3 | — |
| GoodList | item=随机搜索词 | — |
| GoodDetail | item=从上个页面曝光加权选 sku（from_pos_id/seq 溯源；无曝光时 999） | 收藏/取消收藏（if_favor_rate 70）、领品类券、加购 if_cart_rate(100%)：已有同 sku 则 sku_num+1，否则新建 cart_info；若下一 stage 是 order 则仅标记 buyingSku |
| Activity1111 | 双11 活动页 | — |
| Cart | 曝光位 5 | if_cart_add_num(10%) 加数量 / cart_remove(10%) 删除 |
| Order | item=sku_ids | 未登录→登录/注册；生成 order_info(1001)+order_detail（购物车或直购 sku）+payment_info(1601)；选最优优惠券 vs 活动（取减免大者），金额按比例分摊到明细 |
| Payment | item=order_id | payment_info→1602、order_info→1002、coupon_use→1403、状态日志 |
| Mine / OrderList / Discovery / Login / Register | 页面日志；OrderList 触发 if_refund_rate(50%) 退款（order_info→1005 + order_refund_info 0701）否则完成评论（order_info→1004 + comment_info） | |
| End | — | 超时未付订单置 1003、退款单走 refund_payment（0702→0705）、订单 1005→1006 |

订单状态字典：1001 创建/1002 已支付/1003 已取消/1004 已完成/1005 退款中/1006 已退款；
支付状态 1601 创建/1602 已支付/1603 已取消；券状态 1401 已领取/1402 已使用(下单锁定)/1403 已核销；
退款状态 0701 申请/0702 待退款/0705 已退款。

## 3. 业务数据线（DuckDB 入仓模型）

### 3.1 种子表（直接从 `mock/gmall/upstream/gmall.sql` 提取 INSERT 行，确定性不依赖 rng）

| 表 | 行数 | 说明 |
|---|---|---|
| base_province | 34 | 中文省名 + region_id（1..7 大区） |
| base_region | 7 | 大区 |
| base_category1/2/3 | 17 / 113 / 1099 | 三级类目链 |
| base_trademark | 11 | 品牌（与 tm-weight 11 槽位对应） |
| spu_info / sku_info | 12 / 35 | 商品（sku 带 price/tm_id/category3_id 冗余） |
| coupon_info / coupon_range | 5 / 5 | 券（3201 满减/3202 折扣/3203 代金；range 3301 品类/3302 品牌/3303 spu） |
| activity_info / activity_rule / activity_sku | 4 / 5 / 13 | 活动（3101 满减/3102 折扣/3103 特价） |

### 3.2 生成表（复刻 jar 逻辑，rng 驱动）

| 表 | 粒度 | 关键字段与生成规则 |
|---|---|---|
| user_info | 用户 | login_name/email 随机、birthday=基准日-(180~660)月、user_level 7:2:1、phone=13+9 位、性别=phone 尾数奇偶（1/3 概率缺失 M/F 推断）、nick_name 中文名 |
| cart_info | 用户×sku | sku_num 可加、cart_price=sku 价、is_ordered/order_time |
| favor_info | 用户×sku | is_cancel |
| coupon_use | 用户×券×订单 | coupon_status 1401→1402→1403；expire_time |
| order_info | 订单 | consignee/phone 来自用户、province_id=会话省份、out_trade_no 15 位、total_amount=Σ明细 split_total、original_total_amount、activity_reduce_amount、coupon_reduce_amount、feight_fee 5-20、status 1001→1002/1003 |
| order_detail | 订单明细 | order_price=sku 价、sku_num、split_total_amount、split_coupon_amount、split_activity_amount（分摊：按明细金额比例，最后一条兜差额，HALF_UP 2 位） |
| order_detail_activity / order_detail_coupon | 明细×活动/券 | 分摊留痕 |
| payment_info | 订单 | payment_type 1101:1102:1103 = 40:50:10、status 1601→1602、callback_time=+20s |
| order_refund_info | 退款单 | refund_type 1501(30):1502(60)、refund_reason_type 七选一、amount=price×num、status 0701→0702→0705 |
| refund_payment | 退款支付 | 1601→1602 |
| comment_info | 明细×评价 | appraise 1201(80):1202(10):1203(4):1204(1) |
| order_status_log | 订单状态流转 | 每次状态变更一行 |

### 3.3 行为事实表（JSON 日志解析产物，非直接生成）

| 表 | 来源 | 字段 |
|---|---|---|
| fact_start | start 日志 | sid, mid, uid, ar(省), ch, os, entry, open_ad_ms, open_ad_skip_ms, loading_time, ts |
| fact_page_view | page 日志 | sid, mid, uid, ar, page_id, last_page_id, during_time, source_type?, item, item_type, from_pos_id, from_pos_seq, ts |
| fact_action | actions[] | sid, action_id, item, item_type, page_id(所属页), ts |
| fact_display | displays[] | sid, item, item_type, pos_id, pos_seq, page_id(所属页), ts |

### 3.4 与原 jar 的有意差异（诚实声明）

1. **时间跨度**：原 jar 单日运行（mock.date 一天）；本项目按"每日会话数"在
   [2021-01-01, AS_OF_DATE] 区间逐日演算（2021-01-01 ~ 2025-12-31，1826 天），模拟课程中每日跑批的真实用法，支撑同比/环比。
2. **规模缩减**：每日会话数默认 6（原 200），用户 300（原数千）——小而确定，构建秒级。
3. **多线程 → 单线程**：原 jar 线程池并发导致顺序不确定；本项目单线程保证种子 42 可复现。
4. **z_log / LogDb、http/kafka 通道不复刻**；只保留 file 通道 JSON 文件。
5. user_address / ware_* / seckill / spu_poster / financial_* 等 jar 主模式不生成的表不入仓。
6. 优惠分摊逻辑（满减按金额比例、最后一条兜差额）逐行复刻，以"分摊合计 = 减免总额"为正确性锚；
   3102 折扣活动减免按 (10 − benefit_discount)/10 比例计算（原实现直接乘 discount 值，疑似笔误）。
7. **订单明细宽表冗余**：order_detail 冗余品牌/类目链（tm_name/category1~3_name）/用户/省份/
   订单状态，对齐数仓 dwd 明细宽表实践，使语义层星型连接全部 N:1、无需链式 join。
8. **语义层不登记 payment_info**（订单级 1:1 事实在明细粒度会重复计算金额），保留库内直查；
   order_refund_info 经 (order_id, sku_id) 双键与明细行 1:1，sku 级退款可安全聚合。
9. 下单读取购物车时跳过已下单条目（原实现在 if-order-rate=100 时会重复下单已购条目，疑似笔误）；
   收藏取消置 is_cancel='1'（原实现疑似笔误置 '0'）。
10. 活动/优惠券有效期按「区间内随机锚日 ± 20 天」平移（对应原 jar updateRecentlyDate 手工刷新，
    使 1826 天数据域内活动/券可持续生效）。
11. 初始 300 用户的注册时间在数据域内均匀分布（原 jar 全部同日注册），支撑注册趋势分析。

## 4. 配置项 → 生成器参数映射

| 原 mock.* 配置 | 默认值 | 本项目参数（`mock/gmall/config.py`） |
|---|---|---|
| mock.date | 2022-06-08 | 锚定 `settings.AS_OF_DATE`（2025-12-31），不可配 |
| mock.user-session.count | 200 | `SESSIONS_PER_DAY = 8` |
| mock.max.mid | 1000000 | `MAX_MID = 1000` |
| mock.new.user / mock.clear.* | 0/1 | 无需（幂等全量重建） |
| mock.start-time-weight | 24 项 | 原样保留 |
| mock.payment_type_weight | 40:50:10 | 原样保留 |
| mock.page.during-time-ms | 20000 | 原样保留（during_time 上限） |
| mock.error.rate | 3 | 原样保留 |
| mock.detail.source-type-rate | 40:25:15:20 | source_type 权重（query/promotion/recommend/activity） |
| mock.if-cart/favor/order/refund-rate | 100/70/100/50 | 原样保留 |
| mock.search.keyword | 9 词 | 原样保留 |
| mock.user.update-rate | 20 | `USER_UPDATE_RATE`（老用户资料变更比例） |
| mock.tm-weight.male/female | 11 项 | 原样保留（品牌加权选品） |
| mock.refer-weight | 10:2:3:4:5 | 外链 refer 权重（fact_page_view.refer 溯源用） |
| mock.if-get-coupon 等 static 默认 | 50 | 取 AppConfig 静态默认值 |

## 5. 评测锚点与确定性约束

- 种子 42、`AS_OF_DATE = 2025-12-31`（数据域 2021-01-01 ~ 2025-12-31，1826 天）；
- 单一 `random.Random(42)` 主管道按固定顺序消费；派生值（品牌名、省名等）查表不消耗 rng；
- `python -m mock.init_duckdb` 幂等重建 `settings.DB_PATH`，各表行数打印校验；
- golden 首次生成后 sha256 锁定，此后任何生成逻辑改动都必须先跑行数/哈希对照。
