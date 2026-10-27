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


# --------------------------------------------------------------------------- #
# 估算计划解析（扫描行预检两段式，design 2026-10-27 §D3）
# --------------------------------------------------------------------------- #

# 真实扫描算子白名单：只有计划里出现过这些算子，`~N rows` 才代表"要读多少行基表
# 数据"。未知算子 fail-closed（调用方走 EXPLAIN ANALYZE 实测），新增算子名只会
# 让查询慢一点，不会让熔断失效。
SCAN_OPERATORS: frozenset[str] = frozenset(
    {
        "SEQ_SCAN",
        "INDEX_SCAN",
        "TABLE_SCAN",
        "PARQUET_SCAN",
        "READ_PARQUET",
        "READ_CSV",
        "CSV_SCAN",
        "DELIM_SCAN",
        "JSON_SCAN",
        "ARROW_SCAN",
    }
)

_SCAN_OPERATOR_RE = re.compile(r"\b(" + "|".join(sorted(SCAN_OPERATORS)) + r")\b")

# 只认带 `~` 前缀的行数行：不带 `~` 的裸 `N rows` 是 EXPLAIN ANALYZE 的实测口径，
# 误读等于把实测当估算，整条判定链错位。
_ESTIMATE_ROW_RE = re.compile(r"~\s*([\d,]+)\s+rows?", re.IGNORECASE)

# 计划图的边框字符。**不能包含空格**——"~3,003 rows" 去掉空格会变成
# "~3,003rows"，行数正则的 `\s+` 就匹配不上，估算会静默归零（踩过一次）。
_BOX_CHARS = "│┌┐└┘├┤┬┴┼─"


def _strip_boxes(line: str) -> str:
    """去掉 box-drawing 边框字符（保留空格，见 _BOX_CHARS 注释）。"""
    for char in _BOX_CHARS:
        line = line.replace(char, "")
    return line.strip()


def parse_estimated_scan_rows(plan_text: str) -> tuple[int, str]:
    """从 ``EXPLAIN`` 计划文本解析扫描估算行数与真实扫描算子。

    **为什么不做逐算子归属**：DuckDB 的计划是二维 box 布局，join 的两个子算子会
    **并排**画在同一批行上（``│ SEQ_SCAN ││ SEQ_SCAN │``）。按"行"扫描再归属到最近
    算子名的做法会在并排处崩掉——实测 35 条 golden 里有 16 条含 join，逐算子归属
    版本把它们全部判成"不可用"，而且错得**方向危险**：某条查询的 order_detail 估算
    ``~3,755 rows`` 被丢掉，只剩 base_province 的 ``~34 rows`` 被采信，估算被低估
    110 倍——正好是"该熔断却放行"的那种错。

    改用**布局无关**的两步判定：

    1. **存在性**：计划里出现过白名单扫描算子（子串匹配，并排布局照样命中）。只有
       ``COLUMN_DATA_SCAN``（元数据快路径，``count(*)`` 类，报的是"输出 1 行"而不是
       "扫了 1 行"）或 ``EMPTY_RESULT`` 的计划在这里就出局；
    2. **取最大**：全文本所有 ``~N row(s)`` 取最大值。max 只会**高估**不会低估
       （各算子估算的最大值 ≥ 任一扫描算子的估算值），因此判定 ``est × SF < 阈值``
       仍然安全，代价是偶尔多走一次实测。

    返回 ``(rows, operator)``；``rows=0`` 且 ``operator=""`` 表示不可用。
    """
    scan = _SCAN_OPERATOR_RE.search(plan_text)
    if scan is None:
        return 0, ""
    best = 0
    for raw in plan_text.splitlines():
        for match in _ESTIMATE_ROW_RE.finditer(_strip_boxes(raw)):
            rows = int(match.group(1).replace(",", ""))
            if rows > best:
                best = rows
    return best, scan.group(1)


__all__ = [
    "SCAN_OPERATORS",
    "parse_estimated_scan_rows",
    "parse_scan_rows",
    "plan_rows_measurable",
]
