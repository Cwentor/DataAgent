"""意图识别与路由决策中心（Intent Router & Decision Engine）。

把系统从"线性固定执行管线"升级为"具备自主路径决策能力的 Agent"：在生成 DSL
之前，先判定用户输入属于哪一类意图，并据此分派到互不干扰的处理分支：

- ``CHITCHAT``        闲聊 / 与业务数据无关的通用对话 -> 轻量回复，不触碰数仓引擎；
- ``DATA_QUERY``      数据指标 / 趋势查询 -> 走多工具编排 + 受控 DSL 编译链路；
- ``GLOSSARY_EXPLAIN``业务术语 / 指标口径解释 -> 走口径文档 RAG 检索，不执行查询；
- ``SYSTEM_ACTION``   系统控制与状态操作（清空会话 / 权限查看 / 数据源探测）；
- ``CLARIFY``         输入缺失核心维度或存在歧义 -> 主动反问，绝不盲目生成 SQL；
- ``UNSAFE_ACTION``   破坏性指令（DROP/DELETE/删除订单等）与越界敏感实体（薪资等）
  -> 安全拦截，如实告知拒绝，绝不进入 DSL 生成与执行链路（守卫前移）。

分级决策（Fast-Path -> LLM 语义分类 -> 规则兜底）：

1. **Fast-Path 规则拦截（纳秒级）**：显式系统指令（/clear、重置对话、退出）与
   极短打招呼词（你好、hello、再见）直接返回，降低延迟与 API 成本；
2. **LLM 语义分类器**：针对复杂意图提供结构化 Few-Shot Prompt，要求只输出
   ``RouteDecision`` JSON；结合 ``SessionState.history`` 综合研判上下文追问；
3. **规则兜底**：LLM 未配置 / 调用失败 / 置信度低于阈值 / 解析失败时，优雅降级
   到确定性规则判定（离线可运行、可单测），绝不抛出未捕获异常。

安全红线（守卫前移）：
- CHITCHAT 分支绝不调用 semantic/、compiler/ 与底层数据库引擎；
- SYSTEM_ACTION 分支仅暴露白名单化的会话管理 / 权限查看 / 状态探测动作，
  由调用方（web.service）执行动作前再次校验身份归属；
- CLARIFY 用于一切"介于数据查询与业务口径之间的模糊意图"，严禁盲目生成 SQL。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any

from agent.agent import extract_json
from agent.clarify import detect_clarifications
from agent.glossary import METRIC_TERMS
from agent.heuristic import REGIONS, DeterministicNL2DSL, dimension_members
from agent.llm import LLMError, resolve_default_client
from audit.logging import get_logger
from config import settings
from providers import ProviderError, chat_text

logger = get_logger("agent.router.intent")

# 审计 / 响应中的路由耗时字段名（统一契约）
ROUTING_LATENCY_MS = "routing_latency_ms"


# --------------------------------------------------------------------------- #
# 意图类型与路由判决输出契约
# --------------------------------------------------------------------------- #
class IntentType(StrEnum):
    """标准意图枚举（决策中心输出契约）。"""

    CHITCHAT = "chitchat"
    DATA_QUERY = "data_query"
    GLOSSARY_EXPLAIN = "glossary_explain"
    SYSTEM_ACTION = "system_action"
    CLARIFY = "clarify"
    UNSAFE_ACTION = "unsafe_action"


INTENT_TYPE_VALUES: frozenset[str] = frozenset(it.value for it in IntentType)

# 模块级别名（供 __init__ 统一导出）
CHITCHAT = IntentType.CHITCHAT
DATA_QUERY = IntentType.DATA_QUERY
GLOSSARY_EXPLAIN = IntentType.GLOSSARY_EXPLAIN
SYSTEM_ACTION = IntentType.SYSTEM_ACTION
CLARIFY = IntentType.CLARIFY
UNSAFE_ACTION = IntentType.UNSAFE_ACTION

# 拦截原因（extracted_entities.blocked_reason）：区分破坏性指令与越界敏感实体
BLOCKED_DESTRUCTIVE = "destructive_instruction"
BLOCKED_SENSITIVE = "sensitive_entity"

# 拦截后的面向用户拒绝文案（如实告知被拒，绝不虚假确认"操作已完成"）
BLOCKED_DESTRUCTIVE_REPLY = "该系统操作不被支持或已被安全策略拦截。"
BLOCKED_SENSITIVE_REPLY = (
    "您请求的数据不在当前数仓语义目录范围内（如员工薪资等敏感信息不存在或不可查询），"
    "无法为您检索。"
)


@dataclass
class RouteDecision:
    """一次路由判决的完整输出契约。

    - intent: 最终意图（五分类之一）；
    - confidence: 判决置信度 0.0 ~ 1.0（规则 Fast-Path 为 1.0，规则兜底按信号强度估计）；
    - reason: 路由决策原因（供审计与漏斗分析）；
    - extracted_entities: 预提取实体（时间 / 指标 / 地区 / 系统动作），下游可直接使用；
    - routing_latency_ms: 本次路由耗时（毫秒），写入审计与可观测性指标。
    """

    intent: IntentType
    confidence: float
    reason: str
    extracted_entities: dict[str, Any] = field(default_factory=dict)
    routing_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """序列化判决结果供审计与 API 输出（Serialize the routing decision）。"""
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "extracted_entities": self.extracted_entities,
            ROUTING_LATENCY_MS: self.routing_latency_ms,
        }


# --------------------------------------------------------------------------- #
# Fast-Path 规则表（纳秒级拦截，绝不发起任何网络 / 数据库调用）
# --------------------------------------------------------------------------- #
# 显式系统指令 -> 系统动作（会话管理 / 权限查看 / 数据源状态探测）
_SYSTEM_ACTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "reset_session": (
        "/clear",
        "/reset",
        "/new",
        "重置对话",
        "重置会话",
        "重置当前对话",
        "重置当前会话",
        "清空上下文",
        "清空会话",
        "清空当前会话",
        "清除上下文",
        "清除会话",
        "清除当前会话",
        "清空记忆",
        "清除记忆",
        "重置记忆",
        "开始新对话",
        "新开对话",
        "新会话",
    ),
    "view_permissions": (
        "我的权限",
        "查看权限",
        "权限列表",
        "我有什么权限",
        "我有哪些权限",
        "权限查看",
        "能看什么数据",
        "能看哪些数据",
    ),
    "source_status": (
        "数据源状态",
        "数据源健康",
        "数据源探测",
        "数据源是否正常",
        "检查数据源",
        "连接状态",
        "数据库连接状态",
        "数仓状态",
    ),
}

# 整句精确匹配的系统动作（避免误伤正常查询，如单独一个"退出"）
_EXACT_SYSTEM_ACTIONS: dict[str, tuple[str, ...]] = {
    "exit": ("退出", "结束对话", "结束会话", "关闭会话", "退出系统"),
}

# 口径 / 定义类问题触发词（命中即走 RAG 检索）——路由关键词单一来源
# （原定义于 agent/intent.py，双路由合并后统一由五分类路由器持有）
_RAG_PATTERNS: tuple[str, ...] = (
    "口径",
    "怎么算",
    "如何算",
    "如何计算",
    "怎么计算",
    "计算方式",
    "计算公式",
    "是什么意思",
    "什么意思",
    "定义",
    "怎么定义",
    "如何定义",
    "指标解释",
    "解释一下",
    "什么口径",
)

# 闲聊 / 寒暄 / 越界话题触发词（命中即拒绝）
_CHITCHAT_PATTERNS: tuple[str, ...] = (
    "你好",
    "您好",
    "早上好",
    "晚上好",
    "下午好",
    "谢谢",
    "再见",
    "哈喽",
    "嗨",
    "天气",
    "讲个笑话",
    "笑话",
    "今天星期几",
    "现在几点",
    "你是谁",
    "你能做什么",
    "你会什么",
    "写首诗",
    "唱首歌",
    "帮我写",
    "给我写",
    "翻译",
    "讲个故事",
)

# 极短打招呼词（整句精确匹配，纳秒级拦截；复合句由规则层 _CHITCHAT_PATTERNS 处理）
_EXACT_CHITCHAT: frozenset[str] = frozenset(
    {
        "你好",
        "您好",
        "hello",
        "hi",
        "hey",
        "嗨",
        "哈喽",
        "再见",
        "拜拜",
        "谢谢",
        "感谢",
        "thanks",
        "thank you",
        "早上好",
        "中午好",
        "下午好",
        "晚上好",
    }
)

# 破坏性指令拦截（守卫前移）：英文 SQL DDL/DML/DCL 指令形态（DROP TABLE / DELETE FROM
# / TRUNCATE / UPDATE ... SET / INSERT INTO / ALTER TABLE / GRANT / REVOKE）。
# 执行层 exec.guards 四层防线之外的意图层前置防线：此类请求严禁被路由为
# system_action（虚假确认）或 data_query（浪费自愈预算），一律 unsafe_action。
_UNSAFE_SQL_COMMAND_RE = re.compile(
    r"\b(drop\s+(table|schema|database|view)|truncate\s+table|alter\s+table|"
    r"delete\s+from|insert\s+into|update\s+\w+\s+set|grant\s+\w+|revoke\s+\w+)\b",
    re.IGNORECASE,
)

# 破坏性指令拦截：中文破坏性动词 + 数据对象（"删除所有订单记录"）。
# 动词表刻意排除"修改/更新"等低特异性词，避免"更新后的订单"类正常查询误伤；
# 会话管理指令（清空上下文/清空会话）由系统动作白名单先行匹配，不会落入此拦截。
_UNSAFE_CN_COMMAND_RE = re.compile(
    r"(删除|删掉|清除|清空|销毁|抹除|篡改|写入|插入|执行)\s*[一所有全整的这]{0,3}"
    r"[的张个些条]?(订单|数据|表|记录|行|库|数仓|数表|用户|产品|店铺|指标)"
)

# 越界敏感实体拦截：语义目录中不存在且属敏感个人信息的数据（薪资/身份证/手机号等）。
# 命中即拒绝并如实告知"该数据不存在/不可查询"，严禁改述为无关业务数据导出。
_SENSITIVE_ENTITY_RE = re.compile(
    r"薪资|工资|薪酬|社保|公积金|银行卡|身份证|手机号|护照|驾照|住址|家人|健康档案|病历"
)

# 完整新问题标记：命中即视为显式新查询（不得按上下文追问继承）
_METRIC_SIGNAL_RE = re.compile(
    r"|".join(re.escape(term) for term in sorted(METRIC_TERMS, key=len, reverse=True))
)

# 时间表达信号（与 clarify 层同源：绝对 / 相对时间词）
_TIME_SIGNAL_RE = re.compile(
    r"\d{4}\s*年|\d{1,2}\s*月|\d{1,2}\s*[日号]|\d{4}-\d{2}(-\d{2})?"
    r"|上个月|上月|本月|这个月|今年|去年|今天|昨天|昨日|最近|过去|近\s*\d+|本周|上周|历史|累计|全部|所有|至今"
)

# 聚合 / 分析动作信号（分布 / 排行 / 趋势 / 对比 / 明细下钻 / 维度基数与枚举探查）
_ANALYSIS_SIGNAL_RE = re.compile(
    r"分布|排名|排行|趋势|走势|占比|环比|同比|yoy|mom|明细|清单|合计|总共|总数|多少|几个|对比|变化|按天|按月|按周|每日|每月|每省|各品类|各品牌|有哪些|所有|全部|有几个|多少种"
)

# 模糊指代 / 信息不足信号（无指标、无时间、无维度时优先澄清反问）
_VAGUE_SIGNAL_RE = re.compile(
    r"看下|看一下|查一下|帮我看看|那个|这个|数据呢|看看|那.+呢|啥|什么数据"
)


# --------------------------------------------------------------------------- #
# LLM 语义分类器（Few-Shot 严格 JSON 输出）
# --------------------------------------------------------------------------- #
_ROUTER_SYSTEM_PROMPT = """你是 DataAgent 数据分析系统的意图识别器。你的任务是把用户输入分类为以下五类之一，并只输出一个 JSON 对象（禁止输出其他内容）：

