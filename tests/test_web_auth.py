"""Web 网关鉴权（P0）：登录 / 401 / 服务端强制绑定 principal / 客户端 principal 忽略。

覆盖：
- 未带凭证访问 /api/query -> 401；
- 登录签发 JWT + 会话；错误口令 -> 401；
- Bearer JWT / 会话 Cookie 两种凭证都可访问受保护端点；
- 服务端从身份映射 principal：客户端请求体里的 principal 一律被忽略；
- 受限主体（bob）查询退款 -> 无权错误（守卫前移 + 纵深防御）；
- 登出吊销会话。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from web.server import Handler, ThreadingHTTPServer
from web.service import ensure_db


@pytest.fixture(scope="module")
def warehouse():
    """确保本地 DuckDB 数仓文件存在（HTTP 链路读取文件库）。"""
    ensure_db()
    return None


def _start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


def _request(port, method, path, payload=None, headers=None, timeout=10):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), resp
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8")), exc


def _login(port, username, password):
    return _request(port, "POST", "/api/auth/login", {"username": username, "password": password})


# --------------------------------------------------------------------------- #
# 鉴权门槛
# --------------------------------------------------------------------------- #
def test_startup_security_validation():
    """P0-3：严格模式下弱 JWT 密钥与关闭鉴权都被启动校验拒绝。"""
    from config import settings
    from web.server import _startup_security_issues

    original = (settings.AUTH_STRICT, settings.AUTH_JWT_SECRET, settings.AUTH_ENABLED)
    try:
        # 本地开发默认配置：非严格、强密钥、鉴权开启 -> 无问题
        settings.AUTH_STRICT = False
        settings.AUTH_JWT_SECRET = "a" * 64
        settings.AUTH_ENABLED = True
        assert _startup_security_issues("127.0.0.1") == []

        # 严格模式 + 弱默认密钥 -> 拒绝
        settings.AUTH_STRICT = True
        settings.AUTH_JWT_SECRET = "dev-insecure-jwt-secret-change-me"
        issues = _startup_security_issues("127.0.0.1")
        assert any("AUTH_JWT_SECRET" in issue for issue in issues)

        # 严格模式 + 关闭鉴权 -> 拒绝
        settings.AUTH_STRICT = True
        settings.AUTH_JWT_SECRET = "a" * 64
        settings.AUTH_ENABLED = False
        issues = _startup_security_issues("127.0.0.1")
        assert any("AUTH_ENABLED" in issue for issue in issues)

        # 非 localhost 绑定即使未开 AUTH_STRICT 也按严格模式处理
        settings.AUTH_STRICT = False
        settings.AUTH_JWT_SECRET = "dev-insecure-jwt-secret-change-me"
        issues = _startup_security_issues("0.0.0.0")
        assert any("AUTH_JWT_SECRET" in issue for issue in issues)
    finally:
        settings.AUTH_STRICT, settings.AUTH_JWT_SECRET, settings.AUTH_ENABLED = original


def test_query_without_credentials_401():
    server, port = _start_server()
    try:
        status, body, _ = _request(port, "POST", "/api/query", {"query": "GMV"})
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        server.shutdown()
        server.server_close()


def test_metrics_without_credentials_401():
    server, port = _start_server()
    try:
        status, _, _ = _request(port, "GET", "/api/metrics")
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()


def test_metrics_authenticated_returns_snapshot():
    server, port = _start_server()
    try:
        _, login, _ = _login(port, "admin", "admin123")
        status, body, _ = _request(
            port,
            "GET",
            "/api/metrics",
            headers={"Authorization": f"Bearer {login['token']}"},
        )
        assert status == 200
        assert "total_queries" in body
        assert "latency_ms" in body
        assert "intent_distribution" in body
    finally:
        server.shutdown()
        server.server_close()


def test_me_without_credentials_401():
    server, port = _start_server()
    try:
        status, _, _ = _request(port, "GET", "/api/auth/me")
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #
def test_login_success_returns_token_and_principal():
    server, port = _start_server()
    try:
        status, body, _ = _login(port, "bob", "bob123")
        assert status == 200
        assert body["token"]
        assert body["user"]["username"] == "bob"
        assert body["user"]["principal"] == "restricted"
        assert body["session_id"]
    finally:
        server.shutdown()
        server.server_close()


def test_login_wrong_password_401():
    server, port = _start_server()
    try:
        status, _, _ = _login(port, "bob", "wrong")
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()


def test_login_rate_limited_after_repeated_failures():
    """P0-4：连续失败触发用户名+IP 指数退避限流（429 + Retry-After）。"""
    import auth.ratelimit as ratelimit
    from web import server as web_server

    limiter = ratelimit.LoginRateLimiter(max_failures=3, base_seconds=30.0)
    # 使用独立的 limiter 实例避免污染默认单例
    original = web_server.default_login_limiter
    web_server.default_login_limiter = lambda: limiter
    server, port = _start_server()
    try:
        for _ in range(3):
            _login(port, "bob", "wrong")
        status, body, resp = _login(port, "bob", "wrong")
        assert status == 429
        assert "retry_after" in body
        assert resp.headers.get("Retry-After")
        # 其他用户名+IP 不受影响
        status2, _, _ = _login(port, "admin", "admin123")
        assert status2 == 200
    finally:
        web_server.default_login_limiter = original
        server.shutdown()
        server.server_close()


def test_login_missing_fields_400():
    server, port = _start_server()
    try:
        status, _, _ = _request(port, "POST", "/api/auth/login", {"username": "bob"})
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# 服务端强制绑定 principal：客户端 principal 一律忽略
# --------------------------------------------------------------------------- #
def test_query_ignores_client_principal(warehouse):
    """bob 登录后即使请求体里写 principal=admin，服务端仍用 restricted。"""
    server, port = _start_server()
    try:
        _, login, _ = _login(port, "bob", "bob123")
        token = login["token"]
        status, body, _ = _request(
            port,
            "POST",
            "/api/query",
            {"query": "2024年6月成功订单的GMV是多少？", "principal": "admin"},
            {"Authorization": "Bearer " + token},
        )
        assert status == 200
        assert "error" not in body
        assert body["principal"] == "restricted"
        assert body["auth"]["principal"] == "restricted"
    finally:
        server.shutdown()
        server.server_close()


def test_query_as_admin_via_jwt(warehouse):
    server, port = _start_server()
    try:
        _, login, _ = _login(port, "admin", "admin123")
        token = login["token"]
        status, body, _ = _request(
            port,
            "POST",
            "/api/query",
            {"query": "2024年6月成功订单的GMV是多少？"},
            {"Authorization": "Bearer " + token},
        )
        assert status == 200
        assert body["principal"] == "admin"
        assert body["auth"]["username"] == "admin"
        assert body["columns"] == ["gmv"]
    finally:
        server.shutdown()
        server.server_close()


def test_query_restricted_cannot_see_refund(warehouse):
    """受限主体查询退款 -> 无权错误（守卫前移拒绝，而非事后兜底）。"""
    server, port = _start_server()
    try:
        _, login, _ = _login(port, "bob", "bob123")
        token = login["token"]
        status, body, _ = _request(
            port,
            "POST",
            "/api/query",
            {"query": "各品类成功订单的退款金额是多少？"},
            {"Authorization": "Bearer " + token},
        )
        assert status == 200
        assert "error" in body
        assert "无权" in body["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_me_returns_identity(warehouse):
    server, port = _start_server()
    try:
        _, login, _ = _login(port, "analyst", "analyst123")
        token = login["token"]
        status, body, _ = _request(
            port, "GET", "/api/auth/me", headers={"Authorization": "Bearer " + token}
        )
        assert status == 200
        assert body["username"] == "analyst"
        assert body["principal"] == "analyst"
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# 会话凭证（Cookie）
# --------------------------------------------------------------------------- #
def test_query_via_session_cookie(warehouse):
    server, port = _start_server()
    try:
        _, _, resp = _login(port, "bob", "bob123")
        cookie = resp.headers.get("Set-Cookie", "").split(";")[0]
        assert cookie.startswith("session=")
        status, body, _ = _request(
            port,
            "POST",
            "/api/query",
            {"query": "2024年6月成功订单的GMV是多少？"},
            {"Cookie": cookie},
        )
        assert status == 200
        assert body["principal"] == "restricted"
        assert body["auth"]["auth_type"] == "session"
    finally:
        server.shutdown()
        server.server_close()


def test_logout_revokes_session():
    server, port = _start_server()
    try:
        _, login, _ = _login(port, "bob", "bob123")
        sid = login["session_id"]
        # 登出吊销会话（携带会话凭证）
        status, body, _ = _request(port, "POST", "/api/auth/logout", headers={"X-Session-ID": sid})
        assert status == 200
        assert body["revoked"] is True
        # 旧会话不再可用
        status, body, _ = _request(
            port,
            "GET",
            "/api/auth/me",
            headers={"X-Session-ID": sid},
        )
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# 异步查询任务：POST /api/query/async 提交 -> GET /api/tasks/<id> 轮询
# --------------------------------------------------------------------------- #
def test_async_query_flow(warehouse):
    """认证提交 -> 202 + task_id -> 轮询至 success，结果与同步链路一致。"""
    server, port = _start_server()
    try:
        _, login, _ = _login(port, "admin", "admin123")
        headers = {"Authorization": "Bearer " + login["token"]}
        status, body, _ = _request(
            port,
            "POST",
            "/api/query/async",
            {"query": "2024年6月成功订单的GMV是多少？"},
            headers,
        )
        assert status == 202
        assert body["status"] == "pending"
        task_id = body["task_id"]

        deadline = time.time() + 30
        while time.time() < deadline:
            status, snap, _ = _request(port, "GET", f"/api/tasks/{task_id}", headers=headers)
            if snap["status"] in ("success", "failed"):
                break
            time.sleep(0.1)
        assert status == 200
        assert snap["status"] == "success"
        assert snap["result"]["principal"] == "admin"
        assert snap["result"]["columns"] == ["gmv"]
    finally:
        server.shutdown()
        server.server_close()


def test_async_query_requires_auth(warehouse):
    """未认证提交异步查询 -> 401。"""
    server, port = _start_server()
    try:
        status, _, _ = _request(port, "POST", "/api/query/async", {"query": "GMV"})
        assert status == 401
        status, _, _ = _request(port, "GET", "/api/tasks/some-id")
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()


def test_async_query_unknown_task_404(warehouse):
    """认证后查询不存在的 task_id -> 404。"""
    server, port = _start_server()
    try:
        _, login, _ = _login(port, "admin", "admin123")
        status, _, _ = _request(
            port,
            "GET",
            "/api/tasks/00000000000000000000000000000000",
            headers={"Authorization": "Bearer " + login["token"]},
        )
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_async_query_missing_query_400(warehouse):
    """提交缺少 query 字段 -> 400。"""
    server, port = _start_server()
    try:
        _, login, _ = _login(port, "admin", "admin123")
        status, _, _ = _request(
            port,
            "POST",
            "/api/query/async",
            {},
            {"Authorization": "Bearer " + login["token"]},
        )
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()


def test_async_task_foreign_user_404(warehouse):
    """就绪度评审 R1：非属主轮询他人任务 -> 404（快照含查询数据，防跨用户读取）。"""
    server, port = _start_server()
    try:
        _, admin_login, _ = _login(port, "admin", "admin123")
        status, body, _ = _request(
            port,
            "POST",
            "/api/query/async",
            {"query": "2024年6月成功订单的GMV是多少？"},
            {"Authorization": "Bearer " + admin_login["token"]},
        )
        assert status == 202
        task_id = body["task_id"]
        # bob 轮询 admin 的任务 -> 404（视同不存在，不泄露任务状态）
        _, bob_login, _ = _login(port, "bob", "bob123")
        status, snap, _ = _request(
            port,
            "GET",
            f"/api/tasks/{task_id}",
            headers={"Authorization": "Bearer " + bob_login["token"]},
        )
        assert status == 404
        # admin 角色全局可见（与导出下载放行策略一致）
        status, snap, _ = _request(
            port,
            "GET",
            f"/api/tasks/{task_id}",
            headers={"Authorization": "Bearer " + admin_login["token"]},
        )
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# 导出下载属主校验（P1-3 + 就绪度评审 P3 处置）
# --------------------------------------------------------------------------- #
def _create_export(port, headers):
    """以指定身份执行一次带导出语义的查询，返回 (export_id, download_path)。"""
    status, body, _ = _request(
        port,
        "POST",
        "/api/query",
        {"query": "把上个月各省份的GMV导出成表格"},
        headers,
    )
    assert status == 200, body
    assert body.get("download_urls"), body
    path = body["download_urls"][0]
    return path.rsplit("/", 1)[-1], path


def _download(port, path, headers, timeout=10):
    """下载端点返回文件字节（非 JSON），单独封装取状态码与原始响应。"""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc


def test_export_owner_can_download(warehouse):
    """文件所有者本人下载自己的导出 -> 200。"""
    server, port = _start_server()
    try:
        _, login, _ = _login(port, "admin", "admin123")
        _, path = _create_export(port, {"Authorization": "Bearer " + login["token"]})
        status, raw, resp = _download(port, path, {"Authorization": "Bearer " + login["token"]})
        assert status == 200
        assert raw
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    finally:
        server.shutdown()
        server.server_close()


def test_export_foreign_download_403(warehouse):
    """非属主且无 admin 角色（bob/ops）下载他人导出 -> 403（旧探测式实现恒放行）。"""
    server, port = _start_server()
    try:
        _, admin_login, _ = _login(port, "admin", "admin123")
        _, path = _create_export(port, {"Authorization": "Bearer " + admin_login["token"]})
        _, bob_login, _ = _login(port, "bob", "bob123")
        status, _, _ = _download(
            port, path, {"Authorization": "Bearer " + bob_login["token"]}
        )
        assert status == 403
    finally:
        server.shutdown()
        server.server_close()


def test_export_admin_role_can_download_foreign(warehouse):
    """admin 角色可下载他人导出（显式角色判定放行）。"""
    server, port = _start_server()
    try:
        _, bob_login, _ = _login(port, "bob", "bob123")
        _, path = _create_export(port, {"Authorization": "Bearer " + bob_login["token"]})
        _, admin_login, _ = _login(port, "admin", "admin123")
        status, _, _ = _download(
            port, path, {"Authorization": "Bearer " + admin_login["token"]}
        )
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_query_shares_tool_layer_connection_pool(warehouse, monkeypatch):
    """就绪度评审 R3：service 与工具层消费同一连接池单例——未注入连接的
    DATA_QUERY 实际从 exec.pool.default_pool 取用连接（双池并存的旁路面消除）。"""
    import web.service as svc
    from exec import pool as pool_mod
    from web.service import run_query

    # service 导入的正是工具层单例工厂（同一函数对象）
    assert svc.default_pool is pool_mod.default_pool

    acquired = {"n": 0}
    real_pool = pool_mod.default_pool()

    class _CountingPool:
        def acquire(self):
            acquired["n"] += 1
            return real_pool.acquire()

        def release(self, c):
            real_pool.release(c)

    monkeypatch.setattr(pool_mod, "_default_pool", _CountingPool())
    result = run_query(
        "2024年6月成功订单的GMV是多少？", session_id="r3-pool", user="alice"
    )
    assert "error" not in result, result.get("error_detail")
    assert result["columns"] == ["gmv"]
    assert acquired["n"] == 1
