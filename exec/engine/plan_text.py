"""DuckDB 执行计划文本解析（二十一期自 exec.guards 迁出，零依赖模块）。

独立成模块的原因：exec.engine.duckdb_adapter 与 exec.guards 都需要这些
解析器，若放 guards 会与 guards 消费 get_adapter 形成循环依赖
（guards -> engine -> duckdb_adapter -> guards）。guards 侧做兼容
re-export，既有 ``from exec.guards import parse_scan_rows`` 路径不变。
"""

from __future__ import annotations

import re

# TABLE_SCAN 算子块：每个物理算子块末尾都含一行形如 "        100,000 rows" 的
# 行数记录。取所有算子行数的最大值作为"扫描行数"预算口径。
_ROWS_LINE_RE = re.compile(r"(\d[\d,]*)\s+rows", re.MULTILINE)


def parse_scan_rows(plan_text: str) -> int:
    """从 EXPLAIN ANALYZE / profiling 文本中解析最大单算子处理行数。

    DuckDB 的算子块以 box-drawing 字符包围，行形如 "│ 100,000 rows │"，
    因此用非锚定匹配提取所有 "N rows"，取最大值作为"扫描行数"预算口径
    （对多表/CTE 查询即各基表扫描的最大值）。解析失败（无行数记录）返回 0。
    """
    counts: list[int] = []
    for match in _ROWS_LINE_RE.finditer(plan_text):
        counts.append(int(match.group(1).replace(",", "")))
    return max(counts) if counts else 0


def plan_rows_measurable(plan_text: str) -> bool:
    """计划文本是否含可解析的算子行数记录（护栏可计量性探针）。

    与 ``parse_scan_rows`` 返回 0 的两种情形区分开：
    - 真·零扫描（如 ``SELECT 1`` 的 Dummy Scan）→ 返回 True（含 "0 rows"）；
    - 计划格式变更导致完全无行数渲染（DuckDB 2.0 元数据快路径）→ 返回 False，
      调用方应记 warning 留痕，不得把"测不到"静默当成"扫了 0 行"。
    """
    return _ROWS_LINE_RE.search(plan_text) is not None


__all__ = ["parse_scan_rows", "plan_rows_measurable"]
