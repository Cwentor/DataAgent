"""Model Provider 管理端点：配置 CRUD（不回传 Key）+ 连通性探测 + 密钥查看。

路由（均需认证，与 /api/query 同一鉴权语义）：
- GET    /api/settings/providers             -> 全量供应商列表（不含 api_key）+ 模型切换器候选
- POST   /api/settings/providers             -> 创建自定义供应商（协议限白名单：chat/responses/anthropic）
- PUT    /api/settings/providers/<id>        -> 更新（id / is_preset 不可变更；空 Key 保留原值）
- DELETE /api/settings/providers/<id>        -> 删除（预置供应商拒绝，返回 400）
- POST   /api/settings/providers/test        -> 连通性探测（极小 ping 文本，返回 HTTP 200 + 延时）
- POST   /api/settings/providers/<id>/reveal -> 查看已保存的真实 API Key（显式动作，记审计日志）

设计约束：
- 供应商控制开放多供应商接入：任意端点只要接口协议符合白名单
  （openai_chat / openai_responses / anthropic）即可添加；gemini 协议
  不在控制面白名单内（预置与 UI 均不提供）；
- API Key 落盘加密存储（providers.crypto），文件内容不含明文；
- 列表/详情响应**完全不含 api_key 字段**：前端不再回填脱敏串，杜绝
  「把脱敏串当真 Key 用」导致的连通失败；编辑时留空 = 保持原 Key；
- 连通性探测的业务失败（401/429/超时等）以 HTTP 200 + ``success=false`` 返回，
  与传输层错误（404 供应商不存在 / 400 参数非法）严格区分，前端据此渲染
  可理解的 Toast 提示。
"""

from __future__ import annotations

from typing import Any

from audit.logging import get_logger
from providers.errors import ProviderError
from providers.factory import default_provider_factory, invalidate_provider_caches
from providers.models import ApiProtocol, ModelItem, ProviderConfig
from providers.store import default_provider_store

logger = get_logger("web.providers")

# 控制面协议白名单：自定义供应商仅允许接入这三类接口协议。
SUPPORTED_PROTOCOLS: frozenset[ApiProtocol] = frozenset(
    {ApiProtocol.OPENAI_CHAT, ApiProtocol.OPENAI_RESPONSES, ApiProtocol.ANTHROPIC}
)

__all__ = [
    "SUPPORTED_PROTOCOLS",
    "create_provider",
    "delete_provider",
    "list_providers",
    "reveal_provider_key",
    "test_provider",
    "update_provider",
]


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _store():
    """进程级默认供应商存储（与查询链路同一数据源）。"""
    return default_provider_store()


def _factory():
    """进程级默认适配器工厂（写操作后需失效缓存）。"""
    return default_provider_factory()


def _normalize_provider_payload(body: dict[str, Any]) -> dict[str, Any]:
    """规范化创建/更新请求体：协议字符串校验 + 模型条目结构透传 pydantic。

    协议白名单：仅接受 SUPPORTED_PROTOCOLS 内的接口协议（gemini 拒绝）。
    额外防御：客户端提交的 ``is_preset`` 一律忽略（预置标记只由系统管理）。
    """
    payload = {k: v for k, v in dict(body).items() if k != "is_preset"}
    if "protocol" in payload:
        try:
            protocol = ApiProtocol(str(payload["protocol"]))
        except ValueError as exc:
            raise ValueError(f"不支持的 API 协议: {payload['protocol']}") from exc
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ValueError(f"不支持的 API 协议: {protocol.value}")
        payload["protocol"] = protocol
    return payload


def _resolve_test_provider(body: dict[str, Any]) -> tuple[ProviderConfig | None, str, str | None]:
    """解析连通性探测目标：已保存供应商（provider_id）或临时配置（未保存先试）。

    返回 (provider, model_id, error)；provider 为 None 时 error 给出原因。
    """
    provider_id = str(body.get("provider_id") or "").strip()
    model_id = str(body.get("model_id") or "").strip()
    if provider_id:
        provider = _store().get_provider(provider_id)
        if provider is None:
            return None, model_id, f"供应商不存在: {provider_id}"
        return provider, model_id, None
    # 临时配置：前端"添加供应商未保存先测试"场景（base_url / api_key / protocol / model_id）
    base_url = str(body.get("base_url") or "").strip()
    if not base_url:
        return None, model_id, "缺少 provider_id 或 base_url"
    try:
        protocol = ApiProtocol(str(body.get("protocol") or "openai_chat"))
    except ValueError:
        return None, model_id, f"不支持的 API 协议: {body.get('protocol')}"
    if protocol not in SUPPORTED_PROTOCOLS:
        return None, model_id, f"不支持的 API 协议: {protocol.value}"
    try:
        provider = ProviderConfig(
            id="tmp",
            name=str(body.get("name") or "临时供应商"),
            is_preset=False,
            enabled=True,
            base_url=base_url,
            api_key=str(body.get("api_key") or ""),
            protocol=protocol,
            models=[ModelItem(id=model_id)] if model_id else [],
            custom_headers=dict(body.get("custom_headers") or {}),
        )
    except ValueError as exc:
        return None, model_id, f"临时配置非法: {exc}"
    return provider, model_id, None


