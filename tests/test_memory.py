"""会话上下文记忆（Session Memory & Multi-turn Context）单元 + 端到端测试。

覆盖验收标准：
1. 指代追问链路：上个月华东 GMV -> 那华南地区呢 -> 按天看趋势；
2. 话题切换：术语解释 RAG 不拼接遗留 DSL；
3. 安全与隔离：跨用户读取强隔离；RLS 在继承后仅注入一次；自愈失败不污染上轮状态。
"""

from __future__ import annotations

import time

from agent.memory import (
    SessionState,
    SessionStore,
    append_message,
    derive_active_entities,
    normalize_filters,
    resolve_context,
    strip_rls_filters,
)
from semantic.dsl_schema import Filter, FilterOperator, QueryDSL
from web.service import run_query


# --------------------------------------------------------------------------- #
# 公共构造
# --------------------------------------------------------------------------- #
def _last_dsl() -> QueryDSL:
    """上一轮 DSL 示例：上个月华南地区 GMV（守卫后，admin 无 RLS）。"""
    return QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [],
            "time_filter": {
                "granularity": "month",
                "range_type": "relative",
                "relative": {"amount": 1, "unit": "month", "mode": "calendar"},
                "comparison": "none",
                "reference_date": "2024-06-30",
            },
            "filters": [{"field": "province", "operator": "in", "value": ["广东"]}],
        }
    )


def _make_state(session_id: str = "s1", user_id: str = "alice") -> SessionState:
    state = SessionState(session_id=session_id, user_id=user_id)
    state.last_dsl = _last_dsl()
    return state


# --------------------------------------------------------------------------- #
# SessionStore：读写 / 隔离 / TTL / LRU
# --------------------------------------------------------------------------- #
def test_store_get_update_clear():
    store = SessionStore()
    state = SessionState(session_id="s1", user_id="alice")
    store.update("s1", "alice", state)
    got = store.get("s1", "alice")
    assert got is not None and got.user_id == "alice"
    assert store.clear("s1", "alice") is True
    assert store.get("s1", "alice") is None


def test_store_update_rejects_cross_user_overwrite():
    """就绪度评审 R5：同 session_id 被另一用户 update 时拒绝覆盖原用户状态。"""
    store = SessionStore()
    s_a = _make_state("s-x", "alice")
    append_message(s_a, "user", "上个月华东的GMV")
    store.update("s-x", "alice", s_a)

    # bob 用同一 session_id update（模拟会话 id 抢占）
    s_b = SessionState(session_id="s-x", user_id="bob")
    returned = store.update("s-x", "bob", s_b)
    # bob 的写入被拒绝：返回 alice 的现有状态，原状态保留
    assert returned.user_id == "alice"
    got = store.get("s-x", "alice")
    assert got is not None
    assert got.history and got.history[0].content == "上个月华东的GMV"
    # bob 仍然读不到该会话（跨用户隔离语义不变）
    assert store.get("s-x", "bob") is None


def test_store_update_rejects_cross_user_overwrite_persisted(tmp_path):
    """R5 持久层场景：内存 miss 时从 SQLite 校验归属，同样拒绝覆盖。"""
    db = str(tmp_path / "mem-r5.db")
    store1 = SessionStore(db_path=db)
    s_a = _make_state("s-p", "alice")
    append_message(s_a, "user", "按品类展开")
    store1.update("s-p", "alice", s_a)

    # 新实例（模拟重启后另一 worker）：内存为空，bob 的 update 走持久层归属校验
    store2 = SessionStore(db_path=db)
    s_b = SessionState(session_id="s-p", user_id="bob")
    returned = store2.update("s-p", "bob", s_b)
    assert returned.user_id == "alice"
    restored = store2.get("s-p", "alice")
    assert restored is not None
    assert restored.history and restored.history[0].content == "按品类展开"


