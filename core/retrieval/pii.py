"""PII 脱敏器：Parquet 导出沙箱前的最后一道隐私防线。

策略（保守双通道，命中即掩码）：
1. 列名启发式：email / phone / mobile / tel / id_card / idcard / ssn /
   passport 等（大小写不敏感、含分隔符变体）；
2. 值形态正则：邮箱、11 位手机号（1[3-9] 段）、18 位身份证号。

脱敏采用不可还原的确定性哈希掩码（sha256 前 8 位），保留可分组性
（同值同掩码，聚合分析不受影响），杜绝明文进沙箱。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# 列名启发式（词根形态，匹配列名规范化后的任意位置）
_COLUMN_NAME_HINTS: tuple[str, ...] = (
    "email",
    "e_mail",
    "mail",
    "phone",
    "mobile",
    "tel",
    "id_card",
    "idcard",
    "ssn",
    "passport",
)

# 值形态正则：邮箱 / 中国大陆手机号 / 18 位身份证
_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
)

_MASK_SUFFIX = "…"


def _hash_mask(value: str) -> str:
    """确定性掩码：同值同掩码（可分组、不可还原）。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8] + _MASK_SUFFIX


@dataclass
class PiiMaskReport:
    """脱敏报告：列名 -> 掩码命中单元格数（供审计与编排层可视化）。"""

    masked_cells: dict[str, int] = field(default_factory=dict)
    masked_columns: list[str] = field(default_factory=list)

    @property
    def total_masked(self) -> int:
        """被掩码的单元格总数。"""
        return sum(self.masked_cells.values())


def _column_is_pii(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return any(hint in normalized for hint in _COLUMN_NAME_HINTS)


def mask_value(value: Any) -> Any:
    """对单个值做形态检测与掩码（非字符串原样返回）。"""
    if not isinstance(value, str):
        return value
    masked = value
    for pattern in _VALUE_PATTERNS:
        if pattern.search(masked):
            masked = pattern.sub(lambda m: _hash_mask(m.group(0)), masked)
    return masked


def mask_result_set(
    columns: list[str], rows: list[list[Any]]
) -> tuple[list[str], list[list[Any]], PiiMaskReport]:
    """对列/行结果集做 PII 脱敏，返回 (columns, rows, report)。

    - 列名命中：整列所有单元格掩码；
    - 值形态命中：逐单元格掩码；
    - report 记录命中明细（审计用）。
    """
    report = PiiMaskReport()
    pii_cols = {i for i, c in enumerate(columns) if _column_is_pii(c)}
    for i in pii_cols:
        report.masked_columns.append(columns[i])
        report.masked_cells.setdefault(columns[i], 0)

    out_rows: list[list[Any]] = []
    for row in rows:
        out = list(row)
        for i in pii_cols:
            if i < len(out) and out[i] is not None:
                out[i] = _hash_mask(str(out[i]))
                report.masked_cells[columns[i]] += 1
        for i, value in enumerate(out):
            if i in pii_cols or value is None:
                continue
            new_value = mask_value(value)
            if new_value != value:
                out[i] = new_value
                report.masked_cells[columns[i]] = report.masked_cells.get(columns[i], 0) + 1
        out_rows.append(out)
    return columns, out_rows, report


__all__ = ["PiiMaskReport", "mask_result_set", "mask_value"]
