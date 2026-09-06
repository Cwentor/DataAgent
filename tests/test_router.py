"""意图路由 + 语义澄清反问（P0）单元测试。

覆盖：
- 五分类意图路由（Text2SQL / RAG / 闲聊 / 澄清 / 系统动作）；
- 语义澄清：缺失时间窗口、未定义业务指标（高活用户）主动反问；
- 禁止静默回退默认值：启发式不把未定义指标近似映射为已有指标；
- RAG 检索返回口径文档；
- web.service.run_query 的路由响应（intent / action / clarifications / documents）。
"""

from __future__ import annotations

import pytest

from agent.clarify import detect_clarifications, undefined_metric_terms
from agent.errors import PipelineError
from agent.heuristic import DeterministicNL2DSL
from agent.rag import retrieve
from agent.router.intent_router import (
    CHITCHAT,
    CLARIFY,
    DATA_QUERY,
    GLOSSARY_EXPLAIN,
    SYSTEM_ACTION,
    IntentRouter,
    RouteDecision,
    route_decision,
)
from web.service import run_query


# --------------------------------------------------------------------------- #
# 语义澄清：未定义业务指标
# --------------------------------------------------------------------------- #
def test_undefined_metric_terms_detected():
    assert "高活用户" in undefined_metric_terms("高活用户的GMV是多少？")
    assert "高活跃用户" in undefined_metric_terms("高活跃用户的订单数？")


def test_defined_metrics_not_flagged_as_undefined():
    for q in ("活跃用户数是多少？", "去重用户数是多少？", "各品类GMV分布？"):
        assert undefined_metric_terms(q) == [], q


def test_detect_clarifications_empty_for_clear_query():
    assert detect_clarifications("2024年6月各品类成功订单的GMV分布？") == []


# --------------------------------------------------------------------------- #
# 禁止静默回退默认值：启发式拒绝未定义指标
# --------------------------------------------------------------------------- #
def test_heuristic_rejects_high_active_users():
    """未定义指标（高活跃用户）必须拒绝，绝不能静默映射为 active_users。"""
    h = DeterministicNL2DSL()
    with pytest.raises(PipelineError):
        h.run("高活跃用户的订单数？")
    with pytest.raises(PipelineError):
        h.run("高活用户的GMV是多少？")


def test_heuristic_still_parses_defined_metrics():
    h = DeterministicNL2DSL()
    dsl = h.run("活跃用户数是多少？")
    assert dsl.metrics[0].alias == "active_users"


# --------------------------------------------------------------------------- #
# 口径文档 RAG 检索
# --------------------------------------------------------------------------- #
def test_retrieve_gmv_doc():
    docs = retrieve("GMV的口径是什么？")
    assert docs and docs[0].key == "gmv"


def test_retrieve_refund_rate_doc():
    docs = retrieve("退款率怎么算？")
    assert docs and docs[0].key == "refund_rate"


def test_retrieve_empty_for_unrelated():
    assert retrieve("今天天气怎么样？") == []


def test_retrieve_semantic_without_exact_alias():
    """P0-4：无精确别名、仅有语义相近表述时，TF-IDF 稀疏向量余弦召回正确文档。"""
    docs = retrieve("平均每单的金额怎么算")
    assert docs and docs[0].key == "avg_order_amount"
    docs2 = retrieve("每个用户的消费水平")
    assert docs2 and docs2[0].key == "arpu"


# --------------------------------------------------------------------------- #
# web.service.run_query 路由响应
# --------------------------------------------------------------------------- #
def test_run_query_text2sql_has_intent(conn):
    result = run_query("2024年6月成功订单的GMV是多少？", conn=conn)
    assert "error" not in result
    assert result["intent"] == "text2sql"
    assert result["action"] == "text2sql"
    assert result["columns"] == ["gmv"]


def test_run_query_chitchat_rejects(conn):
    result = run_query("今天天气怎么样", conn=conn)
    assert result["intent"] == "chitchat"
    assert result["action"] == "chitchat"
    assert "error" in result


