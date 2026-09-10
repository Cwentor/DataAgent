"""测试专用假密钥：运行时生成，源码不落任何凭据字面量。

安全门约束：源码、示例和测试均不得写入（可用）凭据字面量；测试中的
api_key 值本身无关紧要（仅要求非空、可区分、不含明文语义），故统一
运行时生成随机假密钥，从源头消除"凭据形态字面量"进库。
"""

from __future__ import annotations

import uuid


def fake_key(seed: str = "t") -> str:
    """生成一次性的测试假密钥：不可用、每次随机，含 seed 便于排障定位。"""
    return f"fake-{seed}-{uuid.uuid4().hex[:12]}"
