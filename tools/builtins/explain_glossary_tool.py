"""explain_glossary_tool：口径与指标解释（业务术语字典检索）。

职责：检索业务术语字典（agent.glossary 口径词典）、指标计算公式与同义词
说明，回答『GMV 是怎么算的』『退款率的口径是什么』等口径类问题。

安全约定：**不触发任何数据库执行**——仅检索内存口径词典（TF-IDF 稀疏向量
相似度，见 agent.rag），并按主体过滤可见口径（守卫前移）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.rag import retrieve
from tools.base import BaseTool, DisplayType, ToolContext, ToolResult

__all__ = ["ExplainGlossaryArgs", "ExplainGlossaryTool", "explain_glossary_tool"]


class ExplainGlossaryArgs(BaseModel):
    """口径解释的严格入参。"""

    query: str = Field(min_length=1, description="口径/定义类问题，如：GMV 是怎么算的")
    top_k: int = Field(default=3, ge=1, le=10, description="最多返回的匹配口径文档数")

    model_config = {"extra": "forbid"}


class ExplainGlossaryTool(BaseTool):
    """口径与指标解释工具：只检索词典，不执行 SQL（Glossary explanation tool）。"""

    name = "explain_glossary"
    description = (
        "口径与指标解释：回答指标的定义、计算公式与同义词说明，如"
        "『GMV 是怎么算的』『ARPU 的口径是什么』『退款率怎么计算』。"
        "只检索业务口径词典，不执行任何数据库查询。"
    )
    args_schema = ExplainGlossaryArgs

    def execute(self, validated_args: ExplainGlossaryArgs, ctx: ToolContext) -> ToolResult:
        """检索口径词典并组织 Markdown 解释（Glossary retrieval，不执行任何 SQL）。"""
        documents = retrieve(
            validated_args.query,
            top_k=validated_args.top_k,
            principal=ctx.principal,
        )
        docs = [d.to_dict() for d in documents]
        if not docs:
            return ToolResult(
                success=False,
                data={"documents": [], "count": 0},
                display_type=DisplayType.MARKDOWN.value,
                error_msg="未检索到相关口径文档，请换一种表述或补充指标口径。",
                meta={"error_type": "NoDocumentFound"},
            )
        return ToolResult(
            success=True,
            data={"documents": docs, "count": len(docs)},
            display_type=DisplayType.MARKDOWN.value,
            meta={"matched_keys": [d["key"] for d in docs]},
        )


explain_glossary_tool = ExplainGlossaryTool()
