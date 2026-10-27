"""双后端一致性测试共享工具（二十二期从 test_dual_backend_parity.py 提取）。

比对口径（与二十一期逐字一致，勿改）：
- 数值按 rel=1e-5 + abs=1e-6 双口径容差（DuckDB float vs 各后端 DECIMAL 经
  normalize_cell 统一 float 后的精度差，二十一期 Review Focus #4）；
- 行序无关（排序后逐行比对）：排序键 repr(row)——各引擎对同值行的
  tie-break 顺序不构成契约，行集合一致才是语义等价；
- datetime 统一取日期面（date_trunc 返回 TIMESTAMP(00:00:00) 与各后端
  截断语义等价）。

导入方式：tests/ 为包（含 __init__.py），用 ``from tests.parity_utils import ...``
包内绝对导入。
"""

from __future__ import annotations


def norm_cell(v):
    """跨引擎类型归一（比对口径，非行为差异）。"""
    from datetime import datetime as datetime_cls
    from decimal import Decimal

    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime_cls):
        # datetime 是 date 的子类：date 实例不会进本分支
        # （isinstance(date_obj, datetime) 为 False），无需额外排除
        return v.date()
    return v


def assert_rows_equal(duck_rows, other_rows):
    """行集合一致断言（行序无关 + 数值 rel/abs 双口径容差）。"""
    import pytest

    assert len(duck_rows) == len(other_rows), "行数不一致"
    duck_sorted = sorted(([norm_cell(v) for v in r] for r in duck_rows), key=repr)
    other_sorted = sorted(([norm_cell(v) for v in r] for r in other_rows), key=repr)
    for rd, rm in zip(duck_sorted, other_sorted, strict=True):
        assert len(rd) == len(rm)
        for vd, vm in zip(rd, rm, strict=True):
            if vd is None or vm is None:
                assert vd is vm, f"空值不一致: {vd!r} vs {vm!r}"
                continue
            if isinstance(vd, (int, float)) and isinstance(vm, (int, float)):
                # 容差口径（二十一期 spec §9 Decimal 精度差异）：DECIMAL 除法
                # 舍入差为**绝对量级**，abs 兜底小值、rel 覆盖大值（CI 实测校准）
                assert vm == pytest.approx(vd, rel=1e-5, abs=1e-6), f"数值漂移: {vd!r} vs {vm!r}"
            else:
                assert vm == vd, f"值不一致: {vd!r} vs {vm!r}"


def compile_dsl(dsl, dialect: str) -> str:
    """按方言编译（dict 先过契约校验；临时切换 SQL_DIALECT 用完即还原）。"""
    import config.settings as settings
    from compiler.sql_compiler import compile_sql
    from semantic.dsl_schema import QueryDSL

    model = dsl if isinstance(dsl, QueryDSL) else QueryDSL.model_validate(dsl)
    old = settings.SQL_DIALECT
    settings.SQL_DIALECT = dialect
    try:
        return compile_sql(model)
    finally:
        settings.SQL_DIALECT = old


def abs_window_dsl(granularity: str, start: str, end: str, fill_gaps: bool = False) -> dict:
    """绝对窗口 GMV 按时间分组 DSL（形态取自 golden_dataset；limit 给足以
    覆盖 5 年 day 粒度 1826 行——契约默认 limit=100 会截断补零序列）。"""
    dsl = {
        "metrics": [
            {"kind": "aggregate", "field": "split_total_amount", "agg": "sum", "alias": "gmv"}
        ],
        "dimensions": [{"field": "order_time", "alias": None}],
        "time_filter": {
            "granularity": granularity,
            "range_type": "absolute",
            "relative": None,
            "absolute": {"start": start, "end": end},
            "comparison": "none",
            "reference_date": None,
            "time_field": "order_time",
        },
        "filters": [],
        "limit": 2000,
    }
    if fill_gaps:
        dsl["fill_gaps"] = True
    return dsl


