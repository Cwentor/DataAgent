"""物理类型 → 语义目录 dtype 的归一（二十四期 spec D2/D8）。

为什么单独成模块：物理类型是**引擎知识**，原先散在 `semantic/catalog_loader.py`
的 `_DTYPE_MAP`（只覆盖 DuckDB + MySQL 两族，PG/CH 类型缺失即抛错）。二十
四期把物理元数据读取下沉到 `EngineAdapter.physical_columns()` 后，类型归一
成为四个 adapter 的共同依赖，故收敛到本模块：

- 四后端各自维护"元数据源实际报告的形态"清单（不是 DDL 字面形态）：
  DuckDB `information_schema.columns.data_type` 带精度（`DECIMAL(16,2)`）；
  MySQL 报 `int`/`decimal`/`datetime`（不带精度）；PostgreSQL 报
  `integer`/`numeric`/`timestamp without time zone`；ClickHouse
  `system.columns.type` 报 `Nullable(Decimal(16, 2))` 等带包装的形态。
- 归一后只保留 5 个 dtype：`int` / `float` / `str` / `timestamp` / `bool`
  （与 `semantic/catalog.py::FieldMeta.dtype` 的取值域一致，字面量安全转义
  按 dtype 分支）。

fail-closed（spec D8）：未知类型抛 `ValueError` 并指名原始形态，**不静默降级
为 `str`**——降级会让字面量转义走错分支（安全面问题），且把"schema 演进越界"
伪装成正常。
"""

from __future__ import annotations

import re

# 类型参数括号（`DECIMAL(16, 2)` / `VARCHAR(64)` / `DATETIME64(3)`）；
# 只删括号组本身，保留 `WITH TIME ZONE` 这类后缀词
_TYPE_ARGS_RE = re.compile(r"\s*\([^()]*\)")

# 包裹类型：`Nullable(X)` / `LowCardinality(X)`（ClickHouse 特有，可嵌套）
_WRAPPER_RE = re.compile(r"^(NULLABLE|LOWCARDINALITY)\s*\((.*)\)$", re.IGNORECASE | re.DOTALL)

# 精确形态表：键为**剥壳后**的基名（大写、无参数、空白折叠）
_EXACT: dict[str, str] = {
    # —— 整数族 ——
    "INTEGER": "int",
    "INT": "int",
    "BIGINT": "int",
    "SMALLINT": "int",
    "TINYINT": "int",
    "MEDIUMINT": "int",
    "HUGEINT": "int",
    "UBIGINT": "int",
    "UINTEGER": "int",
    "USMALLINT": "int",
    "UTINYINT": "int",
    "INT2": "int",
    "INT4": "int",
    "INT8": "int",
    "INT16": "int",
    "INT32": "int",
    "INT64": "int",
    "INT128": "int",
    "INT256": "int",
    "UINT8": "int",
    "UINT16": "int",
    "UINT32": "int",
    "UINT64": "int",
    "UINT128": "int",
    "UINT256": "int",
    "SERIAL": "int",
    "BIGSERIAL": "int",
    "SMALLSERIAL": "int",
    # —— 浮点/定点族 ——
    "DOUBLE": "float",
    "DOUBLE PRECISION": "float",
    "REAL": "float",
    "FLOAT": "float",
    "FLOAT4": "float",
    "FLOAT8": "float",
    "FLOAT32": "float",
    "FLOAT64": "float",
    "DECIMAL": "float",
    "NUMERIC": "float",
    "DEC": "float",
    "MONEY": "float",
    # —— 字符串族 ——
    "VARCHAR": "str",
    "CHARACTER VARYING": "str",
    "CHAR": "str",
    "CHARACTER": "str",
    "BPCHAR": "str",
    "TEXT": "str",
    "TINYTEXT": "str",
    "MEDIUMTEXT": "str",
    "LONGTEXT": "str",
    "CITEXT": "str",
    "STRING": "str",
    "FIXEDSTRING": "str",
    "UUID": "str",
    "JSON": "str",
    "JSONB": "str",
    "ENUM": "str",
    "ENUM8": "str",
    "ENUM16": "str",
    # —— 时间族（目录里统一为 timestamp；date 面由 normalize 层承载） ——
    "DATE": "timestamp",
    "DATE32": "timestamp",
    "DATETIME": "timestamp",
    "DATETIME2": "timestamp",
    "DATETIME64": "timestamp",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP WITHOUT TIME ZONE": "timestamp",
    "TIMESTAMP WITH TIME ZONE": "timestamp",
    "TIMESTAMPTZ": "timestamp",
    # —— 布尔 ——
    "BOOL": "bool",
    "BOOLEAN": "bool",
}

# 数字位宽形态兜底（ClickHouse `Int8..Int256` / `UInt8..UInt256` 大小写已归一，
# 但版本演进可能新增位宽；正则兜底避免为每个位宽登记一行）
_INT_WIDTH_RE = re.compile(r"^U?INT(8|16|32|64|128|256)$")


def _unwrap_once(text: str) -> str | None:
    """剥一层包裹类型，返回内层；非包裹形态返回 None。

    仅当括号整体配对（内层括号深度不提前归零）时才剥，避免把
    `MAP(STRING, STRING)` 这类"看起来像包裹"的类型误剥成 `STRING, STRING`。
    """
    m = _WRAPPER_RE.match(text)
    if m is None:
        return None
    inner = m.group(2).strip()
    depth = 0
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None  # 括号不配对，不剥
    return inner if depth == 0 else None


def strip_type_wrappers(raw: str) -> str:
    """剥掉包裹类型与类型参数，返回大写基名（空白折叠）。

    `Nullable(Decimal(16, 2))` → `DECIMAL`；
    `LowCardinality(Nullable(String))` → `STRING`；
    `timestamp(3) without time zone` → `TIMESTAMP WITHOUT TIME ZONE`。
    """
    text = str(raw).strip()
    while True:
        unwrapped = _unwrap_once(text)
        if unwrapped is None:
            break
        text = unwrapped
    text = _TYPE_ARGS_RE.sub("", text)
    return " ".join(text.upper().split())


def map_dtype(raw: str) -> str:
    """物理类型 → 目录 dtype（`int`/`float`/`str`/`timestamp`/`bool`）。

    未知形态抛 `ValueError`（spec D8 fail-closed，文案含原始形态便于运维登记）。
    """
    base = strip_type_wrappers(raw)
    mapped = _EXACT.get(base)
    if mapped is not None:
        return mapped
    if _INT_WIDTH_RE.match(base):
        return "int"
    raise ValueError(
        f"未支持的数据库类型: {raw!r}（归一为 {base!r}），请在 exec/engine/type_map.py 登记"
    )


__all__ = ["map_dtype", "strip_type_wrappers"]
