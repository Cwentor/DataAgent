"""Multi-Tool Agent 调度单元测试：四类验收场景 + LLM 规划 + 自愈 + Max Steps。

覆盖验收标准（Multi-Tool Agent）：
1. "查看上个月的销售总额" -> query_metric_tool（单值指标）；
2. "分析过去半年各省份销售额环比趋势" -> trend_analysis_tool（时序对比）；
3. "把这个月未履约订单明细导出成表格" -> query_metric + export_report 组合，产出下载链接；
4. "GMV 是怎么算的" -> 仅 explain_glossary_tool，绝不触达 SQL 引擎。
"""

from __future__ import annotations

import json

import pytest

from agent.tool_agent import (
    DeterministicPlanner,
    LLMPlanner,
    LLMSynthesizer,
    PlanResult,
    ToolAgent,
    ToolCall,
)
from tools.base import DisplayType, ToolContext, ToolResult
from tools.registry import ToolRegistry


# --------------------------------------------------------------------------- #
# 确定性规划：四类验收场景
# --------------------------------------------------------------------------- #
def test_accept_last_month_sales_total(conn):
    """场景1：上月销售总额 -> query_metric 单值。"""
    agent = ToolAgent(max_steps=5)
    result = agent.run("查看上个月的销售总额", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["query_metric"]
    step = result.steps[0]
    assert step.success is True
    assert step.args == {"query": "查看上个月的销售总额"}
    assert result.chart_spec and result.chart_spec["chart"] == "number"
    assert "gmv" in result.answer


def test_accept_half_year_province_mom_trend(conn):
    """场景2：过去半年各省份环比趋势 -> trend_analysis。"""
    agent = ToolAgent(max_steps=5)
    result = agent.run("分析过去半年各省份销售额环比趋势", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["trend_analysis"]
    assert result.steps[0].success is True
    assert result.rows and len(result.rows) > 0
    assert "环比" in result.answer


def test_accept_unfulfilled_orders_export(conn):
    """场景3：未履约订单明细导出 -> 组合调度（查询 + 导出，导出复用先前结果）。"""
    agent = ToolAgent(max_steps=5)
    result = agent.run("把这个月未履约订单明细导出成表格", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["query_metric", "export_report"]
    assert all(s.success for s in result.steps)
    assert len(result.download_urls) == 1
    url = result.download_urls[0]
    assert url.startswith("/api/export/")
    # 导出工具复用了查询工具的列/行（组合链路），而非重新解析
    export_output = result.steps[1].output or {}
    assert export_output.get("row_count", 0) > 0


def test_accept_gmv_glossary_no_sql(conn):
    """场景4：口径问题 -> 仅 explain_glossary，绝不执行 SQL。"""
    agent = ToolAgent(max_steps=5)
    result = agent.run("GMV 是怎么算的", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["explain_glossary"]
    assert result.documents and result.documents[0]["key"] == "gmv"
    # 未触达 SQL 引擎：没有 query/trend/export 任何一步
    assert not any(t in result.step_tools() for t in ("query_metric", "trend_analysis"))


def test_chitchat_no_tool_call():
    agent = ToolAgent(max_steps=5)
    result = agent.run("今天天气怎么样")
    assert result.steps == []
    assert result.answer


def test_export_truncation_and_desensitization(tmp_path, monkeypatch):
    """导出工具：超限截断 + 敏感列脱敏 + 下载链接可回取。"""
    from tools.builtins import _export_store
    from tools.builtins.export_report_tool import ExportReportArgs, export_report_tool

    store = _export_store.ExportStore(root=tmp_path / "exports")
    monkeypatch.setattr(_export_store, "_default_store", store)
    monkeypatch.setattr(_export_store, "_store_lock", __import__("threading").Lock())

    # 前置查询结果含敏感列（user_id/user_name），且行数超过 limit 以触发截断
    prior = ToolResult(
        success=True,
        data={
            "columns": ["order_id", "user_id", "user_name", "order_amount"],
            "rows": [
                ["o1", "u1", "张三", 100.0],
                ["o2", "u2", "李四", 200.0],
                ["o3", "u3", "王五", 300.0],
                ["o4", "u4", "赵六", 400.0],
                ["o5", "u5", "孙七", 500.0],
                ["o6", "u6", "周八", 600.0],
            ],
            "row_count": 6,
        },
        display_type=DisplayType.TABLE,
    )
    ctx = ToolContext(prior=prior)
    args = ExportReportArgs(format="csv", limit=5)
    result = export_report_tool.run(args.model_dump(), ctx)
    assert result.success
    data = result.data
    assert data["truncated"] is True
    assert data["row_count"] == 5
    assert data["desensitized"] is True
    assert any("脱敏" in n for n in data["notes"])
    assert any("user_id" in n for n in data["notes"])
    # 预览行中敏感列已掩码，非敏感列原样保留
    for row in data["preview"]:
        assert row[0].startswith("o")  # 非敏感列 order_id 原样
        assert row[1] == "***" and row[2] == "***"
    # 落盘文件可回取，且不含明文敏感值
    item = store.get(data["export_id"])
    assert "user_id" in item.read_bytes().decode("utf-8-sig")
    assert "u1" not in item.read_bytes().decode("utf-8-sig")


# --------------------------------------------------------------------------- #
# LLM 规划（FakeLLM，不触发网络）
# --------------------------------------------------------------------------- #
class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if not self.responses:
            raise AssertionError("FakeLLM 响应用尽")
        return self.responses.pop(0)


def test_llm_planner_picks_tool_and_executes(conn):
    """规划 -> 执行 -> 重规划判定 done 终止（R1：LLMPlanner 参与重规划循环）。"""
    llm = FakeLLM(
        [
            json.dumps({"tool": "query_metric", "args": {"query": "上个月GMV"}}),
            json.dumps({"done": "信息已充分"}),
        ]
    )
    agent = ToolAgent(
        planner=LLMPlanner(llm, max_retries=1),
        max_steps=5,
    )
    result = agent.run("上个月GMV是多少", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["query_metric"]
    assert llm.calls == 2  # 1 次规划 + 1 次重规划终止判定


def test_llm_planner_direct_answer(conn):
    llm = FakeLLM([json.dumps({"answer": "这个问题不需要查库"})])
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=1), max_steps=5)
    result = agent.run("随便问问", conn=conn)
    assert result.steps == []
    assert result.answer == "这个问题不需要查库"


def test_llm_planner_retries_on_invalid_tool(conn):
    """LLM 规划器输出非法工具名 -> 校验拦截 -> 反馈重试 -> 成功。"""
    llm = FakeLLM(
        [
            json.dumps({"tool": "drop_table", "args": {}}),
            json.dumps({"tool": "query_metric", "args": {"query": "本月GMV"}}),
            json.dumps({"done": "信息已充分"}),
        ]
    )
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=2), max_steps=5)
    result = agent.run("本月GMV", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["query_metric"]
    assert llm.calls == 3  # 2 次规划（含重试）+ 1 次重规划终止判定


def test_llm_planner_exhausts_retries(conn):
    llm = FakeLLM([json.dumps({"tool": "no_such_tool", "args": {}})] * 3)
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=2), max_steps=5)
    result = agent.run("本月GMV", conn=conn)
    assert result.error is not None
    assert "规划" in result.error


