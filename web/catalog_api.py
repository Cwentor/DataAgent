"""语义目录管理端点（issue #27）：json 唯一事实源不变，API 为受控写入界面。

路由（经 web/api.py 挂载，validate/write 要求 admin 角色）：
- GET   /api/catalog           -> 全量目录 + 版本哈希 + 分节可写性元数据（登录即可）
- POST  /api/catalog/validate  -> 试校验（不落盘，返回校验结果 / diff / 影响面）
- PUT   /api/catalog           -> 受控写入：sections 每节整体替换（可完成删除）
- PATCH /api/catalog           -> 受控写入：节内 deep merge（值 null = 删该键）

高压线（设计 docs/designs/2026-11-03-semantic-catalog-web-api-design.md §2）：
- config/semantic.json 是唯一事实源，本模块是它的受控写入界面，无任何旁路存储；
- 写入管道固定：读 -> 锁内版本哈希校验 -> 白名单校验 -> 合并 ->
  catalog_loader.validate_overlay 全量校验 -> 原子写（.tmp + os.replace）->
  refresh_catalog 热重载 -> 审计 diff 落 audit.jsonl / audit_log；
- 校验失败 / 写入失败绝不落盘、绝不 refresh，目录保持旧状态。

并发模型：进程内 threading.Lock 串行化"读-改-写"临界区（单 uvicorn 进程部署）；
内容哈希乐观锁兜底双入口并发——手改文件后 API 客户端持有的旧 version 自动失效。
多 worker 部署（uvicorn --workers >1）时进程内锁失效，属部署边界（设计 §10）。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid as _uuid
from copy import deepcopy
from typing import Any

from audit.logging import get_logger, get_request_id
from audit.record import AuditRecord
from semantic.catalog_loader import (
    DEFAULT_OVERLAY_PATH,
    refresh_catalog,
    validate_overlay,
)

logger = get_logger("web.catalog")

# --------------------------------------------------------------------------- #
# 分节白名单（设计 §4）：结构性契约节开放编辑需单独立项评审，本次一律只读
# --------------------------------------------------------------------------- #
EDITABLE_SECTIONS: tuple[str, ...] = (
    "aliases",  # 表别名（键须为已声明表，validate_overlay 自检）
    "metric_aliases",  # 聚合产物中文映射
    "value_labels",  # 枚举值中文标签
    "table_labels",  # 表中文标签
    "region_province_mapping",  # 大区-省份映射
    "dimension_members_seed",  # 维度成员离线回退种子
)
READONLY_SECTIONS: tuple[str, ...] = (
    "fact_table",
    "fact_tables",
    "fields",
    "join_rules",
    "fact_join_rules",
    "query_domains",
    "dimension_member_fields",
    "drilldown_dim_fields",
    "metrics",
    "count_entities",
    "paid_filter",
    "default_window",
    "reflector_concepts",
    "out_of_scope_concepts",
    "undefined_metrics",
)

# 影响面提示（设计 §6）：变更节 -> 受影响链路说明（纯提示，不阻断）
IMPACT_HINTS: dict[str, str] = {
    "aliases": "表别名变更影响编译器表别名渲染与查询 SQL 形态",
    "metric_aliases": "聚合产物列中文展示将随新映射变化",
    "value_labels": "影响澄清选项文案、结果枚举标签与 Planner 词表",
    "table_labels": "影响侧栏目录展示与 Planner 提示词",
    "region_province_mapping": "影响区域词展开与诊断下钻",
    "dimension_members_seed": "影响库不可用时的离线维度词表回退",
}

# 写入临界区锁：串行化"读-校验-合并-写-refresh"，杜绝并发写交错覆盖
_WRITE_LOCK = threading.Lock()

__all__ = [
    "EDITABLE_SECTIONS",
    "IMPACT_HINTS",
    "READONLY_SECTIONS",
    "catalog_version",
    "read_catalog",
    "validate_catalog",
    "write_catalog",
]


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _read_overlay_bytes() -> bytes:
    """读取当前 overlay 文件字节（文件由仓库提交与启动校验保证存在）。"""
    return DEFAULT_OVERLAY_PATH.read_bytes()


def catalog_version(raw: bytes | None = None) -> str:
    """内容哈希版本标识（sha256 前 12 位）：手改文件后自动失效，双入口安全。"""
    if raw is None:
        raw = _read_overlay_bytes()
    return hashlib.sha256(raw).hexdigest()[:12]


def _check_sections(sections: dict[str, Any]) -> str | None:
    """写请求白名单校验：未知节 / 只读节出现在 body 即拒绝（不静默剥离）。

    静默丢弃会让运营者误以为改成功，必须显式报错。返回错误信息或 None。
    """
    unknown = sorted(set(sections) - set(EDITABLE_SECTIONS) - set(READONLY_SECTIONS))
    readonly_hit = sorted(set(sections) & set(READONLY_SECTIONS))
    if readonly_hit:
        return "以下节为结构性契约节，只读（开放编辑需单独立项评审）: " + "、".join(readonly_hit)
    if unknown:
        return "未知节: " + "、".join(unknown)
    bad_type = sorted(k for k, v in sections.items() if not isinstance(v, dict))
    if bad_type:
        return "以下节的提交内容必须是 JSON 对象: " + "、".join(bad_type)
    return None


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """递归合并 patch 到 base 拷贝上；值 None 的键删除（PATCH 的删键约定）。"""
    result = deepcopy(base)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _apply_sections(
    data: dict[str, Any], sections: dict[str, Any], action: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """把写请求的 sections 应用到当前配置上，返回 (合并结果, 逐节 diff)。

    PUT = 节整体替换；PATCH = 节内 deep merge（null 删键）。
    diff 键集合为节内顶层键（可写节均为对象形态）。
    """
    merged = deepcopy(data)
    diff: list[dict[str, Any]] = []
    for name, patch in sections.items():
        old = data.get(name)
        if action == "patch" and isinstance(old, dict):
            new = _deep_merge(old, patch)
        else:
            new = deepcopy(patch)
        merged[name] = new
        old_keys = set(old) if isinstance(old, dict) else set()
        new_keys = set(new) if isinstance(new, dict) else set()
        diff.append(
            {
                "section": name,
                "added": sorted(new_keys - old_keys),
                "updated": sorted(k for k in old_keys & new_keys if old[k] != new[k]),
                "removed": sorted(old_keys - new_keys),
            }
        )
    return merged, diff


def _impact_notes(sections: dict[str, Any]) -> list[str]:
    """变更节 -> 影响面提示文案（纯静态映射，不阻断）。"""
    return [IMPACT_HINTS[name] for name in sections if name in IMPACT_HINTS]


def _write_audit(
    action: str, user: str, principal: str, before: str, after: str, diff: list[dict]
) -> None:
    """语义目录变更落审计（JSONL + DuckDB 双写；失败不影响主链路，RF4）。"""
    from web.service import _default_audit_store, write_audit_safely

    store = _default_audit_store()
    if store is None:
        return
    record = AuditRecord(
        request_id=get_request_id() or _uuid.uuid4().hex,
        prompt=f"语义目录变更（{action}）",
        user=user,
        principal=principal,
        catalog_change={
            "action": action,
            "user": user,
            "version_before": before,
            "version_after": after,
            "sections": diff,
        },
    )
    write_audit_safely(store, record)


# --------------------------------------------------------------------------- #
# 端点实现（返回 (http_status, body)，与 providers_api 同构）
# --------------------------------------------------------------------------- #
def read_catalog() -> tuple[int, dict[str, Any]]:
    """GET /api/catalog：全量目录 + 版本哈希 + 分节可写性元数据。

    data 直读文件内容（非 catalog.COLUMNS 运行态投影）——编辑界面的对象是
    文件本身，从运行态反推会引入第二事实源。
    """
    try:
        raw = _read_overlay_bytes()
    except OSError as exc:
        return 500, {"error": f"semantic.json 读取失败: {exc}"}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return 500, {"error": f"semantic.json 解析失败: {exc}"}
    return (
        200,
        {
            "version": catalog_version(raw),
            "editable_sections": list(EDITABLE_SECTIONS),
            "readonly_sections": list(READONLY_SECTIONS),
            "data": data,
        },
    )


def validate_catalog(
    body: dict[str, Any], *, user: str, principal: str
) -> tuple[int, dict[str, Any]]:
    """POST /api/catalog/validate：试校验不落盘，返回校验结果 / diff / 影响面。"""
    if not isinstance(body, dict):
        return 400, {"error": "invalid json"}
    sections = body.get("sections")
    if not isinstance(sections, dict) or not sections:
        return 400, {"error": "sections 必须是非空对象"}
    if err := _check_sections(sections):
        return 400, {"error": err}
    try:
        data = json.loads(_read_overlay_bytes().decode("utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return 500, {"error": f"semantic.json 读取/解析失败: {exc}"}

    # 版本可选：携带时同样校验（试校验基于过期内容会误导运营者）
    req_version = str(body.get("version") or "").strip()
    if req_version:
        current = catalog_version()
        if req_version != current:
            return 409, {"error": "目录已被修改，请重新获取最新内容", "current_version": current}

    action = "patch" if body.get("patch") else "put"
    merged, diff = _apply_sections(data, sections, action)
    errors = validate_overlay(merged)
    logger.info(
        "catalog_validated",
        extra={
            "event": "catalog_validated",
            "user": user,
            "valid": not errors,
            "sections": sorted(sections),
        },
    )
    payload: dict[str, Any] = {
        "valid": not errors,
        "errors": errors,
        "diff": diff,
        "impact": _impact_notes(sections),
    }
    if errors:
        return 400, {"error": "校验失败（未落盘）", **payload}
    return 200, payload


def write_catalog(
    body: dict[str, Any], *, user: str, principal: str, action: str
) -> tuple[int, dict[str, Any]]:
    """PUT/PATCH /api/catalog：受控写入（版本校验 -> 合并 -> 校验 -> 原子写 -> 热重载 -> 审计）。

    action：\"put\"（节整体替换）或 \"patch\"（deep merge，null 删键）。
    """
    if not isinstance(body, dict):
        return 400, {"error": "invalid json"}
    sections = body.get("sections")
    if not isinstance(sections, dict) or not sections:
        return 400, {"error": "sections 必须是非空对象"}
    req_version = str(body.get("version") or "").strip()
    if not req_version:
        return 400, {"error": "version 必填（乐观锁，取自 GET /api/catalog）"}
    if err := _check_sections(sections):
        return 400, {"error": err}

    with _WRITE_LOCK:
        try:
            raw = _read_overlay_bytes()
        except OSError as exc:
            return 500, {"error": f"semantic.json 读取失败: {exc}"}
        current_version = catalog_version(raw)
        if req_version != current_version:
            return 409, {
                "error": "目录已被他人修改，请重新获取最新内容",
                "current_version": current_version,
            }
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return 500, {"error": f"semantic.json 解析失败: {exc}"}

        merged, diff = _apply_sections(data, sections, action)
        errors = validate_overlay(merged)
        if errors:
            logger.warning(
                "catalog_write_rejected",
                extra={"event": "catalog_write_rejected", "user": user, "errors": errors},
            )
            return 400, {"error": "校验失败，未落盘", "errors": errors, "diff": diff}

        # 原子写：同目录 .tmp 写入后 os.replace（同盘原子，崩溃只留 .tmp 孤儿）
        tmp_path = DEFAULT_OVERLAY_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp_path, DEFAULT_OVERLAY_PATH)

        # 热重载；失败自愈重试一次，仍失败则回滚文件——严禁"文件已改、目录未刷"中间态
        try:
            refresh_catalog()
        except Exception:
            logger.exception("catalog_refresh_failed", extra={"event": "catalog_refresh_failed"})
            try:
                refresh_catalog()
            except Exception:
                DEFAULT_OVERLAY_PATH.write_bytes(raw)
                try:
                    refresh_catalog()
                except Exception:
                    logger.exception("catalog_rollback_refresh_failed")
                return 500, {"error": "目录热重载失败，文件已回滚，目录保持旧状态"}
        new_version = catalog_version()
        logger.info(
            "catalog_written",
            extra={
                "event": "catalog_written",
                "user": user,
                "action": action,
                "sections": sorted(sections),
                "version": new_version,
            },
        )
        _write_audit(action, user, principal, current_version, new_version, diff)
        return 200, {"version": new_version, "diff": diff, "impact": _impact_notes(sections)}