def test_run_query_clarify_returns_questions(conn):
    result = run_query("高活用户的GMV是多少？", conn=conn)
    assert result["intent"] == "text2sql"
    assert result["action"] == "clarify"
    assert result["clarifications"][0]["kind"] == "undefined_metric"
    assert result["clarifications"][0]["term"] == "高活用户"
    assert "error" in result


def test_run_query_rag_returns_documents(conn):
    result = run_query("GMV的口径是什么？", conn=conn)
    assert result["intent"] == "rag"
    assert result["action"] == "rag"
    assert result["documents"]
    assert result["documents"][0]["key"] == "gmv"


# --------------------------------------------------------------------------- #
# 意图路由与决策中心（Intent Router & Decision Engine）——五分类验收
# --------------------------------------------------------------------------- #
def test_route_decision_five_way_classification():
    """五分类验收：闲聊 / 系统操作 / 口径解释 / 数据查询 / 澄清反问。"""
    assert route_decision("你好，你是谁").intent == CHITCHAT
    assert route_decision("帮我重置当前会话").intent == SYSTEM_ACTION
    assert route_decision("客单价是怎么定义的").intent == GLOSSARY_EXPLAIN
    assert route_decision("上个月广东的订单总数").intent == DATA_QUERY
    assert route_decision("看下那个数据").intent == CLARIFY


def test_route_decision_contract_fields():
    """RouteDecision 输出契约：intent / confidence / reason / extracted_entities / 路由耗时。"""
    d = route_decision("上个月广东的订单总数")
    assert isinstance(d, RouteDecision)
    assert d.intent == DATA_QUERY
    assert 0.0 <= d.confidence <= 1.0
    assert d.reason
    assert isinstance(d.extracted_entities, dict)
    assert d.routing_latency_ms >= 0
    payload = d.to_dict()
    assert payload["intent"] == "data_query"
    assert payload["routing_latency_ms"] >= 0


def test_route_decision_fast_path_system_actions():
    """Fast-Path 规则拦截：显式系统指令纳秒级命中，零网络 / 零数据库。"""
    for q in ("/clear", "/reset", "重置对话", "清空上下文", "重置当前会话", "帮我重置当前会话"):
        d = route_decision(q)
        assert d.intent == SYSTEM_ACTION, q
        assert d.confidence == 1.0
        assert d.extracted_entities.get("action") == "reset_session"
    assert route_decision("退出").intent == SYSTEM_ACTION


def test_route_decision_fast_path_greeting():
    """Fast-Path 规则拦截：极短打招呼词直接返回 CHITCHAT。"""
    for q in ("你好", "hello", "hi", "再见", "谢谢"):
        d = route_decision(q)
        assert d.intent == CHITCHAT, q
        assert d.confidence == 1.0


def test_router_latency_within_budget():
    """路由层耗时可控：Fast-Path 规则平均判定在毫秒级阈值内（烟测，宽松防 CI 抖动）。"""
    import time

    router = IntentRouter()
    start = time.perf_counter()
    for _ in range(100):
        router.route("你好")
    fast_path_ms = (time.perf_counter() - start) * 1000.0 / 100
    assert fast_path_ms < 10.0, f"Fast-Path 平均耗时 {fast_path_ms:.3f}ms 超阈值"

    start = time.perf_counter()
    for _ in range(50):
        router.route("上个月广东的订单总数是多少")
    rule_ms = (time.perf_counter() - start) * 1000.0 / 50
    assert rule_ms < 20.0, f"规则兜底平均耗时 {rule_ms:.3f}ms 超阈值"


def test_route_decision_contextual_followup_with_last_dsl():
    """有上轮 DSL 时，省略指代应判 DATA_QUERY（交给 memory 消解继承）。"""
    d = route_decision("那华南呢？")
    assert d.intent == CLARIFY  # 无上轮 DSL：信息不足 -> 澄清反问
    d2 = route_decision("那华南呢？", last_dsl=_fake_last_dsl())
    assert d2.intent == DATA_QUERY
    assert d2.extracted_entities.get("inherit") is True


