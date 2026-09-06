"""统一工具协议（Multi-Tool Agent 的基础契约）。

本模块定义受控工具的三要素与标准返回结构，是"注册中心 -> Agent 调度 -> 前端展示"
全链路的事实来源（Single Source of Truth）：

- ``BaseTool``：抽象基类，规范统一工具签名——
  - ``name``：工具标识（供 Agent 引用与审计溯源）；
  - ``description``：面向 LLM 决策的意图/适用场景描述；
  - ``args_schema``：基于 Pydantic 的严格入参 Schema（extra="forbid"，
    非法/越权参数在入口被拦截，绝不透传）；
  - ``execute``：真正的执行逻辑，必须返回 ``ToolResult``。
- ``ToolResult``：标准返回结构——``success / data / display_type / error_msg``
  外加结构化元数据（``duration_ms`` 与 ``meta``），输入、输出、耗时、异常状态
  全部可序列化，供审计链路完整落盘。
- ``ToolContext``：执行期运行时上下文（连接、主体、自愈注入等），由调度内核注入，
  **不属于** LLM 可声明的入参，避免用户伪造连接/执行器等敏感对象。

设计约束（与项目安全基线一致）：
1. 任何数据查询工具底层都必须复用受控的 DSL 校验、AST 只读检查、
   超时/扫描行数/返回行数熔断（见 tools.builtins._query_core），禁止裸跑 SQL；
2. 工具不持有任何业务状态，入参与输出均可 JSON 序列化（确定性与可溯源）；
3. 不引入任何第三方框架：仅 Python 标准库 + Pydantic 契约。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class DisplayType(StrEnum):
    """工具输出的前端展示形态（前端据此选择渲染方式）。"""

    NUMBER = "number"  # 单值卡片
    TABLE = "table"  # 明细表格
    LINE = "line"  # 折线（时间趋势）
    BAR = "bar"  # 柱状
    PIE = "pie"  # 饼图
    PIVOT = "pivot"  # 透视表
    TEXT = "text"  # 纯文本
    MARKDOWN = "markdown"  # Markdown 文本（口径文档等）
    DOWNLOAD = "download"  # 导出文件（带下载链接）


@dataclass
class ToolResult:
    """标准工具返回结构。

    - ``data``：业务数据（JSON 可序列化），如查询结果的 columns/rows、
      口径文档列表、导出元信息；
    - ``display_type``：前端展示形态（DisplayType 或兼容字符串）；
    - ``error_msg``：失败时的业务可读错误（成功时为 None）；
    - ``duration_ms``：本次执行耗时（毫秒，由 BaseTool.run 统一计时）；
    - ``meta``：结构化附加信息（如 SQL、DSL、扫描行数、下载链接），
      供审计与前端调试消费，不改变 data 的业务语义。
    """

    success: bool
    data: Any = None
    display_type: str = DisplayType.TEXT.value
    error_msg: str | None = None
    duration_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化工具结果（Serialize the tool result for audit & output）。"""
        return {
            "success": self.success,
            "data": self.data,
            "display_type": self.display_type,
            "error_msg": self.error_msg,
            "duration_ms": self.duration_ms,
            "meta": self.meta,
        }


@dataclass
class ToolContext:
    """执行期运行时上下文（由调度内核注入，不属于 LLM 入参 Schema）。

    - ``conn``：只读 DuckDB 连接（复用既有受控执行链路的连接管理）；
    - ``principal``：服务端绑定的数据权限主体（用户不可伪造）；
    - ``executor``：受控 SQL 执行器（默认 exec.guards.execute_sql，
      可注入以便自愈/测试替换）；
    - ``rewriter``：SQL 执行自愈重写器（默认 agent.pipeline.rewrite_dsl）；
    - ``request_id``：贯穿审计的请求标识；
    - ``prior``：上一个工具的输出（供组合工具消费，如导出报表复用查询结果）。
    """

    conn: Any = None
    principal: str | None = None
    executor: Any = None
    rewriter: Any = None
    request_id: str | None = None
    prior: ToolResult | None = None
    # 会话上下文继承注入：上轮成功执行的结构化 DSL（来自 agent.memory 的上下文合并）。
    # 非 None 时数据工具跳过 NL->DSL 生成，仍强制 apply_policy + 编译 + 执行护栏。
    base_dsl: Any = None


class BaseTool(ABC):
    """受控工具抽象基类。

    子类只需声明 ``name / description / args_schema`` 并实现
    ``execute(validated_args, ctx)``；参数校验、异常包装与耗时统计由
    ``run()`` 统一完成，保证所有工具的错误处理与审计形态一致。
    """

    name: str = ""
    description: str = ""
    args_schema: type[BaseModel] = BaseModel

    # 允许被 @register_tool 装饰器识别的声明式注册
    register: bool = True

    @abstractmethod
    def execute(self, validated_args: BaseModel, ctx: ToolContext) -> ToolResult:
        """执行工具逻辑，返回 ToolResult。"""

    # ------------------------------------------------------------------ #
    # 公共入口：校验 -> 执行 -> 统一异常包装 + 耗时统计
    # ------------------------------------------------------------------ #
    def run(self, args: dict[str, Any], ctx: ToolContext | None = None) -> ToolResult:
        """校验入参并执行工具，返回标准 ToolResult（永不抛异常）。

        - 入参经 ``args_schema.model_validate`` 严格校验（extra="forbid"），
          任何非法/未知字段在此被拦截；
        - 执行中抛出的任意异常都被包装为 success=False 的 ToolResult，
          并记录异常类型名与信息（供上层自愈与审计）。
        """
        started = time.perf_counter()
        try:
            validated = self.validate_args(args)
            ctx = ctx or ToolContext()
            result = self.execute(validated, ctx)
        except Exception as exc:  # 工具级异常一律结构化，不向上裸抛
            result = ToolResult(
                success=False,
                error_msg=f"{type(exc).__name__}: {exc}",
                meta={"error_type": type(exc).__name__},
            )
        finally:
            result.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return result

    def validate_args(self, args: dict[str, Any]) -> BaseModel:
        """严格校验入参：任何未在 Schema 中声明的字段都被拒绝。

        双重防线：既依赖子类 args_schema 的 ``extra="forbid"``（Pydantic 严格
        模式），也在框架层显式拦截未知字段——即使某子类忘记配置 forbid，
        非法/越权参数（如 ``drop_table``）也无法进入工具执行。
        """
        if isinstance(args, dict):
            declared = set(self.args_schema.model_fields)
            unknown = sorted(set(args) - declared)
            if unknown:
                raise ValueError(
                    f"非法参数: {unknown} 未在工具 {self.name!r} 的 args_schema 中声明"
                )
        return self.args_schema.model_validate(args)

    # ------------------------------------------------------------------ #
    # 注册中心适配
    # ------------------------------------------------------------------ #
    def to_definition(self) -> dict[str, Any]:
        """生成 OpenAI/通用 Function Calling 规范的工具声明（JSON Schema）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_schema.model_json_schema(),
            },
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        """调试用 repr（Debug representation）。"""
        return f"<{type(self).__name__} name={self.name!r}>"


__all__ = ["BaseTool", "DisplayType", "ToolContext", "ToolResult"]
