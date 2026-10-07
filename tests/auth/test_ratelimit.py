"""登录限流（P0-4）单元测试：指数退避 + 成功重置。"""

from __future__ import annotations

import pytest

from auth.ratelimit import LoginRateLimiter, LoginRateLimitError


def test_allows_within_threshold():
    limiter = LoginRateLimiter(max_failures=3, base_seconds=1.0)
    limiter.record_failure("a:1.2.3.4")
    limiter.record_failure("a:1.2.3.4")
    limiter.check("a:1.2.3.4")  # 未达阈值不抛


def test_blocks_after_threshold():
    limiter = LoginRateLimiter(max_failures=2, base_seconds=10.0)
    limiter.record_failure("a:1.2.3.4")
    limiter.record_failure("a:1.2.3.4")
    with pytest.raises(LoginRateLimitError) as excinfo:
        limiter.check("a:1.2.3.4")
    assert excinfo.value.retry_after > 0


def test_success_resets_failure_count():
    limiter = LoginRateLimiter(max_failures=2, base_seconds=10.0)
    limiter.record_failure("a:1.2.3.4")
    limiter.record_success("a:1.2.3.4")
    limiter.check("a:1.2.3.4")  # 重置后放行


def test_keys_are_isolated():
    limiter = LoginRateLimiter(max_failures=2, base_seconds=10.0)
    limiter.record_failure("a:1.2.3.4")
    limiter.record_failure("a:1.2.3.4")
    limiter.check("b:5.6.7.8")  # 其他用户名+IP 不受影响


# --------------------------------------------------------------------------- #
# 持久化（整改指令3-2）：失败计数落盘 SQLite，跨实例一致
# --------------------------------------------------------------------------- #
def test_sqlite_limiter_persists_failures(tmp_path):
    """新实例（模拟重启/多 worker）读取同一 SQLite 后仍处于限流状态。"""
    db = str(tmp_path / "ratelimit.db")
    limiter1 = LoginRateLimiter(max_failures=2, base_seconds=10.0, db_path=db)
    limiter1.record_failure("u:1.1.1.1")
    limiter1.record_failure("u:1.1.1.1")

    # 新实例读取落盘状态：check 应抛限流错误（跨实例一致）
    limiter2 = LoginRateLimiter(max_failures=2, base_seconds=10.0, db_path=db)
    with pytest.raises(LoginRateLimitError) as excinfo:
        limiter2.check("u:1.1.1.1")
    assert excinfo.value.retry_after > 0


def test_sqlite_limiter_success_resets_across_instances(tmp_path):
    db = str(tmp_path / "ratelimit2.db")
    limiter1 = LoginRateLimiter(max_failures=2, base_seconds=10.0, db_path=db)
    limiter1.record_failure("u:2.2.2.2")
    limiter1.record_failure("u:2.2.2.2")

    limiter2 = LoginRateLimiter(max_failures=2, base_seconds=10.0, db_path=db)
    limiter2.record_success("u:2.2.2.2")  # 落库删除
    limiter3 = LoginRateLimiter(max_failures=2, base_seconds=10.0, db_path=db)
    limiter3.check("u:2.2.2.2")  # 成功重置后放行
