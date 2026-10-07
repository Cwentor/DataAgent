"""编排路由审计接入测试（P3 审计闭环）：/api/agent/run done 路径落 audit.jsonl。"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from web.api import app

    return TestClient(app, raise_server_exceptions=False)


def test_agent_run_writes_audit_record(client, monkeypatch, tmp_path):
    """POST /api/agent/run done 路径落 audit.jsonl，含 answered_by 字段。"""
    from audit.store import AuditStore
    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    jsonl = tmp_path / "audit.jsonl"

    def _fake_store() -> AuditStore:
        return AuditStore(jsonl_path=jsonl)

    monkeypatch.setattr("web.api._default_audit_store", _fake_store)

    resp = client.post("/api/agent/run", json={"query": "列举全部省份"})
    assert resp.status_code == 200
    assert resp.json()["phase"] == "done"

    lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["answered_by"] == "enumeration"
    assert rec["prompt"] == "列举全部省份"


def test_agent_run_audit_failure_does_not_break_main(client, monkeypatch):
    """RF4：审计写入失败时 /api/agent/run 仍返回 200，不影响主链路。"""
    from audit.store import AuditStore
    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)

    def _boom(self, record):
        raise OSError("disk full")

    monkeypatch.setattr(AuditStore, "write", _boom)
    monkeypatch.setattr("web.api._default_audit_store", lambda: AuditStore())

    resp = client.post("/api/agent/run", json={"query": "列举全部省份"})
    assert resp.status_code == 200
