"""流式编排 run 注册表（并行会话核心）：SSE 连接与编排执行解耦。

设计契约（并行会话铁律）：
- 编排在后台线程执行，事件持续写入 run 级有界缓冲（append-only，seq 从 1
  单调递增）；SSE 连接只是"游标订阅者"——客户端断开仅退订，run 继续跑完；
- 切回会话 / 刷新页面按 ``?run_id=<id>&after=<seq>`` 重放并实时续传，
  无需重跑编排；
- HITL 暂停态挂在 run 上（resume 后同一 run 续写、seq 连续），取代旧的
  全局 ``_AGENT_PAUSED_STATES`` dict（修复 pop-before-owner-check 越权探测
  缺陷：取 -> 校验属主 -> 清，三步不越权）；
- 多轮上下文：同一会话（session_key = ``webui:<thread>``）的每轮 run 启动前
  从 ``agent/memory`` 装载历史摘要注入 Planner（history_digest），终态回写
  user/assistant 消息；owner 强绑定，跨用户不继承。

并发纪律：``run.cond`` 只保护状态与缓冲读写，绝不在持锁期间写网络帧
（阻塞 IO 会卡死编排线程）；订阅循环"锁内取增量 -> 锁外写帧 -> 空闲心跳"。

与 web/tasks.py 的边界：TaskManager 是"单一最终 result 快照"模型
（/api/query/async 轮询契约），与本模块"事件流 + 游标"正交，互不扩展。
"""

from __future__ import annotations

import re
import threading
import time
import uuid as uuid_mod
from collections.abc import Callable
from typing import Any

from audit.logging import get_logger
from config import settings
from core.orchestrator.agent import run_agent
from core.orchestrator.state import AgentState

logger = get_logger("web.runs")

# run 终态（paused = HITL 挂起等待人工答复；订阅者在缓冲事件全部下发后收尾）
_TERMINAL = ("done", "failed")
_ANSWER_MEMORY_CHARS = 2000  # 回写会话记忆的答案截断长度


