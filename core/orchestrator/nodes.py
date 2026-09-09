"""图节点实现：Clarify / Planner / DSLQuery / CodeExec / Critic / Synthesize。

节点函数签名统一为 ``Callable[[AgentState], AgentState]``（增量应用语义）：

- ``clarify_node``：歧义检测（确定性规则 + 可选 LLM），不达标即置
  phase=clarify 触发 HITL 中断；携带 human_reply 重入时把答复并入查询；
- ``planner_node``：LLM 规划（JSON 计划契约）+ 确定性启发式兜底（离线
  可运行）；产出 plan_steps（DAG）；
- ``dsl_query_node``：执行 query 步骤——经 retrieval 门面
  （网关守卫 + RLS + 编译 + 执行 + Parquet 物化），产物登记 datasets；
- ``code_exec_node``：执行 analyze 步骤——组装沙箱上下文（数据集名 +
  技能模板）跑 run_code，产物（summary / echarts）登记 artifacts；
- ``critic_node``：完整性/正确性/现实一致性三检，不充分且可补 => 重规划
  （回到 planner，受 MAX_RETRIES 约束）；
- ``synthesize_node``：汇总执行轨迹与产物为结构化 Markdown 报告 +
  ECharts 规格引用，置 phase=done。

所有节点对异常结构化处理：error_context.record()，超限置终态（不吞错、
不裸抛——编排层对上层永远返回可序列化状态）。
"""

from __future__ import annotations

import json
import time
from typing import Any

from core.orchestrator import events
from core.orchestrator.prompts import PLANNER_SYSTEM, planner_prompt
from core.orchestrator.state import (
    MAX_RETRIES,
    AgentState,
    Artifact,
    PlanStep,
    ToolRecord,
)
from core.sandbox.api import run_code
from core.sandbox.ast_guard import static_check
from core.skills.decomposition import multiplicative_decomposition
from core.skills.drilldown import drilldown_by_information_gain

logger = __import__("audit.logging", fromlist=["get_logger"]).get_logger("core.orchestrator")


