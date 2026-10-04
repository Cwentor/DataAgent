"""FastAPI 应用：端点路径/认证语义/响应结构逐字段对齐 stdlib 实现（web/server.py）。

迁移纪律（计划 Task 6/7）：
- 每个端点先在 stdlib Handler 中找到对应处理函数，响应字段集/错误体照抄；
  本文件不做任何"顺手改进"；
- 错误体统一 {"error": ...}（HTTPException 经异常处理器渲染，非 FastAPI 默认 detail）；
- 每个响应携带 X-Request-ID（中间件注入，与 stdlib _send_json 一致）；
- 同步阻塞逻辑（run_query / 编排器）经 run_in_threadpool 调度——LLM 与
  DuckDB 均为阻塞 IO，绝不入事件循环（设计文档 §4.3 阶段 2）；
- 静态文件服务逐项对齐 _send_file：路径穿越 403 / 缺失 404 / no-cache。
"""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from audit.logging import get_request_id, set_request_context
from audit.metrics import default_registry as metrics_registry
from auth.errors import AuthenticationError
from auth.gateway import AuthContext, authenticate, create_session, default_identity_store
from auth.ratelimit import LoginRateLimitError, default_login_limiter
from auth.session import default_session_store
from auth.tokens import create_token
from config import settings
from core.orchestrator.agent import run_agent
from tools.builtins._export_store import ExportNotFoundError, default_export_store
from web import providers_api
from web.server import (
    MIME,
    STATIC_DIR,
    _auth_logger,
    _bound_session_id,
    _cookie_session_id,
)
from web.service import run_query
from web.tasks import default_task_manager

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


