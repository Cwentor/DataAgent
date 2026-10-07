"""DataAgent Web UI 服务（FastAPI + uvicorn，十七期 M4 单引擎收敛）+ 统一身份认证网关。

用法:
    python -m web.server [端口]     # 默认 8000

公开路由:
    GET  /              -> 前端主控制台（业务页面）
    GET  /login         -> 独立登录页（未登录强制落地页）
    GET  /static/*      -> 静态资源
    GET  /api/health    -> 健康检查
    GET  /api/v1/agent/chat/stream -> SSE 流式编排（run 注册表：后台执行 +
                                  事件缓冲 + seq 游标重放，支持并行会话/断线重连）
    GET  /api/v1/agent/runs/<run_id> -> run 状态快照（属主校验 fail-closed）
    POST /api/auth/login   -> 登录：校验用户名/口令，签发 JWT + 会话
    POST /api/auth/logout  -> 登出：吊销会话（需 X-Session-ID 或 session Cookie）
    GET  /api/auth/me      -> 当前身份（需 Bearer JWT 或会话）

受保护路由:
    POST /api/query     -> 完整链路（需 Bearer JWT 或会话）
    POST /api/query/async -> 异步提交查询（202 + task_id）
    GET  /api/tasks/<task_id> -> 异步任务快照（属主校验 fail-closed）
    GET  /api/export/<export_id> -> 导出文件下载（属主校验）
    POST /api/agent/run -> 同步编排（HITL 挂起返回 resume_token，属主绑定）
    GET  /api/metrics   -> 进程内可观测指标快照
    GET  /api/schema/summary -> 语义目录摘要（知识上下文）
    GET/POST /api/settings/providers        -> 供应商列表 / 创建自定义（协议限白名单，响应不含 api_key）
    PUT/DELETE /api/settings/providers/<id> -> 供应商更新 / 删除（无预置条目）
    POST /api/settings/providers/test       -> 连通性探测（极小 ping 文本，业务失败 HTTP 200 + success=false）
    POST /api/settings/providers/<id>/reveal -> 查看已保存 API Key（显式动作，记审计）

P0 安全约束（网关层强制）：
- principal 只由服务端从已认证身份映射（auth.gateway.authenticate），
  **请求体中的 principal 一律忽略**；若出现与身份不符的 principal 仅记警告；
- settings.AUTH_ENABLED=False 时（本地开发）回退到服务端默认身份，仍不信任客户端。

结构化日志：所有访问日志与业务日志均输出单行 JSON，request_id 由
X-Request-ID 请求头（或服务端生成）贯穿请求处理与审计链路。
"""

from __future__ import annotations

import sys
from pathlib import Path

from audit.logging import get_logger, setup_logging
from auth.session import default_session_store
from config import settings
from web.service import ensure_db

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

# HITL 暂停态：由 web.runs.RunRegistry 按属主管理（run 级缓冲 + 原子领取）；
# _AGENT_PAUSED_STATES 仅保留给非流式 /api/agent/run 端点（同步编排路径）。
_AGENT_PAUSED_STATES: dict[str, dict] = {}


def _level_from_str(level: str) -> int:
    """把 LOG_LEVEL 字符串映射为 logging 级别（非法值回退 INFO）。"""
    import logging

    return getattr(logging, level.upper(), logging.INFO)


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
    """启动入口：安全预检 -> 建库 -> uvicorn 常驻服务（M4 单引擎收敛）。

    `python -m web.server [端口]` 用法保持兼容；ThreadingHTTPServer 与
    WEB_SERVER_ENGINE 开关已删除（FastAPI 为唯一 HTTP 实现）。
    """
    setup_logging(_level_from_str(settings.LOG_LEVEL), log_file=settings.AUDIT_LOG_FILE)
    host = settings.WEB_HOST
    issues = _startup_security_issues(host)
    if issues:
        for issue in issues:
            print(f"[startup-security] {issue}", file=sys.stderr)
        raise SystemExit(1)
    ensure_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    import uvicorn

    uvicorn.run("web.api:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
