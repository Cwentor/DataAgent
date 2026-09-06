"""审计埋点与结构化日志（P0）单元测试。"""

from __future__ import annotations

import json
import logging

import duckdb

from audit.logging import JsonFormatter, get_request_id, set_request_context, setup_logging
from audit.record import AuditRecord
from audit.store import AuditStore
from web.service import run_query


# --------------------------------------------------------------------------- #
# AuditRecord
# --------------------------------------------------------------------------- #
def test_audit_record_roundtrip():
    record = AuditRecord(
        request_id="req-1",
        session_id="sess-1",
        user="alice",
        prompt="各品类GMV",
        retrieval_context={"fields": ["category", "order_amount"], "tables": ["fact_orders"]},
        dsl={"metrics": []},
        sql="SELECT 1",
        latency_ms=12.34,
        row_count=3,
        error=None,
    )
    data = record.to_dict()
    assert data["request_id"] == "req-1"
    assert data["session_id"] == "sess-1"
    assert data["user"] == "alice"
    assert data["prompt"] == "各品类GMV"
    assert data["retrieval_context"]["fields"] == ["category", "order_amount"]
    assert data["sql"] == "SELECT 1"
    assert data["latency_ms"] == 12.34
    assert data["row_count"] == 3
    assert data["error"] is None
    assert "created_at" in data


# --------------------------------------------------------------------------- #
# AuditStore：JSONL 对象存储 + DuckDB 审计表
# --------------------------------------------------------------------------- #
def test_audit_store_jsonl(tmp_path):
    store = AuditStore(jsonl_path=tmp_path / "audit.jsonl")
    store.write(
        AuditRecord(request_id="r1", prompt="订单数", sql="SELECT 1", row_count=1, error=None)
    )
    store.write(
        AuditRecord(request_id="r2", prompt="退款率", sql=None, row_count=None, error="boom")
    )
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["request_id"] == "r1"
    assert json.loads(lines[1])["error"] == "boom"


