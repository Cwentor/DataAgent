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
    # M4 单引擎（langgraph）：多步计划触发 plan_review 审批门——循环批准直至收敛
    body = None
    for _ in range(6):
        resp = client.post(
            "/api/agent/run",
            json={"query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位"},
        )
        assert resp.status_code == 200
        body = resp.json()
        if body.get("phase") == "plan_review":
            resp = client.post(
                "/api/agent/run",
                json={
                    "query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
                    "resume_token": body["resume_token"],
                    "action": "approve",
                },
            )
            body = resp.json()
        elif body.get("phase") == "clarify":
            resp = client.post(
                "/api/agent/run",
                json={
                    "query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
                    "resume_token": body["resume_token"],
                    "human_reply": "按 2024-06 口径",
                },
            )
            body = resp.json()
        else:
            break
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

    def _collect(params):
        got = []
        with client.stream("GET", "/api/v1/agent/chat/stream", params=params) as r:
            assert r.status_code == 200
            rid = r.headers.get("x-run-id")
            assert rid and rid.startswith("run-")
            assert r.headers["content-type"].startswith("text/event-stream")
            for line in r.iter_lines():
                if line.startswith("data: "):
                    got.append(jsonlib.loads(line[6:]))
        return rid, got

    # M4 单引擎：多步计划触发 plan_review——循环批准直至 done
    run_id, frames = _collect(
        {
            "query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
            "thread": "t-sse",
        }
    )
    all_frames = list(frames)
    for _ in range(6):
        if all_frames and all_frames[-1]["event"] == "done":
            break
        hitl = [f for f in all_frames if f["event"] == "hitl_request"][-1]
        token = hitl["payload"]["hitl"]["resume_token"]
        action = "approve"
        run_id, seg = _collect(
            {"resume_token": token, "action": action, "run_id": run_id, "after": hitl["seq"]}
        )
        all_frames.extend(seg)
    frames = all_frames
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


def test_langgraph_engine_web_resume_roundtrip(client, monkeypatch, tmp_path):
    """web 层 LangGraph 引擎贯通：SSE 挂起 -> resume_token+human_reply -> 续跑。

    registry.resume 走 run_agent(resume_state=...) -> resume_langgraph(Command)，
    seq 连续且终帧正常收敛（done / 终态收尾）。
    """
    import json as jsonlib

    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    with client.stream(
        "GET",
        "/api/v1/agent/chat/stream",
        params={"query": "什么是销售额？", "thread": "t-lg", "autonomy_level": "L2"},
    ) as resp:
        run_id = resp.headers.get("x-run-id", "")
        frames = [jsonlib.loads(ln[6:]) for ln in resp.iter_lines() if ln.startswith("data: ")]
    assert frames and frames[-1]["event"] == "hitl_request"
    token = frames[-1]["payload"]["hitl"]["resume_token"]

    with client.stream(
        "GET",
        "/api/v1/agent/chat/stream",
        params={
            "resume_token": token,
            "human_reply": "按 2024-06 口径，看 GMV",
            "run_id": resp.headers.get("x-run-id", ""),
        },
    ) as resp2:
        frames2 = [jsonlib.loads(ln[6:]) for ln in resp2.iter_lines() if ln.startswith("data: ")]
    assert frames2, "resume 流必须有事件"
    # seq 连续（同一 run 续写）
    all_seqs = [f["seq"] for f in frames] + [f["seq"] for f in frames2]
    assert all_seqs == sorted(all_seqs)
    # clarify 恢复后若产出多步计划 -> 再遇 plan_review 门（M2）：批准后收敛
    if frames2[-1]["event"] == "hitl_request":
        assert frames2[-1]["payload"]["kind"] == "plan_review"
        token2 = frames2[-1]["payload"]["hitl"]["resume_token"]
        with client.stream(
            "GET",
            "/api/v1/agent/chat/stream",
            params={"resume_token": token2, "action": "approve", "run_id": run_id},
        ) as resp3:
            frames3 = [
                jsonlib.loads(ln[6:]) for ln in resp3.iter_lines() if ln.startswith("data: ")
            ]
        assert frames3[-1]["event"] == "done"
    else:
        assert frames2[-1]["event"] in {"done", "error"}


