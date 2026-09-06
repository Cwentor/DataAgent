"""权限策略加载器（P0-3）：从 config/policies.json 重建策略注册表与主体属性。

背景（审计 P0-3）：security/policy.py 的策略注册表原是模块常量（仅 3 个写死主体、
RLS 仅支持"字段 IN 值列表"一种形态）——改权限必须改代码重启。本模块把策略改为
数据驱动：

- 策略来源：config/policies.json（可选；缺失时回退 security.policy 内置默认）；
- RLS 谓词支持参数化模板：{"field": "province", "operator": "in", "param": "principal.provinces"}
  中的 param 在施加策略时从主体属性表（PRINCIPAL_ATTRS）解析为实际值列表，
  不再要求每个主体把 RLS 值写死在策略定义里；
- 主体属性表（principal_attrs）与策略同文件管理，运维加省份/加主体只改配置。

用法：
- 服务启动：web.service.ensure_db() 调用 refresh_policies()；
- CLI 预览：python -m security.policy_loader
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import settings
from security import policy as policy_mod
from security.policy import Policy

# 内置默认快照（reset_default_policies / 无配置回退使用）
_DEFAULT_POLICIES = dict(policy_mod.POLICIES)
_DEFAULT_ATTRS = {k: dict(v) for k, v in policy_mod.PRINCIPAL_ATTRS.items()}

DEFAULT_POLICIES_PATH: Path = settings.PROJECT_ROOT / "config" / "policies.json"


def _read_config(path: Path | str | None) -> dict[str, Any] | None:
    p = Path(path) if path is not None else DEFAULT_POLICIES_PATH
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"策略配置 {p} 顶层必须是 JSON 对象")
    return data


def build_policies(
    path: Path | str | None = None,
) -> tuple[dict[str, Policy], dict[str, dict[str, list[str]]]]:
    """构建 (POLICIES, PRINCIPAL_ATTRS)（纯函数，不改全局）。

    参数化 RLS 模板在此保留原样（param 字段），值在施加策略时按主体解析。
    """
    config = _read_config(path)
    if config is None:
        return dict(_DEFAULT_POLICIES), {k: dict(v) for k, v in _DEFAULT_ATTRS.items()}

    attrs_raw = config.get("principal_attrs", {})
    principal_attrs: dict[str, dict[str, list[str]]] = {}
    for principal, attr_map in dict(attrs_raw).items():
        principal_attrs[str(principal)] = {str(k): list(v) for k, v in dict(attr_map or {}).items()}

    policies_raw = config.get("policies")
    if not isinstance(policies_raw, dict) or not policies_raw:
        raise ValueError("策略配置缺少 policies（策略注册表）")
    policies: dict[str, Policy] = {}
    for name, spec in policies_raw.items():
        if not isinstance(spec, dict):
            raise ValueError(f"策略 {name!r} 的声明必须是对象")
        allowed = frozenset(str(t) for t in spec.get("allowed_tables", []))
        if not allowed:
            raise ValueError(f"策略 {name!r} 的 allowed_tables 不能为空（默认拒绝）")
        policies[str(name)] = Policy(
            name=str(name),
            allowed_tables=allowed,
            forbidden_columns=frozenset(str(c) for c in spec.get("forbidden_columns", [])),
            row_filters=tuple(dict(rf) for rf in spec.get("row_filters", [])),
        )
    return policies, principal_attrs


def refresh_policies(
    path: Path | str | None = None,
) -> tuple[dict[str, Policy], dict[str, dict[str, list[str]]]]:
    """重建并安装策略到 security.policy 全局（服务启动时调用，字典原地更新）。"""
    policies, attrs = build_policies(path)
    policy_mod.POLICIES.clear()
    policy_mod.POLICIES.update(policies)
    policy_mod.PRINCIPAL_ATTRS.clear()
    policy_mod.PRINCIPAL_ATTRS.update(attrs)
    return policies, attrs


def reset_default_policies() -> None:
    """恢复内置默认策略（测试隔离用）。"""
    policy_mod.POLICIES.clear()
    policy_mod.POLICIES.update(_DEFAULT_POLICIES)
    policy_mod.PRINCIPAL_ATTRS.clear()
    policy_mod.PRINCIPAL_ATTRS.update(_DEFAULT_ATTRS)


def main() -> None:
    """命令行入口：从 config/policies.json 构建策略并打印摘要（Build & preview policies）。"""
    policies, attrs = build_policies()
    print(f"策略 {len(policies)} 个、主体属性 {len(attrs)} 组：")
    for name, p in sorted(policies.items()):
        print(f"  - {name}: tables={sorted(p.allowed_tables)}")
        if p.forbidden_columns:
            print(f"      forbidden={sorted(p.forbidden_columns)}")
        for rf in p.row_filters:
            print(f"      rls={rf}")
    for principal, attr_map in sorted(attrs.items()):
        print(f"  attrs[{principal}] = {attr_map}")


if __name__ == "__main__":
    main()