# --------------------------------------------------------------------------- #
# 错误体与响应头契约（与 stdlib _send_json / 404 路由逐字段对齐）
# --------------------------------------------------------------------------- #
@app.exception_handler(StarletteHTTPException)
async def _http_exception(request: Request, exc: StarletteHTTPException) -> Response:
    """FastAPI HTTPException -> stdlib 风格 {"error": ...}。"""
    headers = getattr(exc, "headers", None) or {}
    return Response(
        content=json.dumps({"error": str(exc.detail)}, ensure_ascii=False),
        status_code=exc.status_code,
        media_type="application/json; charset=utf-8",
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception(request: Request, exc: RequestValidationError) -> Response:
    """请求体校验失败按 stdlib 语义收敛为 400 invalid json（而非 422）。"""
    return Response(
        content=json.dumps({"error": "invalid json"}, ensure_ascii=False),
        status_code=400,
        media_type="application/json; charset=utf-8",
    )


@app.middleware("http")
async def _request_id_header(request: Request, call_next):
    """每个响应注入 X-Request-ID（对齐 stdlib _send_json/_send_file）。"""
    response = await call_next(request)
    response.headers["X-Request-ID"] = get_request_id()
    return response


def _require_auth(request: Request) -> AuthContext:
    """认证依赖：与 server.py _authenticate 语义逐行对齐（失败 401 {"error": ...}）。"""
    try:
        return authenticate(request.headers)
    except AuthenticationError as exc:
        _auth_logger.warning("auth_failed", extra={"event": "auth_failed", "error": str(exc)})
        raise HTTPException(status_code=401, detail="unauthorized") from exc


async def _read_json_body(request: Request) -> dict:
    """请求体解析：失败按 stdlib _read_body 语义返回 400 invalid json。"""
    raw = await request.body()
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid json") from None


def _session_cookie(session_id: str, max_age: int) -> str:
    """构造 HttpOnly 会话 Cookie（与 server.py _session_cookie 逐字一致）。"""
    return f"session={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"


# --------------------------------------------------------------------------- #
# 公开路由
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict:
    # 字段集照抄 stdlib do_GET /api/health
    return {"status": "ok"}


@app.get("/api/metrics")
async def metrics(request: Request) -> dict:
    _require_auth(request)
    return metrics_registry().snapshot()


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict:
    ctx = _require_auth(request)
    set_request_context(request_id=get_request_id(), user=ctx.username)
    return ctx.to_dict()


@app.post("/api/auth/login")
async def auth_login(request: Request) -> Response:
    """登录：校验用户名/口令 -> 签发 JWT + 服务端会话（照抄 stdlib _post_login）。"""
    body = await _read_json_body(request)
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")

    limiter = default_login_limiter()
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{username}:{client_ip}"
    try:
        limiter.check(rate_key)
    except LoginRateLimitError as exc:
        # stdlib 契约：429 体含 retry_after 键（HTTPException 渲染器只产 error 键）
        return Response(
            content=json.dumps(
                {"error": str(exc), "retry_after": exc.retry_after}, ensure_ascii=False
            ),
            status_code=429,
            media_type="application/json; charset=utf-8",
            headers={"Retry-After": str(exc.retry_after)},
        )

    store = default_identity_store()
    try:
        user = await run_in_threadpool(store.authenticate, username, password)
    except AuthenticationError:
        limiter.record_failure(rate_key)
        _auth_logger.warning("login_failed", extra={"event": "login_failed", "username": username})
        raise HTTPException(status_code=401, detail="用户名或口令错误") from None
    limiter.record_success(rate_key)

    token = create_token(
        user.username,
        settings.AUTH_JWT_SECRET,
        issuer=settings.AUTH_JWT_ISSUER,
        audience=settings.AUTH_JWT_AUDIENCE,
        ttl_seconds=settings.AUTH_JWT_TTL,
    )
    session = create_session(user)
    set_request_context(request_id=get_request_id(), user=user.username)
    _auth_logger.info(
        "login_ok",
        extra={"event": "login_ok", "username": user.username, "principal": user.principal},
    )
    payload = {
        "token": token,
        "session_id": session.session_id,
        "expires_in": settings.AUTH_JWT_TTL,
        "user": {
            "username": user.username,
            "display_name": user.display_name,
            "principal": user.principal,
            "roles": sorted(user.roles),
            "auth_type": "jwt",
        },
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
        headers={"Set-Cookie": _session_cookie(session.session_id, settings.AUTH_SESSION_TTL)},
    )


@app.post("/api/auth/logout")
async def auth_logout(request: Request) -> Response:
    """登出：吊销服务端会话（X-Session-ID 或 session Cookie）。照抄 stdlib _post_logout。"""
    sid = request.headers.get("X-Session-ID") or _cookie_session_id(request.headers.get("Cookie"))
    revoked = default_session_store().revoke(sid) if sid else False
    return Response(
        content=json.dumps({"ok": True, "revoked": revoked}, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
        headers={"Set-Cookie": _session_cookie("", 0)},
    )


# --------------------------------------------------------------------------- #
# 受保护：/api/query 与 /api/query/async
# --------------------------------------------------------------------------- #
@app.post("/api/query")
async def post_query(request: Request) -> dict:
    """完整链路查询（照抄 stdlib _post_query）。"""
    ctx = _require_auth(request)
    body = await _read_json_body(request)
    query = str(body.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    session_id = ctx.session_id or _bound_session_id(request.headers, ctx.username)
    provider_id = str(body.get("provider_id") or "").strip() or None
    model_id = str(body.get("model_id") or "").strip() or None

    client_principal = body.get("principal")
    if client_principal is not None and str(client_principal) != ctx.principal:
        _auth_logger.warning(
            "client_principal_ignored",
            extra={
                "event": "client_principal_ignored",
                "server_principal": ctx.principal,
                "client_principal": str(client_principal),
            },
        )

    set_request_context(
        request_id=request.headers.get("X-Request-ID"),
        session_id=session_id,
        user=ctx.username,
    )
    result = await run_in_threadpool(
        run_query,
        query,
        ctx.principal,
        request_id=request.headers.get("X-Request-ID"),
        session_id=session_id,
        user=ctx.username,
        provider_id=provider_id,
        model_id=model_id,
    )
    result["auth"] = ctx.to_dict()
    return result


@app.post("/api/query/async")
async def post_query_async(request: Request) -> Response:
    """异步查询提交：202 + task_id（照抄 stdlib _post_query_async）。"""
    ctx = _require_auth(request)
    body = await _read_json_body(request)
    query = str(body.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    session_id = ctx.session_id or _bound_session_id(request.headers, ctx.username)
    client_principal = body.get("principal")
    if client_principal is not None and str(client_principal) != ctx.principal:
        _auth_logger.warning(
            "client_principal_ignored",
            extra={
                "event": "client_principal_ignored",
                "server_principal": ctx.principal,
                "client_principal": str(client_principal),
            },
        )
    request_id = request.headers.get("X-Request-ID")
    set_request_context(request_id=request_id, session_id=session_id, user=ctx.username)
    provider_id = str(body.get("provider_id") or "").strip() or None
    model_id = str(body.get("model_id") or "").strip() or None

    def _run_query_task() -> dict:
        return run_query(
            query,
            ctx.principal,
            request_id=request_id,
            session_id=session_id,
            user=ctx.username,
            provider_id=provider_id,
            model_id=model_id,
        )

    task_id = default_task_manager().submit(_run_query_task, owner=ctx.username)
    _auth_logger.info(
        "async_task_submitted",
        extra={"event": "async_task_submitted", "task_id": task_id, "user": ctx.username},
    )
    return Response(
        content=json.dumps({"task_id": task_id, "status": "pending"}, ensure_ascii=False),
        status_code=202,
        media_type="application/json; charset=utf-8",
    )


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict:
    """异步任务状态与结果（属主校验照抄 stdlib _get_task）。"""
    ctx = _require_auth(request)
    owner = None if "admin" in ctx.roles else ctx.username
    snap = default_task_manager().snapshot(task_id, owner=owner)
    if snap is None:
        raise HTTPException(status_code=404, detail="task not found")
    snap.pop("owner", None)  # 快照对外不暴露属主字段
    return snap


# --------------------------------------------------------------------------- #
# 受保护：/api/settings/providers（Model Provider 配置管理）
# --------------------------------------------------------------------------- #
@app.get("/api/settings/providers")
async def list_providers(request: Request) -> Response:
    ctx = _require_auth(request)
    set_request_context(request_id=get_request_id(), user=ctx.username)
    code, body = providers_api.list_providers()
    return Response(
        content=json.dumps(body, ensure_ascii=False),
        status_code=code,
        media_type="application/json; charset=utf-8",
    )


@app.post("/api/settings/providers")
async def create_provider(request: Request) -> Response:
    ctx = _require_auth(request)
    body = await _read_json_body(request)
    set_request_context(request_id=get_request_id(), user=ctx.username)
    code, payload = providers_api.create_provider(body)
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        status_code=code,
        media_type="application/json; charset=utf-8",
    )


@app.post("/api/settings/providers/test")
async def test_provider(request: Request) -> Response:
    # stdlib _read_body() or {}：本端点容忍无效/空请求体（业务失败 200+success=false）
    ctx = _require_auth(request)
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    set_request_context(request_id=get_request_id(), user=ctx.username)
    code, payload = providers_api.test_provider(body)
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        status_code=code,
        media_type="application/json; charset=utf-8",
    )


@app.put("/api/settings/providers/{provider_id}")
async def update_provider(provider_id: str, request: Request) -> Response:
    ctx = _require_auth(request)
    body = await _read_json_body(request)
    set_request_context(request_id=get_request_id(), user=ctx.username)
    code, payload = providers_api.update_provider(provider_id, body)
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        status_code=code,
        media_type="application/json; charset=utf-8",
    )


@app.delete("/api/settings/providers/{provider_id}")
async def delete_provider(provider_id: str, request: Request) -> Response:
    ctx = _require_auth(request)
    set_request_context(request_id=get_request_id(), user=ctx.username)
    code, payload = providers_api.delete_provider(provider_id)
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        status_code=code,
        media_type="application/json; charset=utf-8",
    )


