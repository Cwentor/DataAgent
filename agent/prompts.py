"""Prompt 模板：让 LLM 只产出受控的 QueryDSL JSON。

P0 —— 最小权限元数据注入（守卫前移）：build_* 系列接受 principal，
把"可用字段白名单"与"口径约定"先按主体过滤后再注入 prompt。
越权字段/口径根本不进入模型视野，而不是生成后再靠守卫拒绝。
security.guard.apply_policy 仍保留为事后纵深防御。
"""

from __future__ import annotations

from security.scope import scoped_field_listing, scoped_fields

# 维度基数探查约定：当用户询问"有几个 [维度]""[维度]数量"时，
# 自动将维度字段映射为 count_distinct 聚合指标，无需强制指定业务度量；
# "有哪些 [维度]" 除 count_distinct 指标外，需将维度字段加入 dimensions 以枚举成员值。
_DIM_COUNT_CONVENTION = (
    '维度基数探查："有几个地区/省份/品牌/品类" -> '
    "metrics=[{kind:aggregate, field:<维度字段>, agg:count_distinct, alias:<维度数>}]；"
    '"有哪些 [维度]" -> metrics=[{kind:aggregate, field:<维度字段>, agg:count_distinct, alias:<维度数>}]'
    " + dimensions=[<维度字段>]"
)

# 固定结构说明块（与 DSL 契约一致，不随主体变化）
_STRUCT_BLOCK = """QueryDSL JSON 结构（所有字段必须严格符合）：
{
  "metrics": [
    {"kind": "aggregate", "field": "<逻辑字段>", "agg": "sum|count|count_distinct|avg|min|max", "alias": "<别名>"},
    {"kind": "ratio", "numerator": {"kind":"aggregate","field":"<字段>","agg":"<聚合>","alias":"<别名>"}, "denominator": {"kind":"aggregate","field":"<字段>","agg":"<聚合>","alias":"<别名>"}, "alias": "<别名>"},
    {"kind": "window", "base": {"kind":"aggregate","field":"<字段>","agg":"<聚合>","alias":"<别名>"}, "func": "cumsum|moving_avg", "window_size": 7, "alias": "<别名>"}
  ],
  "dimensions": [{"field": "<逻辑字段>", "alias": "<可选>"}],
  "time_filter": {
    "granularity": "day|week|month|quarter",
    "range_type": "relative|absolute",
    "relative": {"amount": 1, "unit": "day|week|month|quarter|year", "mode": "trailing|calendar|to_date"},
    "absolute": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
    "comparison": "none|yoy|mom",
    "time_field": "order_time|refund_time|register_time"
  },
  "filters": [{"field": "<逻辑字段>", "operator": "eq|ne|in|gt|gte|lt|lte|between", "value": <标量或列表>}],
  "order_by": [{"field": "<指标别名或维度名>", "direction": "asc|desc"}],
  "limit": 100,
  "fill_gaps": false,
  "top_n": {"n": 3, "partition_by": ["<维度字段>"], "order_by": [{"field": "<指标别名>", "direction": "desc"}]}
}"""