# --------------------------------------------------------------------------- #
# 超时后连接废弃（2026-10-27 审计 §9.3「P0 修复只有单测」）
#
# 原缺陷只在真实后端上确定性复现：外部后端 ``adapter.cancel`` 全是空操作，看门狗
# 抛超时后 worker 线程仍阻塞在同一条连接上，而连接池对此一无所知。单测用 mock
# 连接覆盖了 mark/discard 两段，但「真实驱动在超时后是否真的留下脏状态」只有
# 真后端能证。故 parity 用例各加一条，矩阵 L2 跑 parity 时自动带上。
# --------------------------------------------------------------------------- #

# 各后端「确定性慢查询」候选（3 秒量级）。列表语义：前一条被后端拒绝时换下一条。
# - 不用笛卡尔积做首选：Hive ``hive.mapred.mode=strict`` 会直接拒绝交叉连接，
#   而 execute_sql 前置的 GuardrailAgent 也会把笛卡尔积升级为执行前熔断；
# - ClickHouse 的 ``sleep()`` 单次上限即 3s（超出被截断，不会更慢）；
# - Hive 无 sleep 内建函数，首选 ``reflect`` 调 java Thread.sleep，失败再退回交叉连接。
SLOW_SQLS: dict[str, list[str]] = {
    "mysql": ["SELECT SLEEP(3)"],
    "postgres": ["SELECT pg_sleep(3)"],
    "clickhouse": ["SELECT sleep(3)"],
    "hive": [
        "SELECT reflect('java.lang.Thread', 'sleep', 3000)",
        "SELECT count(*) FROM order_info a, order_info b",
    ],
}


def assert_timeout_discards_connection(adapter, slow_sqls, *, timeout_ms=300, probe_sql="SELECT 1"):
    """真实后端验证 P0 契约：超时后的连接必须被废弃，不得归还复用。

    为什么不走 ``execute_sql``：它前置的 GuardrailAgent 会把交叉连接升级为执行前
    熔断，而交叉连接是 Hive 上不依赖 UDF 授权的兜底减速手段。被测契约本身位于
    ``watchdog``（打标）+ ``connstate``（状态）+ ``pool.release``（分流）三层，
    与 execute_sql 无关，直连 watchdog 更贴近被测对象。

    断言链：
    1. 慢查询超时抛 ``QueryTimeoutError``；
    2. 连接被标记不可复用；
    3. ``pool.release`` 自动转废弃——容量名额回收、补建新连接；
    4. 下次 ``acquire`` 拿到的是**新**连接，且该连接真的能执行查询。
    """
    import pytest

    from exec.engine import connstate
    from exec.engine.watchdog import run_with_timeout
    from exec.errors import QueryTimeoutError
    from exec.pool import ReadOnlyConnectionPool

    pool = ReadOnlyConnectionPool(adapter, max_connections=1)
    conn = None
    try:
        for sql in slow_sqls:
            conn = pool.acquire()
            try:
                run_with_timeout(adapter, conn, sql, timeout_ms)
            except QueryTimeoutError:
                break  # 命中：conn 已被 watchdog 打标
            except Exception:
                # 该候选被后端拒绝（UDF 黑名单 / strict 模式禁笛卡尔积）：
                # 按废弃处理，避免带错误状态的连接回池，换下一条候选
                pool.discard(conn)
                conn = None
                continue
            else:
                pool.release(conn)
                conn = None
                raise AssertionError(f"慢查询未在 {timeout_ms}ms 内超时：{sql!r}")
        else:
            pytest.fail(f"所有慢查询候选都未触发超时：{slow_sqls!r}")

        assert connstate.is_unusable(conn), "超时后连接未被标记为不可复用"
        dirty = conn
        pool.release(dirty)  # 归还点无需感知状态：池应自动转废弃
        conn = None
        assert pool.size == 1, f"废弃后容量名额未回收（size={pool.size}）"

        fresh = pool.acquire()
        try:
            assert fresh is not dirty, "池把超时连接重新发出（P0 回归）"
            assert connstate.is_unusable(fresh) is False
            _, rows = run_with_timeout(adapter, fresh, probe_sql, 5000)
            assert rows, "废弃后补建的连接不可用"
        finally:
            pool.release(fresh)
    finally:
        if conn is not None:
            pool.release(conn)
        pool.close()


__all__ = [
    "SLOW_SQLS",
    "abs_window_dsl",
    "assert_rows_equal",
    "assert_timeout_discards_connection",
    "compile_dsl",
    "norm_cell",
]