def test_audit_store_db_multi_writer_threads(tmp_path):
    """P0-4：多写者写同一审计库不冲突（跨进程锁 + 短连接）。"""
    import threading

    db_path = tmp_path / "audit-multi.duckdb"
    errors: list[Exception] = []

    def writer(tag: str) -> None:
        store = AuditStore(db_path=db_path)
        try:
            for i in range(5):
                store.write(
                    AuditRecord(
                        request_id=f"{tag}-{i}",
                        prompt=f"{tag} #{i}",
                        sql="SELECT 1",
                        error=None,
                    )
                )
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            store.close()

    threads = [threading.Thread(target=writer, args=(f"w{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        count = conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]
        distinct = conn.execute("SELECT count(DISTINCT request_id) FROM audit_log").fetchone()[0]
    finally:
        conn.close()
    assert count == 20
    assert distinct == 20
    assert conn is not None


def test_audit_store_duckdb(tmp_path):
    store = AuditStore(db_path=tmp_path / "audit.duckdb")
    store.write(
        AuditRecord(
            request_id="r1",
            session_id="s1",
            user="u1",
            prompt="GMV",
            retrieval_context={"fields": ["order_amount"]},
            dsl={"metrics": []},
            sql="SELECT SUM(order_amount) AS gmv",
            latency_ms=5.0,
            row_count=1,
            error=None,
        )
    )
    store.close()

    conn = duckdb.connect(str(tmp_path / "audit.duckdb"), read_only=True)
    try:
        rows = conn.execute(
            "SELECT request_id, session_id, user_name, prompt, sql, latency_ms, row_count, error "
            "FROM audit_log"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("r1", "s1", "u1", "GMV", "SELECT SUM(order_amount) AS gmv", 5.0, 1, None)]


# --------------------------------------------------------------------------- #
# run_query 端到端审计
# --------------------------------------------------------------------------- #
def test_run_query_writes_audit(conn, tmp_path):
    store = AuditStore(jsonl_path=tmp_path / "audit.jsonl")
    result = run_query(
        "2024年6月成功订单的GMV是多少？",
        conn=conn,
        session_id="sess-42",
        user="alice",
        audit_store=store,
    )
    assert "error" not in result
    assert result["request_id"]
    assert result["columns"] == ["gmv"]

    line = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert line["session_id"] == "sess-42"
    assert line["user"] == "alice"
    assert line["prompt"] == "2024年6月成功订单的GMV是多少？"
    assert "SELECT" in line["sql"]
    assert line["dsl"] is not None
    assert line["retrieval_context"]["fields"] == ["order_amount", "pay_status"]
    assert line["row_count"] == 1
    assert line["latency_ms"] >= 0
    assert line["error"] is None


def test_run_query_audits_error(conn, tmp_path):
    store = AuditStore(jsonl_path=tmp_path / "audit.jsonl")
    result = run_query("今天天气怎么样", conn=conn, audit_store=store)
    assert "error" in result

    line = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert line["error"] is not None
    assert line["sql"] is None
    assert line["row_count"] is None


# --------------------------------------------------------------------------- #
# 结构化日志：request_id 贯穿
# --------------------------------------------------------------------------- #
def test_json_formatter_includes_request_context():
    set_request_context(request_id="rid-9", session_id="s9", user="u9")
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="web.service",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="query_end_ok",
        args=(),
        exc_info=None,
    )
    record.event = "query_end"
    record.status = "ok"
    record.latency_ms = 3.21
    record.row_count = 5
    payload = json.loads(formatter.format(record))
    assert payload["request_id"] == "rid-9"
    assert payload["session_id"] == "s9"
    assert payload["user"] == "u9"
    assert payload["logger"] == "web.service"
    assert payload["message"] == "query_end_ok"
    assert payload["event"] == "query_end"
    assert payload["status"] == "ok"
    assert payload["latency_ms"] == 3.21
    assert payload["row_count"] == 5


def test_setup_logging_is_idempotent():
    setup_logging()
    before = len(logging.getLogger().handlers)
    setup_logging()
    after = len(logging.getLogger().handlers)
    assert after == before
    # 恢复根 logger，避免污染其他测试的输出捕获
    for h in list(logging.getLogger().handlers):
        if isinstance(h.formatter, JsonFormatter):
            logging.getLogger().removeHandler(h)
    assert get_request_id()


# --------------------------------------------------------------------------- #
# 离线漏斗分析：按 detected_intent + routing_reason 聚合分流质量
# --------------------------------------------------------------------------- #
def _seed_routing_records(store: AuditStore) -> None:
    """构造含意图路由字段的审计样本：3 data_query / 1 chitchat / 1 无标注。"""
    rows = [
        ("r1", "data_query", "fast_path_keywords", 12.0, None),
        ("r2", "data_query", "llm_classifier", 40.0, "PipelineError: 无法识别指标"),
        ("r3", "data_query", "llm_classifier", 8.0, None),
        ("r4", "chitchat", "fast_path_greeting", 1.0, None),
        ("r5", None, None, None, None),
    ]
    for rid, intent, reason, latency, error in rows:
        store.write(
            AuditRecord(
                request_id=rid,
                prompt="q",
                sql="SELECT 1",
                detected_intent=intent,
                routing_latency_ms=latency,
                routing_reason=reason,
                error=error,
            )
        )


def test_routing_funnel_report_aggregates(tmp_path):
    """意图漏斗 / 原因分解 / 耗时分位 / 失败率代理 全量聚合正确。"""
    from audit.analysis import routing_funnel_report

    db = tmp_path / "audit.duckdb"
    store = AuditStore(db_path=db)
    _seed_routing_records(store)
    store.close()

    report = routing_funnel_report(db_path=db)
    assert report["total"] == 5
    intents = {item["intent"]: item for item in report["intents"]}
    assert intents["data_query"]["count"] == 3
    assert intents["data_query"]["share"] == 0.6
    assert intents["chitchat"]["count"] == 1
    assert intents["(unrouted)"]["count"] == 1

    reasons = report["routing_reasons"]["data_query"]
    by_reason = {item["reason"]: item for item in reasons}
    assert by_reason["fast_path_keywords"]["count"] == 1
    assert by_reason["llm_classifier"]["count"] == 2
    assert by_reason["llm_classifier"]["share"] == round(2 / 3, 4)

    lat = report["routing_latency_ms"]
    assert lat["samples"] == 4
    assert lat["p50"] == 10.0  # (8, 12) 中位
    assert lat["max"] == 40.0

    err = {item["intent"]: item for item in report["error_rate_by_intent"]}
    assert err["data_query"]["errors"] == 1
    assert err["data_query"]["error_rate"] == round(1 / 3, 4)
    assert err["chitchat"]["error_rate"] == 0.0


def test_routing_funnel_report_empty_and_missing(tmp_path):
    """audit_log 表缺失 / 库文件缺失 / 空表 -> total=0 空报表而非报错。"""
    from audit.analysis import routing_funnel_report

    empty = routing_funnel_report(db_path=tmp_path / "missing.duckdb")
    assert empty["total"] == 0 and empty["intents"] == []

    bare = tmp_path / "bare.duckdb"
    bare_conn = duckdb.connect(str(bare))
    bare_conn.execute("CREATE TABLE other (a int)")
    report = routing_funnel_report(conn=bare_conn)
    assert report["total"] == 0
    bare_conn.close()

    db = tmp_path / "audit.duckdb"
    AuditStore(db_path=db).close()  # 建表但零记录
    assert routing_funnel_report(db_path=db)["total"] == 0


def test_routing_funnel_report_json_cli(tmp_path, capsys):
    """CLI --json 输出可解析的报表 JSON（生产侧管道消费入口）。"""
    from audit.analysis import main as analysis_main

    db = tmp_path / "audit.duckdb"
    store = AuditStore(db_path=db)
    _seed_routing_records(store)
    store.close()

    import sys

    argv_backup = sys.argv
    try:
        sys.argv = ["audit.analysis", "--db", str(db), "--json"]
        analysis_main()
    finally:
        sys.argv = argv_backup
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 5
    assert payload["intents"][0]["intent"] == "data_query"
