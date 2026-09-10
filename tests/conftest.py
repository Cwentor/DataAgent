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
