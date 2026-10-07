"""gmall.sql 种子数据提取器。

从尚硅谷 Gmall 教学库 dump（mock/gmall/upstream/gmall.sql）中提取 mock 生成器依赖的
种子表（省市区 / 类目 / 品牌 / SPU / SKU / 优惠券 / 活动）的 INSERT 行，
落盘为 mock/gmall/seed_data.json，供数仓构建时确定性加载。

用法（在项目根目录）：
    python -m mock.gmall.seed_extract

说明：
- 只在更新种子数据时手工运行一次；运行期数仓构建不依赖 upstream/ 原始 dump；
- INSERT 行按原始 SQL 字面值解析（NULL / 数字 / 字符串 / 日期字符串），
  不做业务加工，保证与原教学库逐行一致。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 需要提取的种子表（与 docs/plans/2026-10-06-gmall-mock-reverse-spec.md §3.1 一致）
SEED_TABLES = [
    "base_province",
    "base_region",
    "base_category1",
    "base_category2",
    "base_category3",
    "base_trademark",
    "spu_info",
    "sku_info",
    "coupon_info",
    "coupon_range",
    "activity_info",
    "activity_rule",
    "activity_sku",
]

# 提取产物相对 mock/gmall/ 的落盘文件名
SEED_DATA_FILE = "seed_data.json"

_INSERT_RE = re.compile(r"^INSERT INTO `(\w+)` VALUES \((.*)\);$")

_TOKEN_RE = re.compile(
    r"""
    (?P<null>NULL)
  | (?P<num>-?\d+(?:\.\d+)?)
  | (?P<str>'(?:[^']|'')*')
    """,
    re.VERBOSE,
)


def _split_values(raw: str) -> list[str]:
    """按顶层逗号切分 VALUES(...) 内部（字符串内的逗号与引号不切分）。"""
    parts: list[str] = []
    buf: list[str] = []
    in_str = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if in_str:
            buf.append(ch)
            if ch == "'":
                # '' 转义：吞掉第二个引号
                if i + 1 < len(raw) and raw[i + 1] == "'":
                    buf.append("'")
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _parse_token(token: str) -> str | float | int | None:
    """把单个 SQL 字面值解析为 Python 值（NULL→None，数字→int/float，其余→去引号字符串）。"""
    token = token.strip()
    if not token:
        return None
    m = _TOKEN_RE.fullmatch(token)
    if not m:
        return token
    if m.group("null"):
        return None
    if m.group("num"):
        text = m.group("num")
        return float(text) if "." in text else int(text)
    if m.group("str"):
        return m.group("str")[1:-1].replace("''", "'")
    return token


def parse_gmall_sql(sql_path: Path) -> dict[str, list[list]]:
    """解析 gmall.sql，返回 {表名: [行值列表]}（仅含 SEED_TABLES 中出现过的表）。"""
    tables: dict[str, list[list]] = {name: [] for name in SEED_TABLES}
    with open(sql_path, encoding="utf-8") as f:
        for line in f:
            m = _INSERT_RE.match(line.strip())
            if not m:
                continue
            table = m.group(1)
            if table not in tables:
                continue
            row = [_parse_token(tok) for tok in _split_values(m.group(2))]
            tables[table].append(row)
    return tables


def main() -> None:
    sql_path = Path(__file__).resolve().parent / "upstream" / "gmall.sql"
    if not sql_path.exists():
        print(f"[seed_extract] 未找到 {sql_path}，无法提取种子数据")
        sys.exit(1)
    tables = parse_gmall_sql(sql_path)
    out_path = Path(__file__).resolve().parent / SEED_DATA_FILE
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(tables, f, ensure_ascii=False, separators=(",", ":"))
    total = sum(len(rows) for rows in tables.values())
    print(f"[seed_extract] 提取完成：{len(tables)} 张种子表 / {total} 行 -> {out_path}")
    for name in SEED_TABLES:
        print(f"  - {name}: {len(tables[name])} 行")


if __name__ == "__main__":
    main()
