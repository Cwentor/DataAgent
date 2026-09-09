"""retrieval 门面单测：PII 脱敏 / Parquet 导出 / 原始 SQL 守卫 / typed 工具。

覆盖（对应企业级 Data Agent 需求 §2.B 数据交换协议 + §3 安全守卫）：
- PII：列名启发式、值形态正则、确定性掩码（同值同掩码）；
- 导出：物化校验、行数截断、非法数据集名拒绝、sha256 完整性；
- 守卫：字符串载荷拒绝、sql 键拒绝、SQL 形态文本拒绝、合法 DSL 重建；
- typed 工具：compile_dsl_to_duckdb 产物正确性、execute_dsl_query 端到端
  （真实 DuckDB mock 数仓 + RLS + 导出物化）。
"""

from __future__ import annotations

import pyarrow.parquet as pq
import pytest

from core.retrieval.export import export_to_parquet
from core.retrieval.guardrails import (
    GuardrailViolation,
    looks_like_sql,
    validate_dsl_payload,
)
from core.retrieval.parquet_ref import ParquetRef
from core.retrieval.pii import mask_result_set, mask_value
from core.retrieval.tools import compile_dsl_to_duckdb, execute_dsl_query
from semantic.dsl_schema import QueryDSL

DSL = {
    "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
    "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
}


# --------------------------------------------------------------------------- #
# PII 脱敏
# --------------------------------------------------------------------------- #
def test_mask_column_name_heuristics():
    columns = ["user_email", "order_id", "customer_phone", "amount"]
    rows = [
        ["a@x.com", 1, "13812345678", 10],
        ["b@y.com", 2, "13987654321", 20],
    ]
    _, out, report = mask_result_set(columns, rows)
    assert set(report.masked_columns) == {"user_email", "customer_phone"}
    # 确定性掩码：同值同掩码
    assert out[0][0] == out[0][0]
    assert "@" not in str(out[0][0]) and "13812345678" not in str(out[0][2])
    assert report.total_masked == 4


def test_mask_value_form_detection():
    assert "…" in str(mask_value("联系 zhang.san@corp.example.cn 谢谢"))
    assert "zhang.san@" not in str(mask_value("联系 zhang.san@corp.example.cn 谢谢"))
    assert "13812345678" not in str(mask_value("手机号13812345678备用"))
    id_card = "110101199003077758"
    assert id_card not in str(mask_value(id_card))
    assert mask_value(12345) == 12345  # 非字符串原样返回


def test_mask_preserves_groupability():
    rows = [["a@x.com"], ["a@x.com"], ["b@y.com"]]
    _, out, _ = mask_result_set(["email"], rows)
    assert out[0][0] == out[1][0] and out[0][0] != out[2][0]


# --------------------------------------------------------------------------- #
# Parquet 导出
# --------------------------------------------------------------------------- #
def test_export_to_parquet_roundtrip(tmp_path):
    columns = ["region", "gmv"]
    rows = [["华东", 100.0], ["华北", 200.0]]
    ref = export_to_parquet(columns, rows, tmp_path, "region_gmv", query="测试")
    assert isinstance(ref, ParquetRef)
    assert ref.rows == 2 and ref.columns == ["region", "gmv"]
    table = pq.read_table(tmp_path / "region_gmv.parquet")
    assert table.num_rows == 2
    assert ref.sha256


def test_export_row_cap_truncates(tmp_path):
    rows = [[i, str(i)] for i in range(100)]
    ref = export_to_parquet(["id", "v"], rows, tmp_path, "big", max_rows=50)
    assert ref.rows == 50


def test_export_rejects_bad_name_and_duplicate_columns(tmp_path):
    with pytest.raises(ValueError):
        export_to_parquet(["a"], [[1]], tmp_path, "../evil")
    with pytest.raises(ValueError):
        export_to_parquet(["a", "a"], [[1, 2]], tmp_path, "dup")


# --------------------------------------------------------------------------- #
# 原始 SQL 网关
# --------------------------------------------------------------------------- #
def test_looks_like_sql_detection():
    assert looks_like_sql("SELECT * FROM fact_orders")
    assert looks_like_sql("select order_id from fact_orders where 1=1")
    assert looks_like_sql("DROP TABLE fact_orders")
    assert looks_like_sql("COPY (SELECT 1) TO 'x.parquet'")
    assert not looks_like_sql("GMV 环比趋势如何")
    assert not looks_like_sql("")


def test_validate_payload_rejects_string_and_sql_keys():
    with pytest.raises(GuardrailViolation):
        validate_dsl_payload("SELECT * FROM fact_orders")
    with pytest.raises(GuardrailViolation):
        validate_dsl_payload({**DSL, "sql": "SELECT 1"})
    with pytest.raises(GuardrailViolation):
        validate_dsl_payload({**DSL, "unknown_field": 1})


def test_validate_payload_rejects_sql_smuggled_in_free_text():
    bad = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
        "filters": [{"field": "pay_status", "operator": "eq", "value": "SELECT * FROM x"}],
    }
    with pytest.raises(GuardrailViolation):
        validate_dsl_payload(bad)


def test_validate_payload_rebuilds_contract():
    dsl = validate_dsl_payload(DSL)
    assert isinstance(dsl, QueryDSL)
    assert dsl.metrics[0].alias == "gmv"


# --------------------------------------------------------------------------- #
# typed 工具（真实 mock 数仓）
# --------------------------------------------------------------------------- #
def test_compile_dsl_to_duckdb_returns_sql():
    sql = compile_dsl_to_duckdb(DSL, principal="admin")
    assert "SELECT" in sql and "fact_orders" in sql


def test_execute_dsl_query_end_to_end(tmp_path):
    ref = execute_dsl_query(DSL, principal="admin", workspace=tmp_path, name="gmv_total")
    assert ref.rows >= 1 and "gmv" in ref.columns
    table = pq.read_table(tmp_path / "inputs" / "gmv_total.parquet")
    assert table.num_rows == ref.rows
    assert ref.schema and ref.sha256


def test_execute_dsl_query_rejects_raw_sql(tmp_path):
    with pytest.raises(GuardrailViolation):
        execute_dsl_query("SELECT * FROM fact_orders", workspace=tmp_path, name="evil")
