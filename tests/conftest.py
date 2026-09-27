"""共享 pytest fixtures：内存 DuckDB 连接 + 灌数据。"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock.init_duckdb import build_tables  # noqa: E402  (需先注入项目根到 sys.path)


@pytest.fixture(scope="session")
def conn() -> duckdb.DuckDBPyConnection:
    """会话级内存 DuckDB，注入确定性 mock 数据。"""
    c = duckdb.connect(":memory:")
    build_tables(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _clean_session_memory():
    """每个测试后清空会话记忆存储与澄清槽位，避免跨测试状态泄漏。"""
    yield
    from agent.memory import default_session_store
    from agent.slotfill import default_slot_store

    default_session_store().clear_all()
    default_slot_store().clear_all()


@pytest.fixture(autouse=True)
def _clean_run_registry():
    """每个测试后清空 Agent Run 注册表（并行会话缓冲），避免跨测试 run 泄漏。"""
    yield
    from web.runs import default_run_registry

    default_run_registry().clear_all()


@pytest.fixture(autouse=True)
def _default_autonomy_l4(monkeypatch):
    """测试默认 L4 全自动（规格 §5.2 降级档）：Plan Mode 之外的既有用例
    不被审批门打断；Plan Mode 专属用例显式传 autonomy_level 覆盖。"""
    from config import settings

    monkeypatch.setattr(settings, "AGENT_DEFAULT_AUTONOMY", "L4")
    yield


@pytest.fixture(autouse=True)
def _reset_langgraph_singletons():
    """每个测试结束后丢弃 LangGraph 进程级单例（M4 内存护栏）。

    MemorySaver 的 checkpoint 只增不减：每个超级步存一份完整 AgentState
    （含 datasets 预览行 / artifacts 报告 / 工具轨迹），多步诊断流一次可产生
    数十份快照。单例跨测试累积会让重 SSE 套件内存无界增长（实测
    test_agent_stream 因此内存耗尽）；每测试重建同时消除跨测试的
    checkpoint 串染（thread_id 冲突隐患）。生产长驻进程应配置
    ORCHESTRATOR_CHECKPOINT_DB 落盘（SqliteSaver）。
    """
    yield
    import core.orchestrator.langgraph_engine as lge

    lge._app = None
    lge._checkpointer = None


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch, tmp_path):
    """测试默认离线（确定性可复现铁律）：屏蔽本地 .env / providers.json 中的
    真实 LLM Key，重置 Model Provider 网关单例，杜绝用例发起真实网络调用。

    需要 LLM 行为的用例应显式注入 stub 客户端（如 _FakeLLM）或通过
    ``reset_default_provider_store`` 指向临时配置文件，不依赖外部环境。
    """
    from config import settings
    from providers.factory import reset_provider_factory
    from providers.store import ProviderStore, reset_default_provider_store

    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    # 空配置存储：彻底隔离磁盘 config/providers.json——用户在 Web 界面配置的
    # 真实供应商（含 API Key）不得泄漏进测试进程（否则 has_provider() 为真，
    # planner/critic 走真实 LLM，破坏确定性并可能产生真实网络调用）。
    reset_default_provider_store(ProviderStore(tmp_path / "empty-providers.json"))
    reset_provider_factory(None)
    yield
    reset_provider_factory(None)
    reset_default_provider_store(None)


@pytest.fixture()
def planner_clarify_then_plan(monkeypatch):
    """HITL 传输层测试的触发器（十八期）。

    离线字符规则退役后，clarify 唯一来源为 Planner clarification 契约；
    mock planner：首轮输出澄清问题（挂起），恢复轮输出两步直答计划。
    """
    import core.orchestrator.nodes as nodes
    from core.orchestrator.state import PlanStep

    plan_steps = [
        PlanStep(
            id="s1",
            goal="取GMV总量",
            kind="query",
            dsl={
                "metrics": [
                    {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
                ],
                "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
                "time_filter": {
                    "range_type": "absolute",
                    "absolute": {"start": "2024-05-01", "end": "2024-05-15"},
                },
            },
        ),
        PlanStep(id="s2", goal="综合作答", kind="synthesize", depends_on=["s1"]),
    ]
    responses = iter(
        [
            {"clarification": "你关注的指标与时间范围是什么？", "steps": []},
            {"clarification": None, "steps": [s.model_dump() for s in plan_steps]},
        ]
    )
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(nodes, "_llm_json", lambda llm, system, user: next(responses))
