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
import json
import time

import pytest
from fastapi.testclient import TestClient

from web.api import app

_current_client = None

SSE_DELIM = chr(10) * 2  # SSE 帧分隔符（两个真实换行）


class _FakeServer:
    def shutdown(self) -> None:
        pass

    def server_close(self) -> None:
        pass


class _FakeResponse:
    """仿 http.client.HTTPResponse：status/headers/read 语义。

    read 必须尊重 n 且耗尽后返回空——否则调用方的
    "while True: chunk = resp.read(1024); if not chunk: break" 会死循环
    （M4 迁移期实测：全量重复返回导致 buf 每轮翻倍、内存爆掉）。
    """

    def __init__(self, resp) -> None:
        self.status = resp.status_code
        self.headers = resp.headers
        self._content = resp.content
        self._offset = 0

    def read(self, n: int = -1) -> bytes:
        data = self._content[self._offset :]
        if n is not None and n > 0:
            data = data[:n]
        self._offset += len(data)
        return data

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self.headers.items())


class _FakeConnection:
    """仿 http.client.HTTPConnection：M4 起底层为 TestClient（端口参数忽略）。

    每个连接实例持私有 client（无 Cookie 罐继承）——http.client 不持久化
    Cookie，未认证用例不得被 fixture 登录留下的会话 Cookie 意外放行。
    """

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self._client = TestClient(app, raise_server_exceptions=False)
        self._resp = None

    def request(self, method: str, path: str, body=None, headers=None) -> None:
        self._resp = self._client.request(method, path, content=body, headers=headers or {})

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse(self._resp)

    def close(self) -> None:
        pass


def _start_server():
    """M4 单引擎：stdlib 服务已删除，经 TestClient 驱动 FastAPI 实现
    （调用形态保持：返回 (server, port) 桩，测试体零改动）。

    每次新建 client：TestClient 持久化 Set-Cookie，共享实例会让未认证
    用例带着前序登录的会话 Cookie 被放行（实测踩坑），对齐 stdlib
    http.client 无 Cookie 持久化的旧语义。
    """
    global _current_client
    _current_client = TestClient(app, raise_server_exceptions=False)
    return _FakeServer(), 0


def _login(port: int) -> dict:
    """登录换取 token（复用预置 admin 账号）。"""
    resp = _current_client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    return resp.json()


def _sse_events(port: int, qs: str, token: str, timeout: float = 120.0):
    """请求 SSE 端点并解析事件帧（阻塞读至流关闭）。

    M4 单引擎：langgraph 计划审批门在多步计划处挂起（hitl_request 收尾）——
    对 plan_review 类挂起自动循环批准（run_id + resume_token + action 续流，
    seq 连续），"执行到底"用例语义保持不变；clarify 类挂起原样返回给用例断言。
    """
    auth = {"Authorization": f"Bearer {token}"}
    events: list[dict] = []
    content_type = ""
    run_id = ""
    current_qs = qs
    for _ in range(6):
        path = f"/api/v1/agent/chat/stream?{current_qs}"
        with _current_client.stream("GET", path, headers=auth) as resp:
            content_type = resp.headers.get("content-type", "")
            if resp.headers.get("x-run-id"):
                run_id = resp.headers["x-run-id"]
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                while SSE_DELIM in buf:
                    frame, buf = buf.split(SSE_DELIM, 1)
                    for line in frame.splitlines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[len("data: ") :]))
        if not events or events[-1]["event"] != "hitl_request":
            break
        if events[-1]["payload"].get("kind") != "plan_review":
            break  # clarify 挂起：交由用例断言
        resume_token = events[-1]["payload"]["hitl"]["resume_token"]
        current_qs = (
            f"run_id={run_id}&resume_token={resume_token}"
            f"&action=approve&after={events[-1]['seq']}"
        )
    return content_type, events


@pytest.fixture(scope="module")
def sse_server(tmp_path_factory):
    """带鉴权的临时 web 服务（离线 LLM -> 确定性兜底路径）。"""
    from config import settings

    settings.AUTH_JWT_SECRET = "test-secret-sse-Stream-2026"
    settings.AUTH_ENABLED = True
    settings.WORKSPACE_ROOT = tmp_path_factory.mktemp("sse_ws")
    server, port = _start_server()
    print(f"[FIXTURE] AUTH_ENABLED={settings.AUTH_ENABLED}", flush=True)
    token = _login(port)["token"]
    yield port, token
    server.shutdown()
    server.server_close()


