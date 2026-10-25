"""执行结果类型规范化（二十一期 spec D13）：各引擎返回类型统一到全链路口径。

- Decimal -> float：质检断言/快照比对/JSON 序列化与 DuckDB float 对齐
  （pymysql 对 DECIMAL 列返回 decimal.Decimal）；
- bytes -> None 并记 warning：治理管道只产出标量/时间值，二进制出现
  即异常路径，拒绝外透但留痕（不静默吞）；
- 其余类型（None/int/float/str/date/datetime/bool）原样返回。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from audit.logging import get_logger

logger = get_logger("exec.normalize")


def normalize_cell(value: Any) -> Any:
    """单个结果单元格规范化（见模块 docstring）。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        logger.warning("结果含二进制值，已置 None 并留痕", extra={"error": "bytes cell"})
        return None
    return value


__all__ = ["normalize_cell"]
