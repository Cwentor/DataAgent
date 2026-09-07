"""Web 服务核心：NL -> DSL -> SQL -> 结果 -> 解释 -> 图表 的完整链路。

纯标准库 + 既有业务模块，不引入任何 Web 框架，保持"零外部依赖"。
同时负责审计埋点（P0）：把每次问答的 request_id / session_id / user / prompt /
检索上下文 / DSL / 最终 SQL / 耗时 / 返回行数 / 错误 落盘，并输出结构化日志。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import duckdb

from agent.clarify import detect_clarifications
from agent.errors import PipelineError
from agent.memory import (
    SessionState,
    append_message,
    default_session_store,
    derive_active_entities,
    resolve_context,
)
from agent.pipeline import rewrite_dsl
from agent.router import ROUTING_LATENCY_MS, IntentType, route_decision
from agent.router.legacy import _CHITCHAT_REPLY
from agent.slotfill import ClarifyContext, attempt_fill, default_slot_store, pending_kinds
from agent.tool_agent import AgentResult, default_tool_agent
from audit.logging import get_logger, set_request_context
from audit.metrics import default_registry
from audit.record import AuditRecord
from audit.store import AuditStore
from compiler.sql_compiler import CompileError
from config import settings
from exec.guards import (
    MaxRowsScannedExceeded,
    QueryTimeoutError,
    ResultLimitExceeded,
    SqlExecutionError,
    UnsafeSqlError,
    execute_sql,
)
from exec.pool import default_pool
from security.errors import SecurityError
from semantic import catalog
from semantic.dsl_schema import QueryDSL, RatioMetric, WindowMetric

logger = get_logger("web.service")


# 新五分类意图 -> 旧 action 字符串（前端与既有测试向后兼容）
_ACTION_BY_INTENT: dict[IntentType, str] = {
    IntentType.CHITCHAT: "chitchat",
    IntentType.SYSTEM_ACTION: "system_action",
    IntentType.CLARIFY: "clarify",
    IntentType.GLOSSARY_EXPLAIN: "rag",
    IntentType.DATA_QUERY: "text2sql",
}


def _legacy_intent(decision) -> str:
    """把新五分类意图映射为向后兼容的旧 intent 字符串。

    CLARIFY 无独立旧意图：按其候选上游语境（data_query / glossary_explain）映射，
    与旧实现"澄清发生在 TEXT2SQL 语境"的行为保持一致。
    """
    if decision.intent == IntentType.CLARIFY:
        candidate = decision.extracted_entities.get("candidate")
        return candidate if candidate in ("text2sql", "rag") else "text2sql"
    return _ACTION_BY_INTENT[decision.intent]


def _handle_system_action(
    action: str | None,
    *,
    memory_store,
    session_id: str | None,
    owner: str,
    slot_store,
    principal: str | None,
) -> str:
    """执行白名单化的系统控制动作，返回面向用户的提示文案。

    安全约束：
    - 动作白名单固定（会话管理 / 权限查看 / 数据源状态探测 / 退出），未知动作
      一律安全提示，绝不执行任意系统指令；
    - 身份鉴权由调用方网关强制绑定（principal 取自服务端映射），此处仅按
      (session_id, owner) 归属操作会话状态，跨用户 clear 返回 False 不误删；
    - 本分支不触达 semantic/、compiler/ 与底层数据库引擎。
    """
    if action == "reset_session":
        if slot_store is not None:
            slot_store.clear(session_id)
        if memory_store is not None:
            memory_store.clear(session_id, owner)
        return "已清空当前会话的上下文记忆，可以开始新一轮对话。"
    if action == "view_permissions":
        from security.scope import scoped_fields, scoped_tables

        tables = sorted(scoped_tables(principal))
        fields = sorted(scoped_fields(principal))
        return (
            f"当前身份「{principal or '系统默认'}」可查询 {len(tables)} 张表、"
            f"{len(fields)} 个字段。可访问表：{'、'.join(tables)}；"
            f"可引用字段：{'、'.join(fields)}。"
        )
    if action == "source_status":
        db_exists = settings.DB_PATH.exists()
        return (
            f"数据源状态：本地 DuckDB 数仓文件{'已就绪' if db_exists else '缺失（需初始化）'}；"
            f"只读连接池容量 {settings.DB_POOL_SIZE}；并发查询上限 "
            f"{settings.MAX_CONCURRENT_QUERIES}；审计写入"
            f"{'开启' if settings.AUDIT_ENABLED else '关闭'}。"
        )
    if action == "exit":
        return "好的，再见！如需继续分析，随时回来。"
    return "系统操作已完成。"


def _friendly_error(exc: Exception) -> str:
    """将内部异常映射为面向业务用户的安全提示。"""
    if isinstance(exc, QueryTimeoutError):
        return "查询超时，请缩小时间范围后重试"
    if isinstance(exc, MaxRowsScannedExceeded):
        return "查询范围过大，已自动中止，请缩小时间范围后重试"
    if isinstance(exc, ResultLimitExceeded):
        return "查询结果过多，请缩小查询范围后重试"
    if isinstance(exc, SecurityError):
        return "您无权查看该数据，请联系管理员确认权限"
    if isinstance(exc, PipelineError):
        return "暂时无法理解该问题，请补充时间范围或使用已定义的指标"
    if isinstance(exc, UnsafeSqlError):
        return "查询未通过安全校验，请调整问题后重试"
    if isinstance(exc, CompileError):
        return "查询条件无法编译，请调整指标或过滤条件后重试"
    if isinstance(exc, SqlExecutionError):
        return "查询执行出错，请调整条件后重试"
    return "系统繁忙，请稍后重试"


# 工具失败按异常类型名映射为友好提示（与 _friendly_error 同源，供工具层错误使用）
_FRIENDLY_BY_ERROR_CLASS = {
    "QueryTimeoutError": "查询超时，请缩小时间范围后重试",
    "MaxRowsScannedExceeded": "查询范围过大，已自动中止，请缩小时间范围后重试",
    "ResultLimitExceeded": "查询结果过多，请缩小查询范围后重试",
    "SecurityError": "您无权查看该数据，请联系管理员确认权限",
    "PipelineError": "暂时无法理解该问题，请补充时间范围或使用已定义的指标",
    "UnsafeSqlError": "查询未通过安全校验，请调整问题后重试",
    "CompileError": "查询条件无法编译，请调整指标或过滤条件后重试",
    "SqlExecutionError": "查询执行出错，请调整条件后重试",
}

_CIRCUIT_BY_ERROR_CLASS = {
    "QueryTimeoutError": "query_timeout",
    "MaxRowsScannedExceeded": "scan_rows",
    "ResultLimitExceeded": "result_limit",
    "UnsafeSqlError": "unsafe_sql",
}


def _friendly_by_class(error_type: str | None) -> str | None:
    if error_type is None:
        return None
    return _FRIENDLY_BY_ERROR_CLASS.get(error_type)


_default_store: AuditStore | None = None
_store_lock = threading.Lock()

# P0-6 并发闸：全局信号量限制同时执行的查询数（超配额排队等待，配合连接池）
_query_gate = threading.BoundedSemaphore(settings.MAX_CONCURRENT_QUERIES)


def _default_audit_store() -> AuditStore | None:
    """进程内复用的默认审计存储（按配置启用，双写 JSONL + DuckDB）。"""
    global _default_store
    if not settings.AUDIT_ENABLED:
        return None
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                _default_store = AuditStore(
                    jsonl_path=settings.AUDIT_LOG_PATH,
                    db_path=settings.AUDIT_DB_PATH,
                )
    return _default_store


def _referenced_fields(dsl: QueryDSL) -> set[str]:
    """收集 DSL 引用的全部逻辑字段（指标 + 维度 + 过滤）。"""
    fields: set[str] = set()
    for m in dsl.metrics:
        if isinstance(m, RatioMetric):
            fields.add(m.numerator.field)
            fields.add(m.denominator.field)
        elif isinstance(m, WindowMetric):
            fields.add(m.base.field)
        else:
            fields.add(m.field)
    for d in dsl.dimensions:
        fields.add(d.field)
    for f in dsl.filters:
        fields.add(f.field)
    return fields


def _retrieval_context(dsl: QueryDSL) -> dict[str, Any]:
    """从 DSL 派生"检索上下文"：本次查询命中的语义目录字段与物理表。"""
    fields = _referenced_fields(dsl)
    tables = sorted({catalog.COLUMNS[f].table for f in fields if f in catalog.COLUMNS})
    return {"fields": sorted(fields), "tables": tables}


def run_query(
    query: str,
    principal: str | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    user: str | None = None,
    retrieval_context: dict[str, Any] | None = None,
    audit_store: AuditStore | None = None,
) -> dict[str, Any]:
    """执行完整链路，返回可直接交给前端渲染的字典；失败时写入 error 字段。

    conn 传入时复用该连接（测试用内存库），否则打开本地 DuckDB 文件只读。

    每次调用都会：
    1. 注入结构化日志上下文（request_id 贯穿），生成/复用 request_id；
    2. 落一条审计记录（JSONL 对象存储 + DuckDB 审计表），记录耗时/行数/错误等。
    """
    # 注入结构化日志上下文；request_id 由调用方传入或在此生成
    rid = set_request_context(request_id=request_id, session_id=session_id, user=user)
    logger.info("query_start", extra={"event": "query_start"})

    started = time.perf_counter()
    result: dict[str, Any] = {
        "query": query,
        "principal": principal,
        "request_id": rid,
        "session_id": session_id,
    }

    dsl: QueryDSL | None = None
    sql: str | None = None
    row_count: int | None = None
    scan_rows: int | None = None
    rewrites: int | None = None
    error: str | None = None
    circuit_breaker: str | None = None

    ctx_override: dict[str, Any] | None = None
    effective_query = query
    # 会话上下文记忆（Session Memory）：按 (session_id, user_id) 强绑定取状态；
    # 跨用户 / 过期一律返回 None（拒绝继承，Session Bleeding 防护）。
    memory_store = default_session_store() if session_id else None
    owner = user or principal or "anonymous"
    state = memory_store.get(session_id, owner) if memory_store is not None else None
    base_dsl: Any = None
    context_summary: str | None = None
    try:
        # P0-5 澄清槽位回填：命中上一轮挂起上下文且答案可填槽时，合并回原问题
        slot_store = default_slot_store() if session_id else None
        pending = slot_store.get(session_id, owner) if slot_store is not None else None
        if pending is not None and pending_kinds(pending):
            merged = attempt_fill(pending, query)
            if merged is not None:
                effective_query = merged
                result["clarify_filled"] = True
                result["resolved_query"] = effective_query
                result["filled_from"] = pending.original_query
                slot_store.clear(session_id)

        # 意图路由与决策中心：五分类判决（Fast-Path -> LLM -> 规则兜底），
        # 结合会话历史与上轮 DSL 综合研判上下文追问；随之分派到互不干扰的处理分支。
        decision = route_decision(
            effective_query,
            history=state.history if state is not None else None,
            last_dsl=state.last_dsl if state is not None else None,
            principal=principal,
        )
        detected = decision.intent
        # 响应契约：新五分类意图（detected_intent）+ 置信度 + 路由原因 + 路由耗时；
        # intent/action 保持旧值向后兼容（前端渲染与既有测试依赖）
        result["detected_intent"] = detected.value
        result["confidence"] = decision.confidence
        result["routing_reason"] = decision.reason
        result[ROUTING_LATENCY_MS] = decision.routing_latency_ms
        result["intent"] = _legacy_intent(decision)
        result["action"] = _ACTION_BY_INTENT[detected]
        result["message"] = decision.reason

        if detected == IntentType.CHITCHAT:
            if slot_store is not None:
                slot_store.clear(session_id, owner)
            # 记忆状态解耦：闲聊轮绝不污染 last_dsl / active_entities 等结构化数据
            # 查询状态，仅追加用户消息保留多轮对话语境（后续省略指代仍可继承上轮 DSL）
            if memory_store is not None and state is not None:
                append_message(state, "user", effective_query)
                memory_store.update(session_id, owner, state)
            error = _CHITCHAT_REPLY
            result["error"] = error
            result["steps"] = []
        elif detected == IntentType.CLARIFY:
            # 澄清反问：优先用路由判决预提取的澄清问题（缺失时间 / 未定义指标 / 信息不足）
            clarifications = decision.extracted_entities.get("clarifications") or []
            if not clarifications:
                clarifications = [c.to_dict() for c in detect_clarifications(effective_query)]
            pending = tuple(c["kind"] for c in clarifications if isinstance(c, dict))
            if slot_store is not None and pending:
                slot_store.set(
                    session_id,
                    ClarifyContext(original_query=effective_query, pending=pending),
                    owner,
                )
            # 澄清轮保留上轮有效 DSL（用户补口径后可能回到原查询语境）
            if memory_store is not None and state is not None:
                append_message(state, "user", effective_query)
                memory_store.update(session_id, owner, state)
            result["clarifications"] = clarifications
            result["steps"] = []
            error = (
                "；".join(c["question"] for c in clarifications if isinstance(c, dict))
                or decision.reason
            )
            result["error"] = error
        elif detected == IntentType.SYSTEM_ACTION:
            # 系统控制与状态操作（白名单动作，不触达数仓引擎）：
            # 会话管理 / 权限查看 / 数据源状态探测，身份由调用方网关强制绑定
            result["answer"] = _handle_system_action(
                decision.extracted_entities.get("action"),
                memory_store=memory_store,
                session_id=session_id,
                owner=owner,
                slot_store=slot_store,
                principal=principal,
            )
            result["steps"] = []
        elif detected == IntentType.GLOSSARY_EXPLAIN:
            # 口径文档检索：经 explain_glossary_tool 执行，记录调度轨迹（不触达 SQL 引擎）
            if slot_store is not None:
                slot_store.clear(session_id, owner)
            # R3 处置：与 DATA_QUERY 共用同一并发闸——LLM 规划器可能把口径问题
            # 调度到数据工具（此时工具层从统一连接池取连接执行），持闸避免绕过
            # MAX_CONCURRENT_QUERIES 并发上限；纯 RAG 检索持闸耗时忽略不计。
            with _query_gate:
                agent_result = default_tool_agent().run(effective_query, principal, request_id=rid)
            result["steps"] = [s.to_dict() for s in agent_result.steps]
            result["answer"] = agent_result.answer
            result["documents"] = agent_result.documents

            # P1-5: LLM clarify 路径需要回填槽位
            if agent_result.clarifications and slot_store is not None:
                slot_store.set(
                    session_id,
                    ClarifyContext(
                        original_query=effective_query,
                        pending=tuple(c.get("kind") for c in agent_result.clarifications),
                    ),
                    owner,
                )

            result["message"] = (
                "已检索到以下指标口径文档："
                if agent_result.documents
                else "未检索到相关口径文档，请换一种表述或补充指标口径。"
            )
            # 话题切换：口径检索轮显式清理旧 DSL 依赖
            if memory_store is not None and state is not None:
                state.last_dsl = None
                append_message(state, "user", effective_query)
                append_message(state, "assistant", agent_result.answer or "")
                memory_store.update(session_id, owner, state)
            ctx_override = {
                "intent": "rag",
                "documents": [d["key"] for d in agent_result.documents],
            }
        else:
            # DATA_QUERY：Multi-Tool Agent 调度（Plan & Select -> Execute & Guard -> Reflect）
            if slot_store is not None:
                slot_store.clear(session_id, owner)
            agent = default_tool_agent()

            # 会话上下文继承与消解（Contextual Merging）：
            # - 省略指代 / 下钻 -> 注入基于上轮 DSL 的合并结果（base_dsl）；
            # - 话题切换 -> 显式清理旧 DSL 依赖（last_dsl 置空）。
            if memory_store is not None and state is not None:
                resolution = resolve_context(effective_query, state.last_dsl, principal)
                if resolution.mode in ("inherit", "drilldown"):
                    base_dsl = resolution.dsl
                    context_summary = resolution.summary or None
                elif resolution.mode == "fresh" and resolution.reason in (
                    "topic_switch",
                    "reset_intent",
                ):
                    # 话题切换 / 排他重置（"我只需要知道...几个维度"）：显式清理旧 DSL，
                    # 本轮重新独立解析，绝不注入/沿用上轮指标与分组（Base DSL reset）。
                    state.last_dsl = None
                    context_summary = resolution.summary or None

            # P0-6 + R3 处置：未显式注入连接时，从进程级统一只读连接池取用
            # （用完归还，与工具层 _query_core 共享 exec.pool.default_pool 同一
            # 单例，杜绝双池），并用全局信号量限制并发查询数（超配额排队）。
            own_conn = conn is None
            pool = None
            if own_conn:
                pool = default_pool()
                conn = pool.acquire()
            try:
                with _query_gate:
                    agent_result: AgentResult = agent.run(
                        effective_query,
                        principal,
                        conn=conn,
                        executor=execute_sql,  # 模块级绑定：测试桩在调用期生效
                        rewriter=rewrite_dsl,
                        request_id=rid,
                        base_dsl=base_dsl,  # 会话上下文继承注入（None 时行为不变）
                        history=state.history if state is not None else None,
                        last_dsl=state.last_dsl if state is not None else None,
                    )
            finally:
                if own_conn and pool is not None:
                    pool.release(conn)

            result["steps"] = [s.to_dict() for s in agent_result.steps]
            result["answer"] = agent_result.answer
            result["chart_spec"] = agent_result.chart_spec
            result["download_urls"] = agent_result.download_urls
            result["degraded"] = agent_result.degraded
            result["mode"] = "degraded" if agent_result.degraded else "normal"
            result["rewrites"] = agent_result.rewrites
            result["scan_rows"] = agent_result.scan_rows
            # R3 反思层留痕透传（None = 未启用/未触发；前端可忽略）
            result["reflection"] = agent_result.reflection

            # R4 处置：LLM 规划器判定的 clarify 与路由层 CLARIFY 分支同权——
            # 反问写入槽位回填上下文（用户短语回答可结构化合并回原问题），
            # 并向响应透出结构化澄清问题（与 CLARIFY 分支响应契约一致）。
            if agent_result.clarifications:
                result["clarifications"] = agent_result.clarifications
                if slot_store is not None:
                    slot_store.set(
                        session_id,
                        ClarifyContext(
                            original_query=effective_query,
                            pending=tuple(c.get("kind") for c in agent_result.clarifications),
                        ),
                        owner,
                    )

            if agent_result.error:
                error = _friendly_by_class(agent_result.error_type) or "系统繁忙，请稍后重试"
                result["error"] = error
                result["error_detail"] = agent_result.error
                circuit_breaker = _CIRCUIT_BY_ERROR_CLASS.get(agent_result.error_type)
                # 自愈彻底失败不污染上轮有效状态：仅追加用户消息，last_dsl 保持不动
                if memory_store is not None and state is not None:
                    append_message(state, "user", effective_query)
                    memory_store.update(session_id, owner, state)
            else:
                dsl = agent_result.dsl
                sql = agent_result.sql
                columns = agent_result.columns or []
                rows = agent_result.rows or []
                rewrites = agent_result.rewrites
                scan_rows = agent_result.scan_rows
                row_count = len(rows)
                result["dsl"] = dsl.model_dump(mode="json") if dsl is not None else None
                result["sql"] = sql
                result["columns"] = columns
                result["rows"] = rows
                result["explanation"] = agent_result.explanation
                result["viz"] = agent_result.viz
                # 执行闭环写回：查询成功并通过安全校验后，更新 last_dsl / active_entities /
                # 滚动历史（供下一轮省略指代 / 下钻 / 话题切换判定）
                if memory_store is not None and dsl is not None:
                    if state is None:
                        state = SessionState(session_id=session_id, user_id=owner)
                    state.last_dsl = dsl
                    state.active_entities = derive_active_entities(dsl)
                    append_message(state, "user", effective_query)
                    append_message(state, "assistant", agent_result.answer or "", dsl=dsl)
                    memory_store.update(session_id, owner, state)
    except Exception as exc:
        error = _friendly_error(exc)
        result["error"] = error
        result["error_detail"] = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, QueryTimeoutError):
            circuit_breaker = "query_timeout"
        elif isinstance(exc, MaxRowsScannedExceeded):
            circuit_breaker = "scan_rows"
        elif isinstance(exc, ResultLimitExceeded):
            circuit_breaker = "result_limit"
        elif isinstance(exc, UnsafeSqlError):
            circuit_breaker = "unsafe_sql"

    latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
    if ctx_override is not None:
        ctx = ctx_override
    elif retrieval_context is not None:
        ctx = retrieval_context
    else:
        ctx = _retrieval_context(dsl) if dsl is not None else None

    # 澄清槽位回填（P0-5）：把合并来源写进审计检索上下文，保证澄清对话可追溯
    if result.get("clarify_filled"):
        ctx = dict(ctx or {})
        ctx["clarify_filled"] = True
        ctx["filled_from"] = result.get("filled_from")

    # 可观测性打点（P0 / §4 项5）：QPS、P50/P95、意图分布、自愈/熔断/降级
    try:
        default_registry().record_query(
            intent=str(result.get("intent", "unknown")),
            action=str(result.get("action", "unknown")),
            latency_ms=latency_ms,
            error=error,
            degraded=bool(result.get("degraded")),
            rewrites=rewrites or 0,
            clarify_filled=bool(result.get("clarify_filled")),
            circuit_breaker=circuit_breaker,
        )
    except Exception:  # 指标打点失败绝不影响主链路
        logger.exception("metrics_record_failed", extra={"event": "metrics_record_failed"})

    record = AuditRecord(
        request_id=rid,
        session_id=session_id,
        user=user or principal,
        principal=principal,
        prompt=query,
        retrieval_context=ctx,
        dsl=dsl.model_dump(mode="json") if dsl is not None else None,
        sql=sql,
        latency_ms=latency_ms,
        row_count=row_count,
        scan_rows=scan_rows,
        rewrites=rewrites,
        error=error,
        steps=result.get("steps"),
        # 意图路由字段：每轮提问的分类结果 / 路由耗时 / 决策原因（分流漏斗分析）
        detected_intent=result.get("detected_intent"),
        routing_latency_ms=result.get(ROUTING_LATENCY_MS),
        routing_reason=result.get("routing_reason"),
    )

    store = audit_store if audit_store is not None else _default_audit_store()
    if store is not None:
        try:
            store.write(record)
        except Exception:  # 审计失败绝不影响主链路
            logger.exception("audit_write_failed", extra={"event": "audit_write_failed"})

    if error:
        logger.warning(
            "query_end_error",
            extra={"event": "query_end", "status": "error", "latency_ms": latency_ms},
        )
    else:
        logger.info(
            "query_end_ok",
            extra={
                "event": "query_end",
                "status": "ok",
                "latency_ms": latency_ms,
                "row_count": row_count,
            },
        )
    # 响应契约：透传会话继承/重写说明（前端感知多轮上下文复用）
    if context_summary:
        result["context_summary"] = context_summary
    return result


def ensure_db() -> None:
    """确保本地 DuckDB 数仓文件存在，缺失时幂等重建；随后重建语义目录（P0-2）
    与权限策略（P0-3），使"新增表/字段/权限"全部走配置而非改代码。"""
    if not settings.DB_PATH.exists():
        from mock.init_duckdb import main as init_db

        init_db()
    from security.policy_loader import refresh_policies
    from semantic.catalog_loader import refresh_catalog

    refresh_catalog(db_path=settings.DB_PATH)
    refresh_policies()
