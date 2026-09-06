"""工具注册中心（ToolRegistry）：单例 + 装饰器 + Function Calling 适配。

职责：
1. ``register``：注册 BaseTool 实例或子类（同名重复注册抛错，杜绝静默覆盖）；
2. ``get_tool``：按名反查；``list_tools``：枚举全部工具；
3. ``tool_definitions``：生成 OpenAI/通用 Function Calling 规范的工具声明
   （JSON Schema），供 LLM 调度内核注入上下文做 Plan & Select；
4. ``default_registry()``：进程内单例，内置工具在 tools/builtins 导入时自注册。

安全约定：注册中心是"受控能力白名单"——Agent 只能调度已注册工具，
未注册名称一律拒绝（UnknownToolError），从机制上防止越权工具调用。
"""

from __future__ import annotations

import threading
from typing import Any

from tools.base import BaseTool

__all__ = [
    "ToolRegistry",
    "UnknownToolError",
    "default_registry",
    "register_tool",
    "registry",
]


class UnknownToolError(KeyError):
    """请求的工具未注册（Agent 调度了白名单之外的名称）。"""


class DuplicateToolError(ValueError):
    """同名工具重复注册（禁止静默覆盖，保证确定性与可溯源）。"""


class ToolRegistry:
    """线程安全的工具注册表。"""

    def __init__(self) -> None:
        """初始化空注册表（name -> 工具实例，写操作全程持锁）。"""
        self._tools: dict[str, BaseTool] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 注册 / 反查
    # ------------------------------------------------------------------ #
    def register(self, tool: BaseTool | type[BaseTool]) -> BaseTool:
        """注册工具实例或其子类（子类自动实例化）。

        同名重复注册抛 DuplicateToolError；未实现 name/args_schema 的工具抛 ValueError。
        """
        if isinstance(tool, type):
            if not issubclass(tool, BaseTool) or tool is BaseTool:
                raise ValueError(f"只能注册 BaseTool 实例或其子类，收到 {tool!r}")
            instance = tool()
        else:
            instance = tool
        if not isinstance(instance, BaseTool):
            raise ValueError(f"只能注册 BaseTool 实例，收到 {type(instance).__name__}")
        name = instance.name
        if not name:
            raise ValueError("工具必须声明非空 name")
        with self._lock:
            if name in self._tools:
                raise DuplicateToolError(f"工具 {name!r} 已注册，禁止重复注册")
            self._tools[name] = instance
        return instance

    def unregister(self, name: str) -> None:
        """注销工具（供测试隔离使用）。未注册时静默忽略。"""
        with self._lock:
            self._tools.pop(name, None)

    def get_tool(self, name: str) -> BaseTool:
        """按名反查工具；未注册抛 UnknownToolError。"""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"未注册的工具: {name!r}") from exc

    def has(self, name: str) -> bool:
        """判断工具是否已注册（Whether a tool is registered）。"""
        return name in self._tools

    def list_tools(self) -> list[BaseTool]:
        """按注册顺序返回全部工具（确定性，便于测试与文档）。"""
        with self._lock:
            return list(self._tools.values())

    def tool_names(self) -> list[str]:
        """按注册顺序返回工具名清单（Registered tool names in order）。"""
        with self._lock:
            return list(self._tools.keys())

    def __contains__(self, name: object) -> bool:
        """支持 ``name in registry`` 语法（Membership test）。"""
        return name in self._tools

    def __len__(self) -> int:
        """已注册工具数量（Number of registered tools）。"""
        return len(self._tools)

    # ------------------------------------------------------------------ #
    # Function Calling 规范适配
    # ------------------------------------------------------------------ #
    def tool_definitions(self) -> list[dict[str, Any]]:
        """生成 OpenAI/通用 Function Calling 规范的工具声明列表。"""
        return [tool.to_definition() for tool in self.list_tools()]

    def clear(self) -> None:
        """清空全部工具（仅测试使用）。"""
        with self._lock:
            self._tools.clear()


def _register_tool(target: Any = None, *, registry: ToolRegistry | None = None) -> Any:
    """@register_tool 装饰器实现：支持类、实例与指定注册中心三种形态。"""

    def _wrap(obj: BaseTool | type[BaseTool]) -> BaseTool:
        return (registry or default_registry()).register(obj)

    if target is None:
        return _wrap
    if isinstance(target, ToolRegistry):
        # @register_tool(some_registry) 形态：注册到指定中心（测试隔离用）
        def _deco(obj: BaseTool | type[BaseTool]) -> BaseTool:
            return target.register(obj)

        return _deco
    return _wrap(target)


# 兼容三种用法：
#   @register_tool
#   class MyTool(BaseTool): ...
#
#   @register_tool(reg)
#   class MyTool(BaseTool): ...          # 注册到指定注册中心
#
#   @register_tool()
#   def my_tool() -> BaseTool: ...
#   （函数形态由调用方自行 new 后交给 register，装饰器仅支持类/实例）
register_tool = _register_tool


# --------------------------------------------------------------------------- #
# 进程内单例
# --------------------------------------------------------------------------- #
_default_registry: ToolRegistry | None = None
_registry_lock = threading.Lock()


def default_registry() -> ToolRegistry:
    """进程内复用的默认注册中心（惰性初始化，线程安全）。"""
    global _default_registry
    if _default_registry is None:
        with _registry_lock:
            if _default_registry is None:
                _default_registry = ToolRegistry()
    return _default_registry


registry = default_registry  # 便捷别名
