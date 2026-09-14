"""web.runs Run 注册表单测：事件缓冲 / 游标重放 / 属主隔离 / HITL 恢复。

直调 RunRegistry 与 AgentRun 原语（不走 HTTP，风格对齐 test_web.py 直调
私有函数的先例）；编排执行用 run_agent 真实离线链路（conftest 离线铁律：
无 LLM Key 时走确定性启发式兜底，行为可断言）。
"""

from __future__ import annotations

import threading
import time

import pytest

from web.runs import AgentRun, RunRegistry


def _wait_terminal(run: AgentRun, timeout: float = 30.0) -> str:
    """轮询等待 run 终态（done/failed），返回最终状态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with run._cond:
            if run.status in ("done", "failed"):
                return run.status
        time.sleep(0.05)
    raise AssertionError(f"run 未在 {timeout}s 内到达终态（status={run.status}）")


@pytest.fixture()
def registry():
    reg = RunRegistry(max_runs=10, ttl_seconds=3600)
    yield reg
    reg.clear_all()


def test_run_events_seq_monotonic(registry):
    """事件 seq 从 1 单调递增且随帧下发（前端游标续传契约）。"""
    run = registry.start("2024年5月北京的GMV是多少", owner="alice", session_key="webui:t1")
    _wait_terminal(run)
    seqs = [s for s, _ in run._events]
    assert seqs and seqs[0] == 1
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    # 每帧自带同值 seq 字段
    assert all(ev["seq"] == s for s, ev in run._events)
    assert run._events[-1][1]["event"] == "done"


def test_subscribe_replays_from_cursor(registry):
    """游标订阅：after=0 全量重放无缺无重；after=中段只取增量。"""
    run = registry.start("2024年5月北京的GMV是多少", owner="alice", session_key="webui:t1")
    _wait_terminal(run)

    full: list[dict] = []
    registry.subscribe(run.run_id, 0, full.append, owner="alice")
    assert [e["event"] for e in full][-1] == "done"
    assert len(full) == len(run._events)

    mid = len(full) // 2
    tail: list[dict] = []
    registry.subscribe(run.run_id, full[mid - 1]["seq"], tail.append, owner="alice")
    assert [e["seq"] for e in tail] == [e["seq"] for e in full[mid:]]


def test_subscribe_unknown_run_or_owner_emits_error(registry):
    """未知 run_id / 属主不符 -> error 事件（不泄露存在性）。"""
    got: list[dict] = []
    registry.subscribe("run-deadbeef", 0, got.append, owner="alice")
    assert got[-1]["event"] == "error"

    run = registry.start("2024年5月北京的GMV是多少", owner="alice", session_key="webui:t1")
    _wait_terminal(run)
    got2: list[dict] = []
    registry.subscribe(run.run_id, 0, got2.append, owner="mallory")
    assert got2[-1]["event"] == "error"
    assert "不存在" in got2[-1]["payload"]["error"]


def test_buffer_overflow_honest_degradation():
    """缓冲超限丢最旧 -> 游标落在空洞时诚实检测（严禁静默跳过）。"""
    from config import settings
    from web.runs import _error_event

    run = AgentRun("run-x", "alice", "webui:t1")
    for i in range(settings.AGENT_RUN_MAX_EVENTS + 50):
        run.append({"event": "reflection", "payload": {"i": i}})
    # 游标 0 已成空洞（最旧事件被溢出丢弃）
    with run._cond:
        assert run._cursor_hole_locked(0)
    # 非空洞游标不受影响
    with run._cond:
        assert not run._cursor_hole_locked(run._events[0][0])
    # 空洞 error 帧带 seq（前端游标推进契约）
    frame = _error_event("缓冲溢出", seq=9999)
    assert frame["event"] == "error" and frame["seq"] == 9999


def test_status_owner_fail_closed(registry):
    """状态快照属主校验：本人可见、他人 None、admin 全局可见。"""
    run = registry.start("2024年5月北京的GMV是多少", owner="alice", session_key="webui:t1")
    _wait_terminal(run)
    assert registry.status(run.run_id, owner="alice") is not None
    assert registry.status(run.run_id, owner="bob") is None
    assert registry.status(run.run_id, owner=None) is not None  # admin


def test_hitl_pause_resume_seq_continuous(registry):
    """HITL：歧义短问暂停 -> hitl_request 入缓冲 -> resume 同 run 续写，seq 连续。"""
    run = registry.start("GMV呢？", owner="alice", session_key="webui:t1")
    # 歧义问题离线走确定性澄清（clarify 规则：过短无指标细节 -> 暂停）
    deadline = time.time() + 30
    while time.time() < deadline:
        with run._cond:
            if run.status in ("paused", "done", "failed"):
                break
        time.sleep(0.05)
    with run._cond:
        assert run.status == "paused", f"预期 HITL 暂停，实际 {run.status}"
        token = run.resume_token
    assert token

    resumed = registry.resume(run.run_id, "2024年5月按省份的订单金额", owner="alice")
    assert resumed is run
    final = _wait_terminal(run)
    assert final == "done"

    with run._cond:
        seqs = [s for s, _ in run._events]
        kinds = [e["event"] for _, e in run._events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert kinds.count("hitl_request") == 1  # 恢复后旧澄清卡不重发
    assert kinds[-1] == "done"

    # resume 后旧 token 失效（一次性）
    assert registry.resume_by_token(token, "再次答复", owner="alice") is None


def test_hitl_resume_wrong_owner_fail_closed(registry):
    """resume 属主校验：他人携带有效 token 一律拒绝（fail-closed）。"""
    run = registry.start("GMV呢？", owner="alice", session_key="webui:t1")
    deadline = time.time() + 30
    while time.time() < deadline:
        with run._cond:
            if run.status in ("paused", "done", "failed"):
                break
        time.sleep(0.05)
    with run._cond:
        token = run.resume_token
    assert registry.resume_by_token(token, "恶意答复", owner="mallory") is None
    # 原 run 仍处于 paused（未被越权消费）
    with run._cond:
        assert run.status == "paused"
        assert run.resume_token == token


def test_ttl_cleanup(registry):
    """TTL 过期的终态 run 被惰性清理；运行中不受影响。"""
    run = registry.start("2024年5月北京的GMV是多少", owner="alice", session_key="webui:t1")
    _wait_terminal(run)
    with run._cond:
        run.finished_at = time.time() - 7200  # 人为过期
    registry.cleanup()
    assert registry.status(run.run_id, owner="alice") is None


def test_registry_overflow_evicts_oldest_terminal():
    """超容量：最旧终态 run 先淘汰，运行中 run 不淘汰。"""
    reg = RunRegistry(max_runs=2, ttl_seconds=3600)
    try:
        r1 = reg.start("2024年5月北京的GMV是多少", owner="alice", session_key="webui:t1")
        _wait_terminal(r1)
        r2 = reg.start("2024年6月上海的GMV是多少", owner="alice", session_key="webui:t2")
        _wait_terminal(r2)
        r3 = reg.start("2024年7月广州的GMV是多少", owner="alice", session_key="webui:t3")
        _wait_terminal(r3)
        assert reg.status(r1.run_id, owner="alice") is None  # 最旧被淘汰
        assert reg.status(r3.run_id, owner="alice") is not None
    finally:
        reg.clear_all()


def test_parallel_runs_isolated(registry):
    """并行会话隔离：两个 run 各自执行、事件互不串台。"""
    r1 = registry.start("2024年5月北京的GMV是多少", owner="alice", session_key="webui:t1")
    r2 = registry.start("2024年5月各省份的订单量是多少", owner="alice", session_key="webui:t2")
    assert _wait_terminal(r1) == "done"
    assert _wait_terminal(r2) == "done"
    with r1._cond:
        q1 = r1.question
    with r2._cond:
        q2 = r2.question
    assert q1 != q2
    # 双订阅各自拿到各自的完整流
    got1: list[dict] = []
    got2: list[dict] = []
    registry.subscribe(r1.run_id, 0, got1.append, owner="alice")
    registry.subscribe(r2.run_id, 0, got2.append, owner="alice")
    assert got1[-1]["event"] == "done" and got2[-1]["event"] == "done"
    assert got1 != got2


def test_memory_writeback_and_history_inheritance(registry):
    """多轮上下文闭环：首轮回写会话记忆，第二轮自动装载 history_digest。"""
    from agent.memory import default_session_store

    r1 = registry.start("2024年5月北京的GMV是多少", owner="alice", session_key="webui:t-mem")
    assert _wait_terminal(r1) == "done"
    state = default_session_store().get("webui:t-mem", "alice")
    assert state is not None
    roles = [m.role for m in state.history]
    assert "user" in roles and "assistant" in roles

    # 第二轮：start 自动装载历史摘要（无 LLM 时至少不炸，digest 透传进 state）
    r2 = registry.start("那上海呢", owner="alice", session_key="webui:t-mem")
    assert _wait_terminal(r2) in ("done", "failed")
    # 跨用户同 session_key：不继承（owner 强绑定）——bob 无历史，同句短问走
    # 澄清门 paused（alice 因有历史直接放行），隔离生效的实锤
    r3 = registry.start("那华南呢", owner="bob", session_key="webui:t-mem")
    deadline = time.time() + 30
    while time.time() < deadline:
        with r3._cond:
            if r3.status in ("paused", "done", "failed"):
                break
        time.sleep(0.05)
    with r3._cond:
        assert r3.status == "paused"
    bob_state = default_session_store().get("webui:t-mem", "bob")
    assert bob_state is None or all(m.role == "user" for m in bob_state.history)


def test_find_active_idempotent_reuse(registry):
    """幂等保护：同属主+同会话+同提问的执行中 run 被复用，杜绝重复执行。"""
    r1 = registry.start("2024年5月北京的GMV是多少", owner="alice", session_key="webui:t1")
    found = registry.find_active("alice", "webui:t1", "2024年5月北京的GMV是多少")
    assert found is r1  # 复用同一 run，不新建
    # 不同提问 / 不同会话 / 不同属主都不复用
    assert registry.find_active("alice", "webui:t1", "别的问题") is None
    assert registry.find_active("alice", "webui:t2", "2024年5月北京的GMV是多少") is None
    assert registry.find_active("bob", "webui:t1", "2024年5月北京的GMV是多少") is None
    # 终态后不再复用（允许同问重问）
    _wait_terminal(r1)
    assert registry.find_active("alice", "webui:t1", "2024年5月北京的GMV是多少") is None


def test_subscribe_idle_timeout_configurable():
    """空闲守卫阈值可配置（RunRegistry 注入），文案随阈值渲染。

    连接活着但编排长时间无事件时，订阅应下发超时 error 并终止——
    阈值经构造参数注入，避免测试等 300s。
    """
    reg = RunRegistry(max_runs=5, ttl_seconds=3600, idle_timeout_seconds=0.05)
    try:
        # 手工构造 run：保持 running 且不追加任何事件（模拟编排卡死）
        run = AgentRun("run-idle", "alice", "webui:t1")
        with reg._lock:
            reg._runs[run.run_id] = run
        frames: list[dict] = []
        reg.subscribe(run.run_id, 0, frames.append, owner="alice")
        assert frames, "空闲超时应下发事件"
        assert frames[-1]["event"] == "error"
        assert "编排超时" in frames[-1]["payload"]["error"]
        # 文案中的秒数由配置渲染（0.05s -> int() = 0）
        assert "无事件" in frames[-1]["payload"]["error"]
    finally:
        reg.clear_all()


def test_subscribe_heartbeat_keeps_alive():
    """空闲未达阈值时只发心跳（on_idle），不下发终止 error。"""
    reg = RunRegistry(max_runs=5, ttl_seconds=3600, idle_timeout_seconds=30.0)
    try:
        run = AgentRun("run-hb", "alice", "webui:t1")
        with reg._lock:
            reg._runs[run.run_id] = run
        frames: list[dict] = []
        pings: list[int] = []

        # 收到首次心跳后让 run 收尾，订阅循环随即正常返回
        def _on_idle():
            pings.append(1)
            run.close("done")

        reg.subscribe(run.run_id, 0, frames.append, owner="alice", on_idle=_on_idle)
        assert pings, "空闲未超阈值应发心跳"
        assert not [f for f in frames if f["event"] == "error"], "不应误报超时"
    finally:
        reg.clear_all()


def test_concurrent_append_thread_safe():
    """多线程并发 append：seq 严格单调无重复（订阅唤醒不丢）。"""
    run = AgentRun("run-c", "alice", "webui:t1")
    threads = [
        threading.Thread(
            target=lambda k=k: [
                run.append({"event": "reflection", "payload": {"i": k}}) for _ in range(100)
            ]
        )
        for k in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with run._cond:
        seqs = [s for s, _ in run._events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert seqs[0] == 1 and seqs[-1] == 800
