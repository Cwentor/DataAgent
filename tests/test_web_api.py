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
