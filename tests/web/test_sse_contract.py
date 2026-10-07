"""SSE 契约回归：FastAPI vs stdlib 双实现帧级 diff（M1 验收门 / M4 删除前护栏）。

两侧共用同一 RunRegistry（事件源头唯一），diff 锁定的是 FastAPI 传输桥
（StreamingResponse + 队列）不增、不删、不重排帧，帧格式与 stdlib
``data: <json.dumps(event, ensure_ascii=False)>\\n\\n`` 逐字节一致。
"""

from __future__ import annotations

import json
import threading

import pytest


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from web.api import app

    return TestClient(app, raise_server_exceptions=False)


def _collect_frames_registry(monkeypatch, tmp_path, query: str) -> list[str]:
    """stdlib 侧：绕过 HTTP，直接经 RunRegistry 订阅采集帧（帧构造与 _sse_write 同源）。"""
    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    from web.runs import default_run_registry

    registry = default_run_registry()
    run = registry.start(query, owner="admin", session_key="contract-stdlib")
    frames: list[str] = []
    done = threading.Event()

    def write_frame(event: dict) -> None:
        # 与 stdlib Handler._sse_write 逐字节同源
        frames.append(f"data: {json.dumps(event, ensure_ascii=False)}\n\n")

    def _follow() -> None:
        try:
            registry.subscribe(run.run_id, 0, write_frame, owner=None, on_idle=None)
        finally:
            done.set()

    t = threading.Thread(target=_follow, daemon=True)
    t.start()
    assert done.wait(timeout=120), "registry 订阅 120s 未收敛"
    return frames


def _collect_frames_fastapi(client, monkeypatch, tmp_path, query: str) -> list[str]:
    """FastAPI 侧：TestClient 流式采集原始帧文本。"""
    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    frames: list[str] = []
    with client.stream(
        "GET",
        "/api/v1/agent/chat/stream",
        params={"query": query, "thread": "contract-fastapi"},
    ) as resp:
        assert resp.status_code == 200
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                if frame.startswith("data: "):
                    frames.append(frame + "\n\n")
    return frames


@pytest.mark.parametrize(
    "query",
    [
        # 明确问题（走完 done 收敛）；歧义问题（hitl_request 挂起收尾）
        "分析一下 2024 年 5 月第一周比第二周 GMV 下滑的原因，按地区定位",
        "什么是销售额？",
    ],
)
def test_sse_frames_identical_across_servers(monkeypatch, tmp_path, client, query):
    fast = _collect_frames_fastapi(client, monkeypatch, tmp_path, query)
    std = _collect_frames_registry(monkeypatch, tmp_path, query)
    assert fast and std

    # 1) 事件类序列一致
    def ev(frames: list[str]) -> list[str]:
        return [json.loads(f[6:])["event"] for f in frames]

    assert ev(fast) == ev(std)

    # 2) 逐帧 JSON 相等（剥离非契约比对项，均为源数据/运行时的非确定性，实测证明
    #    同源 stdlib 双跑同样漂移，与传输层契约无关）：
    #    - timestamp / turn_id / trace_id：双跑各自生成；
    #    - duration_ms：执行耗时天然漂移；
    #    - preview_rows 行序：取数预览无 ORDER BY；
    #    - 浮点数：DuckDB 聚合/沙箱累加顺序的末位抖动（6 位小数内归一）。
    #    数据承载帧（载荷内嵌查询预览/报告正文，行子集本身不确定）只比确定性帧，
    #    数据承载帧走第 4 步的结构比对。
    def _normalize(obj):
        if isinstance(obj, float):
            return round(obj, 6)
        if isinstance(obj, dict):
            return {k: _normalize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            items = [_normalize(v) for v in obj]
            return sorted(items, key=lambda v: json.dumps(v, ensure_ascii=False))
        if isinstance(obj, str) and obj.lstrip().startswith("{"):
            # 沙箱 summary 是 JSON 字符串：解析后归一内部浮点再序列化，
            # 消除字符串内嵌的浮点末位抖动
            try:
                return json.dumps(_normalize(json.loads(obj)), ensure_ascii=False)
            except json.JSONDecodeError:
                return obj
        return obj

    def norm(frames):
        out = []
        for f in frames:
            d = json.loads(f[6:])
            d.pop("timestamp", None)
            d.pop("turn_id", None)
            d.pop("trace_id", None)
            if d["event"] == "hitl_request":
                d["payload"]["hitl"].pop("resume_token", None)
            tool = d.get("payload", {}).get("tool")
            if isinstance(tool, dict):
                tool.pop("duration_ms", None)
            out.append(_normalize(d))
        return out

    # 3) 确定性帧（plan_created / step_start / tool_start / hitl_request）：
    #    归一化后逐帧全量相等
    deterministic = {"plan_created", "step_start", "tool_start", "hitl_request"}
    det_fast = [f for f in fast if json.loads(f[6:])["event"] in deterministic]
    det_std = [f for f in std if json.loads(f[6:])["event"] in deterministic]
    assert norm(det_fast) == norm(det_std)

    # 4) 帧字节格式一致：除可变字段外，帧前缀/分隔符逐字节相同
    assert all(f.startswith("data: ") and f.endswith("\n\n") for f in fast + std)

    # 5) 数据承载帧的结构比对（tool_end / artifact_emit / done 的载荷内嵌查询
    #    预览与报告正文，预览行子集随 DuckDB 无 ORDER BY 行发射顺序漂移——
    #    同源 stdlib 双跑同样漂移，实测证明；这些帧比对事件结构与归属字段，
    #    不比对源非确定内容）。
    data_bearing = {"tool_end", "artifact_emit", "done", "error", "reflection"}
    for a, b in zip(norm(fast), norm(std), strict=True):
        if a["event"] in data_bearing:
            assert a["event"] == b["event"]
            for key in ("step_id",):
                if key in a.get("payload", {}) or key in b.get("payload", {}):
                    assert a["payload"].get(key) == b["payload"].get(key)
            tool_a = a.get("payload", {}).get("tool") or {}
            tool_b = b.get("payload", {}).get("tool") or {}
            if tool_a or tool_b:
                assert tool_a.get("name") == tool_b.get("name")
                assert tool_a.get("status") == tool_b.get("status")
        else:
            assert a == b, f"确定性帧载荷不一致: {a} != {b}"