def test_llm_planner_illegal_args_rejected(conn):
    """非法参数（extra 字段）被 Pydantic 拦截并反馈 LLM 重试。"""
    llm = FakeLLM(
        [
            json.dumps({"tool": "query_metric", "args": {"query": "GMV", "drop_table": "x"}}),
            json.dumps({"tool": "query_metric", "args": {"query": "GMV"}}),
            json.dumps({"done": "信息已充分"}),
        ]
    )
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=2), max_steps=5)
    result = agent.run("GMV", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["query_metric"]
    assert llm.calls == 3  # 2 次规划（含重试）+ 1 次重规划终止判定


def test_llm_synthesizer_uses_llm_answer(conn):
    llm = FakeLLM([json.dumps({"answer": "上个月销售总额是 280 万"})])
    agent = ToolAgent(
        planner=DeterministicPlanner(),
        synthesizer=LLMSynthesizer(llm),
        max_steps=5,
    )
    result = agent.run("查看上个月的销售总额", conn=conn)
    assert result.error is None
    assert result.answer == "上个月销售总额是 280 万"


# --------------------------------------------------------------------------- #
# 受控调度：Max Steps 上限 + 自愈修复
# --------------------------------------------------------------------------- #
def test_max_steps_cap_enforced(conn):
    class ManyCallsPlanner(DeterministicPlanner):
        def plan(self, query, principal, registry, **kwargs):
            return PlanResult(calls=[ToolCall("query_metric", {"query": query})] * 6)

    agent = ToolAgent(planner=ManyCallsPlanner(), max_steps=4)
    result = agent.run("本月GMV", conn=conn)
    assert len(result.steps) == 4
    assert result.error is None


def test_max_steps_out_of_range_rejected():
    with pytest.raises(ValueError):
        ToolAgent(max_steps=10)
    with pytest.raises(ValueError):
        ToolAgent(max_steps=2)


def test_self_correction_retries_failed_tool(conn):
    """工具报错 -> Self-Correction：planner.correct 给出修正调用并成功。"""
    from tools.builtins.query_metric_tool import QueryMetricArgs, QueryMetricTool

    calls = {"n": 0}

    class FlakyQueryTool(QueryMetricTool):
        def execute(self, validated_args: QueryMetricArgs, ctx=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return ToolResult(
                    success=False,
                    data=None,
                    display_type=DisplayType.TEXT,
                    error_msg="PipelineError: 第一次执行失败（模拟）",
                    meta={"error_type": "PipelineError"},
                )
            return super().execute(validated_args, ctx)

    reg = ToolRegistry()
    reg.register(FlakyQueryTool())

    class RetryPlanner(DeterministicPlanner):
        def plan(self, query, principal, registry, **kwargs):
            return PlanResult(calls=[ToolCall("query_metric", {"query": query})])

        def correct(self, query, principal, failed, record):
            return ToolCall("query_metric", {"query": query}, reason="自愈重试")

    agent = ToolAgent(registry=reg, planner=RetryPlanner(), max_steps=5)
    result = agent.run("本月GMV", conn=conn)
    assert result.error is None
    # 两步：第一次失败 + 自愈重试成功
    assert [s.success for s in result.steps] == [False, True]
    assert calls["n"] == 2


def test_no_retry_on_permanent_error(conn):
    """越权类确定性错误不触发无意义自愈。"""
    from tools.builtins.query_metric_tool import QueryMetricTool

    reg = ToolRegistry()

    class DeniedTool(QueryMetricTool):
        def execute(self, validated_args, ctx=None):
            return ToolResult(
                success=False,
                data=None,
                display_type=DisplayType.TEXT,
                error_msg="SecurityError: 无权访问",
                meta={"error_type": "SecurityError"},
            )

    reg.register(DeniedTool())

    class RetryPlanner(DeterministicPlanner):
        def plan(self, query, principal, registry, **kwargs):
            return PlanResult(calls=[ToolCall("query_metric", {"query": query})])

        def correct(self, query, principal, failed, record):
            return ToolCall("query_metric", {"query": query})

    agent = ToolAgent(registry=reg, planner=RetryPlanner(), max_steps=5)
    result = agent.run("本月GMV", conn=conn)
    assert result.error is not None
    assert "无权" in result.error
    assert len(result.steps) == 1
    assert result.steps[0].success is False


# --------------------------------------------------------------------------- #
# 调度轨迹（审计输入）：steps 结构化可序列化
# --------------------------------------------------------------------------- #
def test_steps_are_json_serializable(conn):
    agent = ToolAgent(max_steps=5)
    result = agent.run("查看上个月的销售总额", conn=conn)
    payload = json.dumps([s.to_dict() for s in result.steps], ensure_ascii=False)
    assert isinstance(payload, str)
    step0 = result.steps[0].to_dict()
    assert step0["tool"] == "query_metric"
    assert step0["success"] is True
    assert "args" in step0 and "duration_ms" in step0 and "display_type" in step0


def test_agent_result_to_dict(conn):
    agent = ToolAgent(max_steps=5)
    result = agent.run("GMV 是怎么算的", conn=conn)
    d = result.to_dict()
    assert d["steps"][0]["tool"] == "explain_glossary"
    assert d["intent"] == "data_query"


# --------------------------------------------------------------------------- #
# LLMPlanner.correct 自愈修复（回归：死代码缺陷修复后必须可用）
# --------------------------------------------------------------------------- #
def test_llm_planner_correct_with_registry(conn):
    """LLM 规划器自愈：工具首次失败 -> correct() 借助注入的 registry 产出修正调用。"""
    from tools.builtins.query_metric_tool import QueryMetricArgs, QueryMetricTool

    calls = {"n": 0}

    class FlakyQueryTool(QueryMetricTool):
        def execute(self, validated_args: QueryMetricArgs, ctx=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return ToolResult(
                    success=False,
                    data=None,
                    display_type=DisplayType.TEXT,
                    error_msg="PipelineError: 首次执行失败（模拟）",
                    meta={"error_type": "PipelineError"},
                )
            return super().execute(validated_args, ctx)

    reg = ToolRegistry()
    reg.register(FlakyQueryTool())

    # FakeLLM：先规划 query_metric，再响应 correct() 的修复请求（仍调用 query_metric），
    # 最后重规划判定 done 终止
    llm = FakeLLM(
        [
            json.dumps({"tool": "query_metric", "args": {"query": "本月GMV"}}),
            json.dumps({"tool": "query_metric", "args": {"query": "本月GMV"}}),
            json.dumps({"done": "信息已充分"}),
        ]
    )
    planner = LLMPlanner(llm, registry=reg, max_retries=1)
    assert planner.registry is reg  # 回归：registry 已构造注入（旧死代码无此属性）

    agent = ToolAgent(registry=reg, planner=planner, max_steps=5)
    result = agent.run("本月GMV", conn=conn)
    assert result.error is None
    assert [s.success for s in result.steps] == [False, True]
    assert calls["n"] == 2
    # 第二步为自愈修复调用（reason 标注）
    assert result.steps[1].args == {"query": "本月GMV"}


def test_llm_planner_correct_without_registry_safe(conn):
    """未注入 registry 时 correct() 安全返回 None（绝不 AttributeError / 静默吞错）。"""
    llm = FakeLLM([json.dumps({"tool": "query_metric", "args": {"query": "本月GMV"}})])
    planner = LLMPlanner(llm, max_retries=1)  # 不注入 registry
    assert planner.registry is None
    failed = ToolCall("query_metric", {"query": "x"})
    record = type(
        "Rec",
        (),
        {"error_msg": "PipelineError: 模拟失败", "tool": "query_metric"},
    )()
    corrected = planner.correct("本月GMV", None, failed, record)
    assert corrected is None  # 安全降级：不修复，交由上层透传错误


# --------------------------------------------------------------------------- #
# R1 观察驱动重规划：中间结果（observation）参与调度导航
# --------------------------------------------------------------------------- #
class RecordingLLM(FakeLLM):
    """记录每次收到的 messages（用于断言轨迹注入）。"""

    def __init__(self, responses):
        super().__init__(responses)
        self.seen_messages: list[list[dict]] = []

    def chat(self, messages):
        self.seen_messages.append([dict(m) for m in messages])
        return super().chat(messages)


def test_replan_continues_based_on_observation(conn):
    """重规划导航：首轮查华南 -> 基于轨迹继续查华北 -> 综合作答。"""
    llm = FakeLLM(
        [
            json.dumps({"tool": "query_metric", "args": {"query": "上个月华南GMV"}}),
            json.dumps({"tool": "query_metric", "args": {"query": "上个月华北GMV"}}),
            json.dumps({"answer": "华北GMV更高"}),
        ]
    )
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=1), max_steps=5)
    result = agent.run("上个月华南和华北哪个GMV更高", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["query_metric", "query_metric"]
    assert result.replans == 1
    assert result.answer == "华北GMV更高"  # 规划器基于轨迹的洞察优先于单输出拼装
    assert llm.calls == 3  # 1 次规划 + 2 次重规划（继续查 + 作答）


def test_replan_prompt_carries_trajectory(conn):
    """重规划请求必须携带已执行轨迹（observation 注入 LLM 上下文）。"""
    llm = RecordingLLM(
        [
            json.dumps({"tool": "query_metric", "args": {"query": "上个月GMV"}}),
            json.dumps({"done": "信息已充分"}),
        ]
    )
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=1), max_steps=5)
    result = agent.run("上个月GMV是多少", conn=conn)
    assert result.error is None
    replan_system = llm.seen_messages[1][0]["content"]
    assert "已经执行过若干工具调用" in replan_system
    assert "query_metric" in replan_system
    assert '"success": true' in replan_system
    assert "剩余可用步数" in replan_system


