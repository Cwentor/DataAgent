"""跨后端目录一致性（二十四期 T8，Review Focus 2 / spec D1）。

不变量：**四后端同源数据 → 同一份语义目录**（逻辑字段 / 表 / 列 / dtype 三元组
全等）。破坏该不变量意味着"同一张表在 PG 上被当成 float、在 CH 上被当成 str"，
字面量安全转义会走错分支。

用例纯本地可跑（不需真库）：以 DuckDB 真实建仓的物理面为基准，另外三个后端用
"各自驱动实际报告的原生类型形态"模拟——原始形态经 `physical_from_rows` →
`exec.engine.type_map.map_dtype` 归一，正是生产链路本身。

刻意把覆写配置里的 `dtype` 全部剥掉：否则 dtype 来自 semantic.json 字面量，
本用例就退化成"配置自比自"，锁不住类型归一的一致性。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import config.settings as settings_module
from exec.engine.base import physical_from_rows
from exec.engine.duckdb_adapter import DuckDbAdapter
from semantic.catalog_loader import DEFAULT_OVERLAY_PATH, build_catalog

# 目录 dtype -> 各后端元数据源**实际报告**的原生形态（不是 DDL 字面形态）。
# 依据：MySQL information_schema 报 `bigint`/`decimal`/`varchar`/`datetime`；
# PostgreSQL 报 `bigint`/`numeric`/`character varying`/`timestamp without time zone`；
# ClickHouse system.columns 报带包装的 `Nullable(...)`/`LowCardinality(...)`。
# 布尔列在当前数仓中不存在（265 列仅 int/str/timestamp/float），故不登记——
# 未来引入 bool 列时本表会 KeyError，正是"必须显式补齐四后端布尔形态"的信号。
_BACKEND_RAW_TYPES: dict[str, dict[str, str]] = {
    "mysql": {
        "int": "bigint",
        "float": "decimal",
        "str": "varchar",
        "timestamp": "datetime",
    },
    "postgres": {
        "int": "bigint",
        "float": "numeric",
        "str": "character varying",
        "timestamp": "timestamp without time zone",
    },
    "clickhouse": {
        "int": "Nullable(Int64)",
        "float": "Nullable(Decimal(16, 2))",
        "str": "LowCardinality(Nullable(String))",
        "timestamp": "Nullable(DateTime)",
    },
}


class _FakeAdapter:
    """按后端名返回模拟物理面的 adapter 替身（成员读取返回空结果）。"""

    def __init__(self, name: str, physical: dict[str, dict[str, str]]) -> None:
        self.name = name
        self.dialect = name
        self._physical = physical

    def physical_columns(self, conn: Any | None = None) -> dict[str, dict[str, str]]:
        return self._physical

    def connect_readonly(self) -> Any:
        return _NoopConn()

    def run_query(self, conn: Any, sql: str) -> tuple[list[str], list[list[Any]]]:
        return [], []


class _NoopConn:
    def close(self) -> None:
        pass


def _no_dtype_overlay(tmp_path) -> str:
    """默认覆写配置去掉全部 `dtype`：强制 dtype 来自物理面（各后端各自归一）。"""
    data = json.loads(DEFAULT_OVERLAY_PATH.read_text(encoding="utf-8"))
    for spec in data["fields"].values():
        spec.pop("dtype", None)
    p = tmp_path / "semantic_no_dtype.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _emulated_physical(
    duck_physical: dict[str, dict[str, str]], backend: str
) -> dict[str, dict[str, str]]:
    """把 DuckDB 物理面翻译成目标后端"驱动会报告的原生类型"再归一回来。"""
    raw_types = _BACKEND_RAW_TYPES[backend]
    rows = [
        (table, column, raw_types[dtype])
        for table, columns in duck_physical.items()
        for column, dtype in columns.items()
    ]
    return physical_from_rows(rows)


def _triples(cat) -> dict[str, tuple[str, str, str]]:
    return {name: (m.table, m.column, m.dtype) for name, m in cat.columns.items()}


@pytest.fixture()
def duck_physical(conn) -> dict[str, dict[str, str]]:
    return DuckDbAdapter().physical_columns(conn)


@pytest.fixture()
def baseline(conn, tmp_path, monkeypatch) -> dict[str, tuple[str, str, str]]:
    """DuckDB 真实建仓为基准目录（dtype 取自物理面）。"""
    monkeypatch.setattr(settings_module, "DB_BACKEND", "duckdb")
    cat = build_catalog(conn=conn, overlay_path=_no_dtype_overlay(tmp_path))
    return _triples(cat)


@pytest.mark.parametrize("backend", sorted(_BACKEND_RAW_TYPES))
def test_catalog_triples_identical_across_backends(backend, duck_physical, baseline, tmp_path):
    """四后端同源数据 → COLUMNS 三元组全等（表/列/dtype 无漂移）。"""
    fake = _FakeAdapter(backend, _emulated_physical(duck_physical, backend))
    cat = build_catalog(adapter=fake, overlay_path=_no_dtype_overlay(tmp_path))
    assert _triples(cat) == baseline


@pytest.mark.parametrize("backend", sorted(_BACKEND_RAW_TYPES))
def test_dtype_comes_from_physical_not_overlay(backend, duck_physical, tmp_path):
    """回归锚：剥掉覆写 dtype 后，目录 dtype 必须等于物理面 dtype（逐字段）。"""
    fake = _FakeAdapter(backend, _emulated_physical(duck_physical, backend))
    cat = build_catalog(adapter=fake, overlay_path=_no_dtype_overlay(tmp_path))
    physical = _emulated_physical(duck_physical, backend)
    for name, meta in cat.columns.items():
        assert meta.dtype == physical[meta.table][meta.column], f"字段 {name} 的 dtype 漂移"


def test_baseline_covers_all_four_dtypes(duck_physical, baseline):
    """基准目录必须真实覆盖 int/float/str/timestamp 四类（否则用例形同虚设）。"""
    dtypes = {dtype for _, _, dtype in baseline.values()}
    assert {"int", "float", "str", "timestamp"} <= dtypes
    assert len(baseline) >= 60  # semantic.json 声明的逻辑字段全部落进目录
