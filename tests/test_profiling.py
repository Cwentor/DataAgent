"""SchemaAgent 动态 profiling 单元测试（pi-agent-harness 对齐：后续项 1）。

覆盖：
- profile_enum_values：低基数字段收录取值、高基数/非字符串字段排除、
  确定性排序、失败降级、进程级缓存；
- schema_digest 枚举注入格式与缺省行为；
- planner_node LLM 规划提示词携带枚举值。
"""

from __future__ import annotations

import duckdb
import pytest

from core.orchestrator.nodes import schema_digest
from core.retrieval.profiling import (
    DEFAULT_MAX_DISTINCT,
    clear_profile_cache,
    profile_enum_values,
)


@pytest.fixture(autouse=True)
def _clean_profile_cache():
    """每个测试前后清空 profiling 缓存，避免跨测试状态泄漏。"""
    clear_profile_cache()
    yield
    clear_profile_cache()


@pytest.fixture
def mem_conn() -> duckdb.DuckDBPyConnection:
    """带 mock 数仓的独立内存连接（确定性种子数据）。"""
    from mock.init_duckdb import build_tables

    c = duckdb.connect(":memory:")
    build_tables(c)
    yield c
    c.close()


def test_profile_enum_values_collects_low_cardinality(mem_conn):
    """低基数字符串字段（province 等）收录实际取值。"""
    result = profile_enum_values(conn=mem_conn)
    assert "province" in result
    assert result["province"], "province 应有非空取值清单"
    assert "pay_status" in result
    assert "SUCCESS" in result["pay_status"]


def test_profile_enum_values_excludes_high_cardinality(mem_conn):
    """高基数字段（超过 DEFAULT_MAX_DISTINCT）不注入，防提示词膨胀。"""
    result = profile_enum_values(conn=mem_conn)
    for name, values in result.items():
        assert len(values) <= DEFAULT_MAX_DISTINCT, f"{name} 不应超过基数阈值"
    # 非字符串字段（数值/时间）一律不出现
    assert "order_amount" not in result
    assert "order_time" not in result


def test_profile_enum_values_deterministic_order(mem_conn):
    """取值清单按值排序（确定性可复现铁律）。"""
    first = profile_enum_values(conn=mem_conn)
    second = profile_enum_values(conn=mem_conn, use_cache=False)
    assert first == second
    assert first["province"] == sorted(first["province"])


def test_profile_enum_values_failure_degrades_gracefully():
    """库不可用（无注入连接 + 连接池不可用）时降级为空 dict，不抛异常。"""
    result = profile_enum_values(conn=None, use_cache=False)
    assert isinstance(result, dict)  # 失败静默降级（查询异常逐字段跳过）


def test_profile_enum_values_uses_cache(mem_conn):
    """进程级缓存：第二次调用命中缓存（不再查库）。"""
    first = profile_enum_values(conn=mem_conn)
    # 关闭传入连接的可用性：缓存命中路径不触发任何查询
    second = profile_enum_values(conn=None)  # 若未命中缓存会尝试连接池
    assert first == second


def test_schema_digest_injects_enum_values():
    """schema_digest 注入枚举值：行尾附"可取值:"清单。"""
    digest = schema_digest({"province": ["上海", "广东"], "gmv": ["不应出现"]})
    line = next((ln for ln in digest.splitlines() if ln.startswith("- province ")), "")
    assert "可取值: 上海|广东" in line
    # gmv 是数值字段：不在注入集时无"可取值"标记
    gmv_line = next((ln for ln in digest.splitlines() if ln.startswith("- gmv ")), "")
    assert "可取值" not in gmv_line


def test_schema_digest_without_enum_values_unchanged():
    """schema_digest 缺省（None）：与旧契约输出一致（无"可取值"标记）。"""
    digest = schema_digest()
    assert "可取值" not in digest
    assert digest.startswith("- ")


def test_planner_prompt_carries_enum_values(monkeypatch):
    """planner_node LLM 规划提示词必须携带 profiling 枚举值。"""
    import core.orchestrator.nodes as nodes

    captured: dict[str, str] = {}
    monkeypatch.setattr(nodes, "_resolve_llm", lambda: object())
    monkeypatch.setattr(
        "core.retrieval.profiling.profile_enum_values",
        lambda *a, **k: {"pay_status": ["SUCCESS", "FAILED"]},
    )

    def fake_llm_json(llm, system, user):
        captured["user"] = user
        return None

    monkeypatch.setattr(nodes, "_llm_json", fake_llm_json)
    from core.orchestrator.state import AgentState

    nodes.planner_node(AgentState(user_query="查 GMV"))
    assert "可取值: SUCCESS|FAILED" in captured["user"]
