"""编排状态契约（AgentState）：图式编排器的事实来源。

需求 §2.A 状态字段全覆盖：
- 身份：session_id / turn_id / trace_id；
- 任务：user_query、plan_steps（含依赖的任务列表）、clarification；
- 执行：tool_calls / tool_results（带 token 预算修剪的轨迹）、scratchpad
  （假设 / 检验 / 中间推理）、artifacts（ParquetRef / summary / ECharts）；
- 韧性：error_context（自愈栈 + 重试计数，上限 MAX_RETRIES=3）；
- 控制：phase（图路由信号）、human_reply（HITL 恢复载荷）。

全部模型 extra="forbid"：编排层自身的演进受契约约束，与项目纪律一致。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from config import settings as _settings
from tools.registry import default_registry


def _default_autonomy() -> str:
    """自主性默认档走 settings（生产 L2；测试经 conftest 置 L4）。"""
    return _settings.AGENT_DEFAULT_AUTONOMY


# 自愈重试上限（需求：max 3 retries）
MAX_RETRIES = 3

# 子任务卡内置能力名：不属于 tools.registry 注册中心、但可在子图执行路径上
# 被白名单裁剪的编排内置能力（沙箱代码执行）。allowed_tools 合法名 =
# registry 工具名 + 本集合（query/analyze 步骤的能力归并见 subagent.py）。
BUILTIN_CAPABILITIES = frozenset({"run_code"})

# 工具轨迹 token 预算（估算：1 token ≈ 4 字符）
TOOL_HISTORY_BUDGET_CHARS = 60_000

Phase = Literal[
    "clarify",  # 需要澄清（HITL 中断）
    "plan_review",  # 分析计划待审批（HITL 中断，M2 Plan Mode）
    "high_risk",  # 沙箱代码执行待确认（HITL 中断，L3 高危确认）
    "plan",  # 规划 / 重规划
    "query",  # DSL 取数
    "analyze",  # 沙箱分析
    "critique",  # 反思
    "synthesize",  # 综合报告
    "done",  # 终态
]


class PlanStep(BaseModel):
    """计划步骤：目标 + 类型 + 依赖（DAG）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="步骤标识（plan 内唯一，如 s1/s2）")
    goal: str = Field(..., description="该步骤要回答/完成的子目标")
    kind: Literal["query", "analyze", "synthesize"] = Field(
        ..., description="步骤类型：取数 / 沙箱分析 / 综合"
    )
    depends_on: list[str] = Field(default_factory=list, description="前置步骤 id 列表")
    # 取数步骤的 DSL 草稿（query 类型时由 Planner 给出；analyze 可空）
    dsl: dict[str, Any] | None = None
    # 分析步骤的代码草稿 / 技能调用（analyze 类型时给出）
    code: str | None = None
    status: Literal["pending", "running", "done", "failed"] = "pending"
    # B 线（M3）fan-out 任务卡（规格 §6.3）：planner 产出时携带（三模式共用通道，
    # 由任务卡 mode 字段区分 attribution/hypothesis/comparison）；缺省 None=普通步骤
    fanout_tasks: list[dict[str, Any]] | None = None


# --------------------------------------------------------------------------- #
# B 线（M3）Subagent 契约（规格 §6.1）：任务卡 / 报告 —— 契约 SSOT 在 state.py
# --------------------------------------------------------------------------- #


class SubagentBudget(BaseModel):
    """子任务预算硬顶（extra="forbid"）。"""

    model_config = ConfigDict(extra="forbid")

    max_steps: int = 6
    timeout_seconds: float = 120.0


