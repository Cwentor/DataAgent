"""会话上下文记忆（Session Memory & Multi-turn Context）。

把"单轮即走查询器"升级为"多轮对话式 Agent"。与纯文本拼接不同，本模块记忆的是
**上一轮成功执行的结构化 DSL 状态契约**（指标 / 时间窗口 / 过滤条件 / 维度），
并据此支持三种多轮语义：

1. **省略指代继承（Slot Inherit）**：如「那华南地区呢？」-> 继承上轮指标与时间
   范围，仅把地区筛选从华东替换为华南；
2. **下钻 / 趋势展开（Drill-Down）**：如「按天看趋势」-> 保留上轮指标与筛选，
   追加时间维度与粒度（由 trend_analysis 工具规范化）；
3. **话题切换重置（Topic Switch）**：如「看今年的总销售额」-> 显式清理旧 DSL
   依赖，避免脏上下文干扰。

安全红线（Session Bleeding 防护）：
- 会话状态按 ``(session_id, user_id)`` 强绑定：跨用户读取一律返回 None（拒绝继承），
  绝不把 A 用户的上下文泄漏给 B 用户；
- 继承产出的 DSL 仍是受控契约（extra="forbid" 校验），调用方必须继续走
  ``security.guard.apply_policy`` + 编译器 + 执行护栏，不绕过任何防线；
- 落库的 last_dsl 含已注入的 RLS 行级过滤，继承合并前先剥离（strip_rls_filters），
  最终 RLS 仍由 apply_policy 统一施加一次，避免重复注入与语义漂移。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.errors import PipelineError
from agent.glossary import METRIC_TERMS
from agent.heuristic import CATEGORIES, PROVINCES, REGIONS, DeterministicNL2DSL
from config import settings
from persistence.kvstore import SqliteKVStore
from security.policy import POLICIES, PRINCIPAL_ATTRS
from semantic.dsl_schema import Dimension, Filter, FilterOperator, QueryDSL, TimeFilter

# 未提供身份时的兜底绑定标识（直接调用 run_query 且未传 user/principal 的测试场景）
_ANONYMOUS = "anonymous"

# 复用的确定性解析器（仅使用其纯函数式解析方法，不持有状态）
_h = DeterministicNL2DSL()

# 触发"趋势 / 下钻"语义的关键词（命中即按上轮 DSL 展开，交由 trend 工具规范化）
_TREND_KEYWORDS: tuple[str, ...] = (
    "按天",
    "每天",
    "每日",
    "逐日",
    "按周",
    "每周",
    "按月",
    "每月",
    "逐月",
    "趋势",
    "走势",
    "累计",
    "移动平均",
    "滑动平均",
    "环比",
    "同比",
    "补零",
    "补齐",
)
_ADD_DIM_KEYWORDS: tuple[str, ...] = ("下钻", "展开", "细分")


# --------------------------------------------------------------------------- #
# 状态契约（Pydantic，extra="forbid"）
# --------------------------------------------------------------------------- #
class ChatMessage(BaseModel):
    """单轮问答的简要记录（滚动保留，控制 Token 消耗）。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dsl: QueryDSL | None = Field(
        default=None, description="assistant 消息可携带该轮成功执行的结构化 DSL"
    )


