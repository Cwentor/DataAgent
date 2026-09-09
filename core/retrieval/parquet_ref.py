"""ParquetRef：沙箱数据交换协议中的一等数据集引用（typed Tool 返回值）。

设计目标：编排器与沙箱之间不传裸结果集（防止大数据集涌入 prompt token），
只传 **可验证的文件引用**：

- ``path``：Parquet 文件在工作区内的相对路径（inputs/<name>.parquet）；
- ``rows`` / ``columns``：行数与列清单（导出时确定，供编排层决策）；
- ``schema``：列名 -> DuckDB 类型名的映射（LLM 生成分析代码时的依据）；
- ``sha256``：文件内容哈希（防篡改 / 可审计）；
- ``query`` / ``dsl``：溯源信息（该数据集由哪次查询产出）。

ParquetRef 本身是不可变 Pydantic 契约（extra=forbid），文件由 retrieval 层
落盘；沙箱侧只读消费。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ParquetRef(BaseModel):
    """指向工作区内一个 Parquet 数据集的不可变引用。"""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    name: str = Field(..., description="数据集逻辑名（编排层与 LLM 引用）")
    path: str = Field(..., description="相对沙箱工作区的 Parquet 文件路径")
    rows: int = Field(..., ge=0, description="行数（导出时确定）")
    columns: list[str] = Field(..., description="列清单（顺序与文件一致）")
    schema_: dict[str, str] = Field(
        default_factory=dict, alias="schema", description="列名 -> 类型名映射"
    )
    sha256: str = Field(default="", description="文件内容 sha256（可审计）")
    query: str = Field(default="", description="产出该数据集的自然语言问题（溯源）")
    dsl: dict[str, Any] = Field(default_factory=dict, description="产出该数据集的 DSL（溯源）")
    masked_cells: int = Field(default=0, ge=0, description="导出时被 PII 脱敏的单元格数（审计）")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