class AgentRun:
    """一次编排的执行容器：状态 + 事件缓冲 + 订阅唤醒（单锁守护全部可变字段）。"""

    def __init__(self, run_id: str, owner: str, session_key: str) -> None:
        self.run_id = run_id
        self.owner = owner
        self.session_key = session_key  # 会话记忆键（webui:<thread>），跨用户不共享
        # 编排侧 session_id：session_key 中的冒号等字符在 Windows 工作区路径
        # 里非法（logs/workspaces/<session_id>/<turn>），净化为安全字符
        self.orch_session_id = re.sub(r"[^A-Za-z0-9._-]", "_", session_key)
        self.status = "running"  # running | paused | done | failed
        self.resume_token: str | None = None  # HITL 恢复句柄（paused 时非空）
        self.paused_state: dict[str, Any] | None = None  # 序列化 AgentState
        self.created_at = time.time()
        self.finished_at: float | None = None
        self.question = ""
        self.error: str | None = None
        # resume 基线：恢复时领取时刻的缓冲末尾 seq（订阅者从此续传，旧事件不重发）
        self.resume_baseline = 0
        # 事件缓冲：[(seq, event_dict)]；seq 单调递增从 1 开始
        self._events: list[tuple[int, dict[str, Any]]] = []
        self._next_seq = 1
        self._cond = threading.Condition()

    # ------------------------------------------------------------------ #
    # 事件缓冲（锁纪律：append 内部持锁，调用方不得在持锁时再写网络）
    # ------------------------------------------------------------------ #
    def append(self, event: dict[str, Any]) -> None:
        """追加事件并唤醒订阅者；缓冲超限丢最旧（重放方按空洞检测降级）。"""
        with self._cond:
            self._append_locked(event)

    def _append_locked(self, event: dict[str, Any]) -> None:
        # 每帧附带单调 seq 游标（前端断线重连按 after=<seq> 增量续传）
        event["seq"] = self._next_seq
        self._events.append((self._next_seq, event))
        self._next_seq += 1
        overflow = len(self._events) - settings.AGENT_RUN_MAX_EVENTS
        if overflow > 0:
            del self._events[:overflow]
        self._cond.notify_all()

    def close(self, status: str, error: str | None = None) -> None:
        """置终态并唤醒全部等待者（done/error 事件已先行入缓冲）。"""
        with self._cond:
            self.status = status
            self.error = error
            self.finished_at = time.time()
            self._cond.notify_all()

    def set_paused(self, state: AgentState, token: str) -> None:
        """HITL 暂停：状态置位与 hitl_request 事件同锁原子写入——订阅者
        不会观察到「已 paused 但事件未入缓冲」的中间态（丢澄清卡竞态防护）。"""
        with self._cond:
            self.resume_token = token
            self.paused_state = state.model_dump(mode="json")
            self.status = "paused"
            self._append_locked(
                {
                    "turn_id": state.turn_id,
                    "event": "hitl_request",
                    "timestamp": int(time.time() * 1000),
                    "payload": {
                        "hitl": {
                            "question": state.clarification or "请补充分析需求",
                            "resume_token": token,
                        }
                    },
                }
            )

    def take_resume_state(self, human_reply: str) -> tuple[AgentState, int] | None:
        """原子领取恢复态（仅 paused 可领）：清 token/state 并回到 running。

        返回 (恢复状态, 恢复基线 seq)——基线 = 领取时刻缓冲末尾，resume 续写
        的事件 seq 均大于它；订阅者默认从基线之后取增量（旧 hitl_request
        不重复下发）。
        """
        with self._cond:
            if self.status != "paused" or not self.resume_token or self.paused_state is None:
                return None
            state = AgentState.model_validate(self.paused_state).model_copy(
                update={"human_reply": human_reply}
            )
            baseline = self._next_seq - 1
            self.resume_token = None
            self.paused_state = None
            self.status = "running"
            return state, baseline

    def last_event_kind(self) -> str | None:
        with self._cond:
            return self._events[-1][1].get("event") if self._events else None

    def snapshot(self) -> dict[str, Any]:
        with self._cond:
            return {
                "run_id": self.run_id,
                "status": self.status,
                "question": self.question,
                "error": self.error,
                "next_seq": self._next_seq,
                "resume_token": self.resume_token,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
            }

    def _read_after_locked(self, cursor: int) -> tuple[list[tuple[int, dict]], int]:
        """持锁读取 cursor 之后的事件，返回 (增量, 新游标)。"""
        pending = [(s, e) for s, e in self._events if s > cursor]
        return pending, (pending[-1][0] if pending else cursor)

    def _cursor_hole_locked(self, cursor: int) -> bool:
        """空洞检测：请求游标早于缓冲最旧 seq-1 = 中段已被溢出丢弃。"""
        return bool(self._events) and cursor < self._events[0][0] - 1


