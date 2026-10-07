"""ENUMERATION 预路由确定性行清单渲染测试（P1 截断分流）。"""

from core.orchestrator import nodes as N
from core.orchestrator.state import AgentState


def _enum_state(query: str, rows: list) -> AgentState:
    """构造 ENUMERATION 意图的 state，datasets 指向含维度列的 parquet。"""
    return AgentState(
        session_id="s1",
        turn_id="t1",
        user_query=query,
        answered_by="heuristic",
        datasets={
            "dim": {
                "path": "_rows.parquet",
                "columns": ["province"],
                "rows": len(rows),
                "audit": {},
            }
        },
    )


def _write_parquet(tmp_path, rows: list, columns: list[str]):
    """把行数据写成 parquet 到 tmp_path/inputs/_rows.parquet。"""
    import duckdb
    import pandas

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    parquet = inputs_dir / "_rows.parquet"
    duckdb.from_df(pandas.DataFrame(rows, columns=columns)).to_parquet(str(parquet))
    return parquet


def test_enumeration_renders_full_list_not_truncated(tmp_path, monkeypatch):
    """枚举题应返回全量清单，不被 preview[:20] 截断。"""
    rows = [{"province": f"省份{i:02d}"} for i in range(34)]
    _write_parquet(tmp_path, rows, ["province"])

    state = _enum_state("列举全部省份", rows)
    monkeypatch.setattr(N, "workspace_path", lambda s: tmp_path)
    report = N._render_enumeration_rows(state)
    # 全量 34 行，不应被截断到 20
    assert "省份33" in report
    assert report.count("省份") >= 34


def test_enumeration_multi_dim_full_list(tmp_path, monkeypatch):
    """RF2 多维枚举返回全量多维清单（字段序 = 词表提取序）。"""
    rows = [{"province": f"省份{i}", "category": f"类目{i}"} for i in range(34)]
    _write_parquet(tmp_path, rows, ["province", "category"])

    state = _enum_state("省份和品类", rows)
    state.datasets["dim"]["columns"] = ["province", "category"]
    monkeypatch.setattr(N, "workspace_path", lambda s: tmp_path)
    report = N._render_enumeration_rows(state)
    assert "省份33" in report and "类目33" in report


def test_enumeration_wide_row_clamped(tmp_path, monkeypatch):
    """RF3 极端宽行宽度钳制（40），行数不截断。"""
    rows = [{"province": "x" * 200}]
    _write_parquet(tmp_path, rows, ["province"])

    state = _enum_state("列举省份", rows)
    monkeypatch.setattr(N, "workspace_path", lambda s: tmp_path)
    report = N._render_enumeration_rows(state)
    assert "x" * 200 not in report
