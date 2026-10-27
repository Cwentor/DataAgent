"""`exec/engine/plan_text.parse_estimated_scan_rows` 的锁定用例（扫描行预检两段式 T1）。

为什么单独测这个解析器：它是设计 §D3 判据的生产实现，错的方式只有两种，
而且都**方向危险**：

- 判"可用"但算低了 → 本该熔断的查询被放行（熔断失效）；
- 判"可用"但拿错算子的数字 → 同上。

所以这里的用例全部围绕"低估"与"退化算子"，而不是解析得漂不漂亮。
本文件原先落在 `tests/test_scan_precheck_calibration.py`（校准脚本期），
解析器下沉生产模块后随迁，断言一字未改。
"""

from __future__ import annotations

from exec.engine.plan_text import parse_estimated_scan_rows

# 并排 box 布局：join 的两个子算子在**同一批行**上左右并列。
# 逐行归属算子名的解析器在这里会崩：`│ SEQ_SCAN ││ SEQ_SCAN │` 去掉边框后
# 变成 `SEQ_SCANSEQ_SCAN`，算子名匹配失败 → 整条查询被判"不可用"；
# 更糟的是若只采信右盒的 `~1 row`，估算会从 3,004 掉到 1（低估 3004 倍）。
_PLAN_SIDE_BY_SIDE = """\
┌───────────────────────────┐
│         PROJECTION        │
│    ────────────────────   │
│     split_total_amount    │
│                           │
│          ~88 rows         │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         HASH_JOIN         │
│    ────────────────────   │
│      Join Type: INNER     │
│                           │
│          ~88 rows         │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐┌─────────────┴─────────────┐
│          SEQ_SCAN         ││          SEQ_SCAN         │
│    ────────────────────   ││    ────────────────────   │
│       order_detail        ││       base_province       │
│                           ││                           │
│        ~3,004 rows        ││           ~1 row          │
└───────────────────────────┘└───────────────────────────┘
"""

# count(*) 元数据快路径：唯一算子报的是"输出 1 行"，不是"扫了 1 行"。
_PLAN_COLUMN_DATA_SCAN = """\
┌───────────────────────────┐
│      COLUMN_DATA_SCAN     │
│    ────────────────────   │
│           ~1 row          │
└───────────────────────────┘
"""

# 空结果折叠：整棵树被折成单算子，没有任何行数行。
_PLAN_EMPTY_RESULT = """\
┌───────────────────────────┐
│        EMPTY_RESULT       │
└───────────────────────────┘
"""

# 实测口径（EXPLAIN ANALYZE）没有 `~` 前缀：误读等于把实测当估算。
_PLAN_ACTUAL_ONLY = """\
┌───────────────────────────┐
│          SEQ_SCAN         │
│    ────────────────────   │
│       order_detail        │
│                           │
│       15,019 rows         │
└───────────────────────────┘
"""


def test_side_by_side_layout_takes_max_not_right_box():
    """并排布局必须取全场最大估算，不能被右侧小表带偏。"""
    rows, operator = parse_estimated_scan_rows(_PLAN_SIDE_BY_SIDE)
    assert rows == 3004
    assert operator == "SEQ_SCAN"


def test_column_data_scan_is_unusable():
    """`count(*)` 元数据快路径的 `~1 row` 不可当估算用（单数 row 也不认）。"""
    assert parse_estimated_scan_rows(_PLAN_COLUMN_DATA_SCAN) == (0, "")


def test_empty_result_is_unusable():
    """空结果折叠计划无任何行数行。"""
    assert parse_estimated_scan_rows(_PLAN_EMPTY_RESULT) == (0, "")


def test_actual_only_plan_is_unusable():
    """裸 `N rows`（无 `~`）是实测口径，解析结果必须是 0（调用方按 rows>=1 判不可用）。"""
    rows, _ = parse_estimated_scan_rows(_PLAN_ACTUAL_ONLY)
    assert rows == 0


def test_dummy_scan_plan_is_unusable():
    """`SELECT 1` 之类无真实扫描算子的计划：没有白名单算子就不放行。"""
    plan = "┌─────────────┐\n│ DUMMY_SCAN  │\n│  0 rows     │\n└─────────────┘\n"
    assert parse_estimated_scan_rows(plan) == (0, "")


def test_plural_and_singular_row_both_match():
    """`~N rows` 与 `~1 row` 都要能解析。

    单数形态本身不是问题（`~1 row` 可能真是小表扫描），问题在于**没有扫描算子**
    时的那 1 行（`COLUMN_DATA_SCAN`）——那条由上一个用例守。
    """
    plural = "┌───────┐\n│ SEQ_SCAN │\n│ ~3,004 rows │\n└───────┘\n"
    singular = "┌───────┐\n│ SEQ_SCAN │\n│ ~1 row │\n└───────┘\n"
    assert parse_estimated_scan_rows(plural) == (3004, "SEQ_SCAN")
    assert parse_estimated_scan_rows(singular) == (1, "SEQ_SCAN")