def _fake_last_dsl():
    from semantic.dsl_schema import QueryDSL

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
            "filters": [{"field": "province", "operator": "in", "value": ["上海"]}],
        }
    )


class _FakeLLM:
    """可控的假 LLM 客户端：按预设返回 payload 或抛出异常。"""

    def __init__(self, payload):
        self.payload = payload

    def chat(self, messages):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_router_llm_failure_falls_back_to_rules():
    """LLM 调用异常 -> 优雅降级到规则兜底，不抛未捕获异常。"""
    router = IntentRouter(llm=_FakeLLM(ValueError("boom")))
    d = router.route("上个月广东的订单总数")
    assert d.intent == DATA_QUERY  # 规则兜底仍给出正确意图
    assert d.reason.startswith("rule:")


def test_router_llm_invalid_json_falls_back_to_rules():
    """LLM 输出无法解析 -> 降级规则，绝不向外抛异常。"""
    router = IntentRouter(llm=_FakeLLM("这不是 JSON"))
    d = router.route("看下那个数据")
    assert d.intent == CLARIFY
    assert d.confidence >= 0.5


def test_router_llm_low_confidence_rejected():
    """LLM 判决置信度低于阈值 -> 拒绝采纳，走规则兜底。"""
    router = IntentRouter(
        llm=_FakeLLM(
            '{"intent": "data_query", "confidence": 0.3, "reason": "low", '
            '"extracted_entities": {}}'
        )
    )
    # 规则层判定该输入信息不足 -> CLARIFY（不会因 LLM 低置信度硬判 data_query）
    d = router.route("看下那个数据")
    assert d.intent == CLARIFY


def test_router_llm_valid_decision_adopted():
    """LLM 高置信度合法判决被采纳。"""
    router = IntentRouter(
        llm=_FakeLLM(
            '{"intent": "system_action", "confidence": 0.9, "reason": "llm says reset", '
            '"extracted_entities": {"action": "reset_session"}}'
        )
    )
    d = router.route("帮我把对话清一清")
    assert d.intent == SYSTEM_ACTION
    assert d.extracted_entities.get("action") == "reset_session"


def test_router_never_raises_on_garbage():
    """任意脏输入：路由层绝不抛未捕获异常（安全兜底）。"""
    router = IntentRouter(llm=_FakeLLM(ValueError("down")))
    for q in ("", "   ", "\n", "ajskdl123!@#", "？？？", "😀😀😀"):
        d = router.route(q)
        assert d.intent in (CHITCHAT, DATA_QUERY, GLOSSARY_EXPLAIN, SYSTEM_ACTION, CLARIFY)
        assert 0.0 <= d.confidence <= 1.0


# --------------------------------------------------------------------------- #
# 端到端验收：run_query 五分类分派
# --------------------------------------------------------------------------- #
def test_e2e_chitchat_no_db_connection(conn, monkeypatch):
    """验收：『你好，你是谁』-> CHITCHAT，且不获取数据库连接（守卫前移）。"""
    import web.service as svc

    acquired = {"n": 0}

    class _CountingPool:
        def acquire(self):
            acquired["n"] += 1
            return conn

        def release(self, c):
            pass

    monkeypatch.setattr(svc, "default_pool", lambda: _CountingPool())

    def _raise_if_executed(c, sql, **kwargs):
        raise AssertionError(f"闲聊轮绝不允许执行 SQL: {sql}")

    monkeypatch.setattr(svc, "execute_sql", _raise_if_executed)

    result = run_query("你好，你是谁", session_id="r-sys-chitchat", user="alice")
    assert result["detected_intent"] == "chitchat"
    assert result["action"] == "chitchat"
    assert result["intent"] == "chitchat"  # 旧值向后兼容
    assert "error" in result
    assert acquired["n"] == 0  # 未获取任何数据库连接
    assert result.get("dsl") is None and result.get("sql") is None


