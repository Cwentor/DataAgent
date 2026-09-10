"""Data Agent 端到端验证套件（需求 §4 步骤 4）。

验收断言：
1. 复杂诊断问题（GMV 两期对比 + 地区/品类定位）产出：多步执行日志、
   数据集（ParquetRef）、归因摘要（summary）、可渲染 ECharts 规格；
2. 自愈回路：沙箱首次失败 -> error_context 记录 -> 重规划 -> 二次成功，
   trace.self_heal_count 与自愈日志可观测；
3. Web 全流程：POST /api/agent/run -> clarify 中断（resume_token）->
   答复恢复 -> done（报告 + 产物），鉴权与属主校验生效。

全部走确定性兜底规划（离线可复现，不依赖外部 LLM）。
"""

from __future__ import annotations

import http.client
import json
import threading

from config import settings


# --------------------------------------------------------------------------- #
# 1) 编排器 E2E：诊断 + 自愈
# --------------------------------------------------------------------------- #
def test_diagnostic_full_pipeline(tmp_path, monkeypatch):
    """GMV 下滑诊断：取数 -> 沙箱归因 -> 报告，含 ECharts 与多步日志。"""
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    from core.orchestrator.agent import run_agent

    trace = run_agent(
        "分析 2024 年 5 月第一周相对第二周 GMV 下滑的原因，按地区和品类定位", session_id="diag"
    )
    assert trace.phase == "done"
    # 多步日志：query 取数 + run_code 沙箱 至少各一次，且全部成功
    tools = [s["tool"] for s in trace.steps if s["ok"]]
    assert "execute_dsl_query" in tools and "run_code" in tools
    # 数据集与产物
    assert trace.artifacts, "必须有分析产物"
    summary_artifacts = [a for a in trace.artifacts if a["kind"] == "summary"]
    echarts_artifacts = [a for a in trace.artifacts if a["kind"] == "echarts"]
    assert summary_artifacts and echarts_artifacts
    chart = echarts_artifacts[0]["payload"]
    assert chart.get("series") and chart["series"][0].get("type") in ("bar", "line")
    # 报告含归因结论文本与执行统计
    assert "分析报告" in trace.report
    assert "关键指标" in trace.report or "归因结论" in trace.report


def test_self_heal_loop_recovers(tmp_path, monkeypatch):
    """沙箱首次失败 -> 记录错误 -> 重规划 -> 二次成功（自愈可观测）。"""
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    from core.orchestrator import nodes
    from core.sandbox.api import SandboxResult

    real_run_code = nodes.run_code
    calls = {"n": 0}

    def flaky_run_code(code, workspace, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return SandboxResult(
                ok=False, backend="subprocess", duration_ms=1.0, error="注入的一次性失败"
            )
        return real_run_code(code, workspace, **kwargs)

    monkeypatch.setattr(nodes, "run_code", flaky_run_code)
    from core.orchestrator.agent import run_agent

    trace = run_agent("分析 2024 年 5 月 GMV 波动原因", session_id="heal")
    assert trace.phase == "done"
    assert trace.self_heal_count >= 1
    assert any((s.get("error") or "").find("注入的一次性失败") >= 0 for s in trace.steps)
    assert any(s["ok"] and s["tool"] == "run_code" for s in trace.steps)
    assert calls["n"] >= 2


def test_self_heal_gives_up_after_cap(tmp_path, monkeypatch):
    """重试耗尽：MAX_RETRIES 次失败后如实放弃（不吞错、不编造）。"""
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    from core.orchestrator import nodes
    from core.sandbox.api import SandboxResult

    def always_fail(code, workspace, **kwargs):
        return SandboxResult(ok=False, backend="subprocess", duration_ms=1.0, error="持续失败")

    monkeypatch.setattr(nodes, "run_code", always_fail)
    from core.orchestrator.agent import run_agent

    trace = run_agent("分析 2024 年 5 月 GMV 波动原因", session_id="giveup")
    assert trace.phase == "done"  # 终态（不悬挂）
    assert trace.self_heal_count >= 1
    assert "未能获得有效" in trace.report or "关键指标" not in trace.report


# --------------------------------------------------------------------------- #
# 2) Web 全流程（HTTP）
# --------------------------------------------------------------------------- #
def _req(port, method, path, payload=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    data = json.dumps(payload).encode() if payload is not None else None
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    body_out = resp.read().decode()
    conn.close()
    return resp.status, json.loads(body_out)


def test_agent_run_http_flow(tmp_path, monkeypatch):
    """HTTP 全流程：登录 -> agent/run -> clarify -> resume -> done。"""
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    from web.server import Handler  # 复用完整路由
    from web.server import ThreadingHTTPServer as _S

    server = _S(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        # 登录拿 token（默认演示账号）
        status, login = _req(
            port, "POST", "/api/auth/login", {"username": "admin", "password": "admin123"}
        )
        assert status == 200, login
        H = {"Authorization": "Bearer " + login["token"]}

        # 未认证 401
        status, _ = _req(port, "POST", "/api/agent/run", {"query": "GMV呢"})
        assert status == 401

        # 歧义问题 -> clarify 中断 + resume_token
        status, body = _req(port, "POST", "/api/agent/run", {"query": "GMV呢"}, H)
        assert status == 200
        assert body["phase"] == "clarify"
        assert body["clarification"] and body["resume_token"]

        # 携带答复恢复 -> done
        status, body = _req(
            port,
            "POST",
            "/api/agent/run",
            {
                "query": "GMV呢",
                "resume_token": body["resume_token"],
                "human_reply": "2024 年 5 月按省份的订单金额",
            },
            H,
        )
        assert status == 200
        assert body["phase"] == "done"
        assert body["report"]
        assert any(s["ok"] for s in body["steps"])

        # 无效 resume token -> 404（属主/过期保护）
        status, body = _req(
            port,
            "POST",
            "/api/agent/run",
            {"query": "GMV呢", "resume_token": "hitl-notexist", "human_reply": "x"},
            H,
        )
        assert status == 404
    finally:
        server.shutdown()
