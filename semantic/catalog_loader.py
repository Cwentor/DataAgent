"""语义目录加载器（P0-2）：从 DuckDB 元数据 + 配置覆写重建受控目录。

背景（审计 P0-2）：semantic/catalog.py 的 COLUMNS/JOIN_RULES 原是模块常量硬编码，
项目自己的 _field_metadata/_table_metadata 元数据表完全不被消费——新增表/字段必须
改 Python 代码。本模块把目录改为"数据驱动"：

- 物理面：读取 DuckDB information_schema.columns（表/列/类型），保证目录与库内一致；
- 逻辑面：读取 config/semantic.json 覆写（可选，JSON 零依赖；.yaml 在装有 PyYAML
  时同样支持）：声明哪些物理列暴露为逻辑字段、逻辑字段名、类型覆写、表别名、
  事实表锚点与受控连接声明（JoinRule，不再裸拼 SQL）；
- 无覆写文件时回退到 catalog.py 内置默认目录（但若库可用，仍会与物理元数据校验）。

用法：
- 服务启动：web.service.ensure_db() 建库后调用 refresh_catalog()，目录即与库同步；
- 运维新增表/字段：改库 + 改 config/semantic.json，无需改任何 Python；
- CLI 预览：python -m semantic.catalog_loader [--db path] [--overlay path]
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import duckdb

from config import settings
from semantic import catalog
from semantic.catalog import FieldMeta, JoinRule

# 内置默认目录快照（reset_defaults / 无覆写回退使用）
_DEFAULT_COLUMNS = dict(catalog.COLUMNS)
_DEFAULT_ALIASES = dict(catalog.ALIASES)
_DEFAULT_FACT_TABLE = catalog.FACT_TABLE
_DEFAULT_FACT_TABLES = tuple(catalog.FACT_TABLES)
_DEFAULT_JOIN_RULES = dict(catalog.JOIN_RULES)
_DEFAULT_FACT_JOIN_RULES = dict(catalog.FACT_JOIN_RULES)
_DEFAULT_DIMENSION_MEMBERS = dict(catalog.DIMENSION_MEMBERS)

# 维度成员词汇表：dim 表 str 字段 distinct 值加载上限（高基数异常表防御截断）
_DIMENSION_MEMBER_CAP = 512

# 默认覆写文件（项目根 config/ 下）
DEFAULT_OVERLAY_PATH: Path = settings.PROJECT_ROOT / "config" / "semantic.json"

# DuckDB data_type -> 目录 dtype（字面量安全转义用）
_DTYPE_MAP: dict[str, str] = {
    "INTEGER": "int",
    "BIGINT": "int",
    "SMALLINT": "int",
    "TINYINT": "int",
    "HUGEINT": "int",
    "UBIGINT": "int",
    "UINTEGER": "int",
    "USMALLINT": "int",
    "UTINYINT": "int",
    "DOUBLE": "float",
    "REAL": "float",
    "FLOAT": "float",
    "DECIMAL": "float",
    "VARCHAR": "str",
    "TEXT": "str",
    "UUID": "str",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP WITH TIME ZONE": "timestamp",
    "DATE": "timestamp",
    "DATETIME": "timestamp",
    "BOOLEAN": "bool",
}


@dataclass(frozen=True)
class Catalog:
    """数据驱动构建的完整语义目录。"""

    columns: dict[str, FieldMeta]
    aliases: dict[str, str]
    fact_table: str
    fact_tables: tuple[str, ...]
    join_rules: dict[str, JoinRule]
    fact_join_rules: dict[str, JoinRule]
    dimension_members: dict[str, tuple[str, ...]]


# --------------------------------------------------------------------------- #
# 物理元数据
# --------------------------------------------------------------------------- #
def _map_dtype(duckdb_type: str) -> str:
    mapped = _DTYPE_MAP.get(str(duckdb_type).upper().strip())
    if mapped is None:
        raise ValueError(f"未支持的 DuckDB 类型: {duckdb_type!r}，请在 catalog_loader 中登记")
    return mapped


def _physical_columns(conn: duckdb.DuckDBPyConnection) -> dict[str, dict[str, str]]:
    """table -> {column: dtype}；跳过下划线前缀的元数据表（_field_metadata 等）。"""
    rows = conn.execute(
        "SELECT table_name, column_name, data_type "
        "FROM information_schema.columns ORDER BY table_name, ordinal_position"
    ).fetchall()
    out: dict[str, dict[str, str]] = {}
    for table, column, dtype in rows:
        if str(table).startswith("_"):
            continue
        out.setdefault(str(table), {})[str(column)] = _map_dtype(str(dtype))
    return out


def _load_dimension_members(
    conn: duckdb.DuckDBPyConnection, columns: dict[str, FieldMeta]
) -> dict[str, tuple[str, ...]]:
    """从数仓 dim 表读取 str 维度字段的 distinct 成员值（数据驱动词汇表）。

    仅加载 dim_ 前缀维度表上的 str 字段（province/category/brand 等），
    字段范围随目录声明自动扩展，新增维度字段无需改代码；表名/列名来自
    目录白名单（非用户输入），每字段 distinct 值按 _DIMENSION_MEMBER_CAP
    截断防御高基数异常表。
    """
    members: dict[str, tuple[str, ...]] = {}
    for name, meta in columns.items():
        if not meta.table.startswith("dim_") or meta.dtype != "str":
            continue
        rows = conn.execute(
            f'SELECT DISTINCT "{meta.column}" FROM "{meta.table}" '
            f"WHERE \"{meta.column}\" IS NOT NULL ORDER BY 1 LIMIT {_DIMENSION_MEMBER_CAP}"
        ).fetchall()
        members[name] = tuple(str(r[0]) for r in rows)
    return members


# --------------------------------------------------------------------------- #
# 覆写文件读取
# --------------------------------------------------------------------------- #
def _read_overlay(path: Path | str | None) -> dict[str, Any] | None:
    """读取覆写配置；.yaml/.yml 需 PyYAML，其余按 JSON 解析。文件缺失返回 None。"""
    if path is None:
        path = DEFAULT_OVERLAY_PATH
    p = Path(path)
    if not p.is_file():
        return None
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                f"覆写文件 {p} 是 YAML，但环境未安装 PyYAML；请改用 .json 或 pip install pyyaml"
            ) from exc
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    else:
        data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"覆写文件 {p} 顶层必须是 JSON/YAML 对象")
    return data


# --------------------------------------------------------------------------- #
# 逻辑目录构建
# --------------------------------------------------------------------------- #
def _build_from_overlay(
    overlay: dict[str, Any], physical: dict[str, dict[str, str]] | None
) -> Catalog:
    raw_fields = overlay.get("fields")
    if not isinstance(raw_fields, dict) or not raw_fields:
        raise ValueError("覆写配置缺少 fields（逻辑字段声明）")
    columns: dict[str, FieldMeta] = {}
    for name, spec in raw_fields.items():
        if not isinstance(spec, dict):
            raise ValueError(f"字段 {name!r} 的声明必须是对象 {{table, column, dtype?}}")
        table = str(spec.get("table", ""))
        column = str(spec.get("column", ""))
        if not table or not column:
            raise ValueError(f"字段 {name!r} 必须声明 table 与 column")
        if physical is not None:
            tbl = physical.get(table)
            if tbl is None:
                raise ValueError(f"覆写字段 {name!r} 指向的表 {table!r} 不在数据库中")
            if column not in tbl:
                raise ValueError(f"覆写字段 {name!r} 指向的列 {table}.{column} 不在数据库中")
        dtype = str(spec.get("dtype", "")) or (
            _map_dtype(physical[table][column]) if physical is not None else "str"
        )
        columns[str(name)] = FieldMeta(table, column, dtype)

    aliases = {str(k): str(v) for k, v in dict(overlay.get("aliases", {})).items()}
    fact_table = str(overlay.get("fact_table", _DEFAULT_FACT_TABLE))
    fact_tables = tuple(str(t) for t in overlay.get("fact_tables", _DEFAULT_FACT_TABLES))

    def _rules(raw: Any) -> dict[str, JoinRule]:
        rules: dict[str, JoinRule] = {}
        for table, spec in dict(raw or {}).items():
            if not isinstance(spec, dict):
                raise ValueError(f"连接声明 {table!r} 必须是对象 {{type, on}}")
            jt = str(spec.get("type", "inner"))
            if jt not in ("inner", "left"):
                raise ValueError(f"连接声明 {table!r} 的 type 只能是 inner/left")
            on = tuple(tuple(map(str, pair)) for pair in spec.get("on", []))
            rules[str(table)] = JoinRule(jt, on)
        return rules

    join_rules = _rules(overlay.get("join_rules", {}))
    fact_join_rules = _rules(overlay.get("fact_join_rules", {}))
    return Catalog(
        columns,
        aliases,
        fact_table,
        fact_tables,
        join_rules,
        fact_join_rules,
        dimension_members=dict(_DEFAULT_DIMENSION_MEMBERS),
    )


def _build_defaults(physical: dict[str, dict[str, str]] | None) -> Catalog:
    """内置默认目录（无覆写时）；库可用时逐字段与物理元数据校验。"""
    if physical is not None:
        for name, meta in _DEFAULT_COLUMNS.items():
            tbl = physical.get(meta.table)
            if tbl is None or meta.column not in tbl:
                raise ValueError(
                    f"默认目录字段 {name!r} 指向 {meta.table}.{meta.column}，但库中没有该列；"
                    "请同步建表或通过 config/semantic.json 覆写"
                )
    return Catalog(
        columns=dict(_DEFAULT_COLUMNS),
        aliases=dict(_DEFAULT_ALIASES),
        fact_table=_DEFAULT_FACT_TABLE,
        fact_tables=_DEFAULT_FACT_TABLES,
        join_rules=dict(_DEFAULT_JOIN_RULES),
        fact_join_rules=dict(_DEFAULT_FACT_JOIN_RULES),
        dimension_members=dict(_DEFAULT_DIMENSION_MEMBERS),
    )


def build_catalog(
    db_path: Path | str | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    overlay_path: Path | str | None = None,
) -> Catalog:
    """构建语义目录（纯函数，不改全局）。

    参数：
    - db_path：只读打开的 DuckDB 文件（物理元数据来源）；与 conn 二选一；
    - conn：已打开连接（物理元数据来源，测试用内存库）；
    - overlay_path：覆写配置文件；默认 config/semantic.json，缺失则用内置默认。
    """
    physical: dict[str, dict[str, str]] | None = None
    own_conn: duckdb.DuckDBPyConnection | None = None
    try:
        if conn is not None:
            physical = _physical_columns(conn)
        elif db_path is not None and Path(db_path).is_file():
            own_conn = duckdb.connect(str(db_path), read_only=True)
            physical = _physical_columns(own_conn)
        elif settings.DB_PATH.is_file():
            own_conn = duckdb.connect(str(settings.DB_PATH), read_only=True)
            physical = _physical_columns(own_conn)

        overlay = _read_overlay(overlay_path)
        if overlay is not None:
            cat = _build_from_overlay(overlay, physical)
        else:
            cat = _build_defaults(physical)

        # 维度成员词汇表：库可用时从 dim 表 distinct 值加载（数据驱动），
        # 不可用时保留内置默认回退（离线可运行）。
        member_conn = conn if conn is not None else own_conn
        if member_conn is not None:
            return replace(cat, dimension_members=_load_dimension_members(member_conn, cat.columns))
        return cat
    finally:
        if own_conn is not None:
            own_conn.close()


def refresh_catalog(
    db_path: Path | str | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    overlay_path: Path | str | None = None,
) -> Catalog:
    """重建并安装语义目录到 semantic.catalog 全局（服务启动时调用）。

    对字典原地 clear+update（COLUMNS/ALIASES/JOIN_RULES/FACT_JOIN_RULES），
    对 FACT_TABLE/FACT_TABLES 重新绑定模块属性——compiler/guard 均通过
    `catalog.XXX` 动态读取，因此刷新即时生效，无需重启。
    """
    cat = build_catalog(db_path, conn, overlay_path)
    catalog.COLUMNS.clear()
    catalog.COLUMNS.update(cat.columns)
    catalog.ALIASES.clear()
    catalog.ALIASES.update(cat.aliases)
    catalog.JOIN_RULES.clear()
    catalog.JOIN_RULES.update(cat.join_rules)
    catalog.FACT_JOIN_RULES.clear()
    catalog.FACT_JOIN_RULES.update(cat.fact_join_rules)
    catalog.FACT_TABLE = cat.fact_table
    catalog.FACT_TABLES = cat.fact_tables
    catalog.DIMENSION_MEMBERS.clear()
    catalog.DIMENSION_MEMBERS.update(cat.dimension_members)
    return cat


def reset_defaults() -> None:
    """恢复内置默认目录（测试隔离用）。"""
    catalog.COLUMNS.clear()
    catalog.COLUMNS.update(_DEFAULT_COLUMNS)
    catalog.ALIASES.clear()
    catalog.ALIASES.update(_DEFAULT_ALIASES)
    catalog.JOIN_RULES.clear()
    catalog.JOIN_RULES.update(_DEFAULT_JOIN_RULES)
    catalog.FACT_JOIN_RULES.clear()
    catalog.FACT_JOIN_RULES.update(_DEFAULT_FACT_JOIN_RULES)
    catalog.FACT_TABLE = _DEFAULT_FACT_TABLE
    catalog.FACT_TABLES = _DEFAULT_FACT_TABLES
    catalog.DIMENSION_MEMBERS.clear()
    catalog.DIMENSION_MEMBERS.update(_DEFAULT_DIMENSION_MEMBERS)


def main() -> None:
    args = sys.argv[1:]
    db_path = None
    overlay_path = None
    i = 0
    while i < len(args):
        if args[i] == "--db" and i + 1 < len(args):
            db_path = args[i + 1]
            i += 2
        elif args[i] == "--overlay" and i + 1 < len(args):
            overlay_path = args[i + 1]
            i += 2
        else:
            raise SystemExit("用法: python -m semantic.catalog_loader [--db path] [--overlay path]")
    cat = build_catalog(db_path=db_path, overlay_path=overlay_path)
    print(f"fact_table={cat.fact_table}  fact_tables={list(cat.fact_tables)}")
    print(f"逻辑字段 {len(cat.columns)} 个：")
    for name in sorted(cat.columns):
        m = cat.columns[name]
        print(f"  - {name}: {m.table}.{m.column} ({m.dtype})")
    print(f"表别名: {cat.aliases}")
    print(
        f"维度成员词汇表: "
        f"{ {k: len(v) for k, v in cat.dimension_members.items()} }"
    )
    print(f"维度连接: { {t: (r.join_type, r.on) for t, r in cat.join_rules.items()} }")
    print(f"事实表连接: { {t: (r.join_type, r.on) for t, r in cat.fact_join_rules.items()} }")


if __name__ == "__main__":
    main()