def test_replan_done_terminates_and_synthesizes(conn):
    """规划器判定 done -> 终止调度，答案由合成器从数据产出。"""
    llm = FakeLLM(
        [
            json.dumps({"tool": "query_metric", "args": {"query": "上个月GMV"}}),
            json.dumps({"done": "信息已充分"}),
        ]
    )
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=1), max_steps=5)
    result = agent.run("上个月GMV是多少", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["query_metric"]
    assert result.replans == 0
    assert "gmv" in result.answer


def test_replan_failure_degrades_gracefully(conn):
    """重规划环节 LLM 故障：不推翻已成功的执行结果，按现有轨迹收敛作答。"""
    llm = FakeLLM([json.dumps({"tool": "query_metric", "args": {"query": "上个月GMV"}})])
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=1), max_steps=5)
    result = agent.run("上个月GMV是多少", conn=conn)
    assert result.error is None  # 重规划失败不转化为顶层错误
    assert result.step_tools() == ["query_metric"]
    assert result.steps[0].success is True
    assert "gmv" in result.answer


def test_replan_can_clarify(conn):
    """重规划阶段可反问澄清：澄清写入结果且调度终止。"""
    llm = FakeLLM(
        [
            json.dumps({"tool": "query_metric", "args": {"query": "上个月GMV"}}),
            json.dumps({"clarify": "需要按哪个维度细分？"}),
        ]
    )
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=1), max_steps=5)
    result = agent.run("上个月GMV是多少", conn=conn)
    assert result.error is None
    assert result.clarifications and result.clarifications[0]["question"] == "需要按哪个维度细分？"
    assert result.intent == "clarify"
    assert "维度" in result.answer


