"""查询结果缓存（生产化）单元测试：LRU/TTL/键隔离/执行集成。"""

from __future__ import annotations

import time

from config import settings
from exec.query_cache import QueryCache, query_cache_key
from tools.builtins._query_core import run_guarded_query


# --------------------------------------------------------------------------- #
# QueryCache 单元
# --------------------------------------------------------------------------- #
def test_query_cache_lru_and_ttl():
    cache = QueryCache(max_entries=2, ttl_seconds=60)
    cache.put("a", ["x"], [[1]], 10)
    cache.put("b", ["x"], [[2]], 20)
    assert len(cache) == 2
    cache.put("c", ["x"], [[3]], 30)  # 超容量：淘汰最久未用的 a
    assert cache.get("a") is None
    assert cache.get("b") == (["x"], [[2]], 20)
    assert cache.get("c") == (["x"], [[3]], 30)

    # LRU 触碰：get(b) 后 b 变最新，下次淘汰 c
    cache.get("b")
    cache.put("d", ["x"], [[4]], 40)
    assert cache.get("b") is not None
    assert cache.get("c") is None


def test_query_cache_ttl_expiry(monkeypatch):
    cache = QueryCache(max_entries=4, ttl_seconds=50)
    clock = {"now": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    cache.put("k", ["x"], [[1]], 5)
    assert cache.get("k") is not None
    clock["now"] += 51  # 超过 TTL
    assert cache.get("k") is None


def test_query_cache_key_distinguishes_principal_and_params():
    base = query_cache_key(
        "restricted", "SELECT 1", statement_timeout_ms=1000, max_scan_rows=10, max_result_rows=5
    )
    assert base == query_cache_key(
        "restricted", " SELECT 1 ", statement_timeout_ms=1000, max_scan_rows=10, max_result_rows=5
    )  # 空白规范化不影响键
    assert base != query_cache_key(
        "admin", "SELECT 1", statement_timeout_ms=1000, max_scan_rows=10, max_result_rows=5
    )  # principal 隔离
    assert base != query_cache_key(
        "restricted", "SELECT 1", statement_timeout_ms=1000, max_scan_rows=99, max_result_rows=5
    )  # 执行参数参与键


# --------------------------------------------------------------------------- #
# run_guarded_query 集成：命中跳过执行、跨主体不串
# --------------------------------------------------------------------------- #
def test_run_guarded_query_uses_result_cache(conn, monkeypatch):
    """开启缓存后同 (principal, SQL) 二次查询不重复执行，cached=True。"""
    monkeypatch.setattr(settings, "QUERY_CACHE_ENABLED", True)
    from exec.query_cache import default_query_cache

    default_query_cache().clear()

    calls = {"n": 0}

    def counting_executor(c, sql, **kwargs):
        calls["n"] += 1
        from exec.guards import execute_sql

        return execute_sql(c, sql, **kwargs)

    from semantic.dsl_schema import AggFunc, AggregateMetric, QueryDSL

    dsl = QueryDSL(
        metrics=[AggregateMetric(field="order_amount", agg=AggFunc.SUM, alias="gmv")],
    )
    first = run_guarded_query(
        "6月GMV是多少", principal="admin", conn=conn, executor=counting_executor, dsl=dsl
    )
    assert first.cached is False and calls["n"] == 1
    second = run_guarded_query(
        "6月GMV是多少", principal="admin", conn=conn, executor=counting_executor, dsl=dsl
    )
    assert second.cached is True and calls["n"] == 1  # 命中缓存，未再执行
    assert second.rows == first.rows

    # 关闭缓存后：恢复真实执行（同一 DSL 第三次调用再次走 executor）
    monkeypatch.setattr(settings, "QUERY_CACHE_ENABLED", False)
    third = run_guarded_query(
        "6月GMV是多少", principal="admin", conn=conn, executor=counting_executor, dsl=dsl
    )
    assert third.cached is False and calls["n"] == 2
    default_query_cache().clear()
