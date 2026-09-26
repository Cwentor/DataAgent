"""FastAPI 端点与 stdlib 实现的契约等价测试（M1 Task 6/7/8）。

迁移纪律：响应字段集/错误体/状态码照抄 stdlib（web/server.py）；
本文件锁定契约，防止 FastAPI 平移时增删字段或改变错误语义。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from web.api import app

    return TestClient(app, raise_server_exceptions=False)


def test_health_matches_stdlib_contract(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_field_set_frozen(client):
    """字段集快照锁：防止 FastAPI 平移时增删响应字段。

    本步骤执行时先起一次 stdlib 服务（WEB_SERVER_ENGINE=stdlib python -m web.server）
    并 `curl /api/health`，把实测字段集逐字写入 expected_fields，再跑本测试。
    """
    expected_fields = {"status"}  # ← 以 stdlib 实测字段集逐字修正
    assert set(client.get("/api/health").json()) == expected_fields


def test_query_endpoint_auth_enforced(client):
    # 认证失败必须 401 且错误体与 stdlib 一致（{"error": ...}，非 FastAPI 默认 detail）
    resp = client.post("/api/query", json={"query": "上月销售额"})
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_metrics_requires_auth(client):
    assert client.get("/api/metrics").status_code == 401


def test_schema_summary_requires_auth(client):
    assert client.get("/api/schema/summary").status_code == 401


def test_unknown_route_matches_stdlib_error_body(client):
    # stdlib 未匹配路由统一 {"error": "not found"} 404，而非 FastAPI 默认 {"detail": "Not Found"}
    resp = client.get("/api/definitely/not/exist")
    assert resp.status_code == 404
    assert resp.json() == {"error": "not found"}


def test_request_id_header_present(client):
    # stdlib _send_json 每个响应都带 X-Request-ID（结构化日志贯穿）
    resp = client.get("/api/health")
    assert resp.headers.get("x-request-id")


def test_invalid_json_body_matches_stdlib_400(client, monkeypatch):
    # stdlib _read_body 解析失败返回 {"error": "invalid json"} 400（非 FastAPI 422）
    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    resp = client.post(
        "/api/query",
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid json"}


def test_query_missing_field_matches_stdlib_400(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    resp = client.post("/api/query", json={"query": ""})
    assert resp.status_code == 400
    assert resp.json() == {"error": "query is required"}


# ---------------------------------------------------------------------------
# Task 7：agent 端点平移 + SSE StreamingResponse（RunRegistry 架构）
# ---------------------------------------------------------------------------


def test_agent_run_sync_endpoint_contract(client, monkeypatch, tmp_path):
    """POST /api/agent/run 同步编排：done 路径返回 AgentTrace 契约字段。"""
    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    resp = client.post(
        "/api/agent/run",
        json={"query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["phase"] == "done"
    assert {"report", "steps", "artifacts", "self_heal_count"} <= set(body)


def test_agent_run_missing_query_400(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    resp = client.post("/api/agent/run", json={"query": ""})
    assert resp.status_code == 400
    assert resp.json() == {"error": "query is required"}


def test_agent_stream_frame_format_and_x_run_id(client, monkeypatch, tmp_path):
    """SSE：X-Run-Id 响应头 + data: <json>\n\n 帧格式 + 终帧 done + 游标增量。"""
    import json as jsonlib

    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    with client.stream(
        "GET",
        "/api/v1/agent/chat/stream",
        # 明确问题（含指标词+时间锚）直通 done；歧义问题会以 hitl_request 挂起收尾
        params={
            "query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
            "thread": "t-sse",
        },
    ) as resp:
        assert resp.status_code == 200
        run_id = resp.headers.get("x-run-id")
        assert run_id and run_id.startswith("run-")
        assert resp.headers["content-type"].startswith("text/event-stream")
        frames = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                frames.append(jsonlib.loads(line[6:]))
    assert frames, "expected SSE data frames"
    assert all(isinstance(f.get("seq"), int) for f in frames), "帧须带单调 seq 游标"
    assert frames[-1]["event"] == "done"

    # 游标重放：after=最后 seq 只回增量（终态 run 重放缓冲尾部，不整轮重发）
    last_seq = frames[-1]["seq"]
    replay = client.get("/api/v1/agent/chat/stream", params={"run_id": run_id, "after": last_seq})
    assert replay.status_code == 200
    assert replay.headers.get("x-run-id") == run_id
    body = replay.text
    for f in frames:
        assert f"data: {jsonlib.dumps(f, ensure_ascii=False)}\n\n" not in body


def test_agent_stream_unknown_run_404(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    resp = client.get("/api/v1/agent/chat/stream", params={"run_id": "run-does-not-exist"})
    assert resp.status_code == 404
    assert resp.json() == {"error": "run not found"}


def test_agent_run_status_snapshot_contract(client, monkeypatch, tmp_path):
    """GET /api/v1/agent/runs/<id>：属主 fail-closed + 快照不含 resume_token。"""

    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    with client.stream(
        "GET",
        "/api/v1/agent/chat/stream",
        params={"query": "上个月的GMV是多少？", "thread": "t-status"},
    ) as resp:
        run_id = resp.headers.get("x-run-id")
        list(resp.iter_lines())
    resp = client.get(f"/api/v1/agent/runs/{run_id}")
    assert resp.status_code == 200
    snap = resp.json()
    assert "resume_token" not in snap
    resp = client.get("/api/v1/agent/runs/run-unknown0000")
    assert resp.status_code == 404
