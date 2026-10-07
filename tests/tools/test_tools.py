"""Multi-Tool 协议与注册表单元测试：注册 / 检索 / 非法参数拦截 / JSON Schema。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, ValidationError

from tools.base import BaseTool, DisplayType, ToolContext, ToolResult
from tools.registry import (
    DuplicateToolError,
    ToolRegistry,
    UnknownToolError,
    default_registry,
    register_tool,
)


class _AddArgs(BaseModel):
    a: float = Field(..., description="加数")
    b: float = Field(..., description="被加数")


class _AddTool(BaseTool):
    """加法工具（测试用）。"""

    name = "test_add"
    description = "计算两个数的和"
    args_schema = _AddArgs

    def execute(self, validated_args: _AddArgs, ctx: ToolContext | None = None) -> ToolResult:
        total = validated_args.a + validated_args.b
        return ToolResult(success=True, data={"total": total}, display_type=DisplayType.NUMBER)


# --------------------------------------------------------------------------- #
# 注册 / 检索
# --------------------------------------------------------------------------- #
def test_register_and_get():
    reg = ToolRegistry()
    reg.register(_AddTool())
    assert reg.has("test_add")
    assert reg.get_tool("test_add") is not None
    assert reg.tool_names() == ["test_add"]


def test_register_via_decorator():
    reg = ToolRegistry()

    @register_tool(reg)
    class Doubler(BaseTool):
        name = "test_doubler"
        description = "翻倍"

        class args_schema(BaseModel):
            x: float

        def execute(self, validated_args, ctx=None):
            return ToolResult(success=True, data={"x2": validated_args.x * 2})

    tool = reg.get_tool("test_doubler")
    result = tool.run({"x": 21})
    assert result.success and result.data == {"x2": 42}


def test_duplicate_registration_rejected():
    reg = ToolRegistry()
    reg.register(_AddTool())
    with pytest.raises(DuplicateToolError):
        reg.register(_AddTool())


def test_unknown_tool_error():
    reg = ToolRegistry()
    with pytest.raises(UnknownToolError):
        reg.get_tool("no_such_tool")


def test_unregister():
    reg = ToolRegistry()
    reg.register(_AddTool())
    reg.unregister("test_add")
    assert not reg.has("test_add")
    with pytest.raises(UnknownToolError):
        reg.get_tool("test_add")


def test_default_registry_is_singleton():
    assert default_registry() is default_registry()
    # 内建工具已随 tools 包自动注册
    assert default_registry().has("query_metric")
    assert default_registry().has("trend_analysis")
    assert default_registry().has("export_report")
    assert default_registry().has("explain_glossary")


# --------------------------------------------------------------------------- #
# OpenAI Function Calling JSON Schema 适配
# --------------------------------------------------------------------------- #
def test_tool_definitions_openai_schema():
    reg = ToolRegistry()
    reg.register(_AddTool())
    defs = reg.tool_definitions()
    assert isinstance(defs, list) and len(defs) == 1
    entry = defs[0]
    assert entry["type"] == "function"
    fn = entry["function"]
    assert fn["name"] == "test_add"
    assert "计算两个数的和" in fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    assert set(params["properties"].keys()) == {"a", "b"}
    assert params["properties"]["a"]["description"] == "加数"


def test_to_definition_matches_registry():
    tool = _AddTool()
    defn = tool.to_definition()
    assert defn["function"]["name"] == "test_add"
    assert defn["function"]["parameters"]["required"] == ["a", "b"]


# --------------------------------------------------------------------------- #
# 非法参数拦截（Pydantic 严格校验：extra=forbid + 类型约束）
# --------------------------------------------------------------------------- #
def test_illegal_extra_arg_rejected():
    tool = _AddTool()
    # validate_args 直接调用：未知字段 -> 抛异常
    with pytest.raises(ValueError) as exc:
        tool.validate_args({"a": 1, "b": 2, "drop_table": "users"})
    assert "非法参数" in str(exc.value)
    # run() 入口：异常被包装为 success=False 的 ToolResult，绝不进入执行
    result = tool.run({"a": 1, "b": 2, "drop_table": "users"})
    assert result.success is False
    assert "非法参数" in (result.error_msg or "")


def test_wrong_type_arg_rejected():
    tool = _AddTool()
    with pytest.raises(ValidationError):
        tool.validate_args({"a": "not-a-number", "b": 2})
    result = tool.run({"a": "not-a-number", "b": 2})
    assert result.success is False
    assert result.data is None


def test_missing_required_arg_rejected():
    tool = _AddTool()
    with pytest.raises(ValidationError):
        tool.validate_args({"a": 1})
    result = tool.run({"a": 1})
    assert result.success is False
    assert result.data is None


def test_run_valid_args_and_duration():
    tool = _AddTool()
    result = tool.run({"a": 1, "b": 2})
    assert result.success
    assert result.data == {"total": 3}
    assert result.duration_ms >= 0
    assert result.error_msg is None


# --------------------------------------------------------------------------- #
# 工具异常封装：execute 抛错 -> success=False + error_msg，不向外泄漏
# --------------------------------------------------------------------------- #
class _BoomTool(BaseTool):
    name = "test_boom"
    description = "抛错工具"

    class args_schema(BaseModel):
        pass

    def execute(self, validated_args, ctx=None):
        raise RuntimeError("内部异常")


def test_tool_exception_wrapped():
    tool = _BoomTool()
    result = tool.run({})
    assert result.success is False
    assert "RuntimeError" in (result.error_msg or "")
    assert result.data is None