# 口径约定条目：(text, required_fields)。required_fields 中任一字段不可见，
# 则该条约定整体不注入（防止把越权字段"教"给模型）。
_CONVENTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        '"GMV/销售额/成交额" -> metrics=[{field:order_amount, agg:sum, alias:gmv}]',
        ("order_amount",),
    ),
    (
        '"订单数" -> metrics=[{field:order_id, agg:count, alias:order_count}]',
        ("order_id",),
    ),
    (
        '"去重用户数/活跃用户" -> metrics=[{field:user_id, agg:count_distinct, alias:active_users}]',
        ("user_id",),
    ),
    (
        '"ARPU/人均消费" -> metrics=[{kind:ratio, numerator:{field:order_amount,agg:sum,alias:gmv}, denominator:{field:user_id,agg:count_distinct,alias:active_users}, alias:arpu}]',
        ("order_amount", "user_id"),
    ),
    (
        '退款/退款率口径：仅当退款字段可见时有效。"退款率" -> ratio(退款金额/订单金额)；"退款金额" -> aggregate(退款金额求和)',
        ("refund_amount", "order_amount"),
    ),
    (
        '时间表达（基本）："上个月" -> relative {amount:1, unit:month, mode:calendar}；'
        '"过去N天" -> relative {amount:N, unit:day, mode:trailing}；'
        '"2024年6月" -> absolute {start:"2024-06-01", end:"2024-07-01"}（end 为下月第一天，半开区间）',
        ("order_time",),
    ),
    (
        '时间表达（季度/至今语义）："上季度/上个季度" -> relative {amount:1, unit:quarter, mode:calendar}；'
        '"本季度" -> absolute 完整自然季度（[季初, 下季初)）；'
        '"本月至今/MTD" -> relative {amount:1, unit:month, mode:"to_date"}（窗口 [月首1日, 锚点)）；'
        '"本季度至今/QTD" -> relative {amount:1, unit:quarter, mode:"to_date"}（窗口 [季初, 锚点)）；'
        '"今年至今/YTD" -> relative {amount:1, unit:year, mode:"to_date"}（窗口 [年初1日, 锚点)）；'
        '"过去N个季度" -> relative {amount:N, unit:quarter, mode:trailing}；'
        "to_date 模式的 granularity 分别为 day/month/quarter（按 QTD/MTD 适当选择）",
        ("order_time",),
    ),
    (
        '时间主轴（time_field）：默认 "order_time"；退款时序分析（"每日退款金额/退款趋势"）'
        '应声明 time_field: "refund_time"，解除 order_time 硬编码',
        ("refund_time", "order_time"),
    ),
    (
        '支付口径：问句中出现"成功/成交"时，filters 中加 {field:pay_status, operator:eq, value:SUCCESS}',
        ("pay_status",),
    ),
    (
        '维度："各品类/按品类" -> category；"品牌" -> brand；"省份/各省" -> province；"每日/按天趋势" -> dimensions=[{field:order_time}] 且 time_filter.granularity=day',
        ("category", "brand", "province", "order_time"),
    ),
    (
        '排序：出现"最高/前N个" -> order_by=[{field:<主指标别名>, direction:desc}] 且 limit=N',
        (),
    ),
    (
        '窗口函数："累计/累计值" -> metrics=[{kind:window, base:{field:<字段>,agg:<聚合>,alias:<别名>}, func:cumsum, alias:<别名>}] 且 dimensions 含 order_time；"N日移动平均" -> func:moving_avg 且 window_size=N',
        ("order_time",),
    ),
    (
        '日期补零：问句含"补零/补齐" -> fill_gaps=true（需时间维度与明确时间窗口，支持 day/week/month/quarter 粒度）',
        ("order_time",),
    ),
    (
        '分组 Top-N："每省/每品牌/每品类 ... Top N ..." -> top_n={n:N, partition_by:[<分区维度>], order_by:[{field:<指标别名>,direction:desc}]}，且 dimensions 含分区维度与排名维度',
        ("province", "brand", "category"),
    ),
    (
        '数值过滤："金额100到5000元" -> filters 中 {field:order_amount, operator:between, value:[100,5000]}',
        ("order_amount",),
    ),
    (
        '同比/环比（comparison）："环比" -> comparison:mom；"同比" -> comparison:yoy；'
        "可与时间维度组合（按位配对）：prev CTE 时间列经 date_add 平移后 JOIN",
        (),
    ),
    (
        _DIM_COUNT_CONVENTION,
        (),
    ),
    (
        "指标覆盖与排他（多轮/独立新问题的关键判定）："
        '用户出现"我只需要知道 / 只看 / 仅统计 / 只要看 / 换成 / 不要之前的…"等排他表述，'
        '或询问"多少个 / 多少种 / 有几个 [维度实体]"（如"多少种品类""有几个地区""多少用户"）时，'
        "一律视为**独立新指标请求**：metrics 只能以该维度实体的 count_distinct 计数为准，"
        "**严禁复用上一轮金额/订单等指标，也不得把该维度实体只塞进 dimensions 延续旧度量**；"
        '例："我只需要知道，广东有多少种品类" -> '
        "metrics=[{field:category, agg:count_distinct, alias:category_count}]，"
        "不保留历史 GMV/分组，filters 仅 {field:province, operator:eq, value:广东}；"
        "filters 中同一字段只允许出现一条过滤条件（同值既不重复 eq 又 in，也不追加多条 AND）。",
        (),
    ),
    (
        "极值/实体维度（问什么就出什么维度）："
        "查询主体必须进入 dimensions —— 产品/商品 -> {field:product_name}，"
        "店铺/门店 -> {field:shop_name}，品类 -> {field:category}，品牌 -> {field:brand}；"
        "极值修饰词 -> order_by 方向：最高/最大/最好 -> desc，最低/最小/最差 -> asc（按主指标别名）；"
        '单数极值（"最高的X是什么/哪一个"）-> limit 1；"最高的N个 / 前N / N个店铺" -> limit N；'
        '例："2024年GMV最高的产品是什么" -> '
        "metrics=[{field:order_amount,agg:sum,alias:gmv}], dimensions=[{field:product_name}], "
        "order_by=[{field:gmv,direction:desc}], limit=1；"
        '纯标量统计（无实体维度，如"2024年总GMV是多少"）不得设置 order_by —— '
        "无维度时编译器会省略 ORDER BY 与 LIMIT。",
        (),
    ),
    (
        "多轮计数 vs 时间微调（继承判定关键）："
        '"2024年呢？/那最近30天呢？/那华南地区呢？" 仅调整时间/筛选，**保留上一轮 metrics**；'
        '但 "2024年有多少订单" 含明确新计数度量，metrics **必须重置**为 '
        "COUNT(order_id)（alias: order_count），严禁继续沿用上一轮 GMV/SUM(order_amount)；"
        '同理 "多少 [用户|商品]" -> COUNT(DISTINCT user_id / product_id)。'
        "计数单元（订单/单/笔/用户/人/客户/商品/产品）一旦与数量词共同出现，一律视为新指标。",
        ("order_id", "order_amount", "user_id", "product_id"),
    ),
    (
        "语义角色必须落到 DSL（不可只在解释中保留）："
        "先识别 metric（指标）、entity_dimension（实体维度）、ranking_direction（排序方向）、"
        "result_cardinality（返回条数）和 time_window（时间窗口），再输出 JSON；"
        '"2024年GMV最高的产品是什么" 必须同时生成 product_name 维度、gmv DESC 排序和 limit=1；'
        '"2024年总GMV是多少" 是纯标量，不应虚构维度或排序；最高/最低、哪个/是什么等语义不能丢失。',
        ("order_amount", "product_name"),
    ),
)

