"""语义目录 Web API 测试（issue #27）：GET / 试校验 / PUT+PATCH 受控写入。

覆盖设计 §8 清单：鉴权与角色门、白名单（只读节/未知节拒绝且不静默剥离）、
乐观锁（version 必填 / 409 冲突）、校验失败不落盘、PUT 节替换 / PATCH null 删键、
热重载生效、审计 catalog_change diff 落 JSONL。
写入测试经 overlay_isolated 夹具把 overlay 指到临时副本，绝不触碰真实
config/semantic.json；退出时从真实路径重建目录全局。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import web.catalog_api as catalog_api
from semantic import catalog, catalog_loader
from semantic.catalog_loader import DEFAULT_OVERLAY_PATH as REAL_OVERLAY_PATH
from web.api import app
from web.service import ensure_db


@pytest.fixture(scope="module")
def warehouse():
    """确保文件数仓存在（写入热重载链路按 settings.DB_PATH 读物理元数据）。"""
    ensure_db()


_shared_client: TestClient | None = None


def _get_client() -> TestClient:
    """模块级单例 TestClient（登录辅助与测试夹具共用，数仓先行就绪）。"""
    global _shared_client
    if _shared_client is None:
        ensure_db()
        _shared_client = TestClient(app, raise_server_exceptions=False)
    return _shared_client


@pytest.fixture(scope="module")
def client(warehouse):
    return _get_client()


def _login(username: str, password: str) -> str:
    resp = _get_client().post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.fixture(scope="module")
def admin_token(client):
    return _login("admin", "admin123")


@pytest.fixture(scope="module")
def ops_token(client):
    return _login("bob", "bob123")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def overlay_isolated(tmp_path, monkeypatch):
    """overlay 文件 -> 临时副本（两个命名空间同步 patch，refresh_catalog 读后者）。"""
    tmp = tmp_path / "semantic.json"
    tmp.write_bytes(REAL_OVERLAY_PATH.read_bytes())
    monkeypatch.setattr(catalog_api, "DEFAULT_OVERLAY_PATH", tmp)
    monkeypatch.setattr(catalog_loader, "DEFAULT_OVERLAY_PATH", tmp)
    yield tmp
    monkeypatch.undo()
    catalog_loader.refresh_catalog()  # 从真实路径重建目录全局，杜绝跨测试泄漏


# --------------------------------------------------------------------------- #
# GET /api/catalog
# --------------------------------------------------------------------------- #
def test_get_catalog_requires_auth(client):
    assert client.get("/api/catalog").status_code == 401


def test_get_catalog_shape(client, admin_token):
    resp = client.get("/api/catalog", headers=_auth(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["version"]) == 12
    assert "value_labels" in body["editable_sections"]
    assert "fields" in body["readonly_sections"]
    assert body["data"]["fact_table"]
    assert body["version"] == catalog_api.catalog_version()


def test_get_catalog_version_tracks_file_change(client, admin_token, overlay_isolated):
    """手改文件后 GET 版本哈希自动变化（双入口乐观锁失效语义）。"""
    before = client.get("/api/catalog", headers=_auth(admin_token)).json()["version"]
    overlay_isolated.write_bytes(
        overlay_isolated.read_bytes().replace(
            b'"fact_table": "order_detail"', b'"fact_table": "order_detail" '
        )
    )
    after = client.get("/api/catalog", headers=_auth(admin_token)).json()["version"]
    assert before != after


# --------------------------------------------------------------------------- #
# POST /api/catalog/validate（试校验不落盘）
# --------------------------------------------------------------------------- #
def test_validate_requires_admin(client, ops_token):
    resp = client.post(
        "/api/catalog/validate",
        json={"sections": {"table_labels": {"order_detail": "订单明细表"}}},
        headers=_auth(ops_token),
    )
    assert resp.status_code == 403


def test_validate_ok_with_diff_and_impact(client, admin_token):
    resp = client.post(
        "/api/catalog/validate",
        json={
            "patch": True,
            "sections": {"value_labels": {"order_status": {"99": "测试状态"}}},
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["errors"] == []
    diff = body["diff"][0]
    assert diff["section"] == "value_labels"
    assert diff["updated"] == ["order_status"]
    assert body["impact"] and any("澄清" in hint for hint in body["impact"])


def test_validate_rejects_readonly_section(client, admin_token):
    resp = client.post(
        "/api/catalog/validate",
        json={"sections": {"fields": {}}},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 400
    assert "只读" in resp.json()["error"]


def test_validate_rejects_unknown_section(client, admin_token):
    resp = client.post(
        "/api/catalog/validate",
        json={"sections": {"no_such_section": {}}},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 400
    assert "未知节" in resp.json()["error"]


def test_validate_reports_breaking_alias(client, admin_token):
    resp = client.post(
        "/api/catalog/validate",
        json={"sections": {"aliases": {"ghost_table": "g"}}},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["valid"] is False
    assert any("ghost_table" in err for err in body["errors"])


def test_validate_version_conflict(client, admin_token):
    resp = client.post(
        "/api/catalog/validate",
        json={"version": "000000000000", "sections": {"table_labels": {}}},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 409
    assert resp.json()["current_version"] == catalog_api.catalog_version()


# --------------------------------------------------------------------------- #
# PUT / PATCH /api/catalog（受控写入）
# --------------------------------------------------------------------------- #
def test_put_requires_version(client, admin_token, overlay_isolated):
    resp = client.put(
        "/api/catalog",
        json={"sections": {"table_labels": {"order_detail": "订单明细表"}}},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 400
    assert "version" in resp.json()["error"]


def test_put_conflict_on_stale_version(client, admin_token, overlay_isolated):
    resp = client.put(
        "/api/catalog",
        json={"version": "000000000000", "sections": {"table_labels": {}}},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 409
    assert resp.json()["current_version"] == catalog_api.catalog_version()


def test_put_replaces_section_and_reloads(client, admin_token, overlay_isolated):
    current = client.get("/api/catalog", headers=_auth(admin_token)).json()
    labels = dict(current["data"]["table_labels"])
    key0 = sorted(labels)[0]
    labels[key0] = "测试表标签"
    resp = client.put(
        "/api/catalog",
        json={"version": current["version"], "sections": {"table_labels": labels}},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] != current["version"]
    assert body["diff"][0]["updated"] == [key0]
    # 文件已原子替换 + 目录热重载生效
    on_disk = json.loads(overlay_isolated.read_text(encoding="utf-8"))
    assert on_disk["table_labels"][key0] == "测试表标签"
    assert catalog.TABLE_LABELS[key0] == "测试表标签"


def test_patch_deep_merges_and_null_deletes(client, admin_token, overlay_isolated):
    current = client.get("/api/catalog", headers=_auth(admin_token)).json()
    status_labels = current["data"]["value_labels"]["order_status"]
    victim = sorted(status_labels)[0]
    kept = [k for k in status_labels if k != victim]
    resp = client.patch(
        "/api/catalog",
        json={
            "version": current["version"],
            "sections": {"value_labels": {"order_status": {victim: None, "98": "补录状态"}}},
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text
    on_disk = json.loads(overlay_isolated.read_text(encoding="utf-8"))
    assert victim not in on_disk["value_labels"]["order_status"]
    assert on_disk["value_labels"]["order_status"]["98"] == "补录状态"
    # 未触及的枚举键保留（deep merge 语义）
    assert set(kept) <= set(on_disk["value_labels"]["order_status"])
    assert catalog.VALUE_LABELS["order_status"].get("98") == "补录状态"
    assert victim not in catalog.VALUE_LABELS["order_status"]


def test_write_rejected_leaves_file_and_catalog_intact(client, admin_token, overlay_isolated):
    before_bytes = overlay_isolated.read_bytes()
    before_aliases = dict(catalog.ALIASES)
    resp = client.put(
        "/api/catalog",
        json={
            "version": catalog_api.catalog_version(before_bytes),
            "sections": {"aliases": {"ghost_table": "g"}},
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 400
    assert overlay_isolated.read_bytes() == before_bytes
    assert dict(catalog.ALIASES) == before_aliases


def test_write_audit_records_catalog_change(
    client, admin_token, overlay_isolated, tmp_path, monkeypatch
):
    """成功写入 -> audit.jsonl 落 catalog_change diff（JSONL 通道断言）。"""
    from audit.store import AuditStore

    audit_jsonl = tmp_path / "audit.jsonl"
    monkeypatch.setattr(
        "web.service._default_store", AuditStore(jsonl_path=audit_jsonl, db_path=None)
    )
    current = client.get("/api/catalog", headers=_auth(admin_token)).json()
    resp = client.patch(
        "/api/catalog",
        json={
            "version": current["version"],
            "sections": {"table_labels": {"order_detail": "订单明细宽表"}},
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text
    lines = [json.loads(line) for line in audit_jsonl.read_text(encoding="utf-8").splitlines()]
    change = [entry["catalog_change"] for entry in lines if entry.get("catalog_change")]
    assert change, "审计 JSONL 未记录 catalog_change"
    entry = change[-1]
    assert entry["action"] == "patch"
    assert entry["user"] == "admin"
    assert entry["version_before"] == current["version"]
    assert entry["version_after"] == resp.json()["version"]
    assert entry["sections"][0]["section"] == "table_labels"
