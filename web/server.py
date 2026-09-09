"""DataAgent Web UI 服务（零依赖，标准库 http.server）+ 统一身份认证网关（P0）。

用法:
    python -m web.server [端口]     # 默认 8000

公开路由:
    GET  /              -> 前端主控制台（业务页面）
    GET  /login         -> 独立登录页（未登录强制落地页）
    GET  /static/*      -> 静态资源
    GET  /api/health    -> 健康检查
    POST /api/auth/login   -> 登录：校验用户名/口令，签发 JWT + 会话
    POST /api/auth/logout  -> 登出：吊销会话（需 X-Session-ID 或 session Cookie）
    GET  /api/auth/me      -> 当前身份（需 Bearer JWT 或会话）

受保护路由:
    POST /api/query     -> 完整链路（需 Bearer JWT 或会话）
    GET/POST /api/settings/providers        -> 供应商列表 / 创建（响应不含 api_key）
    PUT/DELETE /api/settings/providers/<id> -> 供应商更新 / 删除（预置供应商拒绝删除）
    POST /api/settings/providers/test       -> 连通性探测（极小 ping 文本，返回延时）
    POST /api/settings/providers/<id>/reveal -> 查看已保存 API Key（显式动作，记审计）

P0 安全约束（网关层强制）：
- principal 只由服务端从已认证身份映射（auth.gateway.authenticate），
  **请求体中的 principal 一律忽略**；若出现与身份不符的 principal 仅记警告；
- settings.AUTH_ENABLED=False 时（本地开发）回退到服务端默认身份，仍不信任客户端。

结构化日志：所有访问日志与业务日志均输出单行 JSON，request_id 由
X-Request-ID 请求头（或服务端生成）贯穿请求处理与审计链路。
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

from audit.logging import get_logger, get_request_id, set_request_context, setup_logging
from audit.metrics import default_registry
from auth.errors import AuthenticationError
from auth.gateway import AuthContext, authenticate, create_session, default_identity_store
from auth.ratelimit import LoginRateLimitError, default_login_limiter
from auth.session import default_session_store
from auth.tokens import create_token
from config import settings
from core.orchestrator.agent import run_agent
from core.orchestrator.state import AgentState
from tools.builtins._export_store import ExportNotFoundError, default_export_store
from web import providers_api
from web.service import ensure_db, run_query
from web.tasks import default_task_manager

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8000

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".csv": "text/csv; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

_access_logger = get_logger("web.access")
_auth_logger = get_logger("web.auth")

# HITL 暂停态（进程内存；属主绑定）：resume_token -> {owner, state}
_AGENT_PAUSED_STATES: dict[str, dict] = {}


def _level_from_str(level: str) -> int:
    """把 LOG_LEVEL 字符串映射为 logging 级别（非法值回退 INFO）。"""
    import logging

    return getattr(logging, level.upper(), logging.INFO)


class Handler(BaseHTTPRequestHandler):
    """HTTP 请求处理器：静态文件 + 受鉴权保护的 JSON API（stdlib-only HTTP server）。"""

    def _send_json(self, obj: dict, code: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-ID", get_request_id())
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, rel: str) -> None:
        path = (STATIC_DIR / rel).resolve()
        if STATIC_DIR not in path.parents and path != STATIC_DIR:
            return self._send_json({"error": "forbidden"}, 403)
        if not path.is_file():
            return self._send_json({"error": "not found"}, 404)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        # 前端脚本/样式更新必须即时生效：禁止浏览器缓存旧版 app.js，
        # 否则修复后的交互逻辑（供应商/模型保存）对用户表现为"修了没生效"。
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Request-ID", get_request_id())
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ #
    # 请求工具
    # ------------------------------------------------------------------ #
    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _authenticate(self) -> AuthContext | None:
        """从请求头解析身份；失败时返回 None（调用方负责 401 响应）。"""
        try:
            return authenticate(self.headers)
        except AuthenticationError as exc:
            _auth_logger.warning(
                "auth_failed",
                extra={"event": "auth_failed", "error": str(exc)},
            )
            return None

    @staticmethod
    def _session_cookie(session_id: str, max_age: int) -> str:
        """构造 HttpOnly 会话 Cookie（浏览器无 JS 也能维持登录态）。"""
        return f"session={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"

    # ------------------------------------------------------------------ #
    # 公开路由
    # ------------------------------------------------------------------ #
    def do_GET(self) -> None:
        # 每个请求入口注入结构化日志上下文（request_id 贯穿）
        """GET 路由：健康检查 / 指标 / 审计 / 导出下载 / 静态前端文件。"""
        set_request_context(request_id=self.headers.get("X-Request-ID"))
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._send_json({"status": "ok"})
        if parsed.path == "/api/metrics":
            return self._get_metrics()
        if parsed.path.startswith("/api/export/"):
            return self._get_export(parsed.path[len("/api/export/") :])
        if parsed.path == "/api/auth/me":
            return self._get_me()
        if parsed.path.startswith("/api/tasks/"):
            return self._get_task(parsed.path[len("/api/tasks/") :])
        if parsed.path == "/api/settings/providers":
            return self._protected(providers_api.list_providers)
        if parsed.path in ("/", "/index.html"):
            return self._send_file("index.html")
        if parsed.path == "/login":
            return self._send_file("login.html")
        rel = parsed.path[len("/static/") :] if parsed.path.startswith("/static/") else ""
        return self._send_file(rel)

    def do_POST(self) -> None:
        """POST 路由：登录 / 登出 / 问答 / 异步任务等受鉴权保护的 API。"""
        set_request_context(request_id=self.headers.get("X-Request-ID"))
        parsed = urlparse(self.path)
        if parsed.path == "/api/auth/login":
            return self._post_login()
        if parsed.path == "/api/auth/logout":
            return self._post_logout()
        if parsed.path == "/api/query":
            return self._post_query()
        if parsed.path == "/api/query/async":
            return self._post_query_async()
        if parsed.path == "/api/agent/run":
            return self._post_agent_run()
        if parsed.path == "/api/settings/providers":
            return self._post_providers()
        if parsed.path == "/api/settings/providers/test":
            return self._post_providers_test()
        if parsed.path.startswith("/api/settings/providers/") and parsed.path.endswith("/reveal"):
            # 查看已保存的真实 API Key（显式动作；响应含明文，记审计日志）
            provider_id = parsed.path[len("/api/settings/providers/") : -len("/reveal")]
            return self._protected(providers_api.reveal_provider_key, provider_id)
        return self._send_json({"error": "not found"}, 404)

    def do_PUT(self) -> None:
        """PUT 路由：供应商配置更新（id / is_preset 不可变更）。"""
        set_request_context(request_id=self.headers.get("X-Request-ID"))
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/settings/providers/"):
            provider_id = parsed.path[len("/api/settings/providers/") :]
            return self._put_provider(provider_id)
        return self._send_json({"error": "not found"}, 404)

    def do_DELETE(self) -> None:
        """DELETE 路由：供应商配置删除（预置供应商拒绝）。"""
        set_request_context(request_id=self.headers.get("X-Request-ID"))
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/settings/providers/"):
            provider_id = parsed.path[len("/api/settings/providers/") :]
            return self._delete_provider(provider_id)
        return self._send_json({"error": "not found"}, 404)

    # ------------------------------------------------------------------ #
    # 认证端点
    # ------------------------------------------------------------------ #
    def _get_me(self) -> None:
        """返回当前登录身份；未登录返回 401（前端据此切换到登录态）。"""
        ctx = self._authenticate()
        if ctx is None:
            return self._send_json({"error": "unauthorized"}, 401)
        set_request_context(request_id=self.headers.get("X-Request-ID"), user=ctx.username)
        return self._send_json(ctx.to_dict())

    def _get_metrics(self) -> None:
        """可观测性指标（受保护，P0 / §4 项5）：QPS / P50-P95 耗时 / 意图分布 /
        自愈成功率 / 熔断次数 / 澄清触发率 / 降级次数。"""
        ctx = self._authenticate()
        if ctx is None:
            return self._send_json({"error": "unauthorized"}, 401)
        return self._send_json(default_registry().snapshot())

    def _post_login(self) -> None:
        """登录：校验用户名/口令 -> 签发 JWT + 服务端会话。

        返回：token（JWT，供 Authorization: Bearer 使用）、session_id、
        expires_in、user（username/display_name/principal/roles/auth_type）。
        """
        body = self._read_body()
        if body is None:
            return self._send_json({"error": "invalid json"}, 400)
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        if not username or not password:
            return self._send_json({"error": "username and password are required"}, 400)

        limiter = default_login_limiter()
        rate_key = f"{username}:{self.client_address[0]}"
        try:
            limiter.check(rate_key)
        except LoginRateLimitError as exc:
            return self._send_json(
                {"error": str(exc), "retry_after": exc.retry_after},
                429,
                headers={"Retry-After": str(exc.retry_after)},
            )

        store = default_identity_store()
        try:
            user = store.authenticate(username, password)
        except AuthenticationError:
            limiter.record_failure(rate_key)
            _auth_logger.warning(
                "login_failed", extra={"event": "login_failed", "username": username}
            )
            return self._send_json({"error": "用户名或口令错误"}, 401)
        limiter.record_success(rate_key)

        token = create_token(
            user.username,
            settings.AUTH_JWT_SECRET,
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_JWT_AUDIENCE,
            ttl_seconds=settings.AUTH_JWT_TTL,
        )
        session = create_session(user)
        set_request_context(request_id=self.headers.get("X-Request-ID"), user=user.username)
        _auth_logger.info(
            "login_ok",
            extra={"event": "login_ok", "username": user.username, "principal": user.principal},
        )
        return self._send_json(
            {
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
            },
            headers={
                "Set-Cookie": self._session_cookie(session.session_id, settings.AUTH_SESSION_TTL)
            },
        )

    def _post_logout(self) -> None:
        """登出：吊销服务端会话（X-Session-ID 或 session Cookie）。"""
        sid = self.headers.get("X-Session-ID") or _cookie_session_id(self.headers.get("Cookie"))
        if sid:
            revoked = default_session_store().revoke(sid)
        else:
            revoked = False
        return self._send_json(
            {"ok": True, "revoked": revoked},
            headers={"Set-Cookie": self._session_cookie("", 0)},
        )

    # ------------------------------------------------------------------ #
    # 受保护：/api/query/async（异步查询：提交 -> task_id -> 轮询）
    # ------------------------------------------------------------------ #
    def _post_query_async(self) -> None:
        """异步查询提交：立即返回 task_id（202），后台线程执行完整查询链路。

        鉴权与会话语义与 POST /api/query 完全一致（principal 服务端强制绑定）；
        任务函数复用 run_query，护栏（RLS / 资源熔断 / 审计）不旁路。
        客户端轮询 GET /api/tasks/<task_id> 取回状态与结果。
        """
        ctx = self._authenticate()
        if ctx is None:
            return self._send_json({"error": "unauthorized"}, 401)

        body = self._read_body()
        if body is None:
            return self._send_json({"error": "invalid json"}, 400)
        query = str(body.get("query", "")).strip()
        if not query:
            return self._send_json({"error": "query is required"}, 400)

        session_id = ctx.session_id or _bound_session_id(self.headers, ctx.username)
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

        request_id = self.headers.get("X-Request-ID")
        set_request_context(request_id=request_id, session_id=session_id, user=ctx.username)

        # 请求级模型切换：随任务闭包透传（run_query 在工作线程内绑定上下文）
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
        self._send_json({"task_id": task_id, "status": "pending"}, 202)

    def _get_task(self, task_id: str) -> None:
        """查询异步任务状态与结果（认证保护；未知 task_id 或非属主返回 404）。

        属主校验（就绪度评审 R1 处置）：任务快照含查询结果数据，仅提交者
        本人可读取；admin 角色全局可见（与导出下载的属主/admin 放行一致）。
        """
        ctx = self._authenticate()
        if ctx is None:
            return self._send_json({"error": "unauthorized"}, 401)
        owner = None if "admin" in ctx.roles else ctx.username
        snap = default_task_manager().snapshot(task_id, owner=owner)
        if snap is None:
            return self._send_json({"error": "task not found"}, 404)
        snap.pop("owner", None)  # 快照对外不暴露属主字段
        self._send_json(snap)

    # ------------------------------------------------------------------ #
    # 受保护：/api/settings/providers（Model Provider 配置管理与连通性探测）
    # ------------------------------------------------------------------ #
    def _protected(self, endpoint, *args) -> None:
        """统一鉴权封装：认证通过后调用 providers_api 端点并回传 JSON。"""
        ctx = self._authenticate()
        if ctx is None:
            return self._send_json({"error": "unauthorized"}, 401)
        set_request_context(request_id=self.headers.get("X-Request-ID"), user=ctx.username)
        code, body = endpoint(*args)
        return self._send_json(body, code)

    def _post_providers(self) -> None:
        """POST /api/settings/providers：创建自定义供应商。"""
        body = self._read_body()
        if body is None:
            return self._send_json({"error": "invalid json"}, 400)
        return self._protected(providers_api.create_provider, body)

    def _post_providers_test(self) -> None:
        """POST /api/settings/providers/test：连通性探测（业务失败以 200+success=false 返回）。"""
        body = self._read_body() or {}
        return self._protected(providers_api.test_provider, body)

    def _put_provider(self, provider_id: str) -> None:
        """PUT /api/settings/providers/<id>：更新供应商配置。"""
        body = self._read_body()
        if body is None:
            return self._send_json({"error": "invalid json"}, 400)
        return self._protected(providers_api.update_provider, provider_id, body)

    def _delete_provider(self, provider_id: str) -> None:
        """DELETE /api/settings/providers/<id>：删除自定义供应商。"""
        return self._protected(providers_api.delete_provider, provider_id)

    # 受保护：/api/query
    # ------------------------------------------------------------------ #
    def _post_query(self) -> None:
        """完整链路查询。

        鉴权：Bearer JWT / 会话。principal 一律取自服务端映射的身份
        （auth.gateway），请求体中的 principal 字段被忽略（防客户端提权）。

        会话上下文（Session Memory / 澄清槽位）：优先取认证会话 ctx.session_id；
        JWT 认证时允许经 X-Session-ID / session Cookie 显式绑定服务端会话，
        但必须属于当前已认证用户（跨用户借用一律拒绝）。
        """
        ctx = self._authenticate()
        if ctx is None:
            return self._send_json({"error": "unauthorized"}, 401)

        body = self._read_body()
        if body is None:
            return self._send_json({"error": "invalid json"}, 400)
        query = str(body.get("query", "")).strip()
        if not query:
            return self._send_json({"error": "query is required"}, 400)

        session_id = ctx.session_id or _bound_session_id(self.headers, ctx.username)

        # 请求级模型切换（Chat 界面模型切换器）：provider_id / model_id 可选透传，
        # 服务端据此把本次查询的所有 LLM 调用转发到目标供应商
        provider_id = str(body.get("provider_id") or "").strip() or None
        model_id = str(body.get("model_id") or "").strip() or None

        # 客户端传入的 principal 一律忽略（P0：服务端强制绑定）
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
            request_id=self.headers.get("X-Request-ID"),
            session_id=session_id,
            user=ctx.username,
        )
        result = run_query(
            query,
            ctx.principal,
            request_id=self.headers.get("X-Request-ID"),
            session_id=session_id,
            user=ctx.username,
            provider_id=provider_id,
            model_id=model_id,
        )
        result["auth"] = ctx.to_dict()
        return self._send_json(result)

    # ------------------------------------------------------------------ #
    # 受保护：/api/agent/run（Data Agent 编排：多步分析 + 沙箱 + 归因）
    # ------------------------------------------------------------------ #
    def _post_agent_run(self) -> None:
        """POST /api/agent/run：编排器同步执行（多步分析可能较慢）。

        请求体：{"query": "...", "human_reply": 可选（HITL 答复）,
                 "resume_token": 可选（上一次 clarify 暂停态的恢复句柄）}
        响应：
        - phase=clarify（需要澄清）：{"phase": "clarify", "clarification": "...",
          "resume_token": "..."} —— 用户答复后携 token+human_reply 重调；
        - phase=done：AgentTrace（report/steps/artifacts/self_heal_count）。
        暂停态仅驻留本进程内存（属主绑定），进程重启后 token 失效。
        """
        ctx = self._authenticate()
        if ctx is None:
            return self._send_json({"error": "unauthorized"}, 401)
        body = self._read_body()
        if body is None:
            return self._send_json({"error": "invalid json"}, 400)
        query = str(body.get("query", "")).strip()
        if not query:
            return self._send_json({"error": "query is required"}, 400)
        human_reply = body.get("human_reply")
        human_reply = str(human_reply).strip() if isinstance(human_reply, str) else None
        resume_token = str(body.get("resume_token") or "").strip() or None

        set_request_context(request_id=self.headers.get("X-Request-ID"), user=ctx.username)

        resume_state: AgentState | None = None
        if resume_token:
            paused = _AGENT_PAUSED_STATES.pop(resume_token, None)
            if paused is None or paused.get("owner") != ctx.username:
                return self._send_json({"error": "resume token 无效或已过期"}, 404)
            resume_state = AgentState.model_validate(paused["state"])
            resume_state = resume_state.model_copy(update={"human_reply": human_reply})

        result = run_agent(
            query,
            session_id=ctx.session_id or ctx.username,
            human_reply=None if resume_state else human_reply,
            resume_state=resume_state,
        )
        if isinstance(result, AgentState):
            # HITL 中断：登记属主化的暂停态，返回恢复句柄
            import uuid

            token = f"hitl-{uuid.uuid4().hex[:16]}"
            _AGENT_PAUSED_STATES[token] = {
                "owner": ctx.username,
                "state": result.model_dump(mode="json"),
            }
            return self._send_json(
                {
                    "phase": "clarify",
                    "clarification": result.clarification,
                    "resume_token": token,
                    "session_id": result.session_id,
                }
            )
        result_dict = result.to_dict()
        result_dict["auth"] = ctx.to_dict()
        return self._send_json(result_dict)

    # ------------------------------------------------------------------ #
    # 受保护：/api/export/<id>（导出文件下载，P0-4 表格导出链路）
    # ------------------------------------------------------------------ #
    def _get_export(self, export_id: str) -> None:
        """下载此前由 export_report_tool 生成的导出文件（鉴权 + 白名单 id 校验）。"""
        ctx = self._authenticate()
        if ctx is None:
            return self._send_json({"error": "unauthorized"}, 401)
        try:
            item = default_export_store().get(export_id.strip())
        except ExportNotFoundError:
            return self._send_json({"error": "export not found"}, 404)

        # P1-3 + 就绪度评审 P3 处置：导出文件属主校验——只有文件所有者或
        # admin 角色可下载。管理员判定用显式角色（ctx.roles），取代旧
        # scoped_fields(None) 探测式实现（principal=None 恒返回全量、恒不抛
        # 异常，属主校验实际失效为"恒放行"）。
        item_principal = item.meta.get("principal")
        if item_principal is not None and item_principal != ctx.principal:
            if "admin" not in ctx.roles:
                return self._send_json({"error": "forbidden: export access denied"}, 403)

        body = item.read_bytes()
        filename = item.meta.get("filename") or f"export.{item.suffix}"
        # RFC 5987：非 ASCII 文件名用 filename* 携带 UTF-8 编码
        ascii_fallback = filename.encode("ascii", "ignore").decode() or "export"
        disposition = (
            f'attachment; filename="{ascii_fallback}"; ' f"filename*=UTF-8''{quote(filename)}"
        )
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(item.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", disposition)
        self.send_header("X-Content-Type-Options", "nosniff")  # P2: 防止MIME类型混淆攻击
        self.send_header("X-Request-ID", get_request_id())
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ #
    def log_message(self, fmt: str, *args: object) -> None:
        # 结构化访问日志（替代默认 stderr 文本；request_id 已在上下文中）
        """以结构化 JSON 记录访问日志（Structured access logging）。"""
        status = str(args[1]) if len(args) > 1 else "-"
        size = str(args[2]) if len(args) > 2 else "-"
        _access_logger.info(
            fmt % args,
            extra={
                "event": "http_access",
                "method": self.command,
                "path": self.path,
                "client_ip": self.client_address[0],
                "status": status,
                "size": size,
            },
        )


def _cookie_session_id(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    try:
        from http.cookies import SimpleCookie

        jar = SimpleCookie()
        jar.load(cookie_header)
        morsel = jar.get("session")
        return morsel.value if morsel else None
    except Exception:
        return None


def _bound_session_id(headers, username: str) -> str | None:
    """解析请求携带的会话 ID 并校验归属（供 JWT 认证路径绑定会话上下文）。

    会话必须存在且属于当前已认证用户（username 一致）；缺失 / 过期 / 跨用户
    借用一律返回 None —— 该请求退化为"无会话上下文"（不继承任何记忆），
    绝不把其他用户的会话状态带入本次查询。
    """
    sid = headers.get("X-Session-ID") or _cookie_session_id(headers.get("Cookie"))
    if not sid:
        return None
    session = default_session_store().get(sid)
    if session is None or session.username != username:
        return None
    return session.session_id


def _startup_security_issues(host: str) -> list[str]:
    """严格模式下拒绝弱密钥与关闭鉴权。"""
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    strict = settings.AUTH_STRICT or host.strip().lower() not in local_hosts
    if not strict:
        return []
    issues: list[str] = []
    if settings.AUTH_JWT_SECRET in settings.WEAK_JWT_SECRETS:
        issues.append("AUTH_JWT_SECRET 使用弱默认值，必须注入强随机密钥")
    if not settings.AUTH_ENABLED:
        issues.append("AUTH_ENABLED=0 在严格生产模式下不允许")
    return issues


def main() -> None:
    """启动入口：安全预检 -> 建库 -> ThreadingHTTPServer 常驻服务。"""
    setup_logging(_level_from_str(settings.LOG_LEVEL))
    host = settings.WEB_HOST
    issues = _startup_security_issues(host)
    if issues:
        for issue in issues:
            print(f"[startup-security] {issue}", file=sys.stderr)
        raise SystemExit(1)
    ensure_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = ThreadingHTTPServer((host, port), Handler)
    auth_state = (
        "enabled"
        if settings.AUTH_ENABLED
        else f"disabled (default: {settings.AUTH_DEFAULT_PRINCIPAL})"
    )
    display_host = "localhost" if host in {"0.0.0.0", "::"} else host
    print(
        f"DataAgent Web UI running at http://{display_host}:{port}  [auth={auth_state}]", flush=True
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