def test_sse_requires_auth(sse_server):
    """未认证请求 -> 401 JSON，不进入事件流。"""
    port, _ = sse_server
    conn = _FakeConnection("127.0.0.1", port, timeout=5)
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


def test_sse_hitl_flow(sse_server, planner_clarify_then_plan):
    """歧义问题 -> hitl_request（含 resume_token）-> 答复恢复 -> done。

    十八期：clarify 触发改为 mock Planner clarification（离线字符规则退役）。
    """
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


# --------------------------------------------------------------------------- #
# 并行会话（run 注册表版端点）：断线重放 / 游标续订 / 隔离 / 状态快照 / 多轮记忆
# --------------------------------------------------------------------------- #
def _parse_headers_events(port: int, qs: str, token: str, timeout: float = 120.0):
    """请求 SSE 端点：返回 (响应头, 事件列表)。

    与 _sse_events 相同的 plan_review 自动批准循环（多步计划挂起时续流至收敛）。
    """
    auth = {"Authorization": f"Bearer {token}"}
    headers: dict = {}
    events: list[dict] = []
    run_id = ""
    current_qs = qs
    for _ in range(6):
        path = f"/api/v1/agent/chat/stream?{current_qs}"
        conn = _FakeConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("GET", path, headers=auth)
            resp = conn.getresponse()
            headers = dict(resp.getheaders())
            # TestClient 头为小写键：补一个原样键供用例按 "X-Run-Id" 读取
            if resp.headers.get("x-run-id"):
                headers["X-Run-Id"] = resp.headers["x-run-id"]
                run_id = resp.headers["x-run-id"]
            buf = ""
            decoder = codecs.getincrementaldecoder("utf-8")()
            while True:
                chunk = resp.read(1024)
                if not chunk:
                    break
                buf += decoder.decode(chunk)
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    for line in frame.splitlines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[len("data: ") :]))
        finally:
            conn.close()
        if not events or events[-1]["event"] != "hitl_request":
            break
        if events[-1]["payload"].get("kind") != "plan_review":
            break  # clarify 挂起：交由用例断言
        resume_token = events[-1]["payload"]["hitl"]["resume_token"]
        current_qs = (
            f"run_id={run_id}&resume_token={resume_token}"
            f"&action=approve&after={events[-1]['seq']}"
        )
    return headers, events


def test_sse_replay_after_disconnect(sse_server):
    """断开后 run 继续完成：先启动（读两帧即断），再全量重放拿到完整 done 流。"""
    port, token = sse_server
    import urllib.parse

    qs = urllib.parse.urlencode(
        {
            "query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
            "thread": "t-replay",
        }
    )
    # 首连：读到 plan_created 即断开（模拟用户切走会话）
    path = f"/api/v1/agent/chat/stream?{qs}"
    conn = _FakeConnection("127.0.0.1", port, timeout=120)
    run_id = ""
    try:
        conn.request("GET", path, headers={"Authorization": f"Bearer {token}"})
        resp = conn.getresponse()
        run_id = resp.getheader("X-Run-Id") or ""
        assert run_id, "响应头必须登记 X-Run-Id"
        buf = ""
        decoder = codecs.getincrementaldecoder("utf-8")()
        while True:
            chunk = resp.read(256)
            if not chunk:
                break
            buf += decoder.decode(chunk)
            if "plan_created" in buf:
                break
    finally:
        conn.close()
    assert run_id

    # 后台 run 继续执行；随后从 seq 0 全量重放应得到完整事件（含 done）
    deadline = time.time() + 120
    replay: list[dict] = []
    while time.time() < deadline:
        _, replay = _parse_headers_events(port, f"run_id={run_id}&after=0", token)
        if replay and replay[-1]["event"] == "done":
            break
        time.sleep(0.5)
    assert replay and replay[-1]["event"] == "done"
    seqs = [e["seq"] for e in replay]
    assert seqs == sorted(seqs) and seqs[0] == 1