# --------------------------------------------------------------------------- #
# SessionStore 持久化（整改指令3-2）：db_path 落盘 SQLite，跨实例一致
# --------------------------------------------------------------------------- #
def test_store_sqlite_persists_across_instances(tmp_path):
    """新实例（模拟重启/多 worker）从同一 SQLite 恢复会话状态。"""
    db = str(tmp_path / "sessions.db")
    store1 = SessionStore(db_path=db)
    state = SessionState(session_id="s-persist", user_id="alice")
    state.last_dsl = _last_dsl()
    append_message(state, "user", "上个月华东GMV")
    append_message(state, "assistant", "已查询")
    store1.update("s-persist", "alice", state)

    # 新实例（内存为空）从持久层恢复，含 last_dsl 与历史
    store2 = SessionStore(db_path=db)
    restored = store2.get("s-persist", "alice")
    assert restored is not None
    assert restored.user_id == "alice"
    assert restored.last_dsl is not None
    assert [m.alias for m in restored.last_dsl.metrics] == ["gmv"]
    assert [c.content for c in restored.history] == ["上个月华东GMV", "已查询"]


def test_store_sqlite_cross_user_isolation_persisted(tmp_path):
    """跨用户隔离同样作用于持久层恢复的会话。"""
    db = str(tmp_path / "sessions2.db")
    store1 = SessionStore(db_path=db)
    store1.update("s-persist2", "alice", SessionState(session_id="s-persist2", user_id="alice"))

    store2 = SessionStore(db_path=db)
    assert store2.get("s-persist2", "bob") is None  # 跨用户拒绝继承
    assert store2.get("s-persist2", "alice") is not None


def test_store_sqlite_clear_removes_persisted(tmp_path):
    db = str(tmp_path / "sessions3.db")
    store1 = SessionStore(db_path=db)
    store1.update("s-persist3", "alice", SessionState(session_id="s-persist3", user_id="alice"))
    assert store1.clear("s-persist3", "alice") is True

    store2 = SessionStore(db_path=db)
    assert store2.get("s-persist3", "alice") is None


def test_store_cross_user_isolation():
    """跨用户同一 session_id：get 返回 None（拒绝继承），不删除原用户状态。"""
    store = SessionStore()
    store.update("s1", "alice", SessionState(session_id="s1", user_id="alice"))
    assert store.get("s1", "bob") is None
    # 原用户状态不受影响
    assert store.get("s1", "alice") is not None


def test_store_clear_foreign_user():
    store = SessionStore()
    store.update("s1", "alice", SessionState(session_id="s1", user_id="alice"))
    assert store.clear("s1", "bob") is False
    assert store.get("s1", "alice") is not None


def test_store_ttl_expiry():
    store = SessionStore(ttl_seconds=1)
    store.update("s1", "alice", SessionState(session_id="s1", user_id="alice"))
    assert store.get("s1", "alice") is not None
    time.sleep(1.1)
    assert store.get("s1", "alice") is None


def test_store_lru_eviction():
    """超出 max_sessions 时淘汰最久未访问的会话。"""
    store = SessionStore(max_sessions=2)
    store.update("s1", "alice", SessionState(session_id="s1", user_id="alice"))
    store.update("s2", "alice", SessionState(session_id="s2", user_id="alice"))
    store.update("s3", "alice", SessionState(session_id="s3", user_id="alice"))
    assert len(store) == 2
    # s1 最久未访问被淘汰；s2/s3 保留
    assert store.get("s1", "alice") is None
    assert store.get("s2", "alice") is not None
    assert store.get("s3", "alice") is not None


def test_store_history_rolling():
    """历史消息按轮数滚动裁剪（每轮 user + assistant 两条）。"""
    store = SessionStore(history_turns=2)
    state = SessionState(session_id="s1", user_id="alice")
    for i in range(3):
        append_message(state, "user", f"q{i}")
        append_message(state, "assistant", f"a{i}")
    store.update("s1", "alice", state)
    got = store.get("s1", "alice")
    assert got is not None and len(got.history) == 4
    assert got.history[0].content == "q1"


# --------------------------------------------------------------------------- #
# resolve_context：省略指代 / 下钻 / 话题切换
# --------------------------------------------------------------------------- #
def test_resolve_no_history():
    res = resolve_context("那华南地区呢？", None, None)
    assert res.mode == "fresh" and res.reason == "no_history"
    assert res.dsl is None


def test_resolve_topic_switch_on_metric_term():
    """含显式指标词（完整新问题）-> fresh + topic_switch。"""
    res = resolve_context("看今年的总销售额", _last_dsl(), None)
    assert res.mode == "fresh" and res.reason == "topic_switch"
    assert res.dsl is None


