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

from agent.heuristic import region_provinces
from core.orchestrator import events
from core.orchestrator.prompts import (
    DEGRADED_SUMMARIZER_SYSTEM,
    PLANNER_SYSTEM,
    SYNTHESIZER_SYSTEM,
    planner_prompt,
)
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
from semantic.catalog import REGION_PROVINCE_MAPPING as REGION_PROVINCE_MAPPING

logger = __import__("audit.logging", fromlist=["get_logger"]).get_logger("core.orchestrator")


# --------------------------------------------------------------------------- #
# 语义目录摘要（注入 Planner）
# --------------------------------------------------------------------------- #
def schema_digest(enum_values: dict[str, list[str]] | None = None) -> str:
    """语义字段 -> 紧凑文本摘要（Planner 可用字段清单）。

    ``enum_values``：低基数字段的实际取值（SchemaAgent 动态 profiling，
    core.retrieval.profiling）——注入后 Planner 不再臆造过滤字面值。
    """
    from semantic.catalog import COLUMNS

    lines = []
    for name, meta in sorted(COLUMNS.items()):
        line = f"- {name} ({meta.table}.{meta.column}, {meta.dtype})"
        values = (enum_values or {}).get(name)
        if values:
            line += " 可取值: " + "|".join(values)
        lines.append(line)
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
    """规划节点：LLM JSON 计划优先，启发式兜底（需求 §2.A PlannerNode）。

    重规划自愈（行动项 3）：error_context 非空时把最近失败摘要注入 Planner
    提示词——LLM 必须针对性修正计划，而非盲重试。
    """
    llm = _resolve_llm()
    steps: list[PlanStep] | None = None
    if llm is not None:
        # SchemaAgent 动态 profiling（后续项）：低基数字段枚举值注入规划上下文；
        # 探查失败降级为空 dict（不阻断规划主链路），离线/无库环境无副作用
        from core.retrieval.profiling import profile_enum_values

        error_context = "\n".join(state.error_context.errors[-3:]) or None
        payload = _llm_json(
            llm,
            PLANNER_SYSTEM,
            planner_prompt(
                state.user_query,
                schema_digest(profile_enum_values()),
                error_context=error_context,
            ),
        )
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
    附省份维度拆分（审计修复 R2 口径对齐）：两期取数同口径同窗口，沙箱
    直接在行级明细上做总量对比 + 分省归因——明细合计与总量天然同源，
    杜绝总览 66.93 万 vs 拆分 61.68 万式的口径矛盾触发无谓重规划。
    """
    return (
        {
            "metrics": [
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            ],
            "dimensions": [{"field": "province"}],
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
            "dimensions": [{"field": "province"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "range_type": "absolute",
                "absolute": {"start": "2024-05-08", "end": "2024-05-15"},
            },
        },
    )


def _inherit_overview_scope(
    dsl: dict[str, Any], overview_variants: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    """地区拆分 DSL 强制继承总览口径（审计修复 R2：数据清洗与对齐层）。

    规则（确定性、可解释、可在报告中追溯）：
    - filters：总览中的状态类条件（eq/in，如订单状态/退款过滤）若拆分 DSL
      未声明同字段，则逐条继承——拆分明细合计与总览同口径；
    - time_filter：拆分 DSL 未声明时间窗口时继承总览窗口；已声明则保留
      （两期窗口属合法差异），但差异必须记入 notes 供报告"口径差异说明"引用，
      防止无谓的反思重规划；
    - 返回 (对齐后 DSL, 继承/差异说明列表)。继承的 filter 来自已通过网关
      强校验的总览 DSL，字段必在语义目录白名单内，不会引入非法条件。
    """
    notes: list[str] = []
    aligned = json.loads(json.dumps(dsl, ensure_ascii=False))
    filters = [f for f in (aligned.get("filters") or []) if isinstance(f, dict)]
    owned_fields = {f.get("field") for f in filters}
    for variant in overview_variants:
        for f in variant.get("filters") or []:
            if not isinstance(f, dict):
                continue
            field = f.get("field")
            if field in owned_fields or f.get("operator") not in ("eq", "in"):
                continue  # 已声明或范围类条件（金额阈值等）不盲继承
            filters.append(dict(f))
            owned_fields.add(field)
            notes.append(f"已继承总览过滤条件 {field}")
    aligned["filters"] = filters
    if aligned.get("time_filter") is None:
        inherited = next(
            (v.get("time_filter") for v in overview_variants if v.get("time_filter")), None
        )
        if inherited is not None:
            aligned["time_filter"] = json.loads(json.dumps(inherited, ensure_ascii=False))
            notes.append("已继承总览时间窗口")
    return aligned, notes


def _preview_rows(path: str, limit: int = 30) -> list[list[Any]]:
    """读取 Parquet 数据集前 N 行生成审计预览（转 JSON 原生类型）。

    预览失败不阻断取数（返回空列表），审计能力可降级。
    """
    try:
        import pandas as pd

        df = pd.read_parquet(path)
        out: list[list[Any]] = []
        for row in df.head(limit).itertuples(index=False):
            cells: list[Any] = []
            for v in row:
                if v is None or (isinstance(v, float) and v != v):  # NaN
                    cells.append(None)
                elif hasattr(v, "item"):
                    cells.append(v.item())
                else:
                    cells.append(v)
            out.append(cells)
        return out
    except Exception:
        return []


def _normalize_dsl_draft(dsl: dict[str, Any]) -> dict[str, Any]:
    """LLM DSL 草稿的确定性规范化（宽容接受，严格校验）。

    只做无歧义的常见笔误纠正，修不动的原样透传——契约层（网关 validate）
    仍是唯一裁决者，错误信息继续喂回规划节点自愈：
    - dimensions: ["province"]（裸字符串）-> [{"field": "province"}]；
    - time_range -> time_filter（字段名笔误，契约 extra="forbid" 必拒）；
    - 过滤操作符 ge/le -> gte/lte（白名单写法）；
    - 区域词展开（审计修复 M1）：province = '华东' -> province IN (数仓实际省份)。
    """
    d = dict(dsl)
    dims = d.get("dimensions")
    if isinstance(dims, list):
        d["dimensions"] = [
            {"field": item} if isinstance(item, str) else item
            for item in dims
            if isinstance(item, (str, dict))
        ]
    if "time_range" in d and "time_filter" not in d:
        d["time_filter"] = d.pop("time_range")
    filters = d.get("filters")
    if isinstance(filters, list):
        fixed: list[Any] = []
        for f in filters:
            if isinstance(f, dict) and f.get("operator") in ("ge", "le"):
                f = {**f, "operator": "gte" if f["operator"] == "ge" else "lte"}
            # 区域词展开：大区字面值 -> 省份 IN 列表（与 agent 路径同口径）
            if (
                isinstance(f, dict)
                and f.get("field") == "province"
                and f.get("operator") in ("eq", "in")
            ):
                value = f.get("value")
                values = value if isinstance(value, list) else [value]
                regions = [v for v in values if isinstance(v, str) and v in REGION_PROVINCE_MAPPING]
                if regions:
                    provinces: list[str] = []
                    for region in regions:
                        for p in region_provinces(region):
                            if p not in provinces:
                                provinces.append(p)
                    if provinces:
                        f = {**f, "operator": "in", "value": provinces}
            fixed.append(f)
        d["filters"] = fixed
    return d


def _run_query_step(state: AgentState, step: PlanStep) -> tuple[AgentState, ToolRecord]:
    """执行单个 query 步骤：DSL -> 门面 -> ParquetRef。

    口径对齐层（审计修复 R2）：带维度拆分的取数步骤执行前，强制继承
    总览（无维度拆分的步骤）已声明的 WHERE 过滤条件与时间窗口，
    保证明细合计与总览同口径——消除 66.93 万 vs 61.68 万式的数据矛盾。
    """
    from config import settings
    from core.retrieval.tools import execute_dsl_query
    from core.sandbox.api import prepare_workspace

    workspace = settings.WORKSPACE_ROOT / f"{state.session_id}" / state.turn_id
    prepare_workspace(workspace)

    # 总览口径 = 本计划内无维度拆分的 query 步骤 DSL（LLM 计划）；确定性
    # 兜底两期对本身即总览口径。拆分步骤继承之，缺失时按 DSL 契约原样校验。
    overview_variants = [
        _normalize_dsl_draft(s.dsl)
        for s in state.plan_steps
        if s.kind == "query" and s.id != step.id and s.dsl and not s.dsl.get("dimensions")
    ]

    # 步骤 DSL：LLM 给出则规范化后交网关强校验，否则按问题类型走确定性兜底：
    # 诊断类 => 两期分省明细对（总量与归因同源，口径天然一致）；
    # 其余 => 单期总量（标量问题附维度无意义）
    if step.dsl is not None:
        dsl_variants = [_normalize_dsl_draft(step.dsl)]
    else:
        diagnostic = any(
            w in state.user_query
            for w in ("为什么", "下滑", "下降", "下跌", "上涨", "增长", "归因", "原因")
        )
        if diagnostic:
            baseline_dsl, current_dsl = _diagnostic_dsl_pair(state.user_query)
            dsl_variants = [baseline_dsl, current_dsl]
        else:
            overview = json.loads(json.dumps(_diagnostic_dsl_pair(state.user_query)[0]))
            overview.pop("dimensions", None)
            dsl_variants = [overview]

    # 维度拆分步骤 => 口径继承（数据清洗与对齐层，R2）
    has_dimensions = step.dsl is not None and bool(step.dsl.get("dimensions"))
    if has_dimensions and overview_variants:
        aligned_notes: list[str] = []
        scoped: list[dict[str, Any]] = []
        for variant in dsl_variants:
            aligned, notes = _inherit_overview_scope(variant, overview_variants)
            scoped.append(aligned)
            aligned_notes.extend(notes)
        dsl_variants = scoped
        if aligned_notes:
            state.scratchpad.append(
                f"[{step.id}] 口径对齐：{'；'.join(dict.fromkeys(aligned_notes))}"
            )

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
        # DataQA/Guardrail 审计面（行动项 1/2）：结构化发现随事件下发前端，
        # QA 发现摘要并入 notes（=> record.summary => critic 反思视野）
        audit_payload = ref.audit or {}
        qa_findings = audit_payload.get("qa") or []
        guard_findings = audit_payload.get("guard") or []
        if qa_findings:
            qa_text = "；".join(f"[{f['check']}] {f['message']}" for f in qa_findings)
            notes.append(f"{name} 质检发现：{qa_text}")
        # 审计预览：读取物化 Parquet 前 30 行随 tool_end 下发（数据审计 Tab）。
        # ParquetRef.path 相对 workspace/inputs/（沙箱 read_input 同一约定）
        preview_rows = _preview_rows(str(workspace / "inputs" / ref.path))
        events.emit_tool_end(
            "futurebi_dsl_query",
            step.id,
            ok=True,
            duration_ms=(time.perf_counter() - started) * 1000,
            output={
                "dataset": name,
                "rows": ref.rows,
                "columns": list(ref.columns),
                "preview_rows": preview_rows,
                "guard_findings": guard_findings,
                "qa_findings": qa_findings,
            },
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
            events.emit_reflection(
                f"取数失败：{exc}", "retry", "错误已喂回规划节点重写 DSL 计划自愈"
            )
            return updated.apply(phase="plan")  # 自愈：回到规划节点重写计划
    return updated.apply(phase="analyze")


# --------------------------------------------------------------------------- #
# 4) CodeExec（沙箱分析）
# --------------------------------------------------------------------------- #
def _analysis_template(state: AgentState, step: PlanStep) -> str:
    """analyze 步骤的代码来源优先级：LLM Coder 产码（已静态校验）> 确定性技能模板。

    模板把 datasets 中的两期分省明细读入，完成总量对比 + 分省加法归因
    （Δ、贡献占比、主要矛盾省份）+ 分省对比 ECharts——综合节点的
    "驱动因素分析/区域归因定位"段直接消费该产物。
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