class RunRegistry:
    """进程内 run 注册表：start / subscribe / resume / status + 惰性清理。"""

    def __init__(
        self,
        max_runs: int | None = None,
        ttl_seconds: float | None = None,
        idle_timeout_seconds: float | None = None,
    ) -> None:
        self._max_runs = max_runs or settings.AGENT_RUN_MAX_RUNS
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.AGENT_RUN_TTL_SECONDS
        self._idle_timeout = (
            idle_timeout_seconds
            if idle_timeout_seconds is not None
            else settings.AGENT_RUN_IDLE_TIMEOUT_SECONDS
        )
        self._runs: dict[str, AgentRun] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 启动
    # ------------------------------------------------------------------ #
    def start(
        self,
        query: str,
        *,
        owner: str,
        session_key: str,
        history_digest: str | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> AgentRun:
        """启动一次后台编排并立即返回 run（订阅者随后按游标接入）。

        history_digest 未显式传入时自动从会话记忆装载（多轮上下文）。
        """
        self.cleanup()
        if history_digest is None:
            history_digest = history_digest_for(session_key, owner)
        run = AgentRun(f"run-{uuid_mod.uuid4().hex[:16]}", owner, session_key)
        run.question = query
        with self._lock:
            self._runs[run.run_id] = run
            self._evict_overflow_locked()
        threading.Thread(
            target=self._execute,
            args=(run, query, history_digest, provider_id, model_id),
            daemon=True,
            name=f"agent-run-{run.run_id}",
        ).start()
        return run

    @staticmethod
    def _observer(run: AgentRun) -> Callable[[dict[str, Any]], None]:
        """事件观察者：丢弃编排内置的无 token hitl_request（由注册表补 token 版）。"""

        def _on_event(event: dict[str, Any]) -> None:
            if event.get("event") == "hitl_request":
                return
            run.append(event)

        return _on_event

    def _execute(
        self,
        run: AgentRun,
        query: str,
        history_digest: str | None,
        provider_id: str | None,
        model_id: str | None,
    ) -> None:
        """编排执行体：事件入缓冲，HITL 暂停登记恢复态，终态回写会话记忆。"""
        from providers.context import pop_request_model, set_request_model

        pop_request_model()
        if provider_id or model_id:
            set_request_model(provider_id or "", model_id or "")
        try:
            result = run_agent(
                query,
                session_id=run.orch_session_id,
                history_digest=history_digest,
                on_event=self._observer(run),
            )
            self._finish_result(run, query, result)
        except Exception as exc:  # 编排异常收敛为 error 事件（不崩进程）
            self._finish_error(run, exc)
        finally:
            pop_request_model()

    def _resume_execute(self, run: AgentRun, resume_state: AgentState) -> None:
        """从暂停点续跑：事件继续入原缓冲（seq 连续），终态闭环同 _execute。"""
        try:
            result = run_agent(
                run.question,
                session_id=run.orch_session_id,
                resume_state=resume_state,
                on_event=self._observer(run),
            )
            self._finish_result(run, run.question, result)
        except Exception as exc:
            self._finish_error(run, exc)

    def _finish_result(self, run: AgentRun, query: str, result: Any) -> None:
        """编排返回统一收尾：AgentState=再次澄清 / AgentTrace=完成回写记忆。"""
        if isinstance(result, AgentState):
            token = f"hitl-{uuid_mod.uuid4().hex[:16]}"
            run.set_paused(result, token)
            self._remember(run, query, f"（等待用户澄清：{result.clarification or ''}）")
        else:
            run.close("done")
            self._remember(run, query, result.report)

    def _finish_error(self, run: AgentRun, exc: Exception) -> None:
        """异常收尾：run_agent 已发 error 事件则不重复补发（双 error 防护）。"""
        message = f"{type(exc).__name__}: {exc}"
        if run.last_event_kind() != "error":
            run.append(
                {
                    "turn_id": "",
                    "event": "error",
                    "timestamp": int(time.time() * 1000),
                    "payload": {"error": message},
                }
            )
        run.close("failed", error=message)

    def _remember(self, run: AgentRun, query: str, answer: str) -> None:
        """会话记忆回写（多轮上下文闭环）；失败只告警，不影响 run 终态。"""
        try:
            from agent.memory import SessionState, append_message, default_session_store

            store = default_session_store()
            state = store.get(run.session_key, run.owner)
            if state is None:
                state = SessionState(session_id=run.orch_session_id, user_id=run.owner)
            append_message(state, "user", query)
            append_message(state, "assistant", (answer or "")[:_ANSWER_MEMORY_CHARS])
            store.update(run.session_key, run.owner, state)
        except Exception as exc:
            logger.warning(
                "run_memory_write_failed",
                extra={"event": "run_memory_write_failed", "error": str(exc)},
            )

    # ------------------------------------------------------------------ #
    # 订阅（SSE 游标消费者）
    # ------------------------------------------------------------------ #
    def subscribe(
        self,
        run_id: str,
        cursor: int,
        write_frame: Callable[[dict[str, Any]], None],
        *,
        owner: str,
        on_idle: Callable[[], None] | None = None,
    ) -> None:
        """重放 cursor 之后的事件并实时跟随至终态。

        - 客户端断开：write_frame / on_idle 抛 OSError，向上传播（调用方收尾）；
          run 不受影响，继续执行与缓冲；
        - 空洞检测：缓冲溢出丢弃了游标之后的事件时，诚实下发 error 事件收尾，
          严禁静默跳过（用户会误以为事件全部到达）；
        - 空闲心跳：每 AGENT_RUN_KEEPALIVE_SECONDS 无事件调用 on_idle（传输层
          写 SSE 注释帧），防代理层闲置断连；连续空闲超 AGENT_RUN_IDLE_TIMEOUT_SECONDS
          下发超时 error（该守卫兜底"连接活着但编排卡死"；阈值不可低于单次
          LLM 合法耗时上界，否则慢调用会被误判）。
        """
        run = self.get(run_id, owner=owner)
        if run is None:
            write_frame(_error_event("run 不存在或已过期"))
            return
        seq = cursor
        with run._cond:
            if run._cursor_hole_locked(cursor):
                # 空洞帧借用 run 的下一个 seq（前端按 seq 连续性即可察觉异常收尾）
                seq = run._next_seq
                run._next_seq += 1
                hole_event = _error_event("事件缓冲已溢出，游标段落缺失，请重新发起提问", seq)
            else:
                hole_event = None
        if hole_event is not None:
            write_frame(hole_event)
            return
        idle_limit = self._idle_timeout
        last_activity = time.monotonic()
        while True:
            timed_out = False
            with run._cond:
                pending, seq = run._read_after_locked(seq)
                if not pending:
                    if run.status in _TERMINAL or run.status == "paused":
                        return  # 缓冲已全量下发：done/failed 收尾或 paused 待 resume
                    timed_out = not run._cond.wait(timeout=settings.AGENT_RUN_KEEPALIVE_SECONDS)
            for _, event in pending:
                write_frame(event)
                last_activity = time.monotonic()
            if timed_out:
                if time.monotonic() - last_activity > idle_limit:
                    write_frame(_error_event(f"编排超时（{int(idle_limit)}s 无事件），连接已终止"))
                    return
                if on_idle is not None:
                    on_idle()

    # ------------------------------------------------------------------ #
    # HITL 恢复 / 状态查询
    # ------------------------------------------------------------------ #
    def resume(self, run_id: str, human_reply: str, *, owner: str) -> AgentRun | None:
        """恢复 paused run：属主校验 fail-closed，resume 后同一 run 续写缓冲。"""
        run = self.get(run_id, owner=owner)
        return self._resume_run(run, human_reply, owner)

    def resume_by_token(self, token: str, human_reply: str, *, owner: str) -> AgentRun | None:
        """按 resume_token 恢复（兼容不带 run_id 的旧客户端）：token 全局唯一，
        先定位 run 再走属主校验 + 原子领取，属主不符一律 None（fail-closed）。"""
        with self._lock:
            run = next((r for r in self._runs.values() if r.resume_token == token), None)
        if run is None or run.owner != owner:
            return None
        return self._resume_run(run, human_reply, owner)

    def _resume_run(
        self, run: AgentRun | None, human_reply: str, owner: str | None
    ) -> AgentRun | None:
        """原子领取恢复态并启动续跑线程；run.resume_baseline 供订阅续传。"""
        if run is None:
            return None
        taken = run.take_resume_state(human_reply)
        if taken is None:
            return None
        state, baseline = taken
        run.resume_baseline = baseline
        threading.Thread(
            target=self._resume_execute,
            args=(run, state),
            daemon=True,
            name=f"agent-run-resume-{run.run_id}",
        ).start()
        return run

    def status(self, run_id: str, *, owner: str | None) -> dict[str, Any] | None:
        """run 状态快照（轮询端点用）；属主不符 fail-closed 返回 None（404）。"""
        run = self.get(run_id, owner=owner)
        if run is None:
            return None
        return run.snapshot()

    def get(self, run_id: str, *, owner: str | None) -> AgentRun | None:
        """按属主取 run；owner=None（admin）全局可见；不符返回 None（不泄露存在性）。"""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or (owner is not None and run.owner != owner):
                return None
            return run

    def find_active(self, owner: str, session_key: str, question: str) -> AgentRun | None:
        """查找同属主 + 同会话 + 同提问且仍在执行（running/paused）的 run。

        幂等保护：客户端断线重连 / 网络重发时复用既有 run，杜绝同一提问被
        重复执行（重复执行会产出多份同类产物、互相覆盖）。
        """
        with self._lock:
            for run in self._runs.values():
                if (
                    run.owner == owner
                    and run.session_key == session_key
                    and run.question == question
                    and run.status in ("running", "paused")
                ):
                    return run
        return None

    # ------------------------------------------------------------------ #
    # 惰性清理
    # ------------------------------------------------------------------ #
    def cleanup(self) -> None:
        """TTL 过期（终态 / 长期挂起）与超容量的终态 run 淘汰。"""
        with self._lock:
            now = time.time()
            expired = [
                rid
                for rid, r in self._runs.items()
                if (
                    r.status in _TERMINAL
                    and r.finished_at is not None
                    and now - r.finished_at > self._ttl
                )
                or (r.status == "paused" and now - r.created_at > self._ttl)
            ]
            for rid in expired:
                self._runs.pop(rid, None)
            self._evict_overflow_locked()

    def _evict_overflow_locked(self) -> None:
        """超历史上限：从最旧开始丢弃终态 run（须持 _lock；运行中不淘汰）。"""
        overflow = len(self._runs) - self._max_runs
        if overflow <= 0:
            return
        for rid, r in sorted(self._runs.items(), key=lambda kv: kv[1].created_at):
            if overflow <= 0:
                break
            if r.status in _TERMINAL:
                self._runs.pop(rid, None)
                overflow -= 1

    def clear_all(self) -> None:
        """清空全部 run（测试隔离用）。"""
        with self._lock:
            self._runs.clear()


def _error_event(message: str, seq: int | None = None) -> dict[str, Any]:
    """构造 error 事件帧（与 AgentStreamEvent 契约一致；seq 由注册表分配）。"""
    frame: dict[str, Any] = {
        "turn_id": "",
        "event": "error",
        "timestamp": int(time.time() * 1000),
        "payload": {"error": message},
    }
    if seq is not None:
        frame["seq"] = seq
    return frame


def history_digest_for(session_key: str, owner: str) -> str | None:
    """装载同会话最近几轮对话摘要（注入 Planner 的 history_context）。

    与 agent/router 意图路由的历史口径对齐（最近 3 轮、单条截断），
    无历史 / 跨用户 / 过期一律 None（单轮契约，提示词逐字不变）。
    """
    try:
        from agent.memory import default_session_store

        state = default_session_store().get(session_key, owner)
    except Exception as exc:
        logger.warning(
            "run_history_load_failed",
            extra={"event": "run_history_load_failed", "error": str(exc)},
        )
        return None
    if state is None or not state.history:
        return None
    lines: list[str] = []
    for msg in state.history[-6:]:  # 最近 3 轮（每轮 user + assistant）
        role = "用户" if msg.role == "user" else "助手"
        content = (msg.content or "").strip()
        if content:
            lines.append(f"{role}: {content[:500]}")
    return "\n".join(lines) or None


_default_registry: RunRegistry | None = None
_default_lock = threading.Lock()


def default_run_registry() -> RunRegistry:
    """进程内默认注册表（双检锁懒加载单例）。"""
    global _default_registry
    with _default_lock:
        if _default_registry is None:
            _default_registry = RunRegistry()
        return _default_registry
