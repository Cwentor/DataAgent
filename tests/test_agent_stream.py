"""SSE 流式编排端点测试：/api/v1/agent/chat/stream 事件流契约。

验证点：
- 流头：Content-Type=text/event-stream，帧格式 ``data: <json>\\n\\n``；
- 事件序列：诊断问题 -> plan_created / step_start / tool_* / artifact_emit /
  done 全序抵达，turn_id 全局一致；
- 鉴权：未认证 401 JSON（不进入事件流）；
- HITL：hitl_request 事件携带 resume_token，答复后可恢复完成；
- 异常流：resume token 无效 -> error 事件（HTTP 200 事件流内收敛）。

网络模式与 test_web_auth 一致：显式环回地址 + http.client 固定端口连接
（目标仅限本测试启动的临时服务，不构造动态 URL）。
"""

from __future__ import annotations

import codecs
import http.client
import json
import threading

import pytest

from web.server import Handler, ThreadingHTTPServer


def _start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


def _login(port: int) -> dict:
    """登录换取 token（复用预置 admin 账号）。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(
            "POST",
            "/api/auth/login",
            body=json.dumps({"username": "admin", "password": "admin123"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        return json.loads(resp.read().decode())
    finally:
        conn.close()


def _sse_events(port: int, qs: str, token: str, timeout: float = 120.0):
    """请求 SSE 端点并解析事件帧（阻塞读至流关闭）。

    目标固定为本测试启动的 127.0.0.1 临时服务（端口来自 server_bind），
    不做任何动态 URL / 域名解析。
    """
    path = f"/api/v1/agent/chat/stream?{qs}"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Authorization": f"Bearer {token}"})
        resp = conn.getresponse()
        content_type = resp.headers.get("Content-Type", "")
        events: list[dict] = []
        buf = ""
        decoder = codecs.getincrementaldecoder("utf-8")()
        while True:
            chunk = resp.read(1024)
            if not chunk:
                break
            # 增量解码：固定字节切块可能切开多字节字符（报告含中文表格）
            buf += decoder.decode(chunk)
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                for line in frame.splitlines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))
        return content_type, events
    finally:
        conn.close()


@pytest.fixture(scope="module")
def sse_server(tmp_path_factory):
    """带鉴权的临时 web 服务（离线 LLM -> 确定性兜底路径）。"""
    from config import settings

    settings.AUTH_JWT_SECRET = "test-secret-sse-Stream-2026"
    settings.AUTH_ENABLED = True
    settings.WORKSPACE_ROOT = tmp_path_factory.mktemp("sse_ws")
    server, port = _start_server()
    token = _login(port)["token"]
    yield port, token
    server.shutdown()
    server.server_close()


def test_sse_requires_auth(sse_server):
    """未认证请求 -> 401 JSON，不进入事件流。"""
    port, _ = sse_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", "/api/v1/agent/chat/stream?query=hi")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())
        assert resp.status == 401 and body.get("error") == "unauthorized"
    finally:
        conn.close()


def test_sse_stream_contract(sse_server):
    """诊断问题：事件序列 + 帧格式 + turn_id 一致 + done 收尾。"""
    port, token = sse_server
    import urllib.parse

    qs = urllib.parse.urlencode(
        {"query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位"}
    )
    content_type, events = _sse_events(port, qs, token)
    assert content_type.startswith("text/event-stream")
    kinds = [e["event"] for e in events]
    assert "plan_created" in kinds
    assert "step_start" in kinds
    assert "tool_start" in kinds and "tool_end" in kinds
    assert kinds[-1] == "done"
    # turn_id 贯穿全部事件
    assert len({e["turn_id"] for e in events}) == 1
    # artifact_emit 含报告与图表
    artifact_types = {
        e["payload"]["artifact"]["type"] for e in events if e["event"] == "artifact_emit"
    }
    assert "markdown_report" in artifact_types and "echarts" in artifact_types


def test_sse_hitl_flow(sse_server):
    """歧义问题 -> hitl_request（含 resume_token）-> 答复恢复 -> done。"""
    port, token = sse_server
    _, events = _sse_events(port, "query=GMV%E5%91%A2%EF%BC%9F", token)
    hitl = [e for e in events if e["event"] == "hitl_request"]
    assert len(hitl) == 1
    question = hitl[0]["payload"]["hitl"]["question"]
    resume_token = hitl[0]["payload"]["hitl"]["resume_token"]
    assert question and resume_token

    import urllib.parse

    qs = urllib.parse.urlencode(
        {
            "query": "GMV呢？",
            "resume_token": resume_token,
            "human_reply": "2024 年 5 月按省份的订单金额",
        }
    )
    _, resumed = _sse_events(port, qs, token)
    assert resumed[-1]["event"] == "done"


def test_sse_invalid_resume_token(sse_server):
    """无效 resume_token -> error 事件（HTTP 200 事件流内收敛）。"""
    port, token = sse_server
    _, events = _sse_events(port, "query=GMV%E5%91%A2%EF%BC%9F&resume_token=hitl-deadbeef", token)
    assert events[-1]["event"] == "error"
    assert "resume token" in events[-1]["payload"]["error"]