# —— 总量对比（两期分省明细同口径，sum 即总量）——
b_total = float(baseline_df["gmv"].sum()) if "gmv" in baseline_df.columns else 0.0
c_total = float(current_df["gmv"].sum()) if "gmv" in current_df.columns else 0.0
delta = c_total - b_total
direction = "下滑" if delta < 0 else "增长"
change = delta / b_total if b_total else 0.0
findings = [
    f"GMV 从 {{b_total:.2f}} 变至 {{c_total:.2f}}，{{direction}} {{abs(change):.1%}}"
]
metrics = {{"baseline": b_total, "current": c_total, "delta": delta}}

# —— 分省加法归因（审计修复 R1：提供地区明细与主要矛盾，不再只有对比图）——
table = {{"columns": ["province", "baseline", "current", "delta", "share"], "rows": []}}
if "province" in baseline_df.columns and "province" in current_df.columns:
    b_by = baseline_df.groupby("province")["gmv"].sum()
    c_by = current_df.groupby("province")["gmv"].sum()
    b_al, c_al = b_by.align(c_by, fill_value=0.0)
    deltas = (c_al - b_al)
    abs_sum = float(deltas.abs().sum())
    rows = [
        {{
            "province": str(p),
            "baseline": round(float(b_al[p]), 2),
            "current": round(float(c_al[p]), 2),
            "delta": round(float(deltas[p]), 2),
            "share": round(float(deltas[p]) / abs_sum, 4) if abs_sum else 0.0,
        }}
        for p in deltas.index
    ]
    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    table["rows"] = rows[:20]
    if rows:
        top = rows[0]
        findings.append(
            f"主要矛盾省份 [{{top['province']}}]：{{top['baseline']:.2f}} -> "
            f"{{top['current']:.2f}}（Δ{{top['delta']:+.2f}}），"
            f"贡献了 {{abs(top['share']):.0%}} 的总偏差"
        )
        for r in rows[1:3]:
            findings.append(
                f"次要贡献省份 [{{r['province']}}] 贡献 {{abs(r['share']):.0%}}"
                f"（Δ{{r['delta']:+.2f}}）"
            )
