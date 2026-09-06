"""export_report_tool：导出报表 / 数据下钻为可下载文件。

职责：基于查询结果生成 CSV（Excel 兼容）/ Markdown / JSON 下载链接或结构化
数据转储，具备大结果集截断与敏感字段脱敏提示。

- 入参可只给 ``query``（内部先走受控查询），也可由调度内核把上一个查询工具
  的输出经 ``ctx.prior`` 注入（组合调用：查即时指标 -> 导出报表，避免重复查询）；
- 支持格式：csv / excel（Excel 兼容 CSV，UTF-8 BOM）/ markdown / json；
- 大结果集：超过 ``limit`` 自动截断并提示；
- 脱敏：命中敏感字段（user_id/phone/id_card/email 等）时自动掩码并提示；
- 产物经 ExportStore 落盘，返回 ``/api/export/<id>`` 下载链接（web.server 鉴权提供）。
"""

from __future__ import annotations

import csv as _csv
import io
import json as _json
from typing import Any, Literal

from pydantic import BaseModel, Field

from tools.base import BaseTool, DisplayType, ToolContext, ToolResult
from tools.builtins._export_store import default_export_store
from tools.builtins._query_core import run_guarded_query

__all__ = ["ExportReportArgs", "ExportReportTool", "export_report_tool"]

ExportFormat = Literal["csv", "excel", "markdown", "json"]

# 敏感字段（导出时脱敏提示 + 值掩码）
_SENSITIVE_COLUMNS = frozenset(
    {"user_id", "phone", "mobile", "id_card", "email", "address", "user_name", "name"}
)

_EXT_BY_FORMAT = {"csv": "csv", "excel": "csv", "markdown": "md", "json": "json"}


class ExportReportArgs(BaseModel):
    """导出报表的严格入参。"""

    query: str | None = Field(
        default=None, min_length=1, description="自然语言问题；与前置查询结果二选一"
    )
    format: ExportFormat = Field(default="csv", description="导出格式：csv/excel/markdown/json")
    filename: str | None = Field(
        default=None,
        max_length=120,
        description="下载文件名（不含扩展名）；缺省自动生成",
    )
    limit: int = Field(
        default=1000,
        ge=1,
        le=100000,
        description="单次导出最大行数，超出自动截断并提示",
    )

    model_config = {"extra": "forbid"}