# ---------------------------------------------------------------------------
# Task 11：hitl kind 透传 + resume action 贯通 web 层
# ---------------------------------------------------------------------------


def test_hitl_payload_kind_clarify(client, monkeypatch, tmp_path):
    """clarify 挂起的 hitl_request payload 带 kind=clarify（向后兼容扩展）。"""
    import json as jsonlib

    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    with client.stream(
        "GET",
        "/api/v1/agent/chat/stream",
        params={"query": "什么是销售额？", "thread": "t-kind", "autonomy_level": "L2"},
    ) as resp:
        frames = [jsonlib.loads(ln[6:]) for ln in resp.iter_lines() if ln.startswith("data: ")]
    hitl = [f for f in frames if f["event"] == "hitl_request"][-1]
    assert hitl["payload"]["kind"] == "clarify"
    assert hitl["payload"]["hitl"]["resume_token"]


def test_plan_review_stream_pause_and_reject(client, monkeypatch, tmp_path):
    """诊断式问题触发 plan_review 审批门：payload 带 kind+plan_steps；
    resume action=reject 终止且不产出（诚实守卫）。"""
    import json as jsonlib

    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    with client.stream(
        "GET",
        "/api/v1/agent/chat/stream",
        params={
            "query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
            "thread": "t-pr",
            "autonomy_level": "L2",
        },
    ) as resp:
        frames = [jsonlib.loads(ln[6:]) for ln in resp.iter_lines() if ln.startswith("data: ")]
    hitl = [f for f in frames if f["event"] == "hitl_request"][-1]
    assert hitl["payload"]["kind"] == "plan_review"
    assert len(hitl["payload"]["hitl"]["plan_steps"]) > 1
    token = hitl["payload"]["hitl"]["resume_token"]
    run_id = resp.headers.get("x-run-id", "")

    with client.stream(
        "GET",
        "/api/v1/agent/chat/stream",
        params={"resume_token": token, "action": "reject", "run_id": run_id},
    ) as resp2:
        frames2 = [jsonlib.loads(ln[6:]) for ln in resp2.iter_lines() if ln.startswith("data: ")]
    assert frames2[-1]["event"] == "done"
    report = frames2[-1]["payload"].get("report") or ""
    assert "拒绝" in report or frames2[-1]["payload"].get("artifacts") == []


def test_plan_review_stream_edit_triggers_replan(client, monkeypatch, tmp_path):
    """resume action=edit：重规划发生（plan_created 再现）且新计划体现用户指令。"""
    import json as jsonlib

    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    with client.stream(
        "GET",
        "/api/v1/agent/chat/stream",
        params={
            "query": "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
            "thread": "t-pe",
            "autonomy_level": "L2",
        },
    ) as resp:
        frames = [jsonlib.loads(ln[6:]) for ln in resp.iter_lines() if ln.startswith("data: ")]
    hitl = [f for f in frames if f["event"] == "hitl_request"][-1]
    token = hitl["payload"]["hitl"]["resume_token"]
    run_id = resp.headers.get("x-run-id", "")

    with client.stream(
        "GET",
        "/api/v1/agent/chat/stream",
        params={
            "resume_token": token,
            "action": "edit",
            "instruction": "只看华东区域",
            "run_id": run_id,
        },
    ) as resp2:
        frames2 = [jsonlib.loads(ln[6:]) for ln in resp2.iter_lines() if ln.startswith("data: ")]
    kinds = [f["event"] for f in frames2]
    assert kinds.count("plan_created") >= 1  # 重规划发生
    # L2 一次性审批：edit 后重规划执行自动，不再二次审批，直接跑到 done
    assert frames2[-1]["event"] == "done"