{
  "intent": "chitchat | data_query | glossary_explain | system_action | clarify",
  "confidence": 0.0~1.0 的小数,
  "reason": "一句话说明判定理由（简体中文）",
  "extracted_entities": {"time": null, "metrics": [], "regions": [], "action": null}
}

分类规则：
1. chitchat：日常打招呼、身份询问、与业务数据无关的通用对话、礼貌谢辞。
2. data_query：即时指标查询、同环比趋势、报表导出、数据明细下钻（含依赖上轮语境的省略指代，如"那华南呢"）。
3. glossary_explain：询问业务术语、指标口径、计算公式的定义（不执行数据库查询）。
4. system_action：会话管理（清空上下文、重置会话）、权限查看、已连接数据源状态探测。
5. clarify：输入缺少核心维度（如时间范围、具体指标）或存在歧义，需要反问用户补充信息；严禁把模糊输入硬判为 data_query 去生成 SQL。

示例：
问：你好，你是谁
答：{"intent": "chitchat", "confidence": 1.0, "reason": "打招呼与身份询问", "extracted_entities": {}}

问：上个月广东的订单总数
答：{"intent": "data_query", "confidence": 0.99, "reason": "明确的时间与地区指标查询", "extracted_entities": {"time": "上个月", "metrics": ["订单总数"], "regions": ["广东"]}}