class ExportReportTool(BaseTool):
    """报表导出工具：CSV / Markdown / JSON 下载与脱敏（Report export tool）。"""

    name = "export_report"
    description = (
        "导出报表/数据下钻：把查询结果导出为 CSV（Excel 兼容）/ Markdown / JSON "
        "下载文件，如『把这个月未履约订单明细导出成表格』『导出各省GMV的CSV』。"
        "可独立指定 query 执行查询后导出，也可消费上一个查询工具的结果（组合调用）。"
        "大结果集自动截断，敏感字段自动脱敏并给出提示，返回下载链接。"
    )
    args_schema = ExportReportArgs

    def execute(self, validated_args: ExportReportArgs, ctx: ToolContext) -> ToolResult:
        # 1) 取数据：优先复用前置查询输出，否则独立执行受控查询
        """取数（复用前置查询或独立执行）后导出 CSV / Markdown / JSON 并返回下载链接。"""
        prior = ctx.prior
        if prior is not None and prior.success and isinstance(prior.data, dict):
            columns = list(prior.data.get("columns") or [])
            rows = [list(r) for r in prior.data.get("rows") or []]
        elif validated_args.query:
            result = run_guarded_query(
                validated_args.query,
                principal=ctx.principal,
                conn=ctx.conn,
                executor=ctx.executor,
                rewriter=ctx.rewriter,
            )
            columns, rows = result.columns, result.rows
        else:
            return ToolResult(
                success=False,
                error_msg="导出需要 query 或前置查询结果（二者至少其一）",
                meta={"error_type": "ValueError"},
            )

        # 2) 截断 + 脱敏
        truncated = len(rows) > validated_args.limit
        if truncated:
            rows = rows[: validated_args.limit]
        masked_columns = [c for c in columns if c in _SENSITIVE_COLUMNS]
        rows = [
            [
                _mask_value(col, v) if col in _SENSITIVE_COLUMNS else v
                for col, v in zip(columns, row, strict=False)
            ]
            for row in rows
        ]

        notes: list[str] = []
        if truncated:
            notes.append(f"结果已截断：仅导出前 {validated_args.limit} 行")
        if masked_columns:
            notes.append(
                f"已对敏感字段自动脱敏：{', '.join(sorted(masked_columns))}，"
                "请按数据安全规范使用"
            )

        # 3) 序列化
        fmt = validated_args.format
        try:
            content, ext = _serialize(fmt, columns, rows)
        except ValueError as exc:
            return ToolResult(
                success=False,
                error_msg=f"{type(exc).__name__}: {exc}",
                meta={"error_type": "ValueError"},
            )

        # 4) 落盘 + 下载链接
        base_name = validated_args.filename or f"export_{fmt}"
        export_id = default_export_store().save(
            f"{base_name}.{ext}",
            content,
            meta={
                "format": fmt,
                "row_count": len(rows),
                "truncated": truncated,
                "principal": ctx.principal,
            },
        )
        url = default_export_store().url_for(export_id)

        # 预览（JSON 安全，已脱敏）
        preview = rows[:5]
        data = {
            "export_id": export_id,
            "download_url": url,
            "format": fmt,
            "filename": f"{base_name}.{ext}",
            "row_count": len(rows),
            "truncated": truncated,
            "desensitized": bool(masked_columns),
            "notes": notes,
            "columns": columns,
            "preview": preview,
        }
        return ToolResult(
            success=True,
            data=data,
            display_type=DisplayType.DOWNLOAD.value,
            meta={
                "download_url": url,
                "truncated": truncated,
                "desensitized": bool(masked_columns),
            },
        )


def _mask_value(column: str, value: Any) -> Any:
    """确定性掩码：敏感字段值统一替换为 ***（不泄露任何原始信息）。"""
    if value is None or value == "":
        return value
    return "***"


def _serialize(fmt: str, columns: list[str], rows: list[list[Any]]) -> tuple[bytes, str]:
    """按格式序列化 (columns, rows) 为字节内容 + 扩展名。"""
    if fmt in ("csv", "excel"):
        buf = io.StringIO()
        writer = _csv.writer(buf)
        # 写入表头（需要检查列名是否也是公式）
        safe_columns = [_escape_formula_cell(str(c)) for c in columns]
        writer.writerow(safe_columns)
        # 写入数据行
        for row in rows:
            safe_row = [_escape_formula_cell(v) if isinstance(v, str) else v for v in row]
            writer.writerow(safe_row)
        text = buf.getvalue()
        # Excel 兼容：UTF-8 BOM，中文列头不乱码
        return ("\ufeff" + text).encode("utf-8"), _EXT_BY_FORMAT[fmt]

    if fmt == "markdown":
        lines = ["| " + " | ".join(str(c) for c in columns) + " |"]
        lines.append("|" + "|".join(["---"] * len(columns)) + "|")
        for row in rows:
            lines.append("| " + " | ".join(str(v) for v in row) + " |")
        return ("\n".join(lines) + "\n").encode("utf-8"), _EXT_BY_FORMAT[fmt]

    if fmt == "json":
        payload = [dict(zip(columns, row, strict=False)) for row in rows]
        return (
            _json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
            _EXT_BY_FORMAT[fmt],
        )

    raise ValueError(f"不支持的导出格式: {fmt!r}")


def _escape_formula_cell(value: str) -> str:
    """防止CSV/Excel公式注入：将以 =, +, -, @ 开头的单元格值前加空格。

    参见：https://owasp.org/www-community/attacks/CSV_Injection
    """
    if not isinstance(value, str):
        return value
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        # 添加前导空格以防止公式解释，同时保持可见性
        return " " + value
    return value


export_report_tool = ExportReportTool()