@app.post("/api/settings/providers/{provider_id}/reveal")
async def reveal_provider_key(provider_id: str, request: Request) -> Response:
    ctx = _require_auth(request)
    set_request_context(request_id=get_request_id(), user=ctx.username)
    code, payload = providers_api.reveal_provider_key(provider_id)
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        status_code=code,
        media_type="application/json; charset=utf-8",
    )


# --------------------------------------------------------------------------- #
# 受保护：/api/schema/summary 与 /api/export/<id>
# --------------------------------------------------------------------------- #
@app.get("/api/schema/summary")
async def schema_summary(request: Request) -> dict:
    """语义目录摘要（照抄 stdlib Handler._schema_summary）。

    字段级返回 label（中文语义标签，可能为 None）；表级返回 label
    （中文表标签，未登记的新表回退表名本身）。
    """
    ctx = _require_auth(request)
    set_request_context(request_id=get_request_id(), user=ctx.username)
    from semantic.catalog import COLUMNS, TABLE_LABELS

    tables: dict[str, list[dict]] = {}
    for logical, meta in sorted(COLUMNS.items()):
        tables.setdefault(meta.table, []).append(
            {
                "field": logical,
                "column": meta.column,
                "dtype": meta.dtype,
                "label": meta.label,
            }
        )
    return {
        "principal": ctx.principal,
        "tables": [
            {
                "table": name,
                "label": TABLE_LABELS.get(name, name),
                "fields": fields,
            }
            for name, fields in sorted(tables.items())
        ],
    }


