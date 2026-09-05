"""澄清多轮槽位回填（P0-5 / §4 项3）单元 + 端到端测试。"""

from __future__ import annotations

import time

from agent.slotfill import (
    SLOT_MISSING_TIME,
    SLOT_UNDEFINED_METRIC,
    ClarifyContext,
    ClarifySlotStore,
    attempt_fill,
)
from web.service import run_query


# --------------------------------------------------------------------------- #
# 纯函数：attempt_fill
# --------------------------------------------------------------------------- #
def test_fill_missing_time_recent_days():
    ctx = ClarifyContext(original_query="成功订单的GMV是多少？", pending=(SLOT_MISSING_TIME,))
    assert attempt_fill(ctx, "最近30天") == "成功订单的GMV是多少？ 最近30天"
    assert attempt_fill(ctx, "近7天") == "成功订单的GMV是多少？ 近7天"
    assert attempt_fill(ctx, "2024年6月") == "成功订单的GMV是多少？ 2024年6月"


def test_fill_missing_time_all_history():
    ctx = ClarifyContext(original_query="成功订单的GMV是多少？", pending=(SLOT_MISSING_TIME,))
    assert attempt_fill(ctx, "全部历史") is not None


def test_fill_rejects_unrelated_answer():
    ctx = ClarifyContext(original_query="成功订单的GMV是多少？", pending=(SLOT_MISSING_TIME,))
    assert attempt_fill(ctx, "请稍等") is None
    assert attempt_fill(ctx, "") is None


def test_fill_undefined_metric_with_defined_term():
    ctx = ClarifyContext(original_query="高活用户的GMV是多少？", pending=(SLOT_UNDEFINED_METRIC,))
    assert attempt_fill(ctx, "高活用户指活跃用户") is not None
    assert attempt_fill(ctx, "我不确定") is None


# --------------------------------------------------------------------------- #
# ClarifySlotStore：TTL 失效
# --------------------------------------------------------------------------- #
def test_slot_store_expiry():
    store = ClarifySlotStore(ttl_seconds=1)
    store.set("s1", ClarifyContext(original_query="GMV是多少？", pending=(SLOT_MISSING_TIME,)))
    assert store.get("s1") is not None
    time.sleep(1.1)
    assert store.get("s1") is None


def test_slot_store_clear():
    store = ClarifySlotStore(ttl_seconds=600)
    store.set("s1", ClarifyContext(original_query="GMV是多少？", pending=(SLOT_MISSING_TIME,)))
    store.clear("s1")
    assert store.get("s1") is None


# --------------------------------------------------------------------------- #
# 端到端：先反问，再用短语回答 -> 联合原问题执行
# --------------------------------------------------------------------------- #
def test_run_query_clarify_then_fill_time(conn):
    sid = "slot-e2e-time"
    first = run_query("成功订单的GMV是多少？", conn=conn, session_id=sid)
    assert first["action"] == "clarify"
    assert first["clarifications"][0]["kind"] == "missing_time_window"
    assert "error" in first

    second = run_query("最近30天", conn=conn, session_id=sid)
    assert "error" not in second, second.get("error_detail")
    assert second["action"] == "text2sql"
    assert second["clarify_filled"] is True
    assert second["filled_from"] == "成功订单的GMV是多少？"
    assert second["resolved_query"] == "成功订单的GMV是多少？ 最近30天"
    assert second["columns"] == ["gmv"]
    # 合并后的 DSL 携带相对时间窗口（trailing 30 天）
    tf = second["dsl"]["time_filter"]
    assert tf["range_type"] == "relative"
    assert tf["relative"] == {"amount": 30, "unit": "day", "mode": "trailing"}


def test_run_query_fill_persists_audit_context(conn, tmp_path):
    from audit.store import AuditStore

    sid = "slot-e2e-audit"
    store = AuditStore(jsonl_path=tmp_path / "audit.jsonl")
    run_query("成功订单的GMV是多少？", conn=conn, session_id=sid, audit_store=store)
    result = run_query("最近30天", conn=conn, session_id=sid, audit_store=store)
    assert "error" not in result

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    last = __import__("json").loads(lines[-1])
    assert last["retrieval_context"]["clarify_filled"] is True
    assert last["retrieval_context"]["filled_from"] == "成功订单的GMV是多少？"


def test_run_query_slot_is_consumed_after_fill(conn):
    """回填后上下文被消费：第三次不带时间的新问题重新反问，不再复用旧槽位。"""
    sid = "slot-e2e-consumed"
    run_query("成功订单的GMV是多少？", conn=conn, session_id=sid)
    run_query("最近30天", conn=conn, session_id=sid)

    third = run_query("成功订单的GMV是多少？", conn=conn, session_id=sid)
    assert third["action"] == "clarify"
    assert "clarify_filled" not in third


# --------------------------------------------------------------------------- #
# ClarifySlotStore 持久化（整改指令3-2）：落盘 SQLite 跨实例一致
# --------------------------------------------------------------------------- #
def test_slot_store_sqlite_persists_across_instances(tmp_path):
    """新实例（模拟重启/多 worker）从同一 SQLite 恢复澄清槽位。"""
    db = str(tmp_path / "slots.db")
    store1 = ClarifySlotStore(ttl_seconds=600, db_path=db)
    ctx = ClarifyContext(original_query="GMV是多少？", pending=(SLOT_MISSING_TIME,))
    store1.set("slot-persist", ctx)

    # 新实例从持久层恢复（内存为空时自动尝试 _load）
    store2 = ClarifySlotStore(ttl_seconds=600, db_path=db)
    restored = store2.get("slot-persist")
    assert restored is not None
    assert restored.original_query == "GMV是多少？"
    assert SLOT_MISSING_TIME in restored.pending


def test_slot_store_sqlite_clear_removes_persisted(tmp_path):
    db = str(tmp_path / "slots2.db")
    store1 = ClarifySlotStore(ttl_seconds=600, db_path=db)
    store1.set("slot-persist2", ClarifyContext(original_query="x", pending=(SLOT_MISSING_TIME,)))
    store1.clear("slot-persist2")
    store2 = ClarifySlotStore(ttl_seconds=600, db_path=db)
    assert store2.get("slot-persist2") is None


def test_slot_store_sqlite_ttl_expiry_persists_delete(tmp_path):
    """TTL 过期后持久层记录被惰性删除。"""
    db = str(tmp_path / "slots3.db")
    store1 = ClarifySlotStore(ttl_seconds=1, db_path=db)
    store1.set("slot-exp", ClarifyContext(original_query="x", pending=(SLOT_MISSING_TIME,)))
    time.sleep(1.1)
    # 过期后 get 返回 None 且清理持久层
    assert store1.get("slot-exp") is None
    store2 = ClarifySlotStore(ttl_seconds=1, db_path=db)
    assert store2.get("slot-exp") is None
