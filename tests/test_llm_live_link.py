"""真实 LLM 链路集成测试（live）：断连探测与长输出稳定性。

走生产同链路（ProviderStore 解密 -> ProviderFactory -> 适配器流式），验证三类
真实形态：链路可达、长输出不中断、握手指标可观测。默认跳过——离线确定性门禁
铁律 + 真实请求产生计费；显式开启方式：

    LIVE_LLM_TESTS=1 python -m pytest tests/test_llm_live_link.py -v

外部供应商存在分钟级波动（2026-10 实测同一分钟窗口 60s 挂起与秒级返回交替），
本测试失败时先看异常类型并用 llm_link_diag.py 复测分诊，勿直接断定代码回归。
"""

from __future__ import annotations

import os

import pytest

from audit.metrics import default_registry
from config import settings
from providers.factory import default_provider_factory

_LIVE_ENV = os.getenv("LIVE_LLM_TESTS", "").lower()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        _LIVE_ENV not in ("1", "true", "yes"),
        reason="真实链路测试需显式开启（LIVE_LLM_TESTS=1）——外部服务不稳定且产生计费，不进常规门禁",
    ),
]


@pytest.fixture(scope="module")
def live_adapter():
    """生产同链路默认适配器（store 解密 + factory 分派）；无可用供应商则跳过。"""
    adapter = default_provider_factory().default_adapter()
    if adapter is None:
        pytest.skip("无可用默认供应商（未配置 API Key 或全部禁用）")
    return adapter


def test_live_short_request_reachable(live_adapter):
    """短请求探针：链路可达、最小输出成功。

    断连/握手挂起将以真实异常类型上抛（ProviderTimeoutError /
    StreamHandshakeTimeout / ProviderError），失败信息即分诊依据。
    """
    before = dict(default_registry().snapshot().get("llm_handshake", {}))
    text = live_adapter.chat_text(
        [{"role": "user", "content": "只回复两个字：收到"}],
        model=live_adapter.model_id,
        json_mode=False,
        timeout=settings.PROVIDER_TIMEOUT,
    )
    assert "收到" in text
    # 可观测面打通：握手指标键可读且单调不减（是否触发重试取决于链路形态）
    after = dict(default_registry().snapshot().get("llm_handshake", {}))
    assert all(
        after.get(k, 0) >= before.get(k, 0)
        for k in ("handshake_timeout", "retry_success", "retry_fail")
    )


def test_live_long_output_stream_stability(live_adapter):
    """长输出约 110s：300s 流总预算内完成、内容达到规模下限（非中途截断）。

    mid-stream 断连 / 块间空闲超限将以 ProviderTimeoutError / ProviderError
    上抛（按防重复计费铁律不重试，直接失败即预期行为）。
    """
    prompt = (
        "请写一篇约 2500 字的短文，主题《企业数据分析平台的演进：从报表到智能体》。"
        "要求：分五个小节，每节 500 字左右，语言为简体中文，直接输出正文，不要分页或省略。"
    )
    text = live_adapter.chat_text(
        [{"role": "user", "content": prompt}],
        model=live_adapter.model_id,
        json_mode=False,
        timeout=settings.PROVIDER_TIMEOUT,
    )
    assert len(text) >= 1500  # 低于此值视为内容缺失或流提前终止