def test_resolve_inherit_region():
    """省略指代：继承指标与时间，仅替换省份筛选。"""
    res = resolve_context("那华南地区呢？", _last_dsl(), None)
    assert res.mode == "inherit"
    assert res.dsl is not None
    assert "华南" in res.summary
    # 继承指标与时间窗口
    assert [m.alias for m in res.dsl.metrics] == ["gmv"]
    assert res.dsl.time_filter is not None
    assert res.dsl.time_filter.relative.unit.value == "month"
    # 筛选替换为华南（仅一条 province 过滤，无重复）
    province_filters = [f for f in res.dsl.filters if f.field == "province"]
    assert len(province_filters) == 1
    assert province_filters[0].value == ["广东"]


def test_resolve_inherit_single_province():
    res = resolve_context("那广东省呢？", _last_dsl(), None)
    assert res.mode == "inherit"
    province_filters = [f for f in res.dsl.filters if f.field == "province"]
    assert len(province_filters) == 1
    assert province_filters[0].operator.value == "eq"
    assert province_filters[0].value == "广东"


def test_resolve_inherit_time_delta():
    """时间增量：替换时间窗口，继承其余语义。"""
    res = resolve_context("那最近30天呢？", _last_dsl(), None)
    assert res.mode == "inherit"
    assert res.dsl is not None
    assert res.dsl.time_filter.range_type.value == "relative"
    assert res.dsl.time_filter.relative.amount == 30
    assert res.dsl.time_filter.relative.unit.value == "day"
    # 原有省份筛选保持
    assert [f.value for f in res.dsl.filters if f.field == "province"] == [["广东"]]


def test_resolve_drilldown_trend():
    """趋势词 -> drilldown：保留指标与筛选，时间粒度由 trend 工具展开。"""
    res = resolve_context("按天看趋势", _last_dsl(), None)
    assert res.mode == "drilldown" and res.reason == "drilldown_trend"
    assert res.dsl is not None
    assert "趋势" in res.summary
    assert [m.alias for m in res.dsl.metrics] == ["gmv"]
    assert [f.value for f in res.dsl.filters if f.field == "province"] == [["广东"]]


def test_resolve_drilldown_dimension():
    """下钻语句：追加品类维度。"""
    res = resolve_context("按品类展开", _last_dsl(), None)
    assert res.mode == "drilldown" and res.reason == "drilldown_dim"
    assert [d.field for d in res.dsl.dimensions] == ["category"]


def test_resolve_drilldown_generic_dimensions():
    """维度通用化（回归）：按省份 / 按支付状态 / 按性别 / 按品牌展开均被识别。"""
    cases = {
        "按省份展开": "province",
        "按品牌展开": "brand",
        "按支付状态展开": "pay_status",
        "按性别展开": "gender",
    }
    for query, expected in cases.items():
        res = resolve_context(query, _last_dsl(), None)
        assert res.mode == "drilldown" and res.reason == "drilldown_dim", query
        assert [d.field for d in res.dsl.dimensions] == [expected], query


def test_resolve_drilldown_dimension_no_duplicate():
    """同一维度已存在时不重复追加（维度置换：仅一次）。"""
    base = _last_dsl().model_copy(update={"dimensions": [{"field": "category"}]})
    res = resolve_context("再按品类展开", base, None)
    assert res.mode == "drilldown"
    assert [d.field for d in res.dsl.dimensions] == ["category"]


def test_resolve_no_delta():
    """无有效增量的短句 -> fresh（不误拼进上轮语义）。"""
    res = resolve_context("继续", _last_dsl(), None)
    assert res.mode == "fresh" and res.reason == "no_delta"


# --------------------------------------------------------------------------- #
# 排他/独立维度请求 -> RESET（Bad Case 回归，AGENTS 任务模块 A）
# --------------------------------------------------------------------------- #
def _gmv_category_dsl() -> QueryDSL:
    """上一轮 DSL：某时间段各品类 GMV（对应"上个月各品类的 GMV 排名"本轮）。"""
    return QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "category"}],
            "time_filter": {
                "granularity": "month",
                "range_type": "relative",
                "relative": {"amount": 1, "unit": "month", "mode": "calendar"},
                "comparison": "none",
                "reference_date": "2024-06-30",
            },
            "filters": [],
        }
    )


