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
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

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
    """语义目录摘要（照抄 stdlib Handler._schema_summary）。"""
    ctx = _require_auth(request)
    set_request_context(request_id=get_request_id(), user=ctx.username)
    from semantic.catalog import COLUMNS

    tables: dict[str, list[dict]] = {}
    for logical, meta in sorted(COLUMNS.items()):
        tables.setdefault(meta.table, []).append(
            {"field": logical, "column": meta.column, "dtype": meta.dtype}
        )
    return {
        "principal": ctx.principal,
        "tables": [{"table": name, "fields": fields} for name, fields in sorted(tables.items())],
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


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def catch_all(full_path: str) -> Response:
    """未匹配路由统一 {"error": "not found"} 404（对齐 stdlib 404 兜底）。"""
    raise HTTPException(status_code=404, detail="not found")