class SessionState(BaseModel):
    """一个 (session_id, user_id) 会话的可持久化记忆状态。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    user_id: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    history: list[ChatMessage] = Field(default_factory=list)
    last_dsl: QueryDSL | None = Field(
        default=None, description="上一轮成功执行并通过安全校验的结构化 DSL"
    )
    active_entities: dict[str, Any] = Field(
        default_factory=dict, description="当前激活的时间范围 / 筛选值 / 聚合维度（人类可读）"
    )


# --------------------------------------------------------------------------- #
# 会话状态存储（内存实现：TTL 惰性失效 + LRU 容量上限，线程安全）
# --------------------------------------------------------------------------- #
class SessionStore:
    """轻量会话记忆管理器。

    默认基于进程内字典存储（TTL 惰性失效 + 超出容量按最久未访问淘汰 LRU）。
    预留持久化扩展：子类覆写 ``_persist(state)`` / ``_load(session_id)`` 即可
    接入 Redis / SQLite 等后端；对外接口保持 ``get / update / clear / prune`` 不变。

    安全约束：``get`` 必须同时传入 user_id，状态归属不一致时返回 None（强隔离）。
    """

    def __init__(
        self,
        ttl_seconds: int | None = None,
        max_sessions: int | None = None,
        history_turns: int | None = None,
        db_path: str | None = None,
    ) -> None:
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.SESSION_MEMORY_TTL
        self._max = (
            max_sessions if max_sessions is not None else settings.SESSION_MEMORY_MAX_SESSIONS
        )
        self._history_turns = (
            history_turns if history_turns is not None else settings.SESSION_MEMORY_HISTORY_TURNS
        )
        self._items: OrderedDict[str, SessionState] = OrderedDict()  # 最近访问在队尾（LRU）
        self._lock = threading.Lock()
        # 持久化层（整改指令3-2）：配置 db_path 时落盘 SQLite，多 worker/重启一致
        self._kv: SqliteKVStore | None = None
        if db_path:
            self._kv = SqliteKVStore(db_path, table="sessions")

    # ------------------------------------------------------------------ #
    # 持久化扩展点（整改指令3-2）：子类可覆写接入 Redis 等外部后端
    # ------------------------------------------------------------------ #
    def _persist(self, state: SessionState) -> None:
        """把会话状态写入持久层（默认实现：SQLite；子类可覆写为 Redis 等）。"""
        if self._kv is not None:
            self._kv.set(state.session_id, state.model_dump(mode="json"))

    def _load(self, session_id: str) -> SessionState | None:
        """从持久层读取会话状态；未命中返回 None。"""
        if self._kv is None:
            return None
        raw = self._kv.get(session_id, ttl_seconds=self._ttl)
        if raw is None:
            return None
        return SessionState.model_validate(raw)

    def _delete_persisted(self, session_id: str) -> None:
        """从持久层删除会话状态（TTL 失效 / 主动清除 / LRU 淘汰时调用）。"""
        if self._kv is not None:
            self._kv.delete(session_id)

    # ------------------------------------------------------------------ #
    def get(self, session_id: str, user_id: str | None) -> SessionState | None:
        """按 (session_id, user_id) 读取会话状态；跨用户 / 过期返回 None。

        内存未命中时尝试从持久层加载（重启后首次访问可恢复会话）。
        """
        owner = user_id or _ANONYMOUS
        with self._lock:
            state = self._items.get(session_id)
            if state is None:
                state = self._load(session_id)
                if state is not None:
                    # 跨用户强隔离同样作用于持久层恢复的会话
                    if state.user_id != owner:
                        return None
                    if time.time() - state.updated_at.timestamp() > self._ttl:
                        self._delete_persisted(session_id)
                        return None
                    self._items[session_id] = state
                    self._items.move_to_end(session_id)
                    return state
                return None
            if state.user_id != owner:
                return None  # 跨用户强隔离：拒绝继承，不删除原用户状态
            if time.time() - state.updated_at.timestamp() > self._ttl:
                self._items.pop(session_id, None)
                self._delete_persisted(session_id)
                return None
            self._items.move_to_end(session_id)
            return state

    def update(self, session_id: str, user_id: str | None, state: SessionState) -> SessionState:
        """写入（或覆盖）会话状态，滚动裁剪历史并维护 LRU 容量。"""
        state.session_id = session_id
        state.user_id = user_id or _ANONYMOUS
        state.updated_at = datetime.now(UTC)
        # 滚动保留最近 N 轮（每轮 user + assistant 两条）
        max_msgs = self._history_turns * 2
        if len(state.history) > max_msgs:
            state.history = state.history[-max_msgs:]
        with self._lock:
            self._items[session_id] = state
            self._items.move_to_end(session_id)
            while len(self._items) > self._max:
                evicted = self._items.pop(next(iter(self._items)))  # 最久未访问（队首）
                self._delete_persisted(evicted.session_id)
        self._persist(state)  # 持久化扩展点（写入 SQLite / Redis）
        return state

    def clear(self, session_id: str, user_id: str | None) -> bool:
        """删除会话状态，返回是否存在（归属不一致视为不存在，不误删）。"""
        owner = user_id or _ANONYMOUS
        with self._lock:
            state = self._items.get(session_id)
            if state is None or state.user_id != owner:
                return False
            self._items.pop(session_id, None)
            self._delete_persisted(session_id)
            return True

    def prune(self) -> int:
        """清理全部过期会话，返回清理条数。"""
        now = time.time()
        with self._lock:
            expired = [
                sid for sid, s in self._items.items() if now - s.updated_at.timestamp() > self._ttl
            ]
            for sid in expired:
                self._items.pop(sid, None)
                self._delete_persisted(sid)
            return len(expired)

    def clear_all(self) -> int:
        """清空全部会话状态（管理 / 测试用），返回清理条数。"""
        with self._lock:
            n = len(self._items)
            self._items.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


_default_store: SessionStore | None = None
_store_lock = threading.Lock()


def default_session_store() -> SessionStore:
    """进程内复用的默认会话记忆存储。

    配置 ``STATE_STORE_DB`` 时自动接入 SQLite 持久化（多 worker/重启一致）；
    未配置时保持纯内存实现（本地开发/演示）。
    """
    global _default_store
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                _default_store = SessionStore(db_path=settings.STATE_STORE_DB)
    return _default_store


# --------------------------------------------------------------------------- #
# 上下文继承与消解（Contextual Merging）
# --------------------------------------------------------------------------- #
@dataclass
class ContextResolution:
    """一次上下文判定的结果。

    - mode == "inherit"  / "drilldown"：基于上轮 DSL 合并出本轮 DSL（未守卫，
      由调用方继续过 apply_policy）；
    - mode == "fresh"：不继承（reason 区分 no_history / topic_switch / no_delta）。
    """

    mode: str
    dsl: QueryDSL | None = None
    summary: str = ""
    reason: str = ""


@dataclass
class _Deltas:
    """从省略指代语句中提取的语义增量（相对上轮 DSL）。"""

    provinces: list[str] = field(default_factory=list)
    region: str | None = None
    category: str | None = None
    time_filter: TimeFilter | None = None
    trend: bool = False
    comparison: str | None = None
    expand_dim: bool = False

    def has_any(self) -> bool:
        return bool(
            self.provinces
            or self.region
            or self.category is not None
            or self.time_filter is not None
            or self.trend
            or self.comparison
            or self.expand_dim
        )


def _collect_deltas(query: str) -> _Deltas:
    """从省略指代语句中提取语义增量（确定性规则，与启发式解析同源）。"""
    d = _Deltas()
    provinces = [p for p in PROVINCES if p in query]
    if provinces:
        d.provinces = provinces
    else:
        for region in REGIONS:
            if region in query:
                d.region = region
                break
    cats = [c for c in CATEGORIES if c in query]
    if cats:
        d.category = cats[0]
    time_dict = _h._time_filter(query)
    if time_dict is not None:
        d.time_filter = TimeFilter.model_validate(time_dict)
    ql = query.lower()
    d.trend = any(k in ql for k in _TREND_KEYWORDS)
    d.comparison = _h._comparison(query)
    d.expand_dim = _expand_dimension(query) is not None
    return d


def _has_metric_term(query: str) -> bool:
    """是否包含已定义的业务指标词（视为"完整新问题"而非省略指代）。"""
    q = query.lower()
    return any(term in q for term in METRIC_TERMS)


def _expand_dimension(query: str) -> str | None:
    """下钻语句中要追加的维度字段（如「按品类展开」-> category）。

    维度候选集从语义目录 + present.labels 的中文标签派生（与 field_label 同源），
    使"按省份/按品牌/按支付状态/按性别/按类目"等维度均可被识别为下钻目标；
    候选字段全部受语义目录 COLUMNS 白名单约束，绝不引入未登记字段。
    """
    if not any(k in query for k in _ADD_DIM_KEYWORDS):
        return None

    # (中文关键词 -> 逻辑字段) 维度映射：以 labels 中文标签为底，补充常用问法同义词
    try:
        from present.labels import FIELD_LABELS
        from semantic import catalog
    except ImportError:  # pragma: no cover - 依赖缺失时走硬编码回退
        return _expand_dimension_fallback(query)

    dim_keywords: dict[str, str] = {}
    for col in catalog.COLUMNS:
        label = FIELD_LABELS.get(col, col)
        # 只登记"看起来像维度"的字段：非主事实表聚合数值列（用 dtype 粗筛）
        meta = catalog.COLUMNS[col]
        if meta.table == "fact_orders" and col in ("order_id", "product_id", "user_id"):
            continue  # 明细键/事实键不做下钻维度
        if meta.dtype in ("int", "float") and col != "order_id":
            continue  # 数值列不做维度
        dim_keywords[label] = col
        # 为中文标签补充"按X展开"最常用问法同义词（与启发式解析器同源）
        for alias_word in _DIMENSION_SYNONYMS.get(col, ()):
            dim_keywords[alias_word] = col

    for keyword, target_field in dim_keywords.items():
        if keyword in query:
            return target_field
    return _expand_dimension_fallback(query)


# 维度字段 -> 常用中文问法同义词（与 heuristic._dimensions 的问法对齐）
_DIMENSION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "category": ("品类", "类别", "类目"),
    "brand": ("品牌",),
    "province": ("省份", "省", "地区"),
    "pay_status": ("支付状态", "支付方式"),
    "gender": ("性别",),
    "refund_status": ("退款状态",),
}


def _expand_dimension_fallback(query: str) -> str | None:
    """硬编码回退（依赖缺失 / 未命中映射时保持向后兼容）。"""
    if "品类" in query or "类别" in query:
        return "category"
    if "品牌" in query:
        return "brand"
    return None


def _resolve_row_filter(rf: dict, principal: str) -> dict:
    """解析 RLS 参数化模板（与 security.guard 同源实现，避免私有依赖）。"""
    param = rf.get("param")
    if param is None:
        return rf
    if not isinstance(param, str) or not param.startswith("principal."):
        raise PipelineError(f"不支持的 RLS 参数模板: {param!r}")
    attr = param.split(".", 1)[1]
    attrs = PRINCIPAL_ATTRS.get(principal, {})
    if attr not in attrs:
        raise PipelineError(f"主体 {principal!r} 缺少 RLS 属性 {attr!r}")
    resolved = dict(rf)
    resolved.pop("param", None)
    resolved["value"] = attrs[attr]
    return resolved


def strip_rls_filters(dsl: QueryDSL, principal: str | None) -> QueryDSL:
    """剥离主体策略已注入的行级 RLS 过滤（供继承合并前净化）。

    落库的 last_dsl 是守卫后的（含 RLS 过滤）；继承合并时若不去除，合并结果再
    经 apply_policy 会重复注入 RLS 谓词。本函数按主体策略重新解析 RLS 模板，
    删除与之一致的条目，保证合并后 RLS 恰好注入一次（红线：不绕过、不预固化）。
    """
    if principal is None:
        return dsl
    policy = POLICIES.get(principal)
    if policy is None or not policy.row_filters:
        return dsl
    extra = [Filter.model_validate(_resolve_row_filter(rf, principal)) for rf in policy.row_filters]
    kept = [f for f in dsl.filters if f not in extra]
    if len(kept) == len(dsl.filters):
        return dsl
    return dsl.model_copy(update={"filters": kept})


def _apply_deltas(base: QueryDSL, deltas: _Deltas) -> QueryDSL:
    """把语义增量合并进上轮 DSL（结构化合并，不触碰聊天文本）。"""
    filters = list(base.filters)
    if deltas.region is not None or deltas.provinces:
        filters = [f for f in filters if f.field != "province"]
        if deltas.region is not None:
            filters.append(
                Filter(
                    field="province",
                    operator=FilterOperator.IN,
                    value=REGIONS[deltas.region],
                )
            )
        elif len(deltas.provinces) == 1:
            filters.append(
                Filter(field="province", operator=FilterOperator.EQ, value=deltas.provinces[0])
            )
        else:
            filters.append(
                Filter(field="province", operator=FilterOperator.IN, value=deltas.provinces)
            )
    if deltas.category is not None:
        filters = [f for f in filters if f.field != "category"]
        filters.append(Filter(field="category", operator=FilterOperator.EQ, value=deltas.category))
    update: dict[str, Any] = {"filters": filters}
    if deltas.time_filter is not None:
        update["time_filter"] = deltas.time_filter
    return base.model_copy(update=update)


def _compose_summary(deltas: _Deltas, *, expand: str | None = None, trend: bool = False) -> str:
    """把本轮"继承了什么 / 改了什么"转成人话，供前端展示与审计。"""
    parts = ["已继承上一轮指标"]
    if deltas.region is not None:
        parts.append(f"将地区筛选调整为「{deltas.region}」")
    elif deltas.provinces:
        parts.append(f"将地区筛选调整为「{'、'.join(deltas.provinces)}」")
    if deltas.category is not None:
        parts.append(f"将品类筛选调整为「{deltas.category}」")
    if deltas.time_filter is not None:
        parts.append("按你的要求调整了时间范围")
    if expand is not None:
        # 维度中文标签与 present.labels 同源（"按省份展开" 显示为 追加省份维度）
        try:
            from present.labels import FIELD_LABELS

            label = FIELD_LABELS.get(expand, expand)
        except ImportError:
            label = expand
        parts.append(f"追加{label}维度")
    if trend:
        parts.append("按趋势展开分析")
    return "，".join(parts) + "。"


def resolve_context(
    query: str, last_dsl: QueryDSL | None, principal: str | None
) -> ContextResolution:
    """判定本轮查询与上一轮 DSL 的关系，返回合并结果（确定性、可单测）。

    判定规则（按优先级）：
    1. 无上一轮 DSL            -> fresh（no_history，无可继承）；
    2. 含显式业务指标词        -> fresh（topic_switch，完整新问题，调用方清理旧 DSL）；
    3. 无指标词但含语义增量    -> inherit / drilldown（基于上轮 DSL 结构化合并）；
    4. 无任何有效增量          -> fresh（no_delta，避免把无效短句误拼进上轮语义）。
    """
    if last_dsl is None:
        return ContextResolution(mode="fresh", reason="no_history")

    if _has_metric_term(query):
        return ContextResolution(
            mode="fresh",
            reason="topic_switch",
            summary="已识别为新问题，重置上一轮查询上下文",
        )

    deltas = _collect_deltas(query)
    if not deltas.has_any():
        return ContextResolution(mode="fresh", reason="no_delta")

    base = strip_rls_filters(last_dsl, principal)
    merged = _apply_deltas(base, deltas)

    expand = _expand_dimension(query)
    if expand is not None:
        # 归一化维度对象（兼容 dict 输入，防御 model_copy 不做 revalidate）
        dims = [Dimension.model_validate(d) for d in merged.dimensions]
        if not any(d.field == expand for d in dims):
            dims.append(Dimension(field=expand))
        merged = merged.model_copy(update={"dimensions": dims})
        return ContextResolution(
            mode="drilldown",
            dsl=merged,
            summary=_compose_summary(deltas, expand=expand),
            reason="drilldown_dim",
        )
    if deltas.trend or deltas.comparison:
        return ContextResolution(
            mode="drilldown",
            dsl=merged,
            summary=_compose_summary(deltas, trend=True),
            reason="drilldown_trend",
        )
    return ContextResolution(
        mode="inherit",
        dsl=merged,
        summary=_compose_summary(deltas),
        reason="inherit",
    )


# --------------------------------------------------------------------------- #
# 状态维护辅助
# --------------------------------------------------------------------------- #
def derive_active_entities(dsl: QueryDSL) -> dict[str, Any]:
    """从 DSL 派生当前激活语义状态（时间范围 / 筛选 / 维度 / 指标）。"""
    return {
        "metrics": [m.alias for m in dsl.metrics],
        "dimensions": [d.field for d in dsl.dimensions],
        "filters": [
            {"field": f.field, "operator": f.operator.value, "value": f.value} for f in dsl.filters
        ],
        "time_filter": dsl.time_filter.model_dump(mode="json") if dsl.time_filter else None,
    }


def append_message(
    state: SessionState,
    role: Literal["user", "assistant"],
    content: str,
    dsl: QueryDSL | None = None,
) -> None:
    """向会话历史追加一条消息（滚动裁剪由 SessionStore.update 完成）。"""
    state.history.append(ChatMessage(role=role, content=content, dsl=dsl))


__all__ = [
    "ChatMessage",
    "ContextResolution",
    "SessionState",
    "SessionStore",
    "append_message",
    "default_session_store",
    "derive_active_entities",
    "resolve_context",
    "strip_rls_filters",
]