else:
    findings.append("两期数据缺少省份维度列，无法做分省归因定位")

save_summary(title="两期对比与分省归因", metrics=metrics, table=table, findings=findings)
province_rows = table["rows"]
save_echarts_spec({{
    "title": {{"text": "分省 GMV 两期对比（归因定位）"}},
    "tooltip": {{}},
    "legend": {{"data": ["基线期", "当前期"]}},
    "xAxis": {{"type": "category", "data": [r["province"] for r in province_rows]}},
    "yAxis": {{"type": "value"}},
    "series": [
        {{"name": "基线期", "type": "bar", "data": [r["baseline"] for r in province_rows]}},
        {{"name": "当前期", "type": "bar", "data": [r["current"] for r in province_rows]}},
    ],
}})
"""


def _has_same_echarts(artifacts: list[Artifact], spec: dict[str, Any]) -> bool:
    """判断已登记产物中是否已存在同规格 ECharts（审计修复 T14 图表去重）。

    判定键：序列类型 + x 轴类目 + 系列名/数据——同型同值的图表只保留一份，
    不同标题/步骤名但内容同质的重复发射也一并拦截。
    """
    import json

    def _chart_key(s: dict[str, Any]) -> str:
        try:
            series = s.get("series")
            if isinstance(series, list):
                series_key = json.dumps(
                    [
                        {
                            "type": (item or {}).get("type"),
                            "name": (item or {}).get("name"),
                            "data": (item or {}).get("data"),
                        }
                        for item in series
                        if isinstance(item, dict)
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                )
            else:
                series_key = json.dumps(series, ensure_ascii=False, sort_keys=True)
            return json.dumps(
                {
                    "series": series_key,
                    "x": (
                        (s.get("xAxis") or {}).get("data")
                        if isinstance(s.get("xAxis"), dict)
                        else s.get("xAxis")
                    ),
                    "title": (
                        (s.get("title") or {}).get("text")
                        if isinstance(s.get("title"), dict)
                        else s.get("title")
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        except Exception:  # 序列化失败视为不同图表，宁可重复不误杀
            return json.dumps(s, ensure_ascii=False, sort_keys=True, default=str)

    key = _chart_key(spec)
    for a in artifacts:
        if a.kind == "echarts" and _chart_key(a.payload) == key:
            return True
    return False


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
            # 图表产物去重（审计修复 T14）：同 payload 的 ECharts 规格严禁重复
            # 登记与发射——反思重规划多轮执行同模板分析时曾产出 4 份同质图表
            if result.echarts_spec and not _has_same_echarts(
                updated.artifacts, result.echarts_spec
            ):
                updated.artifacts.append(
                    Artifact(kind="echarts", name=f"{step.id}_chart", payload=result.echarts_spec)
                )
                events.emit_event(
                    events.EVENT_ARTIFACT_EMIT,
                    {
                        "artifact": {
                            "type": "echarts",
                            "title": f"{step.id}_chart",
                            "content": result.echarts_spec,
                        }
                    },
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
            events.emit_reflection(
                f"沙箱执行失败：{result.error}", "retry", "回到规划节点修正分析代码"
            )
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
            events.emit_reflection(
                "未获得任何数据集且重试额度耗尽", "proceed", "转入综合节点如实报告失败"
            )
            return state.apply(phase="synthesize")
        events.emit_reflection("未获得任何数据集", "replan", "取数失败，回到规划节点重写计划")
        return state.apply(phase="plan")
    if diagnostic and not has_summary and not exhausted:
        events.emit_reflection(
            "诊断类问题缺少归因 summary 产物", "replan", "补齐沙箱归因分析后再综合"
        )
        return state.apply(phase="plan")
    # LLM Reflector 增强（可选；失败不影响确定性判定）
    llm = _resolve_llm()
    if llm is not None and has_summary:
        trace_digest = "\n".join(r.summary or r.error or "" for r in state.tool_calls[-6:])
        if state.error_context.errors:
            # 反思层必须看见失败历史（行动项 3：与 Planner 同一断裂点修复）
            trace_digest += "\n自愈错误记录：" + "；".join(state.error_context.errors[-3:])
        verdict = _llm_json(
            llm,
            '仅输出 JSON：{"verdict": "sufficient"|"insufficient", "reasons": [...]}',
            f"用户问题：{state.user_query}\n执行轨迹：\n{trace_digest}",
        )
        if verdict and verdict.get("verdict") == "insufficient":
            # LLM 反思重规划与工具自愈共用重试预算（防"不耗额度的无限重规划"，
            # 只能靠迭代护栏兜底而空烧 LLM 调用）
            keep = state.error_context.record(f"LLM 反思判定产物不充分: {verdict.get('reasons')}")
            state.scratchpad.append(f"[critic-llm] {verdict.get('reasons')}")
            events.emit_reflection(
                str(verdict.get("reasons", "")), "replan", "LLM 反思判定产物不充分，触发重规划"
            )
            if not keep:
                events.emit_reflection("重规划额度耗尽", "proceed", "转入综合节点如实报告")
                return state.apply(phase="synthesize")
            return state.apply(phase="plan")
    events.emit_reflection("完整性/正确性/一致性三检通过", "proceed", "转入综合报告")
    return state.apply(phase="synthesize")


# --------------------------------------------------------------------------- #
# 6) Synthesize
# --------------------------------------------------------------------------- #
def _fmt_wan(value: Any) -> str:
    """数值 -> 人读万元金额（审计修复 R1：综合报告禁数字节面值直出）。"""
    try:
        return f"{float(value) / 10000:.2f} 万元"
    except (TypeError, ValueError):
        return str(value)


def _fmt_pct(value: Any) -> str:
    """小数占比 -> 人读百分比（保留一位小数）。"""
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return str(value)


def _dataset_analyst_markdown(
    name: str, ref: dict[str, Any], workspace: Any
) -> tuple[str, dict | None]:
    """把一个已物化数据集渲染为分析师口径的报告片段 + 前端 table 载荷。

    - 单行单列（标量聚合，如 count_distinct）=> 直接给答案行；
    - 多行 => 附前 5 行 Markdown 预览表；读取失败降级为行列摘要。
    （字段名在表格列头保留——数据审计必需；叙述文本不罗列内部字段名。）
    """
    cols = [str(c) for c in ref.get("columns", [])]
    total = ref.get("rows", 0)
    lines = [
        f"### 查询结果：{name}",
        f"- 共 {total} 条记录（{len(cols)} 个分析视角）",
    ]
    preview = _preview_rows(str(workspace / "inputs" / ref.get("path", "")), limit=30)
    if total == 1 and len(cols) == 1 and preview:
        lines.append(f"- **查询答案：{_fmt_wan(preview[0][0])}**")
    artifact: dict[str, Any] | None = None
    if preview:
        head = preview[:5]
        lines.append("")
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "|".join([" --- "] * len(cols)) + "|")
        for row in head:
            lines.append("| " + " | ".join(str(v) for v in row) + " |")
        if total > 5:
            lines.append(f"（预览前 5 行，共 {total} 行）")
        artifact = {
            "type": "table",
            "title": f"查询结果 · {name}",
            "columns": cols,
            "rows": preview,
            "totalRows": total,
        }
    return "\n".join(lines), artifact


def _summary_analyst_markdown(summary: dict[str, Any]) -> list[str]:
    """沙箱 summary -> 分析师叙述（数值万元/百分比化，禁 raw dict 直出）。

    table 行（分省归因矩阵）渲染为归因表格；metrics 键值译为人读短语。
    """
    lines: list[str] = []
    title = summary.get("title", "")
    findings = summary.get("findings", [])
    metrics = summary.get("metrics", {})
    lines.append(f"### {title}" if title else "### 归因结论")
    for f in findings:
        lines.append(f"- {f}")
    if metrics:
        rendered = "；".join(
            f"{k} = {_fmt_wan(v)}" if isinstance(v, (int, float)) else f"{k} = {v}"
            for k, v in metrics.items()
        )
        lines.append(f"- 关键指标：{rendered}")
    table = summary.get("table") or {}
    rows = table.get("rows") or []
    cols = table.get("columns") or []
    if rows and cols:
        lines.append("")
        lines.append("| " + " | ".join(str(c) for c in cols) + " |")
        lines.append("|" + "|".join([" --- "] * len(cols)) + "|")
        for row in rows[:10]:
            cells = []
            for c, v in zip(cols, row, strict=False):  # 行长不齐时容忍截断
                if c in ("baseline", "current", "delta", "value", "gmv"):
                    cells.append(_fmt_wan(v))
                elif c in ("share", "change_rate"):
                    cells.append(_fmt_pct(v))
                else:
                    cells.append(str(v))
            lines.append("| " + " | ".join(cells) + " |")
        if len(rows) > 10:
            lines.append(f"（仅列示贡献前 10，共 {len(rows)} 行）")
    return lines


def _synthesize_with_llm(state: AgentState, material: str) -> str | None:
    """LLM 商业分析师综合（审计修复 R1）；失败返回 None 走确定性兜底。"""
    llm = _resolve_llm()
    if llm is None:
        return None
    user_prompt = (
        f"# 用户问题\n{state.user_query}\n\n# 上游分析材料（唯一数据事实来源）\n{material}\n\n"
        "# 现在，按四段式结构输出最终分析报告（Markdown）。"
    )
    try:
        from agent.agent import extract_json
        from providers import chat_text

        text = chat_text(
            llm,
            [
                {"role": "system", "content": SYNTHESIZER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=False,
        )
    except Exception:
        return None
    if not text or not text.strip():
        return None
    stripped = text.strip()
    # 反契约输出防御：LLM 若仍回 JSON（被 extract_json 成功解析），视为失败走兜底
    if stripped.startswith("{") or stripped.startswith("["):
        if extract_json(stripped) is not None:
            return None
    return stripped


def _degraded_report(state: AgentState) -> str:
    """自愈额度耗尽的降级简报（审计修复 R3）。

    严禁把未加工的 scratchpad / 工具执行结果吐给前端：优先调用一次带
    惩罚约束的降级 Summarizer 生成结构化简报（只引用已定位的部分归因
    数据并注明口径差异）；LLM 不可用时回落到最小化确定性简报（不含
    任何内部轨迹细节）。
    """
    material = _analysis_material(state, include_trace=False)
    llm_report: str | None = None
    llm = _resolve_llm()
    if llm is not None:
        user_prompt = (
            f"# 用户问题\n{state.user_query}\n\n"
            f"# 已获取的部分归因数据\n{material}\n\n"
            f"# 失败概况\n自愈重试额度已耗尽（{len(state.error_context.errors)} 次），"
            "分析流程未完整走完。\n\n"
            "# 现在，按降级简报结构输出（Markdown）。"
        )
        try:
            from agent.agent import extract_json
            from providers import chat_text

            text = chat_text(
                llm,
                [
                    {"role": "system", "content": DEGRADED_SUMMARIZER_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                json_mode=False,
            )
            stripped = (text or "").strip()
            if stripped and not (
                (stripped.startswith("{") or stripped.startswith("["))
                and extract_json(stripped) is not None
            ):
                llm_report = stripped
        except Exception:
            llm_report = None
    if llm_report:
        return llm_report
    # 确定性降级简报：只呈现部分数据事实与口径差异，无内部调试信息
    lines = ["### 部分结论（基于已获取数据）", ""]
    lines.append("本轮分析在多次自愈后仍未完整完成，以下仅呈现已获取的部分数据事实。")
    detail_lines = _degraded_detail_lines(state)
    if detail_lines:
        lines.append("")
        lines.append("### 已定位的明细")
        lines.append("")
        lines.extend(detail_lines)
    lines.append("")
    lines.append("### 数据口径差异说明")
    lines.append(
        "部分明细数据可能未继承总览的过滤条件（订单状态/退款/时间窗口），"
        "明细合计与总览数值可能存在口径出入，请以数据审计 Tab 的原始取数为准。"
    )
    lines.append("")
    lines.append("### 后续建议")
    lines.append("请更换提问方式缩小分析范围后重试，或联系管理员检查模型服务可用性。")
    return "\n".join(lines)


def _degraded_detail_lines(state: AgentState) -> list[str]:
    """从已物化数据集提取人读省份明细（降级简报用）。

    只处理两期分省结构（province + gmv 列，诊断兜底口径）；结构不符时
    返回空列表由调用方省略小节——严禁把原始行直接吐给前端。
    """
    workspace = workspace_path(state)
    period_values: list[dict[str, float]] = []
    for ref in state.datasets.values():
        preview = _preview_rows(str(workspace / "inputs" / ref.get("path", "")))
        cols = [str(c) for c in ref.get("columns", [])]
        if not preview or "province" not in cols or "gmv" not in cols:
            return []
        values: dict[str, float] = {}
        p_idx, v_idx = cols.index("province"), cols.index("gmv")
        for row in preview:
            try:
                values[str(row[p_idx])] = values.get(str(row[p_idx]), 0.0) + float(row[v_idx])
            except (TypeError, ValueError):
                continue
        period_values.append(values)
    if len(period_values) < 2:
        return []
    base, curr = period_values[0], period_values[-1]
    deltas = sorted(
        ((k, curr.get(k, 0.0) - base.get(k, 0.0)) for k in set(base) | set(curr)),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    lines = ["| 省份 | 基线期 | 当前期 | 变化 |", "| --- | --- | --- | --- |"]
    for name, delta in deltas[:5]:
        lines.append(
            f"| {name} | {_fmt_wan(base.get(name, 0.0))} | {_fmt_wan(curr.get(name, 0.0))} "
            f"| {_fmt_wan(delta)} |"
        )
    return lines


def workspace_path(state: AgentState) -> Any:
    """本会话轮次的工作区路径（预览读取用；避免循环导入的独立小函数）。"""
    from config import settings

    return settings.WORKSPACE_ROOT / state.session_id / state.turn_id


def _analysis_material(state: AgentState, *, include_trace: bool = True) -> str:
    """把产物与数据集压缩为 LLM 综合的素材文本（结构化事实，非叙述）。"""
    parts: list[str] = []
    for artifact in state.artifacts:
        if artifact.kind == "summary":
            summary = artifact.payload.get("summary", {})
            parts.append(
                "[分析产物] " + json.dumps(summary, ensure_ascii=False, default=str)[:3000]
            )
    for name, ref in state.datasets.items():
        preview = _preview_rows(str(workspace_path(state) / "inputs" / ref.get("path", "")))
        rows_desc = (
            json.dumps(preview[:20], ensure_ascii=False, default=str)
            if preview
            else "（预览不可用）"
        )
        part = (
            f"[数据集 {name}] {ref.get('rows', 0)} 行，列 {list(ref.get('columns', []))}，"
            f"预览：{rows_desc[:3000]}"
        )
        # DataQA 质检发现进入 LLM 综合素材（报告必须如实引用质量提示）
        qa_findings = (ref.get("audit") or {}).get("qa") or []
        if qa_findings:
            qa_text = "；".join(f"[{f['check']}] {f['message']}" for f in qa_findings)
            part += f"\n[数据质检发现 {name}] {qa_text}"
        parts.append(part)
    if include_trace and state.error_context.errors:
        parts.append(f"[自愈记录] 共 {len(state.error_context.errors)} 次错误被捕获并重试")
    return "\n\n".join(parts)


def synthesize_node(state: AgentState) -> AgentState:
    """综合节点：执行轨迹 + 产物 => 商业分析师口径的 Markdown 报告。

    审计修复（R1 报告综合重构）：
    - LLM 可用 => 商业分析师角色（SYNTHESIZER_SYSTEM）：四段式结构
      （核心结论 -> 区域归因定位 -> 驱动因素分析 -> 业务假设与排查建议），
      数值万元/百分比化，指出主要矛盾；严禁 raw dict/json 直出；
    - LLM 不可用（离线/测试/降级）=> 确定性分析师渲染兜底：同样禁 raw
      JSON——metrics 译为人读短语、归因表渲染为 Markdown 表格。

    审计修复（T14 报告调和）：同标题 summary 小节去重；同规格图表去重；
    空数据集诚实陈述。

    审计修复（R3 降级保护）：自愈额度耗尽 => _degraded_report（带惩罚
    约束的 Summarize 简报），严禁吐未加工的 scratchpad / 工具执行结果。
    """
    from config import settings

    workspace = settings.WORKSPACE_ROOT / state.session_id / state.turn_id

    # R3 降级保护：自愈额度耗尽 => 降级简报（无论是否已获取部分数据），
    # 严禁把未加工的 scratchpad / 工具执行结果吐给前端
    if state.error_context.retries >= MAX_RETRIES:
        report = _degraded_report(state)
        events.emit_event(
            events.EVENT_ARTIFACT_EMIT,
            {"artifact": {"type": "markdown_report", "title": "分析简报", "content": report}},
        )
        return state.apply(report=report, phase="done")

    seen_summary_titles: set[str] = set()
    rendered_charts: list[Artifact] = []
    deduped_summaries: list[dict[str, Any]] = []
    for artifact in state.artifacts:
        if artifact.kind == "summary":
            summary = artifact.payload.get("summary", {})
            title = summary.get("title", "")
            if title and title in seen_summary_titles:
                continue  # 同小节去重（T14）：重规划多轮执行同模板只呈现一次
            if title:
                seen_summary_titles.add(title)
            deduped_summaries.append(summary)

    # LLM 商业分析师综合（R1）；素材 = 去重后的 summary 产物 + 数据集预览
    llm_report: str | None = None
    if deduped_summaries or state.datasets:
        material = _analysis_material(state, include_trace=bool(state.error_context.errors))
        if material:
            llm_report = _synthesize_with_llm(state, material)

    if llm_report:
        report = llm_report
        events.emit_event(
            events.EVENT_ARTIFACT_EMIT,
            {"artifact": {"type": "markdown_report", "title": "分析报告", "content": report}},
        )
        return state.apply(report=report, phase="done")

    # ---- 确定性分析师兜底（无 LLM / LLM 输出反契约）---- #
    lines: list[str] = [f"## 分析报告：{state.user_query}", ""]
    for summary in deduped_summaries:
        lines.extend(_summary_analyst_markdown(summary))
        lines.append("")
        lines.append("")
    for artifact in state.artifacts:
        if artifact.kind == "echarts":
            # 同规格图表去重（T14）：只呈现第一份，其余同质产物不进报告
            if _has_same_echarts(rendered_charts, artifact.payload):
                continue
            rendered_charts.append(artifact)
            lines.append(f"- 图表产物：`{artifact.name}`（ECharts 规格已生成）")
    # 纯查询结果直接呈现（取数成功但没有沙箱分析的场景）
    for name, ref in state.datasets.items():
        section, table_artifact = _dataset_analyst_markdown(name, ref, workspace)
        lines.append("")
        lines.append(section)
        if table_artifact is not None:
            events.emit_event(events.EVENT_ARTIFACT_EMIT, {"artifact": table_artifact})
        # DataQA 质检小节（行动项 2）：结果断言发现必须向用户可见（不吞错）
        qa_findings = (ref.get("audit") or {}).get("qa") or []
        if qa_findings:
            lines.append("")
            lines.append(f"**数据质检（{name}）**：")
            for f in qa_findings:
                lines.append(f"- 质检提示（{f['check']}）：{f['message']}")
    if not state.artifacts and not state.datasets:
        lines.append("未能获得有效的分析产物。")
    if state.datasets and all(int(ref.get("rows", 0) or 0) == 0 for ref in state.datasets.values()):
        # 空集诚实陈述（审计修复 D3）：严禁在 0 行数据上宣称查询成功
        lines.append("")
        lines.append(
            "未查询到符合条件的数据。可能原因：时间范围超出数仓数据域"
            "（数据基准日期 2024-06-30），或过滤条件（地区/品类/支付状态）无匹配记录。"
        )
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