def test_e2e_system_action_reset_session(conn):
    """验收：『帮我重置当前会话』-> SYSTEM_ACTION，成功清空会话内存。"""
    from agent.memory import default_session_store

    sid = "r-sys-reset"
    first = run_query("上个月华东地区的GMV是多少？", conn=conn, session_id=sid, user="alice")
    assert "error" not in first, first.get("error_detail")
    assert default_session_store().get(sid, "alice") is not None

    result = run_query("帮我重置当前会话", conn=conn, session_id=sid, user="alice")
    assert result["detected_intent"] == "system_action"
    assert result["action"] == "system_action"
    assert result["intent"] == "system_action"
    assert "已清空" in result["answer"]
    # 会话记忆已被清空（last_dsl / history 一并移除）
    assert default_session_store().get(sid, "alice") is None
    # 系统操作不触达数仓引擎
    assert result.get("dsl") is None and result.get("sql") is None


def test_e2e_system_action_view_permissions(conn):
    """系统操作：权限查看返回主体可访问的表与字段（不触达数仓引擎）。"""
    result = run_query(
        "我的权限", conn=conn, session_id="r-sys-perm", user="alice", principal="analyst"
    )
    assert result["detected_intent"] == "system_action"
    assert "analyst" in result["answer"]
    assert "fact_orders" in result["answer"]
    assert result.get("sql") is None


def test_e2e_glossary_explain(conn):
    """验收：『客单价是怎么定义的』-> GLOSSARY_EXPLAIN（口径文档 RAG，不执行查询）。"""
    result = run_query("客单价是怎么定义的", conn=conn, session_id="r-gloss", user="alice")
    assert result["detected_intent"] == "glossary_explain"
    assert result["action"] == "rag"
    assert result["intent"] == "rag"  # 旧值向后兼容
    assert result["documents"]
    assert result["documents"][0]["key"] == "avg_order_amount"
    assert result.get("sql") is None  # 不执行数据库查询


def test_e2e_glossary_branch_holds_query_gate(conn, monkeypatch):
    """就绪度评审 R3：GLOSSARY_EXPLAIN 分支与 DATA_QUERY 共用同一并发闸——
    LLM 规划器把口径问题调度到数据工具时不得绕过 MAX_CONCURRENT_QUERIES。"""
    import web.service as svc

    class _CountingGate:
        def __init__(self):
            self.entered = 0

        def __enter__(self):
            self.entered += 1
            return self

        def __exit__(self, *exc_info):
            return False

    gate = _CountingGate()
    monkeypatch.setattr(svc, "_query_gate", gate)
    result = run_query("客单价是怎么定义的", conn=conn, session_id="r3-gate", user="alice")
    assert result["detected_intent"] == "glossary_explain"
    assert gate.entered == 1


def test_e2e_data_query_compiles(conn):
    """验收：『上个月广东的订单总数』-> DATA_QUERY，正常触发受控编译。"""
    result = run_query("上个月广东的订单总数", conn=conn, session_id="r-dq", user="alice")
    assert result["detected_intent"] == "data_query"
    assert result["action"] == "text2sql"
    assert "error" not in result, result.get("error_detail")
    assert result["dsl"] is not None
    assert result["sql"] and "SELECT" in result["sql"]
    assert result["routing_latency_ms"] >= 0


def test_e2e_clarify_vague_input(conn):
    """验收：『看下那个数据』-> CLARIFY，给出主动反问而非盲目生成 SQL。"""
    result = run_query("看下那个数据", conn=conn, session_id="r-vague", user="alice")
    assert result["detected_intent"] == "clarify"
    assert result["action"] == "clarify"
    assert result["intent"] == "text2sql"  # 旧值兼容：澄清发生在上游数据查询语境
    assert "error" in result
    assert result["clarifications"]
    assert result["clarifications"][0]["kind"] == "insufficient_info"
    assert "请补充" in result["clarifications"][0]["question"]
    assert result.get("sql") is None


