"""Multi-Tool Agent 调度内核：Plan & Select -> Execute & Guard -> Replan & Synthesize。

把原有单路径"NL -> DSL -> SQL 执行"升级为"工具调度状态循环"：
1. **Plan & Select**：将已注册工具清单（Function Calling JSON Schema）注入 LLM
   上下文（或使用确定性规则），由 LLM/规则决定直接回答、反问澄清或调用一个
   或多个工具；
2. **Execute & Guard**：入参经 Pydantic 严格校验（args_schema, extra="forbid"），
   触发工具执行；未知工具名 / 非法参数 / 越权行为一律被拦截并结构化记录；
3. **Replan（观察驱动重规划，R1）**：支持迭代的规划器（LLMPlanner）在每批调用
   执行完毕后拿到完整调度轨迹（含结果摘要），自行决策：继续调用工具补齐信息、
   给出最终洞察、反问澄清或终止；中间结果（observation）真正参与导航，
   而非仅用于失败自愈；
4. **Self-Correction & Synthesize**：工具报错触发一次自愈修复（受 Max Steps
   约束）；最终合成综合洞察 + 图表渲染指令（ChartSpec）+ 导出链接。

调度轨迹（ToolInvocationRecord）包含每一步的工具名、入参、耗时、成功/异常状态
与输出摘要，可完整接入审计链路（web.service 落 audit record.steps）。

确定性兜底：未配置 LLM 时使用关键词规则规划（离线可运行、可单测），
与既有确定性 Agent 哲学一致；确定性规划器不参与重规划循环（iterative=False），
行为与单批调度完全一致。
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent.agent import extract_json
from agent.clarify import Clarification, detect_clarifications
from agent.errors import PipelineError
from agent.llm import OpenAICompatClient
from agent.router import IntentType
from audit.logging import get_logger
from config import settings
from semantic.dsl_schema import QueryDSL
from tools.base import ToolContext, ToolResult
from tools.registry import ToolRegistry, default_registry

logger = get_logger("agent.tool_agent")

CHITCHAT_REPLY = "抱歉，我是数据分析助手，只能回答与业务数据相关的问题。"

# 确定性规划关键词
_EXPORT_KEYWORDS = (
    "导出",
    "下载",
    "表格",
    "明细",
    "清单",
    "报表",
    "csv",
    "excel",
    "markdown",
    "转储",
)
_TREND_KEYWORDS = (
    "环比",
    "同比",
    "趋势",
    "走势",
    "累计",
    "移动平均",
    "滑动平均",
    "补零",
    "每日",
    "每周",
    "每月",
    "按天",
    "按月",
    "按周",
    "连续",
    "yoy",
    "mom",
    "变化",
)


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class ToolCall:
    """一次工具调用计划（调度内核的最小执行单元）。"""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class ToolInvocationRecord:
    """一次工具调用的完整轨迹（审计与前端展示共用）。"""

    step: int
    tool: str
    args: dict[str, Any]
    success: bool
    duration_ms: float = 0.0
    error_msg: str | None = None
    error_type: str | None = None
    display_type: str | None = None
    summary: str | None = None
    output: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "args": self.args,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "error_msg": self.error_msg,
            "error_type": self.error_type,
            "display_type": self.display_type,
            "summary": self.summary,
            "output": self.output,
        }


@dataclass
class PlanResult:
    """规划结果：调用哪些工具 / 直接回答 / 反问澄清。"""

    calls: list[ToolCall] = field(default_factory=list)
    answer: str | None = None
    clarifications: list[Clarification] = field(default_factory=list)


@dataclass
class AgentResult:
    """Agent 一次调度的最终结果（复合输出：洞察 + 图表 + 导出链接 + 轨迹）。"""

    query: str
    answer: str = ""
    steps: list[ToolInvocationRecord] = field(default_factory=list)
    error: str | None = None
    error_type: str | None = None
    degraded: bool = False
    intent: str = IntentType.DATA_QUERY.value

    # 数据工具产物（供 web 层透传）
    dsl: QueryDSL | None = None
    sql: str | None = None
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    explanation: str | None = None
    viz: dict[str, Any] | None = None
    chart_spec: dict[str, Any] | None = None
    download_urls: list[str] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    clarifications: list[dict[str, Any]] = field(default_factory=list)
    rewrites: int = 0
    scan_rows: int = 0
    # R1 观察驱动重规划：执行后基于轨迹追加的调度轮数（0 = 单批调度）
    replans: int = 0

    def step_tools(self) -> list[str]:
        return [s.tool for s in self.steps]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "answer": self.answer,
            "steps": [s.to_dict() for s in self.steps],
            "error": self.error,
            "error_type": self.error_type,
            "degraded": self.degraded,
            "intent": self.intent,
            "chart_spec": self.chart_spec,
            "download_urls": self.download_urls,
            "documents": self.documents,
            "clarifications": self.clarifications,
        }


# --------------------------------------------------------------------------- #
# 规划器
# --------------------------------------------------------------------------- #
class Planner(ABC):
    """规划器抽象：决定本轮调度调用哪些工具（或直接回答/反问）。"""

    # R1 观察驱动重规划：是否支持在执行后基于轨迹继续决策（ToolAgent 据此
    # 决定是否进入重规划循环）。确定性规划器保持单批调度语义，不参与循环。
    iterative: bool = False

    @abstractmethod
    def plan(
        self,
        query: str,
        principal: str | None,
        registry: ToolRegistry,
        *,
        history: Any = None,
        last_dsl: Any = None,
    ) -> PlanResult: ...

    def plan_next(
        self,
        query: str,
        principal: str | None,
        registry: ToolRegistry,
        *,
        steps: list[ToolInvocationRecord],
        outputs: list[ToolResult],
        remaining_steps: int,
    ) -> PlanResult:
        """观察驱动重规划：把已执行轨迹交给规划器，决策下一步动作。

        返回值语义与 ``plan`` 一致：``calls`` 继续执行 / ``answer`` 直接作答 /
        ``clarifications`` 反问澄清；三者皆空表示信息已充分（终止调度）。
        默认实现保守终止：不支持迭代的规划器不会在此被调用（ToolAgent 以
        ``iterative`` 门控），此处仅作协议兜底。
        """
        return PlanResult()

    def correct(
        self,
        query: str,
        principal: str | None,
        failed: ToolCall,
        record: ToolInvocationRecord,
    ) -> ToolCall | None:
        """自愈修复：工具失败后返回修正后的调用（None 表示不修复）。"""
        return None


class DeterministicPlanner(Planner):
    """确定性规划：消费统一五分类意图判决（IntentRouter）+ 关键词（趋势/导出）-> 工具。

    双路由合并（历史缺陷修复）：不再使用旧的独立三分类 classify_intent，
    而是复用 agent.router.intent_router 的五分类判决中心（Fast-Path -> LLM -> 规则
    兜底），保证 Agent 内部分派与 web.service 的分流决策完全一致，杜绝"两层路由
    结论打架"导致的意图漂移。
    """

    def plan(
        self,
        query: str,
        principal: str | None,
        registry: ToolRegistry,
        *,
        history: Any = None,
        last_dsl: Any = None,
    ) -> PlanResult:
        from agent.router import IntentType, route_decision

        # 与 web.service 分流共用同一五分类判决中心：携带会话状态（history/last_dsl），
        # 使"那华南呢"这类上下文追问被正确判为 DATA_QUERY（而非孤立输入误判 CLARIFY）
        decision = route_decision(query, history=history, last_dsl=last_dsl, principal=principal)
        intent = decision.intent

        if intent == IntentType.CHITCHAT:
            return PlanResult(answer=CHITCHAT_REPLY)
        if intent == IntentType.SYSTEM_ACTION:
            # 系统控制动作由 web.service 白名单执行；Agent 层不触达数仓引擎
            return PlanResult(answer="系统操作已由上层安全处理，无需查询数据。")
        if intent == IntentType.GLOSSARY_EXPLAIN:
            return PlanResult(calls=[ToolCall("explain_glossary", {"query": query})])
        if intent == IntentType.CLARIFY:
            # 澄清反问：优先使用路由判决预提取的澄清问题（缺失时间 / 未定义指标 / 信息不足）
            clarifications = decision.extracted_entities.get("clarifications") or []
            if not clarifications:
                clarifications = [c.to_dict() for c in detect_clarifications(query)]
            return PlanResult(
                clarifications=[Clarification(**c) for c in clarifications if isinstance(c, dict)]
            )

        # DATA_QUERY：关键词分派（趋势 / 导出 / 即时点查）
        ql = query.lower()
        if any(k in ql for k in _EXPORT_KEYWORDS):
            # 组合调用：先查询（复用 query_metric），再把结果交给导出工具
            return PlanResult(
                calls=[
                    ToolCall("query_metric", {"query": query}, reason="导出前先查询数据"),
                    ToolCall("export_report", {"query": query}, reason="导出为可下载文件"),
                ]
            )
        if any(k in ql for k in _TREND_KEYWORDS):
            return PlanResult(
                calls=[ToolCall("trend_analysis", {"query": query}, reason="时序/对比分析")]
            )
        return PlanResult(calls=[ToolCall("query_metric", {"query": query}, reason="即时指标点查")])


class LLMPlanner(Planner):
    """LLM 规划：把工具清单（JSON Schema）注入上下文，由 LLM 决策工具调用。

    协议：LLM 只输出一个 JSON 对象，取值四选一：
    - ``{"tool": "<已注册工具名>", "args": {...}}``：调用工具；
    - ``{"answer": "..."}``：直接回答（无需工具）；
    - ``{"clarify": "..."}``：反问澄清；
    - ``{"done": "..."}```：信息已充分，终止调度（重规划阶段使用）。
    任何非法工具名 / 非法参数都会被校验拦截并反馈 LLM 重试（max_retries 次）。

    R1 观察驱动重规划（iterative=True）：``plan_next`` 把已执行轨迹（含每步
    结果摘要与解释）喂回 LLM，由其基于中间结果决定继续查询 / 作答 / 反问 /
    终止——中间结果（observation）真正参与调度导航，而非仅用于失败自愈。
    """

    iterative = True

    def __init__(
        self,
        client: OpenAICompatClient,
        registry: ToolRegistry | None = None,
        max_retries: int = 2,
    ):
        self.client = client
        self.registry = registry  # 构造注入：correct() 自愈路径依赖工具注册表校验
        self.max_retries = max_retries

    def plan(
        self,
        query: str,
        principal: str | None,
        registry: ToolRegistry,
        *,
        history: Any = None,
        last_dsl: Any = None,
    ) -> PlanResult:
        tools_json = json.dumps(registry.tool_definitions(), ensure_ascii=False)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是数据分析 Agent 的规划器。根据用户问题决定是否调用工具。\n"
                    "可用的工具清单（OpenAI Function Calling 规范）：\n"
                    + tools_json
                    + "\n\n输出要求：只输出一个 JSON 对象，三选一：\n"
                    '{ "tool": "<工具名>", "args": {...} }\n'
                    '{ "answer": "无需查询的直接回答文本" }\n'
                    '{ "clarify": "需要向用户追问他的一句问题" }\n'
                    "禁止输出解释或多余文字。若问题需要数据但缺少关键信息，输出 clarify。"
                ),
            },
            {"role": "user", "content": f"问题：{query}"},
        ]
        return self._decide(query, registry, messages, context="规划")

    def plan_next(
        self,
        query: str,
        principal: str | None,
        registry: ToolRegistry,
        *,
        steps: list[ToolInvocationRecord],
        outputs: list[ToolResult],
        remaining_steps: int,
    ) -> PlanResult:
        """观察驱动重规划：携带完整执行轨迹再次决策（R1 核心入口）。"""
        tools_json = json.dumps(registry.tool_definitions(), ensure_ascii=False)
        trajectory = json.dumps(
            self._trajectory_view(steps, outputs), ensure_ascii=False
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是数据分析 Agent 的规划器。已经执行过若干工具调用，"
                    "执行轨迹（含结果摘要）如下，请判断当前信息是否足以回答问题：\n"
                    + trajectory
                    + "\n\n若信息已足够 -> 输出 {\"answer\": \"基于轨迹的最终中文洞察\"}；"
                    "若还差数据（如对比类问题只查了一个对象）-> 继续调用工具补齐；"
                    "若需用户补充 -> 输出 clarify；若无需继续 -> 输出 done。\n"
                    "可用的工具清单（OpenAI Function Calling 规范）：\n"
                    + tools_json
                    + "\n\n剩余可用步数："
                    + str(remaining_steps)
                    + "（必须在预算内决策，预算紧张时优先收敛作答）。\n"
                    "输出要求：只输出一个 JSON 对象，四选一：\n"
                    '{ "tool": "<工具名>", "args": {...} }\n'
                    '{ "answer": "基于已有轨迹的最终中文洞察" }\n'
                    '{ "clarify": "需要向用户追问的一句问题" }\n'
                    '{ "done": "信息已充分或无必要继续" }\n'
                    "禁止输出解释或多余文字。"
                ),
            },
            {"role": "user", "content": f"问题：{query}"},
        ]
        return self._decide(query, registry, messages, context="重规划")

    def _decide(
        self,
        query: str,
        registry: ToolRegistry,
        messages: list[dict[str, str]],
        *,
        context: str,
    ) -> PlanResult:
        """共享决策核：调 LLM -> 解析校验 -> 非法输出反馈重试（plan/plan_next 共用）。"""
        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            raw = self.client.chat(messages)
            try:
                obj = extract_json(raw)
                if "answer" in obj:
                    return PlanResult(answer=str(obj["answer"]))
                if "clarify" in obj:
                    return PlanResult(
                        clarifications=[
                            Clarification(
                                kind="llm_clarify", term=None, question=str(obj["clarify"])
                            )
                        ]
                    )
                name = str(obj.get("tool", ""))
                if not name:
                    # {"done": ...} 或空对象：信息已充分，终止调度
                    return PlanResult()
                args = obj.get("args") or {}
                tool = registry.get_tool(name)  # 未注册 -> UnknownToolError
                tool.validate_args(args)  # 非法参数 -> ValidationError
                reason = "LLM 决策" if context == "规划" else "LLM 重规划"
                return PlanResult(calls=[ToolCall(name, dict(args), reason=reason)])
            except Exception as exc:
                last_error = exc
                messages = [
                    *messages[:2],
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": f"你上次的输出无效：{str(exc)[:400]}\n请重新输出合法 JSON。",
                    },
                ]
        raise PipelineError(
            f"LLM {context}器重试 {self.max_retries} 次后仍无法产出合法决策: {last_error}"
        ) from last_error

    @staticmethod
    def _trajectory_view(
        steps: list[ToolInvocationRecord], outputs: list[ToolResult]
    ) -> list[dict[str, Any]]:
        """把调度轨迹压缩为 LLM 可消费的观察视图（截断防 Token 膨胀）。"""
        view: list[dict[str, Any]] = []
        for s, o in zip(steps, outputs, strict=False):
            item: dict[str, Any] = {
                "step": s.step,
                "tool": s.tool,
                "args": s.args,
                "success": s.success,
            }
            if s.success:
                if s.summary:
                    item["summary"] = s.summary
                data = o.data if isinstance(o.data, dict) else {}
                if data.get("explanation"):
                    item["explanation"] = str(data["explanation"])[:200]
                if isinstance(data.get("rows"), list):
                    item["row_count"] = len(data["rows"])
            else:
                item["error"] = (s.error_msg or "")[:200]
            view.append(item)
        return view

    def correct(
        self,
        query: str,
        principal: str | None,
        failed: ToolCall,
        record: ToolInvocationRecord,
    ) -> ToolCall | None:
        """自愈修复：把工具失败原因喂回 LLM，重新规划一次。

        依赖构造注入的 ``registry`` 校验修正后的工具调用（get_tool + validate_args），
        未注入注册表时无法自愈，安全返回 None（绝不静默吞掉内部错误）。
        """
        if self.registry is None:
            logger.warning(
                "llm_correct_skipped",
                extra={
                    "event": "llm_correct_skipped",
                    "reason": "missing_registry",
                    "tool": failed.tool,
                },
            )
            return None
        messages = [
            {
                "role": "system",
                "content": (
                    "你是数据分析 Agent 的规划器。上一次工具调用失败，请根据报错"
                    "重新输出 JSON：{ 'tool': ..., 'args': {...} } 或 { 'answer': ... }。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"问题：{query}\n失败工具：{failed.tool}\n"
                    f"失败原因：{record.error_msg or ''}\n请给出修正后的调用。"
                ),
            },
        ]
        try:
            raw = self.client.chat(messages)
            obj = extract_json(raw)
            name = str(obj.get("tool", ""))
            args = obj.get("args") or {}
            tool = self.registry.get_tool(name)
            tool.validate_args(args)
            return ToolCall(name, dict(args), reason="LLM 自愈修复")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            # 收窄异常范围：仅捕获可预期的解析/校验错误，杜绝"静默吞 AttributeError"类死代码
            logger.warning(
                "llm_correct_failed",
                extra={
                    "event": "llm_correct_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "tool": failed.tool,
                },
            )
            return None


# --------------------------------------------------------------------------- #
# 总结器
# --------------------------------------------------------------------------- #
class Synthesizer(ABC):
    """总结器抽象：把工具执行结果合成最终洞察。"""

    @abstractmethod
    def synthesize(self, result: AgentResult, outputs: list[ToolResult], query: str) -> None: ...


class DeterministicSynthesizer(Synthesizer):
    """确定性合成：基于工具输出拼装洞察 + 图表指令（零幻觉、可测）。"""

    def synthesize(self, result: AgentResult, outputs: list[ToolResult], query: str) -> None:
        if result.clarifications:
            result.answer = "；".join(c["question"] for c in result.clarifications)
            return
        if not outputs:
            result.answer = CHITCHAT_REPLY
            return

        last = outputs[-1]
        if not last.success:
            result.error = last.error_msg
            result.error_type = (last.meta or {}).get("error_type")
            result.answer = last.error_msg or "工具执行失败"
            return

        data = last.data or {}
        tool_name = result.steps[-1].tool if result.steps else ""
        if tool_name == "explain_glossary":
            docs = data.get("documents", [])
            result.documents = docs
            titles = "、".join(d.get("title", "") for d in docs)
            result.answer = f"已检索到 {len(docs)} 条口径文档：{titles}"
            return
        if tool_name == "export_report":
            result.download_urls = (
                [last.meta.get("download_url", "")] if last.meta.get("download_url") else []
            )
            url = last.meta.get("download_url", "")
            notes = data.get("notes") or []
            note_txt = "；".join(notes)
            result.answer = (
                f"已生成导出文件（{data.get('filename', '')}，{data.get('row_count', 0)} 行）。"
                + (f"下载链接：{url}" if url else "")
                + (f"；{note_txt}" if note_txt else "")
            )
            return
        # query_metric / trend_analysis：数据型工具
        result.explanation = data.get("explanation")
        result.viz = data.get("viz")
        result.chart_spec = data.get("chart_spec")
        result.dsl = (
            QueryDSL.model_validate(data["dsl"]) if isinstance(data.get("dsl"), dict) else None
        )
        result.sql = data.get("sql")
        result.columns = data.get("columns")
        result.rows = data.get("rows")
        result.rewrites = int(data.get("rewrites") or 0)
        result.scan_rows = int(data.get("scan_rows") or 0)
        result.degraded = result.degraded or bool(data.get("degraded"))

        rows = result.rows or []
        viz = result.viz or {}
        explanation = (result.explanation or "").rstrip("。")
        if viz.get("chart") == "number" and rows:
            label = viz.get("y") or (result.columns[0] if result.columns else "数值")
            value = rows[0][0]
            if isinstance(value, float):
                value = round(value, 2)
            result.answer = f"{label} = {value}；{explanation}。"
        else:
            result.answer = f"{explanation}。返回 {len(rows)} 行结果。"


class LLMSynthesizer(Synthesizer):
    """LLM 总结：把工具输出喂回 LLM 合成最终洞察（含图表指令）。"""

    def __init__(self, client: OpenAICompatClient):
        self.client = client

    def synthesize(self, result: AgentResult, outputs: list[ToolResult], query: str) -> None:
        if result.answer:
            # R1 重规划作答：规划器已基于完整轨迹给出最终洞察，
            # 这里仅回填数据字段（dsl/sql/rows/viz/chart_spec），不再重复调 LLM
            preset = result.answer
            DeterministicSynthesizer().synthesize(result, outputs, query)
            result.answer = preset
            return
        if not outputs:
            return DeterministicSynthesizer().synthesize(result, outputs, query)
        last = outputs[-1]
        if not last.success:
            return DeterministicSynthesizer().synthesize(result, outputs, query)
        tools_summary = json.dumps(
            [s.to_dict() for s in result.steps if s.success and s.output],
            ensure_ascii=False,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是数据分析助手。基于工具返回结果，给用户一段简洁的中文洞察。"
                    '输出 JSON：{"answer": "洞察文本"}。'
                ),
            },
            {
                "role": "user",
                "content": f"问题：{query}\n工具结果：{tools_summary}",
            },
        ]
        try:
            raw = self.client.chat(messages)
            obj = extract_json(raw)
            result.answer = str(obj.get("answer", ""))
            if obj.get("chart") and result.chart_spec is None:
                result.chart_spec = obj["chart"]
        except Exception:
            DeterministicSynthesizer().synthesize(result, outputs, query)


# --------------------------------------------------------------------------- #
# Agent 调度循环
# --------------------------------------------------------------------------- #
class ToolAgent:
    """Multi-Tool 调度状态循环（Max Steps 受控，杜绝无限循环）。"""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        planner: Planner | None = None,
        synthesizer: Synthesizer | None = None,
        max_steps: int = 5,
    ) -> None:
        self.registry = registry or default_registry()
        self.planner = planner or DeterministicPlanner()
        self.synthesizer = synthesizer or DeterministicSynthesizer()
        if not 3 <= max_steps <= 5:
            raise ValueError("max_steps 必须在 3~5 之间（受控调度，杜绝无限循环）")
        self.max_steps = max_steps
        self._history: Any = None
        self._last_dsl: Any = None

    # ------------------------------------------------------------------ #
    def run(
        self,
        query: str,
        principal: str | None = None,
        conn: Any = None,
        *,
        executor: Any = None,
        rewriter: Any = None,
        request_id: str | None = None,
        base_dsl: Any = None,
        history: Any = None,
        last_dsl: Any = None,
    ) -> AgentResult:
        """执行一次完整的多工具调度，返回复合结果 AgentResult（不抛异常）。

        base_dsl：会话上下文继承注入的结构化 DSL（agent.memory 合并产物），
        非 None 时数据工具以其为基础执行，仍走安全守卫 + 编译 + 执行护栏。
        history / last_dsl：本轮会话状态透传给规划器（与 web.service 分流
        共用同一意图判决中心，保证"上下文追问"不被误判为澄清）。
        """
        result = AgentResult(query=query)
        self._history = history
        self._last_dsl = last_dsl

        try:
            plan = self.planner.plan(
                query,
                principal,
                self.registry,
                history=self._history,
                last_dsl=self._last_dsl,
            )
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.error_type = type(exc).__name__
            result.answer = result.error
            return result

        if plan.answer is not None:
            result.answer = plan.answer
            # 直接回答：五分类体系无独立意图，保留旧兼容字符串供调用方区分
            result.intent = "direct_answer"
            return result
        if plan.clarifications:
            result.clarifications = [c.to_dict() for c in plan.clarifications]
            result.answer = "；".join(c.question for c in plan.clarifications)
            result.intent = IntentType.CLARIFY.value
            return result

        outputs: list[ToolResult] = []
        replan_answer: str | None = None

        # 首轮计划执行（含失败自愈；True 表示不可恢复失败，终止调度）
        stopped = self._execute_calls(
            calls=plan.calls,
            query=query,
            principal=principal,
            conn=conn,
            executor=executor,
            rewriter=rewriter,
            request_id=request_id,
            base_dsl=base_dsl,
            result=result,
            outputs=outputs,
        )

        # R1 观察驱动重规划：每批执行完毕后把完整轨迹喂回规划器，由其基于
        # 中间结果决定"继续查 / 作答 / 反问 / 终止"。仅 iterative 规划器参与，
        # 全程受 max_steps 硬预算约束，杜绝无限循环。
        if not stopped and self.planner.iterative:
            while (
                len(result.steps) < self.max_steps
                and replan_answer is None
                and not result.clarifications
            ):
                try:
                    nxt = self.planner.plan_next(
                        query,
                        principal,
                        self.registry,
                        steps=result.steps,
                        outputs=outputs,
                        remaining_steps=self.max_steps - len(result.steps),
                    )
                except Exception as exc:
                    # 重规划失败不推翻已成功的执行结果：记录告警后按现有轨迹收敛作答
                    logger.warning(
                        "replan_failed",
                        extra={
                            "event": "replan_failed",
                            "error": f"{type(exc).__name__}: {exc}"[:300],
                        },
                    )
                    break
                if nxt.answer is not None:
                    replan_answer = nxt.answer
                    break
                if nxt.clarifications:
                    result.clarifications = [c.to_dict() for c in nxt.clarifications]
                    replan_answer = "；".join(c.question for c in nxt.clarifications)
                    result.intent = IntentType.CLARIFY.value
                    break
                if not nxt.calls:
                    break  # 规划器判定信息已充分（done）
                result.replans += 1
                stopped = self._execute_calls(
                    calls=nxt.calls,
                    query=query,
                    principal=principal,
                    conn=conn,
                    executor=executor,
                    rewriter=rewriter,
                    request_id=request_id,
                    base_dsl=base_dsl,
                    result=result,
                    outputs=outputs,
                )
                if stopped:
                    break

        if replan_answer is not None:
            # 预置规划器洞察：合成器据此跳过重复 LLM 调用，仅回填数据字段
            result.answer = replan_answer
        self._log_steps(result.steps)

        try:
            self.synthesizer.synthesize(result, outputs, query)
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.error_type = type(exc).__name__
        if replan_answer is not None:
            # 规划器基于完整轨迹给出的洞察优先于单输出数据拼装答案
            result.answer = replan_answer

        result.degraded = result.degraded or any((o.meta or {}).get("degraded") for o in outputs)
        return result

    # ------------------------------------------------------------------ #
    def _execute_calls(
        self,
        *,
        calls: list[ToolCall],
        query: str,
        principal: str | None,
        conn: Any,
        executor: Any,
        rewriter: Any,
        request_id: str | None,
        base_dsl: Any,
        result: AgentResult,
        outputs: list[ToolResult],
    ) -> bool:
        """顺序执行一批计划调用（失败触发一次自愈修复）。

        返回 True 表示调度应终止（不可恢复失败）；False 表示本批正常完成
        或因预算耗尽收敛（预算耗尽由调用方的循环条件自然兜住）。
        """
        for call in calls:
            if len(result.steps) >= self.max_steps:
                return False
            record, tool_result = self._execute_once(
                call,
                query,
                principal,
                conn,
                executor,
                rewriter,
                request_id,
                len(result.steps) + 1,
                outputs,
                base_dsl,
            )
            outputs.append(tool_result)
            result.steps.append(record)

            if record.success:
                continue  # 成功 -> 继续下一个计划调用

            # Self-Correction：工具失败时触发一次修复（受 Max Steps 约束）
            if self._is_permanent_error(tool_result) or len(result.steps) >= self.max_steps:
                return True
            try:
                corrected = self.planner.correct(query, principal, call, record)
            except Exception:
                corrected = None
            if corrected is None:
                return True
            rec2, res2 = self._execute_once(
                corrected,
                query,
                principal,
                conn,
                executor,
                rewriter,
                request_id,
                len(result.steps) + 1,
                outputs,
                base_dsl,
            )
            outputs.append(res2)
            result.steps.append(rec2)
            if not rec2.success:
                return True
        return False

    # ------------------------------------------------------------------ #
    def _execute_once(
        self,
        call: ToolCall,
        query: str,
        principal: str | None,
        conn: Any,
        executor: Any,
        rewriter: Any,
        request_id: str | None,
        step_no: int,
        prior_outputs: list[ToolResult],
        base_dsl: Any = None,
    ) -> tuple[ToolInvocationRecord, ToolResult]:
        """执行一次工具调用，返回 (轨迹记录, 工具结果)。"""
        try:
            tool = self.registry.get_tool(call.tool)  # UnknownToolError -> run() 捕获
        except Exception:
            # 工具不存在时返回失败结果，避免向上抛异常（保持run()的"不抛异常"承诺）
            error_msg = f"Unknown tool: {call.tool}"
            error_type = "UnknownToolError"
            tool_result = ToolResult(
                success=False,
                error_msg=error_msg,
                meta={"error_type": error_type},
            )
            record = ToolInvocationRecord(
                step=step_no,
                tool=call.tool,
                args=call.args,
                success=False,
                duration_ms=0.0,
                error_msg=error_msg,
                error_type=error_type,
            )
            return record, tool_result

        ctx = ToolContext(
            conn=conn,
            principal=principal,
            executor=executor,
            rewriter=rewriter,
            request_id=request_id,
            prior=prior_outputs[-1] if prior_outputs else None,
            base_dsl=base_dsl,
        )
        tool_result = tool.run(call.args, ctx)
        # 打点：工具调用成功/失败计入进程级可观测性（audit.metrics）
        try:
            from audit.metrics import default_registry as _metrics_registry

            _metrics_registry().record_tool_call(success=tool_result.success)
        except Exception:  # pragma: no cover - 打点失败不影响主流程
            pass
        record = ToolInvocationRecord(
            step=step_no,
            tool=call.tool,
            args=call.args,
            success=tool_result.success,
            duration_ms=tool_result.duration_ms,
            error_msg=tool_result.error_msg,
            error_type=(tool_result.meta or {}).get("error_type"),
            display_type=tool_result.display_type,
        )
        record.summary = _summarize(tool_result)
        record.output = _summarize(tool_result)
        return record, tool_result

    @staticmethod
    def _is_permanent_error(tool_result: ToolResult) -> bool:
        """越权/未注册等确定性错误不重试（避免无意义的自愈循环）。"""
        error_type = (tool_result.meta or {}).get("error_type")
        return error_type in {
            "SecurityError",
            "UnknownToolError",
            "PermissionError",
        }

    @staticmethod
    def _log_steps(steps: list[ToolInvocationRecord]) -> None:
        for s in steps:
            logger.info(
                "tool_call",
                extra={
                    "event": "tool_call",
                    "tool": s.tool,
                    "success": s.success,
                    "duration_ms": s.duration_ms,
                },
            )


def _summarize(tool_result: ToolResult) -> dict[str, Any] | None:
    """把工具输出压缩为可审计/可展示的摘要（避免把整表数据塞进审计）。"""
    if not tool_result.success:
        return None
    data = tool_result.data
    if isinstance(data, dict):
        summary: dict[str, Any] = {}
        for key in ("row_count", "download_url", "format", "filename", "count", "matched_keys"):
            if key in data:
                summary[key] = data[key]
        if "columns" in data and isinstance(data["columns"], list):
            summary["columns"] = list(data["columns"])
        if "viz" in data:
            summary["chart"] = (data.get("viz") or {}).get("chart")
        return summary
    return {"value": data}


# --------------------------------------------------------------------------- #
# 默认 Agent 工厂
# --------------------------------------------------------------------------- #
_default_agent: ToolAgent | None = None
_agent_lock = threading.Lock()


def default_tool_agent() -> ToolAgent:
    """进程内复用的默认 ToolAgent（LLM 已配置 -> LLM 规划 + 总结；否则确定性）。

    双检锁保护并发首调（就绪度评审 P3 卫生项处置：原工厂无锁且 _agent_lock
    为死变量，并发首调可能重复构造）。
    """
    global _default_agent
    if _default_agent is None:
        with _agent_lock:
            if _default_agent is None:
                registry = default_registry()
                if settings.LLM_API_KEY:
                    client = OpenAICompatClient(
                        base_url=settings.LLM_BASE_URL,
                        api_key=settings.LLM_API_KEY,
                        model=settings.LLM_MODEL,
                        temperature=settings.LLM_TEMPERATURE,
                        timeout=settings.LLM_TIMEOUT,
                    )
                    planner = LLMPlanner(
                        client, registry=registry, max_retries=settings.LLM_MAX_RETRIES
                    )
                    synthesizer = LLMSynthesizer(client)
                else:
                    planner = DeterministicPlanner()
                    synthesizer = DeterministicSynthesizer()
                _default_agent = ToolAgent(
                    registry=registry,
                    planner=planner,
                    synthesizer=synthesizer,
                    max_steps=int(getattr(settings, "MAX_AGENT_STEPS", 5)),
                )
    return _default_agent


# 允许测试注入自定义 Agent（与 web.service 现有 monkeypatch 风格一致）
def set_default_tool_agent(agent: ToolAgent | None) -> None:
    global _default_agent
    _default_agent = agent


__all__ = [
    "AgentResult",
    "DeterministicPlanner",
    "DeterministicSynthesizer",
    "LLMPlanner",
    "LLMSynthesizer",
    "PlanResult",
    "Planner",
    "Synthesizer",
    "ToolAgent",
    "ToolCall",
    "ToolInvocationRecord",
    "default_tool_agent",
    "set_default_tool_agent",
]