class SubagentTask(BaseModel):
    """受限任务卡：上下文切片 + 工具子集白名单 + 预算（规格 §6.1）。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    goal: str
    # 上下文切片：schema digest 片段 / 相关过滤条件 / 上轮 DSL 引用
    context_slice: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    budget: SubagentBudget = Field(default_factory=SubagentBudget)

    def model_post_init(self, __context: Any) -> None:
        unknown = (
            set(self.allowed_tools)
            - set(default_registry().tool_names())
            - set(BUILTIN_CAPABILITIES)
        )
        if unknown:
            raise ValueError(f"allowed_tools 越过注册中心白名单: {sorted(unknown)}")


class SubagentReport(BaseModel):
    """结构化报告（父图唯一消费物）；audit 与 ParquetRef.audit 同源同格式。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: Literal["done", "failed", "timeout"] = "done"
    findings: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)


class ToolRecord(BaseModel):
    """一次工具调用记录（轨迹与审计的最小单元）。"""

    model_config = ConfigDict(extra="forbid")

    step_id: str = ""
    tool: str = Field(..., description="工具名（execute_dsl_query / run_code / skill:*）")
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    # 结果摘要（字符串化，供 LLM 上下文与 token 预算修剪）
    summary: str = ""
    duration_ms: float = 0.0
    error: str | None = None