def test_replan_respects_max_steps_budget(conn):
    """重规划循环受 max_steps 硬预算约束：预算耗尽立即收敛，绝不无限循环。"""
    tool_resp = json.dumps({"tool": "query_metric", "args": {"query": "本月GMV"}})
    llm = FakeLLM([tool_resp] * 8)  # 响应充足：若不受预算约束将无限调度
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=1), max_steps=3)
    result = agent.run("本月GMV", conn=conn)
    assert result.error is None
    assert len(result.steps) == 3
    assert llm.calls == 3  # 1 次规划 + 2 次重规划（第 3 步后预算耗尽不再询问）


def test_replan_invalid_output_stops_gracefully(conn):
    """重规划输出持续非法：校验拦截 -> 重试耗尽 -> 优雅终止（已执行结果保留）。"""
    llm = FakeLLM(
        [
            json.dumps({"tool": "query_metric", "args": {"query": "本月GMV"}}),
            json.dumps({"tool": "drop_table", "args": {}}),
            json.dumps({"tool": "drop_table", "args": {}}),
        ]
    )
    agent = ToolAgent(planner=LLMPlanner(llm, max_retries=1), max_steps=5)
    result = agent.run("本月GMV", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["query_metric"]
    assert result.steps[0].success is True


def test_deterministic_planner_skips_replan_loop(conn):
    """确定性规划器 iterative=False：不进入重规划循环（单批调度语义不变）。"""
    spy = {"n": 0}

    class SpyPlanner(DeterministicPlanner):
        def plan_next(self, *args, **kwargs):
            spy["n"] += 1
            return PlanResult()

    agent = ToolAgent(planner=SpyPlanner(), max_steps=5)
    result = agent.run("查看上个月的销售总额", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["query_metric"]
    assert spy["n"] == 0
    assert DeterministicPlanner.iterative is False
    assert LLMPlanner.iterative is True


# --------------------------------------------------------------------------- #
# R2 问题分解与对比综合
# --------------------------------------------------------------------------- #
def test_decompose_comparison_regions():
    """对比分解：『A 和 B 哪个更 X』-> 两个保留时间窗口的单实体子查询。"""
    from agent.tool_agent import decompose_comparison

    pairs = decompose_comparison("上个月华南和华北哪个GMV更高")
    assert pairs is not None
    assert [entity for entity, _ in pairs] == ["华南", "华北"]  # 按出现位置排序
    assert [sub for _, sub in pairs] == ["上个月华南GMV", "上个月华北GMV"]


def test_decompose_comparison_negative_cases():
    """非对比 / 无双实体的问题不分解（保持整问单查，绝不冒险猜测）。"""
    from agent.tool_agent import decompose_comparison

    assert decompose_comparison("查看上个月的销售总额") is None  # 非对比型
    assert decompose_comparison("上个月哪个品类GMV最高") is None  # 有触发词但无双实体
    assert decompose_comparison("上个月华南GMV是多少") is None  # 单实体


def test_accept_regional_comparison_decomposed_and_compared(conn):
    """端到端：对比问题 -> 分解为两次单实体查询 -> 跨步对比作答 + 对比柱状图。"""
    agent = ToolAgent(max_steps=5)
    result = agent.run("上个月华南和华北哪个GMV更高", conn=conn)
    assert result.error is None
    assert result.step_tools() == ["query_metric", "query_metric"]
    assert all(s.success for s in result.steps)
    # 两次调用的子查询各自聚焦单个大区（对比分解链路）
    assert "华南" in result.steps[0].args["query"]
    assert "华北" in result.steps[1].args["query"]
    # 对比综合：双方标签 + 高低结论
    assert "华南" in result.answer and "华北" in result.answer
    assert "更高" in result.answer or "持平" in result.answer
    # 对比柱状图：两行（对比项, 指标值），与作答标签一致
    assert result.chart_spec and result.chart_spec["chart"] == "bar"
    assert [r[0] for r in result.chart_spec["rows"]] == ["华南", "华北"]


def test_comparison_synthesis_skipped_for_non_comparison(conn):
    """非对比问题即使碰巧多组输出也不触发对比综合（单输出作答路径不变）。"""
    agent = ToolAgent(max_steps=5)
    result = agent.run("查看上个月的销售总额", conn=conn)
    assert result.error is None
    assert "对比结果" not in result.answer
    assert result.chart_spec and result.chart_spec["chart"] == "number"


def test_llm_planner_plan_prompt_carries_decomposition_guidance():
    """用 RecordingLLM 断言首轮规划提示含对比分解指引。"""
    from tools.registry import default_registry

    llm = RecordingLLM([json.dumps({"done": "无需工具"})])
    planner = LLMPlanner(llm, max_retries=1)
    planner.plan("华南和华北哪个GMV更高", None, default_registry())
    system = llm.seen_messages[0][0]["content"]
    assert "对比类问题" in system
    assert "分别查询每个对比对象" in system
