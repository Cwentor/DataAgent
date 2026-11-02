"""export_report_tool：导出报表 / 数据下钻为可下载文件。

职责：基于查询结果生成 CSV（Excel 兼容）/ 真 xlsx 工作簿 / Markdown / JSON 下载
链接或结构化数据转储，具备大结果集截断与敏感字段脱敏提示。

- 入参可只给 ``query``（内部先走受控查询），也可由调度内核把上一个查询工具
  的输出经 ``ctx.prior`` 注入（组合调用：查即时指标 -> 导出报表，避免重复查询）；
- 支持格式：csv / excel（Excel 兼容 CSV，UTF-8 BOM，非真 xlsx）/
  xlsx（真 Excel 工作簿，openpyxl 序列化，类型保真）/ markdown / json；
- 大结果集：超过 ``limit`` 自动截断并提示；
- 脱敏：命中敏感字段（user_id/phone/id_card/email 等）时自动掩码并提示；
- 产物经 ExportStore 落盘，返回 ``/api/export/<id>`` 下载链接（web.server 鉴权提供）。
"""

from __future__ import annotations

import csv as _csv
import io
import json as _json
import math
import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tools.base import BaseTool, DisplayType, ToolContext, ToolResult
from tools.builtins._export_store import default_export_store
from tools.builtins._query_core import run_guarded_query

__all__ = ["ExportReportArgs", "ExportReportTool", "export_report_tool"]

ExportFormat = Literal["csv", "excel", "xlsx", "markdown", "json"]

# 敏感字段（导出时脱敏提示 + 值掩码）
_SENSITIVE_COLUMNS = frozenset(
    {"user_id", "phone", "mobile", "id_card", "email", "address", "user_name", "name"}
)

_EXT_BY_FORMAT = {
    "csv": "csv",
    "excel": "csv",
    "xlsx": "xlsx",
    "markdown": "md",
    "json": "json",
}

# xlsx 格式硬限：openpyxl 单 sheet 最大行数（含表头）
_XLSX_MAX_ROWS = 1048576
# Excel 数字为 IEEE 754 double，超过 2^53 的整数写数字格必丢精度（改写文本保真）
_INT53_LIMIT = 2**53

# 严格 ISO 形态识别（无歧义才转日期格，其余一律文本，不猜时区不猜格式；
# 锚点 + fullmatch 双保险，防正则被改用 match/search 时前缀误判）
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?$")


class ExportReportArgs(BaseModel):
    """导出报表的严格入参。"""

    query: str | None = Field(
        default=None, min_length=1, description="自然语言问题；与前置查询结果二选一"
    )
    format: ExportFormat = Field(
        default="csv",
        description=(
            "导出格式：csv / xlsx（真 Excel 工作簿）/ "
            "excel（Excel 兼容 CSV，非真 xlsx）/ markdown / json"
        ),
    )
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
    """报表导出工具：xlsx / CSV / Markdown / JSON 下载与脱敏（Report export tool）。"""

    name = "export_report"
    description = (
        "导出报表/数据下钻：把查询结果导出为 xlsx（真 Excel 工作簿）/ "
        "CSV（Excel 兼容）/ Markdown / JSON 下载文件，如『把这个月未履约订单明细"
        "导出成表格』『导出各省GMV的Excel』。用户要 Excel 文件或需二次加工"
        "（长数字/前导零/日期保真）时选 xlsx。"
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


def _text_cell(value: str, ws: Any) -> Any:
    """文本单元格：显式 data_type='s'，杜绝 "=" 开头被 openpyxl 推断为公式。

    openpyxl 对以 = 开头的字符串默认推断 data_type='f'，Excel 打开即执行，
    构成公式注入面（OWASP CSV Injection 的 xlsx 变体）；强制 's' 后落盘即文本，
    且无 CSV 前导空格方案的显示污染副作用。
    """
    from openpyxl.cell import WriteOnlyCell

    cell = WriteOnlyCell(ws, value=value)
    cell.data_type = "s"
    return cell


def _parse_iso_datetime(value: str) -> date | datetime | None:
    """严格 ISO 形态解析（形态 + 语义双校验），失败返回 None 由调用方保持文本。

    正则只验形态（如 2025-13-01 形态合法但月份非法），语义校验靠 fromisoformat；
    两道都过才转日期格，任何一道失败一律保持文本原值，绝不抛错中断导出。
    """
    try:
        if _ISO_DATE_RE.fullmatch(value):
            return date.fromisoformat(value)
        if _ISO_DATETIME_RE.fullmatch(value):
            # 统一 T 分隔为空格后解析（date.fromisoformat 3.11+ 才认 T 形态）
            return datetime.fromisoformat(value.replace("T", " "))
    except ValueError:
        # 形态合法但值非法（2025-13-01）：保守降级为文本，不中断导出
        pass
    return None


def _xlsx_cell(value: Any, ws: Any) -> Any:
    """值 -> openpyxl 单元格（类型保真映射，见设计文档 D3/D4）。

    上游 `_json_safe` 已把 date/datetime 字符串化、Decimal 浮点化，且 ctx.prior
    复用路径可能整体 JSON 往返，因此类型保真只能基于值形态保守识别：

    - bool 先于 int 判定（bool 是 int 子类）；
    - int 且 |v| <= 2^53 写数字格（double 精度边界内无损），超界写文本；
    - float 非有限值（inf/nan）写文本（Excel 无此语义，不静默改值）；
    - 严格 ISO 形态的字符串转日期格，其余字符串一律强制文本格；
    - 原生 date/datetime（防御路径）直写，openpyxl 原生支持。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) <= _INT53_LIMIT:
            return value
        return _text_cell(str(value), ws)
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return _text_cell(str(value), ws)
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, str):
        parsed = _parse_iso_datetime(value)
        if parsed is not None:
            return parsed
        return _text_cell(value, ws)
    # Decimal 等其余类型：str 化文本（_json_safe 已转 float，此处为防御路径）
    return _text_cell(str(value), ws)


def _serialize_xlsx(columns: list[str], rows: list[list[Any]]) -> tuple[bytes, str]:
    """真 xlsx 序列化（openpyxl write_only 流式写，内存恒定不随行数膨胀）。

    - 类型保真与公式注入防护见 ``_xlsx_cell`` / ``_text_cell``；
    - 格式硬限：单 sheet 最大行数（含表头）超限显式报错并建议 CSV，严禁截断；
    - openpyxl 未安装时抛 ValueError 携带安装指引（execute 层统一归一为诚实报错）。
    """
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise ValueError("xlsx 导出依赖 openpyxl 未安装，请先执行 pip install openpyxl") from exc

    if len(rows) + 1 > _XLSX_MAX_ROWS:
        raise ValueError(
            f"xlsx 单表行数上限 {_XLSX_MAX_ROWS}（含表头），当前数据 {len(rows)} 行已超限；"
            "请改用 csv 格式导出或收窄查询范围"
        )

    buf = io.BytesIO()
    wb = Workbook(write_only=True)
    ws = wb.create_sheet()
    # 表头恒为文本格（列名不做日期识别）
    ws.append([_text_cell(str(c), ws) for c in columns])
    for row in rows:
        ws.append([_xlsx_cell(v, ws) for v in row])
    wb.save(buf)
    return buf.getvalue(), _EXT_BY_FORMAT["xlsx"]


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

    if fmt == "xlsx":
        return _serialize_xlsx(columns, rows)

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