问：客单价是怎么定义的
答：{"intent": "glossary_explain", "confidence": 0.98, "reason": "询问指标定义口径", "extracted_entities": {"metrics": ["客单价"]}}

问：帮我重置当前会话
答：{"intent": "system_action", "confidence": 1.0, "reason": "会话重置系统指令", "extracted_entities": {"action": "reset_session"}}

问：看下那个数据
答：{"intent": "clarify", "confidence": 0.8, "reason": "缺少具体指标与时间，信息不足需反问", "extracted_entities": {}}
"""


def _history_text(history: Any) -> str:
    """把 SessionState.history 转成最近几轮的纯文本摘要（供 LLM 综合研判）。"""
    if not history:
        return ""
    lines: list[str] = []
    for msg in history[-6:]:  # 最近 3 轮（每轮 user + assistant）
        role = "用户" if msg.role == "user" else "助手"
        lines.append(f"{role}: {msg.content}")
    return "\n".join(lines)


def _normalize_intent(raw: Any) -> IntentType | None:
    """把 LLM 输出中的意图字符串规整为合法 IntentType；非法返回 None。"""
    if raw is None:
        return None
    value = str(raw).strip().lower().replace("-", "_")
    if value in INTENT_TYPE_VALUES:
        return IntentType(value)
    # 容忍少数常见同义写法
    synonyms = {
        "text2sql": DATA_QUERY,
        "sql": DATA_QUERY,
        "rag": GLOSSARY_EXPLAIN,
        "explain": GLOSSARY_EXPLAIN,
        "system": SYSTEM_ACTION,
        "action": SYSTEM_ACTION,
    }
    return synonyms.get(value)


# --------------------------------------------------------------------------- #
# 实体预提取（确定性、零网络）
# --------------------------------------------------------------------------- #
_h = DeterministicNL2DSL()  # 复用的确定性解析器（仅使用纯函数式解析方法）


def extract_entities(query: str) -> dict[str, Any]:
    """从 query 预提取时间 / 指标 / 地区 / 品类实体，供下游直接使用。"""
    q = query.lower()
    entities: dict[str, Any] = {}

    # 时间：复用启发式解析器（仅取时间过滤，不触碰任何引擎）
    time_filter = _h._time_filter(query)
    entities["time"] = time_filter

    # 指标：命中的已定义指标别名（去重、按原词序）
    metrics: list[str] = []
    seen: set[str] = set()
    for term in sorted(METRIC_TERMS, key=len, reverse=True):
        if term in q and term not in seen:
            seen.add(term)
            metrics.append(term)
    entities["metrics"] = metrics

    # 地区 / 品类
    regions = [r for r in REGIONS if r in query]
    provinces = [p for p in dimension_members("province") if p in query]
    categories = [c for c in dimension_members("category") if c in query]
    if regions:
        entities["region"] = regions[0]
    if provinces:
        entities["provinces"] = provinces
    if categories:
        entities["category"] = categories[0]

    return entities


# --------------------------------------------------------------------------- #
# IntentRouter：分级路由核心
# --------------------------------------------------------------------------- #
class IntentRouter:
    """生产级意图路由器：Fast-Path -> LLM 语义分类 -> 规则兜底 三级分派。

    - ``route(query, history=None, last_dsl=None, principal=None)`` 为主入口；
    - 任何异常都不外抛：LLM 调用失败 / 解析失败一律降级到规则兜底，
      保证路由层绝不成为主链路的未捕获异常来源。
    """

    def __init__(
        self,
        min_confidence: float | None = None,
        llm: Any = None,
        enable_llm: bool = True,
    ) -> None:
        """初始化置信度阈值与 LLM 客户端（未配置供应商时纯规则路由）。"""
        self.min_confidence = (
            min_confidence if min_confidence is not None else settings.ROUTER_MIN_CONFIDENCE
        )
        # 无可用供应商 / API Key 时不构造 LLM 客户端（守卫前移：绝不发起无意义网络请求）。
        # resolve_default_client 内部检查网关可用性（首位有 Key 的启用供应商 / 环境变量），
        # 返回请求感知的分发代理或 None。
        self._llm = llm
        if self._llm is None and enable_llm:
            self._llm = resolve_default_client()

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def route(
        self,
        query: str,
        history: Any = None,
        last_dsl: Any = None,
        principal: str | None = None,
    ) -> RouteDecision:
        """对 query 做分级意图判决，返回 RouteDecision（绝不抛未捕获异常）。"""
        started = time.perf_counter()
        try:
            decision = self._fast_path(query)
            if decision is None:
                decision = self._llm_classify(query, history, principal)
            if decision is None:
                decision = self._rule_decision(query, last_dsl, principal)
        except Exception as exc:  # 异常兜底：路由层任何故障都安全降级为澄清反问
            logger.exception(
                "router_fallback",
                extra={"event": "router_fallback", "error": f"{type(exc).__name__}: {exc}"},
            )
            decision = RouteDecision(
                intent=CLARIFY,
                confidence=0.1,
                reason="router_fallback",
                extracted_entities={"clarifications": _insufficient_clarification()},
            )
        decision.routing_latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return decision

    # ------------------------------------------------------------------ #
    # 第 1 级：Fast-Path 规则拦截（纳秒级，零网络 / 零数据库）
    # ------------------------------------------------------------------ #
    def _fast_path(self, query: str) -> RouteDecision | None:
        q = query.strip()
        if not q:
            return RouteDecision(
                intent=CLARIFY,
                confidence=1.0,
                reason="empty_input",
                extracted_entities={"clarifications": _insufficient_clarification()},
            )
        ql = q.lower()

        # 1) 显式系统指令（子串匹配）
        for action, patterns in _SYSTEM_ACTION_PATTERNS.items():
            for pattern in patterns:
                if pattern in ql:
                    return RouteDecision(
                        intent=SYSTEM_ACTION,
                        confidence=1.0,
                        reason=f"system_action:{action}",
                        extracted_entities={"action": action},
                    )
        # 2) 整句精确系统动作（如单独一个"退出"）
        for action, exacts in _EXACT_SYSTEM_ACTIONS.items():
            if ql in exacts:
                return RouteDecision(
                    intent=SYSTEM_ACTION,
                    confidence=1.0,
                    reason=f"system_action:{action}",
                    extracted_entities={"action": action},
                )
        # 3) 破坏性指令拦截（守卫前移，先于一切放行判决）：
        #    DROP/DELETE/删除订单等请求严禁判为 system_action（虚假确认）或
        #    data_query，一律 unsafe_action 如实拒绝。
        if _UNSAFE_SQL_COMMAND_RE.search(ql):
            return RouteDecision(
                intent=UNSAFE_ACTION,
                confidence=1.0,
                reason="fast_path:unsafe_sql_command",
                extracted_entities={"blocked_reason": BLOCKED_DESTRUCTIVE},
            )
        if _UNSAFE_CN_COMMAND_RE.search(q):
            return RouteDecision(
                intent=UNSAFE_ACTION,
                confidence=1.0,
                reason="fast_path:unsafe_cn_command",
                extracted_entities={"blocked_reason": BLOCKED_DESTRUCTIVE},
            )
        # 4) 越界敏感实体拦截（薪资/身份证等不在语义目录的敏感数据）：拒绝而非改述
        if _SENSITIVE_ENTITY_RE.search(q):
            return RouteDecision(
                intent=UNSAFE_ACTION,
                confidence=1.0,
                reason="fast_path:sensitive_entity",
                extracted_entities={"blocked_reason": BLOCKED_SENSITIVE},
            )
        # 5) 极短打招呼词（整句精确匹配）
        if ql in _EXACT_CHITCHAT:
            return RouteDecision(
                intent=CHITCHAT,
                confidence=1.0,
                reason="fast_path:greeting",
                extracted_entities={},
            )
        return None

    # ------------------------------------------------------------------ #
    # 第 2 级：LLM 语义分类器（复杂意图；失败 / 低置信度 -> None 走规则兜底）
    # ------------------------------------------------------------------ #
    def _llm_classify(
        self, query: str, history: Any, principal: str | None
    ) -> RouteDecision | None:
        if self._llm is None:
            return None
        messages = [{"role": "system", "content": _ROUTER_SYSTEM_PROMPT}]
        user_content = f"当前会话最近对话：\n{_history_text(history)}\n" if history else ""
        user_content += f"用户输入：{query}"
        messages.append({"role": "user", "content": user_content})
        try:
            # 经 providers.chat_text 统一流转：适配器（UnifiedChatRequest）与
            # 旧形态 client（chat(messages)->str）两种客户端透明兼容
            raw = chat_text(self._llm, messages)
            data = extract_json(raw)
        except (
            LLMError,
            ProviderError,
            ValueError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as exc:
            logger.warning(
                "router_llm_fallback",
                extra={
                    "event": "router_llm_fallback",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return None

        intent = _normalize_intent(data.get("intent"))
        if intent is None:
            return None
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < self.min_confidence:
            return None

        entities = data.get("extracted_entities") or {}
        if not isinstance(entities, dict):
            entities = {}
        reason = str(data.get("reason") or f"llm_classify:{intent.value}")
        return RouteDecision(
            intent=intent,
            confidence=round(min(max(confidence, 0.0), 1.0), 4),
            reason=reason,
            extracted_entities=entities,
        )

    # ------------------------------------------------------------------ #
    # 第 3 级：规则兜底（确定性、离线可运行、可单测）
    # ------------------------------------------------------------------ #
    def _rule_decision(self, query: str, last_dsl: Any, principal: str | None) -> RouteDecision:
        q = query.strip()
        ql = q.lower()
        entities = extract_entities(query)

        # 1) 口径 / 定义询问 -> GLOSSARY_EXPLAIN（RAG 检索，不执行查询）
        if any(p in ql for p in _RAG_PATTERNS):
            return RouteDecision(
                intent=GLOSSARY_EXPLAIN,
                confidence=0.95,
                reason="rule:glossary_pattern",
                extracted_entities=entities,
            )

        # 2) 闲聊 / 寒暄 -> CHITCHAT（绝对禁止触碰数仓引擎）
        if any(p in ql for p in _CHITCHAT_PATTERNS):
            return RouteDecision(
                intent=CHITCHAT,
                confidence=0.95,
                reason="rule:chitchat_pattern",
                extracted_entities=entities,
            )

        # 3) 数据查询信号（指标 / 时间 / 分析动作）。
        #    地区 / 品类词不算完整查询信号：仅含地区而缺指标与时间的输入
        #    （如无历史语境下的"那华南呢"）应走澄清反问，绝不盲目生成 SQL。
        looks_data_query = bool(
            _METRIC_SIGNAL_RE.search(q)
            or _TIME_SIGNAL_RE.search(q)
            or _ANALYSIS_SIGNAL_RE.search(q)
        )

        if looks_data_query:
            # 语义澄清（守卫前移）：缺失时间窗口 / 未定义业务指标 -> 主动反问，
            # 严禁静默回退默认值，也绝不盲目生成 SQL。
            clarifications = detect_clarifications(q)
            if clarifications:
                return RouteDecision(
                    intent=CLARIFY,
                    confidence=0.9,
                    reason=f"clarify:{clarifications[0].kind}",
                    extracted_entities={
                        **entities,
                        "candidate": DATA_QUERY.value,
                        "clarifications": [c.to_dict() for c in clarifications],
                    },
                )
            return RouteDecision(
                intent=DATA_QUERY,
                confidence=0.9,
                reason="rule:data_query_signal",
                extracted_entities=entities,
            )

        # 4) 省略指代 / 上下文追问：依赖上轮 DSL 的语义继承（交给 memory 消解）
        if last_dsl is not None:
            return RouteDecision(
                intent=DATA_QUERY,
                confidence=0.85,
                reason="rule:contextual_followup",
                extracted_entities={**entities, "inherit": True},
            )

        # 5) 信息不足 / 模糊指代 -> CLARIFY（主动反问，绝不硬猜）
        if _VAGUE_SIGNAL_RE.search(q):
            return RouteDecision(
                intent=CLARIFY,
                confidence=0.8,
                reason="clarify:insufficient_info",
                extracted_entities={
                    **entities,
                    "clarifications": _insufficient_clarification(),
                },
            )

        # 6) 兜底：无法归类的输入一律安全反问
        return RouteDecision(
            intent=CLARIFY,
            confidence=0.5,
            reason="rule:fallback_clarify",
            extracted_entities={"clarifications": _insufficient_clarification()},
        )


def _insufficient_clarification() -> list[dict[str, Any]]:
    """信息不足时的默认反问（kind / term / question 与 clarify 契约一致）。"""
    return [
        {
            "kind": "insufficient_info",
            "term": None,
            "question": "请补充要查询的指标（例如：GMV、订单数、客单价）与时间范围（例如：上个月、最近30天），以便我为您查询。",
        }
    ]


# --------------------------------------------------------------------------- #
# 模块级便捷入口
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _default_router() -> IntentRouter:
    """进程内复用的默认意图路由器。"""
    return IntentRouter()


def route_decision(
    query: str,
    history: Any = None,
    last_dsl: Any = None,
    principal: str | None = None,
) -> RouteDecision:
    """便捷入口：对 query 做五分类意图判决（供 web.service 与测试使用）。"""
    return _default_router().route(query, history=history, last_dsl=last_dsl, principal=principal)


__all__ = [
    "BLOCKED_DESTRUCTIVE",
    "BLOCKED_DESTRUCTIVE_REPLY",
    "BLOCKED_SENSITIVE",
    "BLOCKED_SENSITIVE_REPLY",
    "CHITCHAT",
    "CLARIFY",
    "DATA_QUERY",
    "GLOSSARY_EXPLAIN",
    "INTENT_TYPE_VALUES",
    "ROUTING_LATENCY_MS",
    "SYSTEM_ACTION",
    "UNSAFE_ACTION",
    "IntentRouter",
    "IntentType",
    "RouteDecision",
    "extract_entities",
    "route_decision",
]