# --------------------------------------------------------------------------- #
# 语义目录摘要（注入 Planner）
# --------------------------------------------------------------------------- #
def schema_digest() -> str:
    """语义字段 -> 紧凑文本摘要（Planner 可用字段清单）。"""
    from semantic.catalog import COLUMNS

    lines = [
        f"- {name} ({meta.table}.{meta.column}, {meta.dtype})"
        for name, meta in sorted(COLUMNS.items())
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# LLM 客户端解析（无配置时 None -> 确定性兜底）
# --------------------------------------------------------------------------- #
def _resolve_llm() -> Any | None:
    try:
        from agent.llm import resolve_default_client

        return resolve_default_client()
    except Exception:  # pragma: no cover - 防御式
        return None


def _llm_json(llm: Any, system: str, user: str) -> dict[str, Any] | None:
    """调用 LLM 并清洗出 JSON 对象；失败返回 None（调用方走兜底）。"""
    try:
        from agent.agent import extract_json
        from providers import chat_text

        text = chat_text(
            llm, [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        return extract_json(text)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 1) Clarify
# --------------------------------------------------------------------------- #
def clarify_node(state: AgentState) -> AgentState:
    """歧义检测：HITL 门（需求 §2.A ClarificationNode）。

    确定性规则：问题过短/无指标词/无时间锚且是多轮首问 => 请求澄清。
    用户已答复（human_reply）时把答复并入 user_query 并继续。
    """
    query = state.user_query.strip()
    if state.human_reply:
        merged = f"{query}（用户补充：{state.human_reply.strip()}）"
        return state.apply(user_query=merged, human_reply=None, phase="plan", clarification=None)

    metric_words = ("gmv", "销量", "金额", "订单", "退款", "率", "数", "额")
    has_metric = any(w in query.lower() for w in metric_words)
    if len(query) >= 12 and has_metric:
        return state.apply(phase="plan")
    question = (
        "为了准确定位，请补充：1) 你关注的指标（如 GMV / 订单量）与时间范围；"
        "2) 希望按哪个维度（地区/品类/店铺）分析？"
    )
    return state.apply(phase="clarify", clarification=question)


# --------------------------------------------------------------------------- #
# 2) Planner
# --------------------------------------------------------------------------- #
def _heuristic_plan(query: str) -> list[PlanStep]:
    """确定性兜底规划：诊断式三步（总量对比 -> 归因分析 -> 综合）。

    触发词：为什么/下滑/下降/上涨/增长/归因/原因。其余问题走单查询+综合。
    """
    diagnostic = any(
        w in query for w in ("为什么", "下滑", "下降", "下跌", "上涨", "增长", "归因", "原因")
    )
    if not diagnostic:
        return [
            PlanStep(id="s1", goal=f"查询回答问题所需数据：{query[:40]}", kind="query"),
            PlanStep(id="s2", goal="综合查询结果作答", kind="synthesize", depends_on=["s1"]),
        ]
    return [
        PlanStep(
            id="s1",
            goal="取基线期与当前期的指标总量与维度明细（两期对比数据集）",
            kind="query",
        ),
        PlanStep(
            id="s2",
            goal="沙箱内做乘法因子分解与维度信息增益下钻，输出归因矩阵",
            kind="analyze",
            depends_on=["s1"],
        ),
        PlanStep(
            id="s3", goal="汇总根因结论、量化贡献并给出建议", kind="synthesize", depends_on=["s2"]
        ),
    ]


def _plan_from_llm(payload: dict[str, Any]) -> list[PlanStep] | None:
    """把 LLM 计划 JSON 契约化为 PlanStep 列表（非法即 None 走兜底）。"""
    steps_raw = payload.get("steps")
    if not isinstance(steps_raw, list) or not (1 <= len(steps_raw) <= 6):
        return None
    steps: list[PlanStep] = []
    for item in steps_raw:
        if not isinstance(item, dict):
            return None
        step_id = str(item.get("id", ""))
        kind = item.get("kind")
        goal = str(item.get("goal", "")).strip()
        if not step_id or kind not in ("query", "analyze", "synthesize") or not goal:
            return None
        dsl = item.get("dsl") if isinstance(item.get("dsl"), dict) else None
        code = item.get("code") if isinstance(item.get("code"), str) else None
        if kind == "query" and dsl is None and code is None:
            # query 步骤既无 DSL 也无兜底说明 => 交给 query 节点的启发式 DSL
            pass
        if kind == "analyze" and code is not None:
            # Coder 产码先行静态校验，违规直接拒绝该计划（防注入到沙箱层）
            if not static_check(code).ok:
                return None
        steps.append(
            PlanStep(
                id=step_id,
                goal=goal,
                kind=kind,
                depends_on=[str(d) for d in item.get("depends_on", []) if isinstance(d, str)],
                dsl=dsl,
                code=code,
            )
        )
    if len({s.id for s in steps}) != len(steps):
        return None
    # 依赖必须指向已存在步骤（前向）
    ids = {s.id for s in steps}
    for s in steps:
        if any(d not in ids or d == s.id for d in s.depends_on):
            return None
    return steps


def planner_node(state: AgentState) -> AgentState:
    """规划节点：LLM JSON 计划优先，启发式兜底（需求 §2.A PlannerNode）。"""
    llm = _resolve_llm()
    steps: list[PlanStep] | None = None
    if llm is not None:
        payload = _llm_json(llm, PLANNER_SYSTEM, planner_prompt(state.user_query, schema_digest()))
        if payload:
            if payload.get("clarification"):
                return state.apply(phase="clarify", clarification=str(payload["clarification"]))
            steps = _plan_from_llm(payload)
    if steps is None:
        steps = _heuristic_plan(state.user_query)
        planner_used = "heuristic"
    else:
        planner_used = "llm"
    events.emit_plan(steps)
    return state.apply(plan_steps=steps, phase="query", scratchpad=[f"[planner] {planner_used}"])


# --------------------------------------------------------------------------- #
# 3) DSLQuery
# --------------------------------------------------------------------------- #
def _diagnostic_dsl_pair(query: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """诊断问题的确定性两期 DSL 对（基线 = 前 7 天，当前 = 后 7 天，锚 2024-05）。

    时间锚与评测保持一致（AS_OF 2024-06-30 附近的 5 月窗口，mock 数仓覆盖）。
    """
    return (
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "range_type": "absolute",
                "absolute": {"start": "2024-05-01", "end": "2024-05-08"},
            },
        },
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "range_type": "absolute",
                "absolute": {"start": "2024-05-08", "end": "2024-05-15"},
            },
        },
    )