# --------------------------------------------------------------------------- #
# 端点实现（返回 (http_status, body)）
# --------------------------------------------------------------------------- #
def list_providers() -> tuple[int, dict[str, Any]]:
    """GET /api/settings/providers：脱敏列表 + 前端模型切换器候选。"""
    return 200, {
        "providers": _store().public_list(),
        "choices": _factory().list_choices(),
    }


def create_provider(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /api/settings/providers：创建自定义供应商（is_preset 固定为 False）。

    接口协议必须符合白名单（chat/responses/anthropic），gemini 返回 400。
    """
    if not isinstance(body, dict) or not str(body.get("name") or "").strip():
        return 400, {"error": "供应商名称（name）必填"}
    try:
        payload = _normalize_provider_payload(body)
        payload.setdefault("is_preset", False)
        provider = _store().create_provider(payload)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    invalidate_provider_caches(provider.id)
    logger.info("provider_created", extra={"event": "provider_created", "provider_id": provider.id})
    return 200, {"provider": _store().public_view(provider)}


def update_provider(provider_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """PUT /api/settings/providers/<id>：更新配置（脱敏 Key / 空 Key 保留原值）。"""
    if not isinstance(body, dict) or not body:
        return 400, {"error": "请求体不能为空"}
    try:
        payload = _normalize_provider_payload(body)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    updated = _store().update_provider(provider_id, payload)
    if updated is None:
        return 404, {"error": f"供应商不存在: {provider_id}"}
    invalidate_provider_caches(provider_id)
    logger.info("provider_updated", extra={"event": "provider_updated", "provider_id": provider_id})
    return 200, {"provider": _store().public_view(updated)}


def delete_provider(provider_id: str) -> tuple[int, dict[str, Any]]:
    """DELETE /api/settings/providers/<id>：预置供应商拒绝删除（400）。"""
    deleted = _store().delete_provider(provider_id)
    if not deleted:
        existing = _store().get_provider(provider_id)
        if existing is None:
            return 404, {"error": f"供应商不存在: {provider_id}"}
        return 400, {"error": "预置供应商不可删除，可禁用或编辑"}
    invalidate_provider_caches(provider_id)
    logger.info("provider_deleted", extra={"event": "provider_deleted", "provider_id": provider_id})
    return 200, {"ok": True}


def reveal_provider_key(provider_id: str) -> tuple[int, dict[str, Any]]:
    """POST /api/settings/providers/<id>/reveal：查看已保存的真实 API Key。

    显式授权动作（前端「👁 查看」按钮触发）：响应含明文 Key，访问行为记
    审计日志（provider_key_revealed）。供应商不存在返回 404。
    """
    key = _store().reveal_key(provider_id.strip())
    if key is None:
        return 404, {"error": f"供应商不存在: {provider_id}"}
    logger.info(
        "provider_key_revealed",
        extra={"event": "provider_key_revealed", "provider_id": provider_id},
    )
    return 200, {"api_key": key}


def test_provider(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /api/settings/providers/test：极小 ping 文本验证连通性与延时。

    业务失败（401 鉴权失败 / 429 配额超限 / 超时等）以 HTTP 200 返回
    ``success=false`` 与可读 error 文案；传输层错误（供应商不存在）用 404。
    """
    provider, model_id, error = _resolve_test_provider(body or {})
    if provider is None:
        return 404, {"error": error}
    if not model_id and provider.models:
        model_id = provider.models[0].id
    if not model_id:
        return 400, {"error": "缺少待测试的模型（model_id）"}
    try:
        from providers.adapters import build_adapter

        result = build_adapter(provider, model_id).test_connection()
    except ProviderError as exc:
        return 200, {"success": False, "latency_ms": 0.0, "error": str(exc)}
    return 200, {
        "success": result.success,
        "latency_ms": result.latency_ms,
        "error": result.error,
    }
