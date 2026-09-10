"""Agent 编排层公共入口：run_pipeline(query) -> QueryDSL。

自动分派：
- 配置了可用的 Model Provider / LLM_API_KEY -> 使用 LLMNL2DSL
  （LLM 产出 JSON + 严格校验 + 重试，客户端经 providers.chat_text 统一流转）；
- 未配置（离线）      -> 使用 DeterministicNL2DSL 启发式兜底。

两者都不允许返回裸 SQL；失败统一抛 PipelineError（拒绝而非猜测）。

可选 principal 参数：在 DSL 生成后施加安全守卫（表级/列级/行级 RLS），
见 security.guard.apply_policy。
"""

from __future__ import annotations

from functools import lru_cache

from agent.agent import LLMNL2DSL
from agent.errors import PipelineError
from agent.heuristic import DeterministicNL2DSL
from agent.llm import LLMError, resolve_default_client
from config import settings
from security.guard import apply_policy
from semantic.dsl_schema import QueryDSL

__all__ = ["PipelineError", "run_pipeline", "run_pipeline_with_status"]


@lru_cache(maxsize=1)
def _default_agent() -> object:
    """构造默认 Agent（按可用 LLM 配置分派，进程内缓存）。

    客户端从 Model Provider 网关解析（首位已配置 Key 的启用供应商 ->
    环境变量回退）；无任何可用配置时回退确定性启发式实现。
    """
    client = resolve_default_client()
    if client is not None:
        return LLMNL2DSL(client, max_retries=settings.LLM_MAX_RETRIES)
    return DeterministicNL2DSL()


def run_pipeline_with_status(query: str, principal: str | None = None) -> tuple[QueryDSL, bool]:
    """生成 DSL 并返回是否因 LLM 故障切换到启发式降级模式。"""
    degraded = False
    try:
        dsl = _default_agent().run(query, principal=principal)
    except (LLMError, PipelineError) as original_error:
        # LLM 结构/语义重试耗尽时，仅对确定性覆盖范围内的问题安全降级。
        try:
            dsl = DeterministicNL2DSL().run(query, principal=principal)
        except Exception:
            raise original_error from original_error
        degraded = True
    return apply_policy(dsl, principal), degraded


def run_pipeline(query: str, principal: str | None = None) -> QueryDSL:
    """自然语言 -> QueryDSL；LLM 网络故障时安全降级到确定性启发式。"""
    dsl, _ = run_pipeline_with_status(query, principal)
    return dsl


def rewrite_dsl(
    query: str,
    dsl: QueryDSL,
    error: str,
    attempts: int = 1,
    principal: str | None = None,
) -> QueryDSL:
    """SQL 执行自愈：把精确的编译/引擎报错喂回 LLM，重写 DSL。

    仅当配置了 LLM（LLMNL2DSL）时才有意义；确定性兜底会抛 PipelineError，
    由调用方透传原始执行报错。attempts 为修正轮数（至少 1 次）。
    重写 Prompt 同样按主体过滤字段白名单（守卫前移）。
    """
    agent = _default_agent()
    rewrite = getattr(agent, "rewrite", None)
    if rewrite is None:
        raise PipelineError("当前 Agent 不支持 SQL 自愈重写")
    return rewrite(query, dsl, error, attempts=attempts, principal=principal)