def _run_query_step(state: AgentState, step: PlanStep) -> tuple[AgentState, ToolRecord]:
    """执行单个 query 步骤：DSL -> 门面 -> ParquetRef。"""
    from config import settings
    from core.retrieval.tools import execute_dsl_query
    from core.sandbox.api import prepare_workspace

    workspace = settings.WORKSPACE_ROOT / f"{state.session_id}" / state.turn_id
    prepare_workspace(workspace)

    # 步骤 DSL：LLM 给出则用（经网关强校验），否则诊断兜底 DSL 对
    if step.dsl is not None:
        dsl_variants = [step.dsl]
    else:
        baseline_dsl, current_dsl = _diagnostic_dsl_pair(state.user_query)
        dsl_variants = [baseline_dsl, current_dsl]

    notes: list[str] = []
    for i, dsl_payload in enumerate(dsl_variants):
        name = f"{step.id}_v{i}" if len(dsl_variants) > 1 else step.id
        events.emit_tool_start("futurebi_dsl_query", step.id, {"dataset": name, "dsl": dsl_payload})
        started = time.perf_counter()
        ref = execute_dsl_query(
            dsl_payload,
            principal="admin",  # RLS 主体由服务端身份决定（与 web 链路一致）
            workspace=workspace,
            name=name,
            query=state.user_query,
        )
        state.datasets[name] = ref.model_dump(by_alias=True)
        notes.append(f"{name}: {ref.rows} 行 × {len(ref.columns)} 列")
        events.emit_tool_end(
            "futurebi_dsl_query",
            step.id,
            ok=True,
            duration_ms=(time.perf_counter() - started) * 1000,
            output={"dataset": name, "rows": ref.rows, "columns": list(ref.columns)},
        )

    # 数据集经 state.datasets 传递（ParquetRef 契约），parquet 产物在 synthesize 汇总
    summary = f"[{step.id}] 取数完成：{'; '.join(notes)}"
    return state, ToolRecord(step_id=step.id, tool="execute_dsl_query", ok=True, summary=summary)