# 无论主体如何都成立的安全约束（末尾附加）
_SAFETY_TAIL = """如果问题超出可控范围或缺少关键信息，输出：{"error": "无法可靠解析"}"""


def build_system_prompt(principal: str | None = None) -> str:
    """按主体构造最小权限 System Prompt。

    字段白名单 = 主体可见字段；口径约定 = 仅保留引用字段全部可见的条目。
    """
    allowed = scoped_fields(principal)
    whitelist = scoped_field_listing(principal)
    convention_lines = [
        "- " + text for text, required in _CONVENTIONS if not required or set(required) <= allowed
    ]
    conventions = "\n".join(convention_lines)
    return (
        "你是企业级 ChatBI 的语义解析器。你只能输出一个 JSON 对象，表示受限查询 DSL（QueryDSL）。\n"
        "不要输出任何解释、Markdown 代码块或多余文字；不要生成 SQL；不要输出不存在的字段。\n\n"
        + _STRUCT_BLOCK
        + "\n\n可引用的逻辑字段（当前主体可用白名单，其余一律不得出现）：\n"
        + whitelist
        + "\n\n语义约定：\n"
        + conventions
        + "\n\n"
        + _SAFETY_TAIL
    )


def build_messages(query: str, principal: str | None = None) -> list[dict[str, str]]:
    """构造首轮对话消息：系统 Prompt（含字段白名单）+ 用户问题。"""
    return [
        {"role": "system", "content": build_system_prompt(principal)},
        {"role": "user", "content": f"问题：{query}\n请仅输出符合上述结构的 JSON。"},
    ]


def build_fix_messages(
    query: str, raw_output: str, error: str, principal: str | None = None
) -> list[dict[str, str]]:
    """构造重试消息：把校验错误反馈给 LLM，要求修正。"""
    return [
        {"role": "system", "content": build_system_prompt(principal)},
        {"role": "user", "content": f"问题：{query}\n请仅输出符合上述结构的 JSON。"},
        {"role": "assistant", "content": raw_output},
        {
            "role": "user",
            "content": f"你上次的输出无效，原因：{error}\n请重新输出修正后的 JSON。",
        },
    ]


def build_rewrite_messages(
    query: str, dsl_json: str, error: str, principal: str | None = None
) -> list[dict[str, str]]:
    """构造 SQL 执行自愈消息：把精确的引擎报错反馈给 LLM，要求重写 DSL。"""
    return [
        {"role": "system", "content": build_system_prompt(principal)},
        {"role": "user", "content": f"问题：{query}\n请仅输出符合上述结构的 JSON。"},
        {"role": "assistant", "content": dsl_json},
        {
            "role": "user",
            "content": (
                "你上次产出的 DSL 在编译/执行时报错："
                + error
                + "\n请根据该报错修正 DSL（例如缩小时间窗口、调整维度/过滤条件、"
                "改用受支持字段或修正字段引用），重新输出符合上述结构的 JSON。"
            ),
        },
    ]