class Artifact(BaseModel):
    """产物：数据集 / 统计摘要 / 图表规格。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["parquet", "summary", "echarts"]
    name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ErrorContext(BaseModel):
    """自愈上下文：结构化错误栈 + 重试计数。"""

    model_config = ConfigDict(extra="forbid")

    errors: list[str] = Field(default_factory=list, description="按序追加的错误摘要")
    retries: int = Field(default=0, ge=0, le=MAX_RETRIES)

    def record(self, error: str) -> bool:
        """记录一次错误；超过重试上限返回 False（编排层据此终止）。"""
        self.errors.append(error[-2000:])
        self.retries += 1
        return self.retries <= MAX_RETRIES


class AgentState(BaseModel):
    """编排器全局状态（跨节点共享，图引擎不可变传递、节点返回增量）。"""

    model_config = ConfigDict(extra="forbid")

    # 身份
    session_id: str = "default"
    turn_id: str = "t1"
    trace_id: str = "tr1"
    # 任务
    user_query: str = ""
    # 会话历史摘要（多轮上下文：同会话追问时由调用方装载，注入 Planner 提示词；
    # 空串 = 单轮/无历史，提示词与旧契约逐字一致）
    history_digest: str = ""
    plan_steps: list[PlanStep] = Field(default_factory=list)
    clarification: str | None = Field(default=None, description="向用户发出的澄清问题")
    clarification_options: list[str] = Field(
        default_factory=list, description="选项式澄清候选（前端 pill 按钮，点击即答复）"
    )
    clarification_rounds: int = Field(
        default=0, description="澄清已发生轮次（二轮仍歧义转带假设作答，防循环）"
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="口径假设（分级透明作答，十九期 M3）：选定合理口径的理由说明，报告头部呈现",
    )
    human_reply: str | None = Field(default=None, description="HITL 恢复时的用户答复")
    # 执行轨迹
    tool_calls: list[ToolRecord] = Field(default_factory=list)
    scratchpad: list[str] = Field(default_factory=list, description="中间推理/假设/检验记录")
    artifacts: list[Artifact] = Field(default_factory=list)
    # 报告
    report: str = ""
    # 诚实兜底（十八期）：意图不可确定时的拒答原因（区别于 no_data_reason 的
    # 数据缺失语义）；非空时 critic/synthesize 短路直达诚实拒答报告
    blocked_reason: str | None = Field(
        default=None, description="无法作答的原因（意图不可确定/意图-DSL 错位）"
    )
    # 报告产出方式："llm"（LLM 规划成功）| "heuristic"（兜底接管，报告需降级标注）| "blocked"（拒答）
    answered_by: str = Field(default="", description="规划产出方式（降级可见化标注依据）")
    # LLM Planner 回传的意图（仅诊断可观测；L3 守卫不依赖它，用确定性 L1 重判）
    intent_type: str | None = Field(
        default=None,
        description="LLM 回传意图类型（diagnostic/cardinality/metric_scalar/unknown）",
    )
    intent_anchors: list[str] = Field(default_factory=list, description="LLM 回传意图锚定字段")
    # 控制与韧性
    phase: Phase = "plan"
    error_context: ErrorContext = Field(default_factory=ErrorContext)
    # 数据集名 -> ParquetRef（编排器内传递，不进 LLM prompt）
    datasets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # 计划步骤 id -> 该步骤产出的数据集名列表（重规划时按同 id 覆盖）。
    # analyze 步骤据此解析本轮依赖的真实输入——严禁按 datasets 字典首尾
    # 取数：跨轮次累积时首尾会指向上（几）轮遗留数据集，产出假结论。
    step_outputs: dict[str, list[str]] = Field(default_factory=dict)
    # 自主性分级（M2 Plan Mode，规格 §5.2）：会话级偏好随 run 请求传入，
    # L1 每步确认 / L2 计划确认（默认）/ L3 高危确认 / L4 全自动
    autonomy_level: Literal["L1", "L2", "L3", "L4"] = Field(
        default_factory=lambda: _default_autonomy()
    )
    # B 线（M3）subagent 报告收集（父图唯一消费物）；LangGraph 主图侧经
    # subagent.SubagentState 以 append reducer 声明同名通道（规格 §4.2 唯一例外）
    subagent_reports: list[SubagentReport] = Field(default_factory=list)
    # 用户在 plan_review 审批卡选择的"修改"指令（回 plan 重规划时与
    # error_context 同一注入位注入 Planner 提示词）
    plan_edit_instruction: str | None = None
    # L2 语义锚（规格 §5.2"仅 plan 后审批一次"）：本轮已审批即置位，
    # 自愈驱动的重规划不再打断用户（否则审批循环不收敛，实测踩坑）
    plan_reviewed: bool = False
    # L3 高危确认锚：用户已批准本轮沙箱代码执行（analyze 步骤含 LLM 产码）。
    # planner 每次产出新计划时复位（新计划 = 新的执行授权需求）
    high_risk_approved: bool = False
    # 上次因 LLM 反思触发重规划时的产物进展指纹（重规划无进展护栏）：
    # 指纹不变说明重规划未带来任何新数据/新分析，必须停止空转。
    last_replan_fingerprint: str = ""
    # 无数据诚实守卫（2026-09 审计修复）：取数执行前的时间域守卫拦截原因
    # （查询窗口整体晚于数仓数据域上界 = 必然空集）。置位后 analyze/critic
    # 直接短路到 synthesize 的"如实说明无数据"报告——严禁重规划空转、
    # 严禁拿兜底窗口数据冒充用户指定时段、严禁对空集编造归因结论。
    no_data_reason: str | None = Field(
        default=None, description="时间域守卫拦截原因（无数据诚实报告）"
    )
    iteration: int = Field(default=0, ge=0, description="图迭代步数（防死循环护栏）")

    def apply(self, **updates: Any) -> AgentState:
        """不可变更新：返回应用增量后的新状态（图引擎的节点返回语义）。"""
        return self.model_copy(update=updates, deep=True)

    def prune_tool_history(self, budget_chars: int = TOOL_HISTORY_BUDGET_CHARS) -> None:
        """token 预算修剪：保留最近轨迹，超预算从最旧开始丢弃（原地）。"""
        total = sum(len(r.summary) + len(r.error or "") for r in self.tool_calls)
        while total > budget_chars and len(self.tool_calls) > 1:
            removed = self.tool_calls.pop(0)
            total -= len(removed.summary) + len(removed.error or "")


def estimate_tokens(state: AgentState) -> int:
    """粗估状态的可注入字符量（编排层预算决策用）。"""
    text = (
        state.user_query
        + state.report
        + "".join(state.scratchpad)
        + "".join(r.summary for r in state.tool_calls)
    )
    return len(text) // 4