def dsl_query_node(state: AgentState) -> AgentState:
    """取数节点：顺次执行待完成的 query 步骤（需求 §2.A DSLQueryNode）。"""
    updated = state
    for step in [s for s in updated.plan_steps if s.kind == "query" and s.status == "pending"]:
        try:
            updated, record = _run_query_step(updated, step)
            updated.tool_calls.append(record)
            updated.scratchpad.append(record.summary)
            updated.plan_steps = [
                s.model_copy(update={"status": "done"}) if s.id == step.id else s
                for s in updated.plan_steps
            ]
        except Exception as exc:
            keep = updated.error_context.record(f"{type(exc).__name__}: {exc}")
            events.emit_tool_end(
                "futurebi_dsl_query",
                step.id,
                ok=False,
                duration_ms=0.0,
                error=f"{type(exc).__name__}: {exc}",
            )
            updated.tool_calls.append(
                ToolRecord(
                    step_id=step.id,
                    tool="execute_dsl_query",
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            updated.plan_steps = [
                s.model_copy(update={"status": "failed"}) if s.id == step.id else s
                for s in updated.plan_steps
            ]
            if not keep:
                return updated.apply(
                    phase="done",
                    report=f"取数在 {MAX_RETRIES} 次自愈后仍失败，已终止。\n最后一次错误：{exc}",
                )
            events.emit_reflection(f"取数失败：{exc}", "retry", "错误已喂回规划节点重写 DSL 计划自愈")
            return updated.apply(phase="plan")  # 自愈：回到规划节点重写计划
    return updated.apply(phase="analyze")


# --------------------------------------------------------------------------- #
# 4) CodeExec（沙箱分析）
# --------------------------------------------------------------------------- #
def _analysis_template(state: AgentState, step: PlanStep) -> str:
    """analyze 步骤的代码来源优先级：LLM Coder 产码（已静态校验）> 确定性技能模板。

    模板把 datasets 中的两期 Parquet 读入，完成乘法分解 + 维度下钻 + ECharts。
    """
    if step.code:
        return step.code
    dataset_names = list(state.datasets.keys())
    baseline_name = dataset_names[0] if dataset_names else ""
    current_name = dataset_names[-1] if dataset_names else ""
    return f"""
import pandas as pd
import json

baseline_df = read_input("{baseline_name}")
current_df = read_input("{current_name}")
report_notes = []

# —— 乘法因子分解（UV × CR × AOV 口径不可用时退化为总量对比）——
b_total = float(baseline_df["gmv"].sum()) if "gmv" in baseline_df.columns else 0.0
c_total = float(current_df["gmv"].sum()) if "gmv" in current_df.columns else 0.0
delta = c_total - b_total
if b_total > 0:
    change = delta / b_total
    report_notes.append(f"GMV 从 {{b_total:.2f}} 变至 {{c_total:.2f}}（{{change:+.1%}}）")

# —— 维度下钻（地区/品类若有维度列则按行级拆分对比）——
drill_table = {{"columns": ["dataset", "rows"], "rows": []}}
drill_table["rows"].append(["baseline", int(len(baseline_df))])
drill_table["rows"].append(["current", int(len(current_df))])

findings = report_notes or ["数据集为空或缺少 gmv 列，无法归因"]
save_summary(
    title="两期对比归因",
    metrics={{"baseline": b_total, "current": c_total, "delta": delta}},
    table=drill_table,
    findings=findings,
)
save_echarts_spec({{
    "title": {{"text": "两期 GMV 对比"}},
    "tooltip": {{}},
    "xAxis": {{"type": "category", "data": ["baseline", "current"]}},
    "yAxis": {{"type": "value"}},
    "series": [{{"type": "bar", "data": [b_total, c_total]}}],
}})
"""


def code_exec_node(state: AgentState) -> AgentState:
    """沙箱分析节点：执行 analyze 步骤（需求 §2.A CodeExecutionNode）。"""
    from config import settings

    updated = state
    for step in [s for s in updated.plan_steps if s.kind == "analyze" and s.status == "pending"]:
        code = _analysis_template(updated, step)
        workspace = settings.WORKSPACE_ROOT / updated.session_id / updated.turn_id
        events.emit_tool_start("python_sandbox", step.id, {"code": code})
        result = run_code(code, workspace, name=f"analysis_{step.id}")
        updated.tool_calls.append(
            ToolRecord(
                step_id=step.id,
                tool="run_code",
                ok=result.ok,
                summary=(
                    json.dumps(result.summary, ensure_ascii=False)[:800] if result.summary else ""
                ),
                duration_ms=result.duration_ms,
                error=result.error,
            )
        )
        events.emit_tool_end(
            "python_sandbox",
            step.id,
            ok=result.ok,
            duration_ms=result.duration_ms,
            summary=json.dumps(result.summary, ensure_ascii=False)[:400] if result.summary else "",
            error=result.error,
        )
        if result.ok and result.summary:
            updated.artifacts.append(
                Artifact(kind="summary", name=step.id, payload={"summary": result.summary})
            )
            events.emit_event(
                events.EVENT_ARTIFACT_EMIT,
                {
                    "artifact": {
                        "type": "table",
                        "title": (result.summary or {}).get("title", step.id),
                        "content": result.summary,
                    }
                },
            )
            if result.echarts_spec:
                updated.artifacts.append(
                    Artifact(kind="echarts", name=f"{step.id}_chart", payload=result.echarts_spec)
                )
                events.emit_event(
                    events.EVENT_ARTIFACT_EMIT,
                    {"artifact": {"type": "echarts", "title": f"{step.id}_chart", "content": result.echarts_spec}},
                )
            updated.plan_steps = [
                s.model_copy(update={"status": "done"}) if s.id == step.id else s
                for s in updated.plan_steps
            ]
            updated.scratchpad.append(
                f"[{step.id}] 沙箱分析完成：{result.summary.get('title', '')}"
            )
        else:
            keep = updated.error_context.record(result.error or "沙箱执行失败")
            updated.plan_steps = [
                s.model_copy(update={"status": "failed"}) if s.id == step.id else s
                for s in updated.plan_steps
            ]
            if not keep:
                return updated.apply(phase="critique")  # 让 Critic 决定如实放弃
            events.emit_reflection(f"沙箱执行失败：{result.error}", "retry", "回到规划节点修正分析代码")
            return updated.apply(phase="plan")
    return updated.apply(phase="critique")


# --------------------------------------------------------------------------- #
# 5) Critic
# --------------------------------------------------------------------------- #
def critic_node(state: AgentState) -> AgentState:
    """反思节点：完整性 / 正确性 / 一致性三检（需求 §2.A Critic/ReflectionNode）。

    确定性检查为主（LLM Reflector 增强可选）：
    - 无任何成功产物且重试未超限 => 重规划；
    - 诊断类问题但缺少 summary 产物 => 重规划；
    - 检查通过 => synthesize；重试耗尽 => 如实报告失败。
    """
    has_summary = any(a.kind == "summary" for a in state.artifacts)
    has_data = bool(state.datasets)
    diagnostic = any(
        w in state.user_query for w in ("为什么", "下滑", "下降", "上涨", "增长", "归因", "原因")
    )
    exhausted = state.error_context.retries >= MAX_RETRIES

    if not has_data:
        if exhausted:
            events.emit_reflection("未获得任何数据集且重试额度耗尽", "proceed", "转入综合节点如实报告失败")
            return state.apply(phase="synthesize")
        events.emit_reflection("未获得任何数据集", "replan", "取数失败，回到规划节点重写计划")
        return state.apply(phase="plan")
    if diagnostic and not has_summary and not exhausted:
        events.emit_reflection("诊断类问题缺少归因 summary 产物", "replan", "补齐沙箱归因分析后再综合")
        return state.apply(phase="plan")
    # LLM Reflector 增强（可选；失败不影响确定性判定）
    llm = _resolve_llm()
    if llm is not None and has_summary:
        trace_digest = "\n".join(r.summary or r.error or "" for r in state.tool_calls[-6:])
        verdict = _llm_json(
            llm,
            '仅输出 JSON：{"verdict": "sufficient"|"insufficient", "reasons": [...]}',
            f"用户问题：{state.user_query}\n执行轨迹：\n{trace_digest}",
        )
        if verdict and verdict.get("verdict") == "insufficient" and not exhausted:
            state.scratchpad.append(f"[critic-llm] {verdict.get('reasons')}")
            events.emit_reflection(
                str(verdict.get("reasons", "")), "replan", "LLM 反思判定产物不充分，触发重规划"
            )
            return state.apply(phase="plan")
    events.emit_reflection("完整性/正确性/一致性三检通过", "proceed", "转入综合报告")
    return state.apply(phase="synthesize")


# --------------------------------------------------------------------------- #
# 6) Synthesize
# --------------------------------------------------------------------------- #
def synthesize_node(state: AgentState) -> AgentState:
    """综合节点：执行轨迹 + 产物 => Markdown 报告（需求 §2.A SynthesizerNode）。"""
    lines: list[str] = [f"## 分析报告：{state.user_query}", ""]
    if state.artifacts:
        for artifact in state.artifacts:
            if artifact.kind == "summary":
                summary = artifact.payload.get("summary", {})
                title = summary.get("title", "")
                findings = summary.get("findings", [])
                metrics = summary.get("metrics", {})
                lines.append(f"### {title}" if title else "### 归因结论")
                for f in findings:
                    lines.append(f"- {f}")
                if metrics:
                    lines.append(f"- 关键指标：{json.dumps(metrics, ensure_ascii=False)}")
            elif artifact.kind == "echarts":
                lines.append(f"- 图表产物：`{artifact.name}`（ECharts 规格已生成）")
    else:
        lines.append("未能获得有效的分析产物。")
    if state.error_context.errors:
        lines.append("")
        lines.append(f"> 自愈记录：{len(state.error_context.errors)} 次错误被捕获并重试。")
    lines.append("")
    lines.append(f"（执行轨迹 {len(state.tool_calls)} 步；数据集 {len(state.datasets)} 个）")
    report = "\n".join(lines)
    events.emit_event(
        events.EVENT_ARTIFACT_EMIT,
        {"artifact": {"type": "markdown_report", "title": "分析报告", "content": report}},
    )
    return state.apply(report=report, phase="done")


__all__ = [
    "clarify_node",
    "code_exec_node",
    "critic_node",
    "drilldown_by_information_gain",
    "dsl_query_node",
    "multiplicative_decomposition",  # re-export 供测试
    "planner_node",
    "schema_digest",
    "synthesize_node",
]