def test_e2e_llm_planner_clarify_fills_slot(conn, monkeypatch):
    """就绪度评审 R4：LLM 规划器在 DATA_QUERY 分支输出的 clarify 与路由层
    CLARIFY 分支同权——写入槽位回填上下文，用户短语回答可合并回原问题。"""
    import web.service as svc
    from agent.slotfill import default_slot_store
    from agent.tool_agent import AgentResult

    clarifications = [
        {"kind": "missing_time_window", "term": None, "question": "请问要查询哪个时间范围？"}
    ]

    class _ClarifyAgent:
        """模拟 LLM 规划器把带指标的模糊问题判为需反问（clarify 早退路径）。"""

        def run(self, query, principal, **kwargs):
            return AgentResult(
                query=query,
                answer=clarifications[0]["question"],
                clarifications=clarifications,
                intent="clarify",
            )

    real_agent_factory = svc.default_tool_agent
    monkeypatch.setattr(svc, "default_tool_agent", lambda: _ClarifyAgent())
    sid = "r4-slot"
    first = run_query("上个月的GMV是多少", conn=conn, session_id=sid, user="alice")
    assert first["clarifications"] == clarifications
    # 槽位已按属主登记
    pending = default_slot_store().get(sid, "alice")
    assert pending is not None
    assert "missing_time_window" in pending.pending

    # 下一轮短语回答：恢复真实 Agent，回填合并回原问题执行
    monkeypatch.setattr(svc, "default_tool_agent", real_agent_factory)
    second = run_query("最近30天", conn=conn, session_id=sid, user="alice")
    assert second.get("clarify_filled") is True
    assert second["resolved_query"] == "上个月的GMV是多少 最近30天"
    assert "error" not in second, second.get("error_detail")


def test_e2e_context_isolation_through_chitchat(conn):
    """上下文隔离验收：华东上月GMV -> 天气(闲聊) -> 那华南呢？
    中间的闲聊绝不破坏第 3 轮对第 1 轮 GMV 指标的继承（记忆状态解耦）。"""
    sid = "r-ctx-isolate"
    first = run_query("上个月华东地区的GMV是多少？", conn=conn, session_id=sid, user="alice")
    assert "error" not in first, first.get("error_detail")

    chitchat = run_query("今天天气怎么样", conn=conn, session_id=sid, user="alice")
    assert chitchat["detected_intent"] == "chitchat"
    assert "error" in chitchat

    third = run_query("那华南呢？", conn=conn, session_id=sid, user="alice")
    assert "error" not in third, third.get("error_detail")
    assert third["context_summary"] and "华南" in third["context_summary"]
    # 继承第 1 轮的指标（GMV）与时间窗口，仅替换地区筛选
    prov = [f for f in third["dsl"]["filters"] if f["field"] == "province"]
    assert prov == [{"field": "province", "operator": "in", "value": ["广东"]}]
    assert [m["alias"] for m in third["dsl"]["metrics"]] == ["gmv"]
    assert third["dsl"]["time_filter"] == first["dsl"]["time_filter"]


def test_e2e_audit_records_routing_fields(conn, tmp_path):
    """审计追踪：每轮提问落盘 detected_intent / routing_latency / routing_reason。"""
    import json

    from audit.store import AuditStore

    store = AuditStore(jsonl_path=tmp_path / "audit-router.jsonl")
    run_query(
        "上个月广东的订单总数",
        conn=conn,
        session_id="r-audit",
        user="alice",
        audit_store=store,
    )
    run_query("帮我重置当前会话", conn=conn, session_id="r-audit", user="alice", audit_store=store)
    store.close()

    lines = [
        json.loads(line)
        for line in (tmp_path / "audit-router.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert lines[0]["detected_intent"] == "data_query"
    assert lines[0]["routing_latency_ms"] >= 0
    assert lines[0]["routing_reason"].startswith("rule:")
    assert lines[1]["detected_intent"] == "system_action"
    assert lines[1]["routing_reason"].startswith("system_action:")
