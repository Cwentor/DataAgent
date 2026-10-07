"""Subagent 契约与受限子图测试（M3 Task 13；规格 §6）。

覆盖：白名单越集拒绝 / 报告契约 / 无 LLM 确定性降级（离线可运行可单测）。
fan-out 并行语义在 tests/test_fanout.py（Task 14）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.orchestrator.subagent import (
    SubagentBudget,
    SubagentReport,
    SubagentTask,
    run_subagent,
)
from tools.registry import default_registry


def test_task_rejects_unknown_tool():
    """allowed_tools 越过注册中心白名单：构造即拒绝（受控能力白名单铁律）。"""
    with pytest.raises(ValueError):
        SubagentTask(
            task_id="t1",
            goal="g",
            context_slice={},
            allowed_tools=["__definitely_not_registered__"],
            budget=SubagentBudget(),
        )


def test_task_tool_subset_must_be_registry_subset():
    registered = default_registry().tool_names()
    if registered:
        task = SubagentTask(
            task_id="t2",
            goal="g",
            context_slice={},
            allowed_tools=registered[:1],
            budget=SubagentBudget(),
        )
        assert task.allowed_tools == registered[:1]


def test_task_budget_defaults():
    """预算硬顶缺省：步数 6 / 超时 120s（规格 §6.4）。"""
    task = SubagentTask(task_id="t3", goal="g")
    assert task.budget.max_steps == 6
    assert task.budget.timeout_seconds == 120.0


def test_report_extra_forbid_and_status_literal():
    with pytest.raises(ValidationError):
        SubagentReport(
            task_id="t",
            status="crashed",  # type: ignore[arg-type]
            findings=[],
            artifacts=[],
            audit={},
            metrics={},
        )
    report = SubagentReport(
        task_id="t",
        status="done",
        findings=["f"],
        artifacts=[],
        audit={"guard": "ok", "qa": []},
        metrics={},
    )
    assert report.status == "done"


def test_subagent_deterministic_without_llm(monkeypatch):
    """无 LLM：四节点子图以确定性兜底跑通，报告 audit 与 ParquetRef 同构（{guard, qa}）。"""
    monkeypatch.setattr("config.settings.LLM_API_KEY", "")
    task = SubagentTask(
        task_id="t4",
        goal="2024年6月成功订单的总销售额(GMV)是多少？",
        context_slice={},
        allowed_tools=[],
        budget=SubagentBudget(max_steps=8, timeout_seconds=120),
    )
    report = run_subagent(task, thread_id="u1:s1:sub:t4", observer=None)
    assert report.task_id == "t4"
    assert report.status in {"done", "failed", "timeout"}
    assert set(report.audit) == {"guard", "qa"}


def test_subagent_budget_hard_cap(monkeypatch):
    """预算硬顶：max_steps 不足以跑完四节点 -> timeout 收敛（不炸、不挂死）。"""
    monkeypatch.setattr("config.settings.LLM_API_KEY", "")
    task = SubagentTask(
        task_id="t5",
        goal="2024年6月成功订单的总销售额(GMV)是多少？",
        budget=SubagentBudget(max_steps=1, timeout_seconds=30),
    )
    report = run_subagent(task, thread_id="u1:s1:sub:t5", observer=None)
    assert report.status == "timeout"


def test_task_allows_builtin_capability_run_code():
    """内置能力名 run_code 属于合法白名单（沙箱代码执行可在任务卡授权）。"""
    task = SubagentTask(task_id="t6", goal="g", allowed_tools=["run_code"])
    assert task.allowed_tools == ["run_code"]


def test_allowed_tools_gates_query_steps(monkeypatch):
    """白名单不含查询能力：query 步骤执行路径被裁剪，findings 如实披露拒绝原因。"""
    monkeypatch.setattr("config.settings.LLM_API_KEY", "")
    task = SubagentTask(
        task_id="t7",
        goal="2024年6月成功订单的总销售额(GMV)是多少？",
        allowed_tools=["run_code"],
        budget=SubagentBudget(max_steps=8, timeout_seconds=120),
    )
    report = run_subagent(task, thread_id="u1:s1:sub:t7", observer=None)
    assert report.status in {"done", "failed", "timeout"}
    assert any("未授权" in f and "已跳过" in f for f in report.findings)


def test_allowed_tools_with_query_capability_executes(monkeypatch):
    """白名单含查询类工具：query 步骤正常放行，findings 不含数据查询拒绝披露。"""
    monkeypatch.setattr("config.settings.LLM_API_KEY", "")
    task = SubagentTask(
        task_id="t8",
        goal="2024年6月成功订单的总销售额(GMV)是多少？",
        allowed_tools=["query_metric"],
        budget=SubagentBudget(max_steps=8, timeout_seconds=120),
    )
    report = run_subagent(task, thread_id="u1:s1:sub:t8", observer=None)
    assert report.status in {"done", "failed", "timeout"}
    assert not any("未授权数据查询" in f for f in report.findings)