def test_resolve_exclusive_count_intent_resets():
    """Bad Case：'我只需要知道，广东有多少种品类' 必须 RESET，绝不继承上轮 gmv 指标/品类分组。"""
    res = resolve_context("我只需要知道，广东有多少种品类", _gmv_category_dsl(), None)
    assert res.mode == "fresh" and res.reason == "reset_intent"
    # RESET 不产出基于上轮 DSL 的合并结果（交由调用方独立重解析）
    assert res.dsl is None


def test_resolve_dimension_cardinality_resets_without_exclusive_word():
    """无排他词，但命中'多少个/几个 [维度]' -> 同样 RESET（新指标 count_distinct）。"""
    res = resolve_context("广东有多少个品类？", _gmv_category_dsl(), None)
    assert res.mode == "fresh" and res.reason == "reset_intent"
    assert res.dsl is None


def test_resolve_enum_dimension_resets():
    """枚举型'有哪些 [维度]'也是独立新问题 -> RESET（不复用上轮金额指标）。"""
    res = resolve_context("有哪些品类？", _gmv_category_dsl(), None)
    assert res.mode == "fresh" and res.reason == "reset_intent"
    assert res.dsl is None


def test_resolve_inherit_still_works_after_reset_rule():
    """回归：纯粹的维度替换追问仍未受影响，保持 inherit（指标不变，仅省筛选替换）。"""
    res = resolve_context("那广东省呢？", _last_dsl(), None)
    assert res.mode == "inherit"
    prov = [f for f in res.dsl.filters if f.field == "province"]
    assert prov[0].value == "广东"
    assert [m.alias for m in res.dsl.metrics] == ["gmv"]


# --------------------------------------------------------------------------- #
# 时间修改贪婪判定 + 计数度量被覆盖（补 Bug 回归，验收 1 & 2）
# --------------------------------------------------------------------------- #
def test_resolve_time_plus_order_count_resets_metrics():
    """验收 1：上轮 metrics=[gmv]、province=广东，当前问 '2024年有多少订单'
    -> 必须判定为全新计数指标（NEW_QUERY/RESET_METRIC），而非"仅微调时间"。

    即使问句含时间词"2024年"，也不得推断为时间微调继承：计数实体词（多少+订单）
    一律把多轮判定推向 topic_switch，调用方重新解析出 COUNT(order_id)。
    """
    res = resolve_context("2024年有多少订单", _last_dsl(), None)
    assert res.mode == "fresh" and res.reason == "topic_switch"
    assert "已识别为新问题" in res.summary
    # RESET 不产出基于上轮 DSL 的合并结果（严禁沿用 SUM(order_amount)/gmv）
    assert res.dsl is None


def test_resolve_pure_time_inherit_keeps_metric():
    """验收 2（对照组）：上轮 metrics=[gmv]，当前问 '2024年的呢'
    -> 无新度量表达，仅调整时间，必须保留 gmv 指标（inherit）。"""
    res = resolve_context("2024年的呢", _last_dsl(), None)
    assert res.mode == "inherit"
    assert [m.alias for m in res.dsl.metrics] == ["gmv"]
    # 时间窗口被替换为 2024 年绝对区间
    assert res.dsl.time_filter is not None
    assert res.dsl.time_filter.range_type.value == "absolute"
    assert res.dsl.time_filter.absolute.start.isoformat() == "2024-01-01"
    assert [m.field for m in res.dsl.metrics] == ["order_amount"]  # 仍是 SUM，未变 count


# --------------------------------------------------------------------------- #
# Filter 去重归一化（模块 B）
# --------------------------------------------------------------------------- #
def test_normalize_filters_collapses_eq_and_in_same_value():
    """同一个字段同时出现 province eq '广东' 与 province in ['广东'] -> 折叠为单条 eq。"""
    norm = normalize_filters(
        [
            Filter(field="province", operator=FilterOperator.EQ, value="广东"),
            Filter(field="province", operator=FilterOperator.IN, value=["广东"]),
        ]
    )
    assert len(norm) == 1
    assert norm[0].field == "province"
    assert norm[0].operator == FilterOperator.EQ
    assert norm[0].value == "广东"


