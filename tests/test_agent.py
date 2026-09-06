"""Agent（NL -> DSL）单元测试：启发式兜底 + LLM 编排。"""

from __future__ import annotations

import pytest

from agent.agent import LLMNL2DSL, extract_json
from agent.errors import PipelineError
from agent.heuristic import DeterministicNL2DSL
from eval.eval_runner import load_golden
from semantic.dsl_schema import QueryDSL


# --------------------------------------------------------------------------- #
# 启发式兜底
# --------------------------------------------------------------------------- #
def test_heuristic_covers_all_golden_questions():
    """启发式应能复现 golden 中全部**单轮**问题的预期 DSL。"""
    h = DeterministicNL2DSL()
    for item in load_golden():
        if item.get("type") == "multi_turn":
            continue  # 多轮用例由 eval.multi_turn 评测单独覆盖
        expected = QueryDSL.model_validate(item["dsl"])
        dsl = h.run(item["question"])
        assert dsl == expected, "未命中: " + str(item["id"])


def test_heuristic_rejects_unknown_query():
    h = DeterministicNL2DSL()
    with pytest.raises(PipelineError):
        h.run("今天天气怎么样？")


# --------------------------------------------------------------------------- #
# LLM 编排（用假客户端，不触发网络）
# --------------------------------------------------------------------------- #
class FakeLLM:
    """可脚本化的假 LLM 客户端。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if not self.responses:
            raise AssertionError("FakeLLM 响应用尽")
        return self.responses.pop(0)


def test_extract_json_strips_markdown_fence():
    import json as _json

    raw = _json.dumps(
        {"metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}]}
    )
    fenced = "```json\n" + raw + "\n```"
    assert extract_json(fenced)["metrics"][0]["alias"] == "gmv"


def test_llm_agent_valid_output():
    import json as _json

    payload = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
        "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
    }
    fake = FakeLLM([_json.dumps(payload)])
    agent = LLMNL2DSL(fake, max_retries=1)
    dsl = agent.run("2024年6月GMV多少")
    assert dsl.metrics[0].alias == "gmv"
    assert fake.calls == 1


def test_llm_agent_retries_then_succeeds():
    import json as _json

    bad = "这不是 JSON"
    good = _json.dumps(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "order_count"}
            ]
        }
    )
    fake = FakeLLM([bad, good])
    agent = LLMNL2DSL(fake, max_retries=2)
    dsl = agent.run("订单数")
    assert dsl.metrics[0].alias == "order_count"
    assert fake.calls == 2


def test_llm_agent_rejects_when_exhausted():
    fake = FakeLLM(["not json", "still not json", "nope"])
    agent = LLMNL2DSL(fake, max_retries=2)
    with pytest.raises(PipelineError):
        agent.run("随便问")
    assert fake.calls == 3


def test_llm_agent_rejects_error_flag():
    import json as _json

    fake = FakeLLM([_json.dumps({"error": "无法可靠解析"})])
    agent = LLMNL2DSL(fake, max_retries=0)
    with pytest.raises(PipelineError):
        agent.run("超出范围")


def test_llm_agent_rewrite_success():
    """SQL 执行自愈：把精确报错喂回 LLM 重写 DSL（至少 1 次）。"""
    import json as _json

    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
        }
    )
    corrected = _json.dumps(
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
            },
        }
    )
    fake = FakeLLM([corrected])
    agent = LLMNL2DSL(fake, max_retries=1)
    new_dsl = agent.rewrite("2024年6月GMV多少", dsl, "Binder Error: 模拟引擎报错")
    assert new_dsl.time_filter is not None
    assert fake.calls == 1  # 至少调用一次 LLM


def test_llm_agent_rewrite_retries_then_succeeds():
    import json as _json

    dsl = QueryDSL.model_validate(
        {"metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}]}
    )
    good = _json.dumps(
        {"metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}]}
    )
    fake = FakeLLM(["not json", good])
    agent = LLMNL2DSL(fake, max_retries=2)
    new_dsl = agent.rewrite("GMV", dsl, "timeout", attempts=2)
    assert new_dsl.metrics[0].alias == "gmv"
    assert fake.calls == 2


def test_deterministic_agent_rewrite_rejects():
    """确定性兜底无自愈能力：重写明确抛错，由上层透传原始报错。"""
    h = DeterministicNL2DSL()
    dsl = h.run("2024年6月GMV多少")
    with pytest.raises(PipelineError, match="不支持"):
        h.rewrite("2024年6月GMV多少", dsl, "Binder Error: x")


# --------------------------------------------------------------------------- #
# 维度成员词汇表数据驱动（审计 §3.2-4）：识别能力跟随 catalog.DIMENSION_MEMBERS
# --------------------------------------------------------------------------- #
def test_heuristic_vocabulary_data_driven(monkeypatch):
    """新增维度成员后离线启发式即可识别，无需改代码；成员移除后不再被识别。"""
    from semantic import catalog

    h = DeterministicNL2DSL()
    monkeypatch.setitem(
        catalog.DIMENSION_MEMBERS,
        "province",
        catalog.DIMENSION_MEMBERS["province"] + ("西藏",),
    )
    dsl = h.run("西藏的GMV是多少")
    assert any(f.field == "province" and f.value == "西藏" for f in dsl.filters)

    monkeypatch.setitem(
        catalog.DIMENSION_MEMBERS,
        "province",
        tuple(p for p in catalog.DIMENSION_MEMBERS["province"] if p != "广东"),
    )
    dsl = h.run("广东的GMV是多少")
    assert not any(f.field == "province" for f in dsl.filters)


def test_heuristic_region_expansion_intersects_warehouse(monkeypatch):
    """大区展开与数仓省份成员取交集：库中裁撤的省份不产出无效过滤。"""
    from semantic import catalog

    h = DeterministicNL2DSL()
    monkeypatch.setitem(catalog.DIMENSION_MEMBERS, "province", ("北京",))
    dsl = h.run("华东的GMV是多少")  # 华东四省均不在库 -> 不产出省份过滤
    assert not any(f.field == "province" for f in dsl.filters)

    monkeypatch.setitem(catalog.DIMENSION_MEMBERS, "province", ("上海", "江苏"))
    dsl = h.run("华东的GMV是多少")
    prov = [f for f in dsl.filters if f.field == "province"]
    assert len(prov) == 1 and set(prov[0].value) == {"上海", "江苏"}