def test_sse_cursor_resume_no_overlap(sse_server):
    """中途游标续订：after=<已见 seq> 只收增量，无重复无缺失。"""
    port, token = sse_server
    import urllib.parse

    qs = urllib.parse.urlencode(
        {
            "query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
            "thread": "t-cursor",
        }
    )
    headers, events = _parse_headers_events(port, qs, token)
    run_id = headers.get("X-Run-Id") or ""
    assert run_id and events[-1]["event"] == "done"

    mid = len(events) // 2
    _, tail = _parse_headers_events(port, f"run_id={run_id}&after={events[mid - 1]['seq']}", token)
    assert [e["seq"] for e in tail] == [e["seq"] for e in events[mid:]]


def test_sse_parallel_threads_isolated(sse_server):
    """两个会话并行执行：事件互不串台，各自 turn_id 一致且互不相同。"""
    import urllib.parse

    port, token = sse_server
    qs1 = urllib.parse.urlencode({"query": "2024年5月北京的GMV是多少", "thread": "t-pa"})
    qs2 = urllib.parse.urlencode({"query": "2024年5月各省份的订单量是多少", "thread": "t-pb"})
    _, ev1 = _parse_headers_events(port, qs1, token)
    _, ev2 = _parse_headers_events(port, qs2, token)
    assert ev1[-1]["event"] == "done" and ev2[-1]["event"] == "done"
    t1 = {e["turn_id"] for e in ev1}
    t2 = {e["turn_id"] for e in ev2}
    assert len(t1) == 1 and len(t2) == 1 and t1 != t2


def test_sse_run_status_endpoint(sse_server):
    """GET /api/v1/agent/runs/<id>：属主可见；他人 404；resume_token 不外泄。"""
    port, token = sse_server
    import urllib.parse

    qs = urllib.parse.urlencode({"query": "2024年5月北京的GMV是多少", "thread": "t-status"})
    headers, events = _parse_headers_events(port, qs, token)
    run_id = headers.get("X-Run-Id") or ""
    assert run_id and events[-1]["event"] == "done"

    # 属主查询
    conn = _FakeConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(
            "GET", f"/api/v1/agent/runs/{run_id}", headers={"Authorization": f"Bearer {token}"}
        )
        resp = conn.getresponse()
        snap = json.loads(resp.read().decode())
        assert resp.status == 200 and snap["run_id"] == run_id
        assert snap["status"] == "done"
        assert "resume_token" not in snap
    finally:
        conn.close()

    # 他人查询（登录 analyst 账号；admin 预置账号可见属主豁免，故用非 admin）
    conn2 = _FakeConnection("127.0.0.1", port, timeout=10)
    try:
        conn2.request(
            "POST",
            "/api/auth/login",
            body=json.dumps({"username": "analyst", "password": "analyst123"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp2 = conn2.getresponse()
        if resp2.status == 200:
            other_token = json.loads(resp2.read().decode())["token"]
            conn2.request(
                "GET",
                f"/api/v1/agent/runs/{run_id}",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            resp3 = conn2.getresponse()
            assert resp3.status == 404
        else:
            # 环境无 analyst 账号：属主隔离由 test_web_runs 覆盖
            pass
    finally:
        conn2.close()

    # 未知 run_id -> 404
    conn4 = _FakeConnection("127.0.0.1", port, timeout=10)
    try:
        conn4.request(
            "GET", "/api/v1/agent/runs/run-deadbeef", headers={"Authorization": f"Bearer {token}"}
        )
        assert conn4.getresponse().status == 404
    finally:
        conn4.close()


def test_sse_multi_turn_memory_inheritance(sse_server):
    """同 thread 两轮追问：第二轮 history_digest 装载（服务端记忆可见）。"""
    import urllib.parse

    from agent.memory import default_session_store

    port, token = sse_server
    qs1 = urllib.parse.urlencode({"query": "2024年5月北京的GMV是多少", "thread": "t-mem"})
    _, ev1 = _parse_headers_events(port, qs1, token)
    assert ev1[-1]["event"] == "done"

    # 服务端会话记忆已回写首轮 user/assistant（第二轮规划的语境来源）
    state = default_session_store().get("webui:t-mem", "admin")
    assert state is not None
    roles = [m.role for m in state.history]
    assert "user" in roles and "assistant" in roles

    qs2 = urllib.parse.urlencode({"query": "那上海2024年5月的GMV是多少", "thread": "t-mem"})
    _, ev2 = _parse_headers_events(port, qs2, token)
    assert ev2[-1]["event"] == "done"
    # 第二轮 turn_id 与第一轮不同（独立 run/轮次）
    assert {e["turn_id"] for e in ev1}.isdisjoint({e["turn_id"] for e in ev2})