def test_normalize_filters_union_multiple_in():
    """多个 IN -> 并集去重（布尔等价，消除重复成员）。"""
    norm = normalize_filters(
        [
            Filter(field="province", operator=FilterOperator.IN, value=["广东", "浙江"]),
            Filter(field="province", operator=FilterOperator.IN, value=["浙江", "江苏"]),
        ]
    )
    assert len(norm) == 1
    assert sorted(norm[0].value) == ["广东", "江苏", "浙江"]


def test_normalize_filters_preserves_single_and_distinct_fields():
    """单条件 / 不同字段不误折叠。"""
    single = normalize_filters([Filter(field="category", operator=FilterOperator.EQ, value="手机")])
    assert single == [Filter(field="category", operator=FilterOperator.EQ, value="手机")]
    multi = normalize_filters(
        [
            Filter(field="province", operator=FilterOperator.EQ, value="广东"),
            Filter(field="category", operator=FilterOperator.EQ, value="手机"),
        ]
    )
    assert len(multi) == 2


# --------------------------------------------------------------------------- #
# strip_rls_filters：RLS 去重净化
# --------------------------------------------------------------------------- #
def test_strip_rls_filters_removes_injected_rls():
    """守卫后 last_dsl 含 RLS 过滤；strip 后仅剩用户声明过滤。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [],
            "filters": [
                {"field": "province", "operator": "in", "value": ["上海", "江苏", "浙江", "山东"]},
                {
                    "field": "province",
                    "operator": "in",
                    "value": ["广东", "浙江", "江苏", "北京", "上海"],
                },
            ],
        }
    )
    stripped = strip_rls_filters(dsl, "analyst")
    province_filters = [f for f in stripped.filters if f.field == "province"]
    assert len(province_filters) == 1
    assert province_filters[0].value == ["上海", "江苏", "浙江", "山东"]


def test_strip_rls_filters_admin_untouched():
    dsl = _last_dsl()
    assert strip_rls_filters(dsl, None) is dsl
    assert strip_rls_filters(dsl, "admin") is dsl


# --------------------------------------------------------------------------- #
# derive_active_entities
# --------------------------------------------------------------------------- #
def test_derive_active_entities():
    entities = derive_active_entities(_last_dsl())
    assert entities["metrics"] == ["gmv"]
    assert entities["dimensions"] == []
    assert entities["filters"][0]["field"] == "province"
    assert entities["time_filter"] is not None


# --------------------------------------------------------------------------- #
# 端到端：指代追问链路（验收标准 1）
# --------------------------------------------------------------------------- #
def test_e2e_inherit_region_then_trend(conn):
    sid = "mem-e2e-inherit"
    # 轮次 1：上个月华东地区 GMV
    first = run_query("上个月华东地区的GMV是多少？", conn=conn, session_id=sid, user="alice")
    assert "error" not in first, first.get("error_detail")
    prov_f1 = [f for f in first["dsl"]["filters"] if f["field"] == "province"]
    assert prov_f1 == [
        {"field": "province", "operator": "in", "value": ["上海", "江苏", "浙江", "山东"]}
    ]

    # 轮次 2：那华南地区呢？ -> 继承上轮时间与指标，仅替换地区
    second = run_query("那华南地区呢？", conn=conn, session_id=sid, user="alice")
    assert "error" not in second, second.get("error_detail")
    assert second["context_summary"] and "华南" in second["context_summary"]
    assert second["session_id"] == sid
    prov_f2 = [f for f in second["dsl"]["filters"] if f["field"] == "province"]
    assert prov_f2 == [{"field": "province", "operator": "in", "value": ["广东"]}]
    # 时间窗口与指标被继承（与第一轮完全一致）
    assert second["dsl"]["time_filter"] == first["dsl"]["time_filter"]
    assert second["dsl"]["metrics"] == first["dsl"]["metrics"]

    # 轮次 3：按天看趋势 -> 在继承基础上追加按日分组的时间粒度
    third = run_query("按天看趋势", conn=conn, session_id=sid, user="alice")
    assert "error" not in third, third.get("error_detail")
    assert third["context_summary"] and "趋势" in third["context_summary"]
    dims3 = [d["field"] for d in third["dsl"]["dimensions"]]
    assert "order_time" in dims3
    assert third["dsl"]["time_filter"]["granularity"] == "day"
    # 继承的省份筛选仍在
    prov_f3 = [f for f in third["dsl"]["filters"] if f["field"] == "province"]
    assert prov_f3 == [{"field": "province", "operator": "in", "value": ["广东"]}]
    assert [m["alias"] for m in third["dsl"]["metrics"]] == ["gmv"]


# --------------------------------------------------------------------------- #
# 端到端：话题切换（验收标准 2）
# --------------------------------------------------------------------------- #
def test_e2e_topic_switch_resets(conn):
    sid = "mem-e2e-switch"
    first = run_query("上个月华东地区的GMV是多少？", conn=conn, session_id=sid, user="alice")
    assert "error" not in first

    # 轮次 4：术语解释 -> RAG，清理旧 DSL 依赖
    rag = run_query("帮我解释一下什么叫客单价", conn=conn, session_id=sid, user="alice")
    assert rag["action"] == "rag"
    # 下一轮省略指代不再继承（无法独立解析 -> 报错而非误拼旧 DSL）
    after = run_query("那广东省呢？", conn=conn, session_id=sid, user="alice")
    assert "error" in after
    assert "context_summary" not in after


# --------------------------------------------------------------------------- #
# 端到端：排他 + 维度计数 RESET（Bad Case 回归，验收标准 1）
# --------------------------------------------------------------------------- #
def test_e2e_exclusive_count_intent_resets_previous_metric(conn):
    """Session1 上个月各品类 GMV 排名 -> Session2 '我只需要知道，广东有多少种品类'。

    验收：第二轮的款式 DSL 必须把指标解析为 count_distinct(category)，不再沿用 gmv
    指标或 category 分组；province 过滤唯一（绝不出现 eq 与 IN 重复）。
    """
    sid = "mem-e2e-excl-count"
    # 轮次 1：上个月各品类的 GMV 排名
    first = run_query("上个月各品类的GMV排名", conn=conn, session_id=sid, user="alice")
    assert "error" not in first, first.get("error_detail")
    assert [m["alias"] for m in first["dsl"]["metrics"]] == ["gmv"]
    assert [d["field"] for d in first["dsl"]["dimensions"]] == ["category"]

    # 轮次 2：排他 + 维度基数请求 -> 必须 RESET，指标改为 count_distinct(category)
    second = run_query("我只需要知道，广东有多少种品类", conn=conn, session_id=sid, user="alice")
    assert "error" not in second, second.get("error_detail")
    # 指标被重置为 count_distinct(category)，不再沿用上轮的 gmv
    metrics = second["dsl"]["metrics"]
    assert len(metrics) == 1
    assert metrics[0]["field"] == "category"
    assert metrics[0]["agg"] == "count_distinct"
    assert [m["alias"] for m in metrics] != ["gmv"]
    # count 型基数查询不按品类分组（否则组内 count 恒为 1）
    assert [d["field"] for d in second["dsl"]["dimensions"]] == []
    # 单个有效的 province 过滤（广东），且整条 DSL 绝不含重复 province 条件
    prov = [f for f in second["dsl"]["filters"] if f["field"] == "province"]
    assert len(prov) == 1
    assert prov[0] == {"field": "province", "operator": "eq", "value": "广东"}
    assert len(second["dsl"]["filters"]) == 1


# --------------------------------------------------------------------------- #
# 端到端：跨用户隔离（验收标准 3）
# --------------------------------------------------------------------------- #
def test_e2e_cross_user_isolation(conn):
    sid = "mem-e2e-isolate"
    first = run_query("上个月华东地区的GMV是多少？", conn=conn, session_id=sid, user="alice")
    assert "error" not in first

    # 另一个用户用相同 session_id：状态隔离，拒绝继承
    other = run_query("那华南地区呢？", conn=conn, session_id=sid, user="bob")
    assert "context_summary" not in other
    assert "error" in other  # 无指标词且无上下文 -> 无法独立解析 -> 报错（绝不误拼）

    # 原用户 A 的状态未被破坏，仍可正常继承
    again = run_query("那广东省呢？", conn=conn, session_id=sid, user="alice")
    assert "error" not in again, again.get("error_detail")
    assert again["context_summary"] and "广东" in again["context_summary"]


# --------------------------------------------------------------------------- #
# 端到端：自愈兼容（验收标准 3）
# --------------------------------------------------------------------------- #
def test_e2e_self_heal_updates_last_dsl(conn, monkeypatch):
    """继承 DSL 执行报错 -> 自愈重写成功 -> 更新 last_dsl。"""
    import web.service as svc
    from agent.memory import default_session_store
    from exec.guards import SqlExecutionError

    sid = "mem-e2e-heal-ok"
    first = run_query("上个月华东地区的GMV是多少？", conn=conn, session_id=sid, user="alice")
    assert "error" not in first

    calls = {"n": 0}
    real_execute = svc.execute_sql

    def fake_execute(c, sql, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SqlExecutionError("Binder Error: 模拟精确引擎报错")
        return real_execute(c, sql, **kwargs)

    monkeypatch.setattr(svc, "execute_sql", fake_execute)
    monkeypatch.setattr(svc, "rewrite_dsl", lambda q, d, e, attempts=1, principal=None: d)

    second = run_query("那华南地区呢？", conn=conn, session_id=sid, user="alice")
    assert "error" not in second, second.get("error_detail")
    assert second["rewrites"] == 1
    # last_dsl 已更新为第二轮（华南）
    state = default_session_store().get(sid, "alice")
    assert state is not None and state.last_dsl is not None
    prov = [f.value for f in state.last_dsl.filters if f.field == "province"]
    assert prov == [["广东"]]


def test_e2e_self_heal_failure_preserves_last_dsl(conn, monkeypatch):
    """继承 DSL 彻底失败（自愈也失败）-> 不污染上轮有效状态。"""
    import web.service as svc
    from agent.memory import default_session_store
    from exec.guards import SqlExecutionError

    sid = "mem-e2e-heal-fail"
    first = run_query("上个月华东地区的GMV是多少？", conn=conn, session_id=sid, user="alice")
    assert "error" not in first
    before = default_session_store().get(sid, "alice").last_dsl.model_dump(mode="json")

    def boom(c, sql, **kwargs):
        raise SqlExecutionError("Binder Error: 模拟精确引擎报错")

    monkeypatch.setattr(svc, "execute_sql", boom)

    second = run_query("那华南地区呢？", conn=conn, session_id=sid, user="alice")
    assert "error" in second
    after = default_session_store().get(sid, "alice").last_dsl.model_dump(mode="json")
    assert after == before  # 上轮有效 DSL 未被污染


# --------------------------------------------------------------------------- #
# 端到端：RLS 在继承链路中仅注入一次（验收标准 3 红线）
# --------------------------------------------------------------------------- #
def test_e2e_rls_injected_once_after_inherit(conn):
    sid = "mem-e2e-rls"
    first = run_query(
        "上个月华东地区的GMV是多少？", conn=conn, session_id=sid, user="alice", principal="analyst"
    )
    assert "error" not in first, first.get("error_detail")

    second = run_query(
        "那华南地区呢？", conn=conn, session_id=sid, user="alice", principal="analyst"
    )
    assert "error" not in second, second.get("error_detail")
    prov = [f for f in second["dsl"]["filters"] if f["field"] == "province"]
    # 用户增量（华南 in 广东） + RLS（analyst 五省），恰好各一份，无重复注入
    assert len(prov) == 2
    assert ["广东"] in [f["value"] for f in prov]
    assert len([f for f in prov if f["value"] == ["广东", "浙江", "江苏", "北京", "上海"]]) == 1


def test_collect_deltas_vocabulary_data_driven(monkeypatch):
    """多轮继承的维度增量抽取跟随数据驱动词汇表（审计 §3.2-4）。

    词汇表已由 catalog_loader 从数仓 distinct 重建，新增成员后省略指代
    追问（"那西藏呢"）即可被结构化合并，无需改代码。
    """
    from agent import memory
    from semantic import catalog

    monkeypatch.setitem(
        catalog.DIMENSION_MEMBERS,
        "province",
        catalog.DIMENSION_MEMBERS["province"] + ("西藏",),
    )
    d = memory._collect_deltas("那西藏呢")
    assert d.provinces == ["西藏"]