@app.get("/api/export/{export_id}")
async def get_export(export_id: str, request: Request) -> Response:
    """导出文件下载（照抄 stdlib _get_export：鉴权 + 属主/admin 校验 + RFC 5987）。"""
    ctx = _require_auth(request)
    try:
        item = default_export_store().get(export_id.strip())
    except ExportNotFoundError:
        raise HTTPException(status_code=404, detail="export not found") from None

    item_principal = item.meta.get("principal")
    if item_principal is not None and item_principal != ctx.principal:
        if "admin" not in ctx.roles:
            raise HTTPException(status_code=403, detail="forbidden: export access denied")

    body = item.read_bytes()
    filename = item.meta.get("filename") or f"export.{item.suffix}"
    ascii_fallback = filename.encode("ascii", "ignore").decode() or "export"
    disposition = f'attachment; filename="{ascii_fallback}"; ' f"filename*=UTF-8''{quote(filename)}"
    return Response(
        content=body,
        media_type=MIME.get(item.suffix, "application/octet-stream"),
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
        },
    )


# --------------------------------------------------------------------------- #
# 静态前端文件（照抄 stdlib _send_file：穿越 403 / 缺失 404 / no-cache）
# --------------------------------------------------------------------------- #
def _serve_file(rel: str) -> Response:
    path = (STATIC_DIR / rel).resolve()
    if STATIC_DIR not in path.parents and path != STATIC_DIR:
        raise HTTPException(status_code=403, detail="forbidden")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    body = path.read_bytes()
    return Response(
        content=body,
        media_type=MIME.get(path.suffix, "application/octet-stream"),
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/")
async def index() -> Response:
    return _serve_file("index.html")


@app.get("/index.html")
async def index_html() -> Response:
    return _serve_file("index.html")


@app.get("/login")
async def login_page() -> Response:
    return _serve_file("login.html")


@app.get("/static/{rel:path}")
async def static_file(rel: str) -> Response:
    return _serve_file(rel)


# --------------------------------------------------------------------------- #
# 受保护：/api/agent/run（Data Agent 编排：多步分析 + 沙箱 + 归因）
# 照抄 stdlib _post_agent_run / _get_agent_run_status / _get_agent_chat_stream
# （RunRegistry 版：执行与连接解耦，游标重放，X-Run-Id 契约）
# --------------------------------------------------------------------------- #
import queue as _queue  # noqa: E402
import threading as _threading  # noqa: E402
import time  # noqa: E402
import uuid as _uuid  # noqa: E402

from fastapi.responses import StreamingResponse  # noqa: E402

from core.orchestrator.state import AgentState  # noqa: E402
from web.runs import default_run_registry  # noqa: E402
from web.server import _AGENT_PAUSED_STATES  # noqa: E402


@app.post("/api/agent/run")
async def post_agent_run(request: Request) -> Response:
    """编排器同步执行（多步分析可能较慢）。照抄 stdlib _post_agent_run。"""
    ctx = _require_auth(request)
    body = await _read_json_body(request)
    query = str(body.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    human_reply = body.get("human_reply")
    human_reply = str(human_reply).strip() if isinstance(human_reply, str) else None
    resume_token = str(body.get("resume_token") or "").strip() or None

    set_request_context(request_id=request.headers.get("X-Request-ID"), user=ctx.username)

    resume_action = str(body.get("action") or "").strip() or None
    if resume_action:
        human_reply = json.dumps(
            {
                "kind": "plan_review",
                "action": resume_action,
                "instruction": str(body.get("instruction") or "").strip() or None,
            }
        )
    resume_state: AgentState | None = None
    if resume_token:
        paused = _AGENT_PAUSED_STATES.pop(resume_token, None)
        if paused is None or paused.get("owner") != ctx.username:
            raise HTTPException(status_code=404, detail="resume token 无效或已过期")
        resume_state = AgentState.model_validate(paused["state"])
        resume_state = resume_state.model_copy(update={"human_reply": human_reply})

    result = await run_in_threadpool(
        run_agent,
        query,
        session_id=ctx.session_id or ctx.username,
        human_reply=None if resume_state else human_reply,
        resume_state=resume_state,
    )
    if isinstance(result, AgentState):
        # HITL 中断：登记属主化的暂停态，返回恢复句柄
        token = f"hitl-{_uuid.uuid4().hex[:16]}"
        _AGENT_PAUSED_STATES[token] = {
            "owner": ctx.username,
            "state": result.model_dump(mode="json"),
        }
        # M2/M4：挂起相位按真实值返回（clarify | plan_review | high_risk），
        # plan_review 附带步骤 DAG、high_risk 附带待确认的分析步骤
        payload = {
            "phase": result.phase,
            "resume_token": token,
            "session_id": result.session_id,
        }
        if result.phase == "plan_review":
            payload["plan_steps"] = [step.model_dump(mode="json") for step in result.plan_steps]
            payload["summary"] = result.plan_steps[0].goal if result.plan_steps else ""
        elif result.phase == "high_risk":
            payload["steps"] = [
                {"id": s.id, "goal": s.goal}
                for s in result.plan_steps
                if s.kind == "analyze" and s.status == "pending" and s.code
            ]
        elif result.phase == "exploration":
            # M6：探索查询审批卡载荷（SQL 摘要 + 触达表 + 敏感提示）
            pending = result.exploration_pending or {}
            payload["exploration"] = {
                "sql": pending.get("sql", ""),
                "tables": pending.get("tables", []),
                "sensitive": pending.get("sensitive", False),
            }
        else:
            payload["clarification"] = result.clarification
        return Response(
            content=json.dumps(payload, ensure_ascii=False),
            media_type="application/json; charset=utf-8",
        )
    result_dict = result.to_dict()
    result_dict["auth"] = ctx.to_dict()
    return Response(
        content=json.dumps(result_dict, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
    )


@app.get("/api/v1/agent/runs/{run_id}")
async def get_agent_run_status(run_id: str, request: Request) -> Response:
    """run 状态快照（属主 fail-closed；照抄 stdlib _get_agent_run_status）。"""
    ctx = _require_auth(request)
    set_request_context(request_id=request.headers.get("X-Request-ID"), user=ctx.username)
    owner = None if "admin" in ctx.roles else ctx.username
    snap = await run_in_threadpool(default_run_registry().status, run_id.strip(), owner=owner)
    if snap is None:
        raise HTTPException(status_code=404, detail="run not found")
    snap.pop("resume_token", None)  # 恢复句柄仅经事件流下发，轮询快照不暴露
    return Response(
        content=json.dumps(snap, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
    )


def _sse_frame_bytes(event: dict) -> bytes:
    """SSE 帧构造：与 stdlib _sse_write 逐字节同源（json.dumps ensure_ascii=False）。"""
    frame = json.dumps(event, ensure_ascii=False)
    return f"data: {frame}\n\n".encode()


@app.get("/api/v1/agent/chat/stream")
async def agent_chat_stream(request: Request):
    """SSE 流式编排端点（RunRegistry 版，照抄 stdlib _get_agent_chat_stream 三形态）。

    订阅循环在 worker 线程执行（subscribe 阻塞语义），帧经无界队列桥接为
    StreamingResponse；客户端断开经 closed 事件向 write_frame 抛 OSError，
    与 stdlib"断开仅退订、run 继续执行"的语义逐字对齐。
    """
    ctx = _require_auth(request)
    params: dict[str, str] = {}
    for key, values in request.query_params.multi_items():
        if key not in params:
            params[key] = values
    query = params.get("query", "").strip()
    human_reply = params.get("human_reply", "").strip() or None
    resume_token = params.get("resume_token", "").strip() or None
    provider_id = params.get("provider_id", "").strip() or None
    model_id = params.get("model_id", "").strip() or None
    autonomy_level = params.get("autonomy_level", "").strip() or None
    run_id = params.get("run_id", "").strip() or None
    # M2 Plan Mode：审批动作三选一（approve/edit/reject）编码进 human_reply 通道，
    # 引擎侧解码后注入对应 gate（clarify 的纯文本答复不受影响）
    resume_action = params.get("action", "").strip() or None
    if resume_action:
        human_reply = json.dumps(
            {
                "kind": "plan_review",
                "action": resume_action,
                "instruction": params.get("instruction", "").strip() or None,
            }
        )
    thread = params.get("thread", "").strip()
    try:
        after = max(0, int(params.get("after", "0").strip() or "0"))
    except ValueError:
        after = 0

    set_request_context(request_id=request.headers.get("X-Request-ID"), user=ctx.username)
    registry = default_run_registry()
    # admin 全局可见（与任务快照 / 导出下载的属主放行口径一致）
    owner = None if "admin" in ctx.roles else ctx.username

    # 形态 2：纯游标订阅（重连 / 刷新恢复）——run 未知直接 404
    if run_id and not resume_token:
        found = await run_in_threadpool(registry.get, run_id, owner=owner)
        if found is None:
            raise HTTPException(status_code=404, detail="run not found")
        return _sse_follow(registry, run_id, after, owner)

    # 形态 3：HITL 恢复（属主严格匹配；token 无效以 error 事件在流内收敛）
    if resume_token:
        if run_id:
            run = await run_in_threadpool(
                registry.resume, run_id, human_reply or "", owner=ctx.username
            )
        else:
            run = await run_in_threadpool(
                registry.resume_by_token, resume_token, human_reply or "", owner=ctx.username
            )
        if run is None:
            return _sse_error_stream("resume token 无效或已过期", run_id or "")
        return _sse_follow(registry, run.run_id, run.resume_baseline, owner)

    # 形态 1：新提问启动 run（thread 缺省回退认证会话键——旧客户端兼容）
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    session_key = f"webui:{thread}" if thread else (ctx.session_id or ctx.username)
    # 幂等保护：同一会话 + 同一提问且 run 仍在执行时复用，严禁重复启动编排
    existing = await run_in_threadpool(registry.find_active, ctx.username, session_key, query)
    run = existing or await run_in_threadpool(
        registry.start,
        query,
        owner=ctx.username,
        session_key=session_key,
        provider_id=provider_id,
        model_id=model_id,
        autonomy_level=autonomy_level,
    )
    return _sse_follow(registry, run.run_id, 0, owner)


def _sse_follow(registry, run_id: str, after: int, owner: str | None) -> StreamingResponse:
    """打开 SSE 响应并按游标订阅 run 事件流（照抄 stdlib _sse_follow 语义）。"""
    frames: _queue.SimpleQueue = _queue.SimpleQueue()
    closed = _threading.Event()

    def write_frame(event: dict) -> None:
        if closed.is_set():
            # 与 stdlib 一致：断开向订阅循环抛 OSError，run 继续执行并入缓冲
            raise OSError("client disconnected")
        frames.put(_sse_frame_bytes(event))

    def on_idle() -> None:
        if closed.is_set():
            raise OSError("client disconnected")
        frames.put(b": ping\n\n")

    def _pump() -> None:
        try:
            registry.subscribe(run_id, after, write_frame, owner=owner, on_idle=on_idle)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端断开：编排线程继续跑完并入缓冲，可重连重放
        finally:
            frames.put(None)

    _threading.Thread(target=_pump, daemon=True, name=f"sse-{run_id}").start()

    def gen():
        try:
            while True:
                item = frames.get()
                if item is None:
                    break
                yield item
        finally:
            closed.set()  # 生成器被丢弃（客户端断开）=> 订阅循环随之收敛

    headers: dict[str, str] = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "close",
    }
    if run_id:
        headers["X-Run-Id"] = run_id
    return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8", headers=headers)


def _sse_error_stream(message: str, run_id: str) -> StreamingResponse:
    """token 无效等场景：HTTP 200 事件流内报错（与 stdlib 契约一致，不抛裸 4xx）。"""
    event = {
        "turn_id": "",
        "event": "error",
        "timestamp": int(time.time() * 1000),
        "payload": {"error": message},
    }
    headers: dict[str, str] = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "close",
    }
    if run_id:
        headers["X-Run-Id"] = run_id
    return StreamingResponse(
        iter([_sse_frame_bytes(event)]),
        media_type="text/event-stream; charset=utf-8",
        headers=headers,
    )


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def catch_all(full_path: str) -> Response:
    """未匹配路由统一 {"error": "not found"} 404（对齐 stdlib 404 兜底）。"""
    raise HTTPException(status_code=404, detail="not found")
