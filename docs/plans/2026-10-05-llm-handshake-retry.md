# LLM 流式握手期超时安全重试 实施计划

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。

**Goal:** `_http_post_sse` 在收到任何响应字节之前（握手期）超时时抛专用异常
`StreamHandshakeTimeout` 并安全重试一次（可配置关闭），把中转站链路瞬时挂起转化为
可用性；mid-stream 超时行为不变（防重复计费铁律）。

**Architecture:** providers 传输层内闭环——`_http_post_sse` 用 `handshake_done` 标志
（`getresponse()` 返回即置位）区分握手期/mid-stream 超时；新增
`_consume_stream_handshake_retry` 工厂闭包包装（重建全新 HTTP 请求，绝不复用旧连接），
4 个流式调用点统一接入；`audit.metrics` 新增分桶计数器由 providers 直接打点。

**Tech Stack:** Python 3.12 标准库 `http.client`（无新依赖）；pytest + monkeypatch；
black + ruff 门禁。

**Spec:** `docs/superpowers/specs/2026-10-05-llm-handshake-retry-design.md`（提交
91c166c）——执行者须同时读本计划与 spec，冲突以 spec 为准。

## Global Constraints

- 环境事实：python 直连 `C:/Users/cwt15/.conda/envs/futurebi/python.exe`（`conda run`
  子命令会崩）；bash PATH 无 black/ruff，用 `python -m black` / `python -m ruff`；
- 提交前门禁全绿：`python -m black --check .`、`python -m ruff check .`、
  `python -m pytest -q`（若 ruff 报 import 顺序，运行 `python -m ruff check --fix .`
  自动修复后复跑）；
- 铁律：响应头到达（`getresponse()` 返回）之后的任何超时/断连/错误帧**一律不重试**
  （防重复计费，`chat_text` docstring 已固化）；
- 重试必须**重建全新 HTTP 请求**（工厂闭包重新调用 `_http_post_sse`），绝不复用旧
  连接对象；
- Python 注释与 docstring 使用简体中文（专有名词保留英文）；测试函数名与 commit
  信息主体遵循项目既有风格（commit: `type(scope): 简体中文描述`）；
- 不改动：编排层（core/orchestrator）、web 层、前端、非流式 `_http_post`、
  `_llm_json` 及其 8 处测试桩格局、既有测试 `tests/test_providers.py`；
- 行号参考基于提交 91c166c 的文件状态；Task 2 会使其后行号漂移约 +10 行，一切修改
  以锚文本定位为准。

## Review Focus

1. connect 阶段超时（`conn.request()` 内部触发）与 getresponse 阶段超时都属握手期
   ——二者均在 `handshake_done` 置位前；测试覆盖 getresponse 形态，request 形态在
   Task 2 实现审查中确认同分支（同一 `except TimeoutError` 且置位未发生）；
2. `PROVIDER_HANDSHAKE_TIMEOUT` 配置为 0 或负数 → 运行时 `max(..., 1)` 钳制为 1s，
   不崩溃、不抛配置错误 → Task 2 测试 `test_handshake_budget_clamps_nonpositive_config`；
3. `PROVIDER_HANDSHAKE_RETRY_MAX=0`（关闭重试）→ 握手超时直接上抛，只打
   `handshake_timeout` 计数、绝不打 `retry_fail`（从未重试过）→ Task 3 测试
   `test_openai_chat_retry_disabled`；
4. 重试第二次请求的 headers / api_key / payload / timeout 必须与首次逐字段一致
   （鉴权头完整性——流式改造批次 2 教训：桩测试必须断言捕获的真实入参）→ Task 3
   测试 `test_openai_chat_handshake_retry_recovers` 内断言 `calls[0] == calls[1]`；
5. 退避 `time.sleep` 发生在失败连接已关闭之后（`conn.close()` 在 finally），退避期间
   不持有任何连接或锁 → Task 3 实现审查确认（`_http_post_sse` 生成器抛异常时
   finally 已执行）。

---

## Task 1: 基础设施——专用异常 + 配置项 + 指标计数器

**Files:**
- Modify: `providers/errors.py`（`StreamHandshakeRejected` 类之后、`ERROR_MESSAGES` 之前插入新类；`__all__` 增加一名）
- Modify: `config/settings.py`（`if PROVIDER_STREAM_MAX_SECONDS <= 0:` 守卫块之后插入两条配置）
- Modify: `audit/metrics.py`（`__init__` 的 `_degrade_outcomes` 之后、`record_degrade` 方法之后、`snapshot` 的 `"degrade_outcomes"` 行之后各插一段）
- Test: `tests/test_provider_handshake_retry.py`（新建）

**Interfaces:**
- Consumes: 无（首任务，纯新增）。
- Produces（后续任务依赖的精确签名）:
  - `providers.errors.StreamHandshakeTimeout`：`ProviderTimeoutError` 子类，类属性 `code = "timeout"`；
  - `settings.PROVIDER_HANDSHAKE_TIMEOUT: int`（默认 30）、`settings.PROVIDER_HANDSHAKE_RETRY_MAX: int`（默认 1）；
  - `MetricsRegistry.record_llm_handshake(kind: str) -> None`（kind ∈ `handshake_timeout` / `retry_success` / `retry_fail`）；
  - `MetricsRegistry.snapshot()` 返回 dict 新增键 `"llm_handshake": dict[str, int]`。

- [ ] **Step 1: 写失败测试（新建测试文件）**

创建 `tests/test_provider_handshake_retry.py`，初始内容（完整）：

```python
"""LLM 流式握手期超时安全重试测试。

spec: docs/superpowers/specs/2026-10-05-llm-handshake-retry-design.md
覆盖：异常体系兼容性、配置默认值、指标计数器、握手期判定与独立预算、
mid-stream 禁重试、重试包装（成功 / 双失败 / 开关关闭 / 鉴权头一致）、
三协议适配器接入。
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from audit.metrics import MetricsRegistry
from config import settings
from providers.errors import (
    ERROR_MESSAGES,
    ProviderTimeoutError,
    StreamHandshakeRejected,
    StreamHandshakeTimeout,
    error_message,
)


# --------------------------------------------------------------------------- #
# 异常体系兼容性
# --------------------------------------------------------------------------- #
def test_stream_handshake_timeout_hierarchy():
    """专用异常：ProviderTimeoutError 子类 + code 仍为 timeout（上层捕获面零变化）。"""
    assert issubclass(StreamHandshakeTimeout, ProviderTimeoutError)
    exc = StreamHandshakeTimeout("握手期超时（未收到任何响应字节）: read timed out")
    assert exc.code == "timeout"
    assert error_message(exc) == ERROR_MESSAGES["timeout"]


def test_stream_handshake_timeout_not_stream_handshake_rejected():
    """不是 StreamHandshakeRejected 子类：回退资格判定恒 False，绝不误触发非流式重发。"""
    assert not issubclass(StreamHandshakeTimeout, StreamHandshakeRejected)


def test_stream_handshake_timeout_not_fallback_eligible():
    """_stream_fallback_eligible 对新异常恒为 False（含附加 hints 场景）。"""
    from providers.adapters import _stream_fallback_eligible

    exc = StreamHandshakeTimeout("握手期超时（未收到任何响应字节）: read timed out")
    assert _stream_fallback_eligible(exc) is False
    assert _stream_fallback_eligible(exc, "stream_options") is False


# --------------------------------------------------------------------------- #
# 配置默认值
# --------------------------------------------------------------------------- #
def test_handshake_settings_defaults():
    """两项新配置默认值：握手预算 30s、安全重试 1 次。"""
    assert settings.PROVIDER_HANDSHAKE_TIMEOUT == 30
    assert settings.PROVIDER_HANDSHAKE_RETRY_MAX == 1


# --------------------------------------------------------------------------- #
# 指标计数器
# --------------------------------------------------------------------------- #
def test_record_llm_handshake_and_snapshot():
    """record_llm_handshake 锁内计数 + snapshot 以 llm_handshake 键导出。"""
    reg = MetricsRegistry()
    reg.record_llm_handshake("handshake_timeout")
    reg.record_llm_handshake("retry_success")
    reg.record_llm_handshake("retry_success")
    reg.record_llm_handshake("retry_fail")
    assert reg.snapshot()["llm_handshake"] == {
        "handshake_timeout": 1,
        "retry_success": 2,
        "retry_fail": 1,
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_provider_handshake_retry.py -v`
Expected: FAIL / ERROR——`ImportError: cannot import name 'StreamHandshakeTimeout'`（异常类不存在导致整个文件收集失败；settings 两属性与 metrics 方法尚不存在）。

- [ ] **Step 3: 最小实现**

3a. `providers/errors.py`——在 `StreamHandshakeRejected` 类定义块（`code = "provider_error"` 行）之后、`ERROR_MESSAGES` 注释行之前插入（完整）：

```python
class StreamHandshakeTimeout(ProviderTimeoutError):
    """流式握手期超时：连接建立 / 发送请求 / 等待响应头阶段挂起，
    未从 socket 收到任何响应字节。

    仅此异常允许触发握手安全重试（计费假设：上游对"连接建立但未返回任何
    响应字节"的请求未启动推理、不产生计费——免责锚点见 spec §前提假设）；
    mid-stream 超时永远是 ProviderTimeoutError 本类，结构上不可能误重试。
    扩展预留：未来编排层若需区分两种超时，可加 err.is_handshake 属性；
    本期上层无 code=="timeout" 分支区分（2026-10-05 核验），无需额外字段。
    """

    code = "timeout"
```

同文件 `__all__` 列表中，`"StreamHandshakeRejected",` 一行之后插入：

```python
    "StreamHandshakeTimeout",
```

3b. `config/settings.py`——在

```python
if PROVIDER_STREAM_MAX_SECONDS <= 0:
    PROVIDER_STREAM_MAX_SECONDS = 300
```

之后插入（完整）：

```python
# 流式握手期超时（秒）：连接建立 + 发送请求 + 等待响应头的独立预算，与调用方
# timeout、流总预算取 min——握手挂起无需硬等满 PROVIDER_TIMEOUT，保证开启重试后
# 最坏总延迟不高于单次 PROVIDER_TIMEOUT 上限；运行时对 <=0 钳制为 1（防非法配置）
PROVIDER_HANDSHAKE_TIMEOUT: int = int(os.getenv("PROVIDER_HANDSHAKE_TIMEOUT", "30"))
# 握手期超时安全重试次数：仅限未收到任何响应字节的超时（防重复计费假设见
# docs/superpowers/specs/2026-10-05-llm-handshake-retry-design.md §前提假设）；<=0 关闭
PROVIDER_HANDSHAKE_RETRY_MAX: int = int(os.getenv("PROVIDER_HANDSHAKE_RETRY_MAX", "1"))
```

3c. `audit/metrics.py`——三处插入：

`__init__` 中 `self._degrade_outcomes: Counter[str] = Counter()` 之后：

```python
        # LLM 流式握手重试事件（2026-10）：handshake_timeout=握手期超时发生 /
        # retry_success=重试救回 / retry_fail=重试后仍失败
        self._llm_handshake: Counter[str] = Counter()
```

`record_degrade` 方法之后：

```python
    def record_llm_handshake(self, kind: str) -> None:
        """登记一次 LLM 流式握手重试事件（handshake_timeout / retry_success / retry_fail）。"""
        with self._lock:
            self._llm_handshake[kind] += 1
```

`snapshot()` 返回 dict 中 `"degrade_outcomes": dict(self._degrade_outcomes),` 一行之后：

```python
                "llm_handshake": dict(self._llm_handshake),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_provider_handshake_retry.py -v`
Expected: 5 passed（hierarchy / not_rejected / not_fallback_eligible / settings_defaults / record_and_snapshot）。

- [ ] **Step 5: 全量门禁**

Run: `python -m black --check . && python -m ruff check . && python -m pytest -q`
Expected: 全绿（黑格式无 diff、ruff 无告警、全量测试通过——snapshot 新增键不破坏既有 metrics 测试，因其断言均为子集键或精确 dict？若是精确 dict 比较导致 FAIL，同步更新该测试的期望 dict 加入 `"llm_handshake": {}`）。

- [ ] **Step 6: Commit**

```bash
git add providers/errors.py config/settings.py audit/metrics.py tests/test_provider_handshake_retry.py
git commit -m "feat(providers): 握手期超时专用异常 StreamHandshakeTimeout + 握手预算/重试配置 + 握手指标计数器"
```

---

## Task 2: `_http_post_sse` 握手期独立预算与超时分流

**Files:**
- Modify: `providers/adapters.py`——顶部 `from providers.errors import (...)` 列表增加 `StreamHandshakeTimeout`；`_http_post_sse` 函数（当前 241-307 行）按下方完整新版替换
- Test: `tests/test_provider_handshake_retry.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `StreamHandshakeTimeout`、`settings.PROVIDER_HANDSHAKE_TIMEOUT`。
- Produces: `_http_post_sse(url, *, payload, headers, timeout, max_seconds, api_key=None) -> Iterator[str]` 签名不变；行为变化——握手期（`getresponse()` 返回前）超时抛 `StreamHandshakeTimeout`，其余行为逐字节一致。

- [ ] **Step 1: 写失败测试（追加到测试文件末尾）**

```python
# --------------------------------------------------------------------------- #
# _http_post_sse 握手期判定与独立预算
# --------------------------------------------------------------------------- #
def _make_conn(*, lines=None, handshake_error=False, read_error=False):
    """工厂生成连接测试桩：每测试独立状态（类属性桩会跨测试污染）。

    - handshake_error=True：getresponse 抛 TimeoutError（等待响应头挂起 => 握手期形态）；
    - read_error=True：getresponse 正常返回 200，但 readline 抛 TimeoutError
      （响应头已到达 => mid-stream 形态）；
    - created_timeouts 记录构造时收到的 timeout 实参（预算 min 语义断言用）。
    """

    class Conn:
        created_timeouts: ClassVar[list] = []

        def __init__(self, host, port, timeout=None):
            Conn.created_timeouts.append(timeout)

        def request(self, method, path, body=None, headers=None):
            pass

        def getresponse(self):
            if handshake_error:
                raise TimeoutError("read timed out")
            resp_lines = list(lines or [])

            class Resp:
                status = 200

                def readline(self, limit=-1):
                    if read_error:
                        raise TimeoutError("read timed out")
                    if not resp_lines:
                        return b""
                    return resp_lines.pop(0).encode("utf-8")

                def read(self):
                    return b'{"error": "bad request"}'

            return Resp()

        def close(self):
            pass

    return Conn


def test_handshake_getresponse_timeout_raises_dedicated_type(monkeypatch):
    """getresponse 阶段挂起（未收到任何响应头字节）=> StreamHandshakeTimeout。"""
    conn_cls = _make_conn(handshake_error=True)
    monkeypatch.setattr("http.client.HTTPSConnection", conn_cls)
    with pytest.raises(StreamHandshakeTimeout) as ei:
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=30
            )
        )
    assert "握手期超时" in str(ei.value)


def test_readline_timeout_after_response_header_stays_mid_stream(monkeypatch):
    """响应头已到达后 readline 超时 => mid-stream：ProviderTimeoutError 本类（非子类实例）。"""
    conn_cls = _make_conn(read_error=True)
    monkeypatch.setattr("http.client.HTTPSConnection", conn_cls)
    with pytest.raises(ProviderTimeoutError) as ei:
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=30
            )
        )
    assert type(ei.value) is ProviderTimeoutError


def test_handshake_budget_min_of_caller_timeout(monkeypatch):
    """握手预算 = min(配置 100, 调用方 timeout 5, 总预算 300) => 构造连接收到 5。"""
    monkeypatch.setattr("config.settings.PROVIDER_HANDSHAKE_TIMEOUT", 100)
    conn_cls = _make_conn(handshake_error=True)
    monkeypatch.setattr("http.client.HTTPSConnection", conn_cls)
    with pytest.raises(StreamHandshakeTimeout):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=300
            )
        )
    assert conn_cls.created_timeouts == [5]


def test_handshake_budget_clamps_nonpositive_config(monkeypatch):
    """PROVIDER_HANDSHAKE_TIMEOUT=0 非法 => 运行时钳制为 1s，不崩溃。"""
    monkeypatch.setattr("config.settings.PROVIDER_HANDSHAKE_TIMEOUT", 0)
    conn_cls = _make_conn(handshake_error=True)
    monkeypatch.setattr("http.client.HTTPSConnection", conn_cls)
    with pytest.raises(StreamHandshakeTimeout):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=60, max_seconds=300
            )
        )
    assert conn_cls.created_timeouts == [1]
```

测试文件 import 区需同步追加：`from providers.adapters import _http_post_sse`（并入既有 `from providers.adapters import` 不可用——本文件 Task 1 只有函数内局部 import；在顶部 import 块新增独立行 `from providers.adapters import _http_post_sse`，Task 3 再合并其他名字）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_provider_handshake_retry.py -v`
Expected: 新增 4 个测试 FAIL——`test_handshake_getresponse_timeout_raises_dedicated_type` 与两个预算测试实际抛 `ProviderTimeoutError`（非 `StreamHandshakeTimeout`），`test_readline_timeout_after_response_header_stays_mid_stream` 可能意外 PASS（现状本就抛 ProviderTimeoutError——精确类型断言使其成为回归守卫）；Task 1 的 5 个测试仍 PASS。

- [ ] **Step 3: 最小实现**

3a. `providers/adapters.py` 顶部错误 import 列表（`from providers.errors import (` 块内）按字母序在 `"StreamHandshakeRejected",` 之后追加一行：

```python
    StreamHandshakeTimeout,
```

3b. `_http_post_sse` 函数整体替换为下方完整新版（锚文本：`def _http_post_sse(`；docstring 与逻辑变化处已加注释）：

```python
def _http_post_sse(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    max_seconds: int,
    api_key: str | None = None,
) -> Iterator[str]:
    """发送流式 JSON POST，逐条产出 SSE data 负载（惰性生成器）。

    两道防无限流防线：
    - 块间空闲上限 timeout 与流总剩余预算取最小值，经 sock.settimeout 收紧
      单次 readline 的等待上限（慢滴流持续供字节也无法拖过总预算）；
    - 单行字节上限 _SSE_MAX_LINE_BYTES：网关永不换行时 readline 在该处被
      截断，达到上限仍无换行 => ProviderError 熔断（防内存无界增长）。
    总预算耗尽抛 ProviderTimeoutError。首包 HTTP 状态非 2xx 与 _http_post
    同映射；EOF（b""）正常结束迭代——终止帧校验归消费方（_consume_stream）。

    握手期（连接建立 + 发送请求 + 等待响应头）使用独立预算
    min(PROVIDER_HANDSHAKE_TIMEOUT, timeout, max_seconds)：getresponse 返回
    （已从 socket 读到响应头字节）即进入 mid-stream 域。握手期超时抛
    StreamHandshakeTimeout——未收到任何字节 => 重试无重复计费风险（计费假设
    与免责锚点见 spec §前提假设），是否重试由 _consume_stream_handshake_retry
    裁决；mid-stream 超时抛 ProviderTimeoutError，防重复计费铁律禁止重试。
    """
    from config import settings

    body = json.dumps(payload).encode("utf-8")
    merged_headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        **headers,
    }
    if api_key:
        merged_headers["Authorization"] = f"Bearer {api_key}"
    parsed = _validate_outbound_url(url)
    conn_cls = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    # 握手期独立预算：与调用方 timeout、流总预算取 min（最坏总延迟不膨胀）；
    # 配置 <=0 运行时钳制为 1s（非法配置防崩）
    handshake_timeout = min(
        max(settings.PROVIDER_HANDSHAKE_TIMEOUT, 1),
        timeout,
        max_seconds,
    )
    conn = conn_cls(parsed.hostname, parsed.port, timeout=handshake_timeout)
    started = time.perf_counter()
    # 握手完成标志：getresponse 返回即置位（已从 socket 读到响应头字节，
    # 按"读到任意字节即进入 mid-stream 域"的保守口径——此后一律禁重试）
    handshake_done = False
    try:
        conn.request("POST", parsed.path or "/", body=body, headers=merged_headers)
        resp = conn.getresponse()
        handshake_done = True
        if resp.status >= 400:
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.status == 400:
                # 握手阶段 400 专用类型：回退资格判定只认它——此时尚未产出
                # 任何 chunk，重发无重复计费风险；mid-stream 错误帧/断连永远
                # 是其他错误类型，结构上不可能误触发回退
                raise StreamHandshakeRejected(
                    f"模型服务返回 HTTP 400: {_brief(raw)}", code="provider_error"
                )
            _raise_for_status(resp.status, raw)
        while True:
            # 防线一：剩余预算语义——块间空闲上限与总剩余取最小值收紧单次读
            remaining = max_seconds - (time.perf_counter() - started)
            if remaining <= 0:
                raise ProviderTimeoutError(f"流式总时长超限（>{max_seconds}s）")
            sock = getattr(conn, "sock", None)
            if sock is not None:
                sock.settimeout(min(timeout, remaining))
            # 防线二：行字节上限——无换行慢滴流单行熔断
            line = resp.readline(_SSE_MAX_LINE_BYTES)
            if not line:
                return  # EOF：终止帧校验归消费方
            if len(line) == _SSE_MAX_LINE_BYTES and not line.endswith(b"\n"):
                raise ProviderError("SSE 行超长（无换行慢滴流），已熔断", code="provider_error")
            yield from _iter_sse_payloads([line.decode("utf-8", errors="replace")])
    except TimeoutError as exc:  # socket.timeout（3.10+ 即 TimeoutError）
        if not handshake_done:
            # 握手期：connect / request / getresponse 阶段挂起，未收到任何响应
            # 字节——重试无重复计费风险（计费假设见 spec §前提假设）
            raise StreamHandshakeTimeout(f"握手期超时（未收到任何响应字节）: {exc}") from exc
        raise ProviderTimeoutError(f"请求超时: {exc}") from exc
    except (http.client.HTTPException, OSError) as exc:
        raise ProviderError(f"网络请求失败: {exc}", code="provider_error") from exc
    finally:
        conn.close()
```

**实现审查点（Review Focus #1/#5）**：`conn.request()`（内部触发 connect）与
`conn.getresponse()` 的超时都在 `handshake_done = True` 之前发生，同落
`except TimeoutError` 的握手分支——connect 形态无需额外测试；`finally` 中
`conn.close()` 在异常传播前执行，连接不跨退避存活。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_provider_handshake_retry.py -v`
Expected: 9 passed（Task 1 的 5 个 + Task 2 的 4 个）。

- [ ] **Step 5: 全量门禁**

Run: `python -m black --check . && python -m ruff check . && python -m pytest -q`
Expected: 全绿。重点回归 `tests/test_providers.py` 既有 SSE 测试（含
`test_http_post_sse_idle_timeout_maps_to_provider_timeout`——getresponse 正常返回后
readline 超时仍为 `ProviderTimeoutError`，行为不变）。

- [ ] **Step 6: Commit**

```bash
git add providers/adapters.py tests/test_provider_handshake_retry.py
git commit -m "feat(providers): _http_post_sse 握手期独立预算与超时分流（StreamHandshakeTimeout）"
```

---

## Task 3: `_consume_stream_handshake_retry` 重试包装 + 4 个流式调用点接入

**Files:**
- Modify: `providers/adapters.py`——顶部 import 增加 `from audit.metrics import default_registry`；`_consume_stream` 函数之后插入模块常量与新函数；4 个流式调用点（锚文本 `_consume_stream(\n            _http_post_sse(`）逐处改造
- Test: `tests/test_provider_handshake_retry.py`（追加）

**Interfaces:**
- Consumes: Task 1 的异常/配置/指标接口、Task 2 的 `_http_post_sse` 握手超时语义。
- Produces:
  - `providers.adapters._consume_stream_handshake_retry(build: Callable[[], Iterator[str]], *, extract_delta, terminal, extract_usage, extract_error=None) -> tuple[str, dict[str, Any] | None]`；
  - 4 个流式调用点（openai_chat 主路径 / openai_chat stream_options 去参路径 / responses / anthropic）经该包装接入握手重试；
  - 指标事件：`handshake_timeout`（每次握手超时）、`retry_success`（重试后成功）、`retry_fail`（重试开启且最后一次仍握手超时）。

- [ ] **Step 1: 写失败测试（追加到测试文件末尾）**

测试文件顶部 import 区改为（完整替换既有 providers 相关 import 块）：

```python
from audit.metrics import MetricsRegistry
from config import settings
from providers.adapters import (
    OpenAIChatAdapter,
    OpenAIResponsesAdapter,
    AnthropicAdapter,
    _http_post_sse,
    _stream_fallback_eligible,
)
from providers.errors import (
    ERROR_MESSAGES,
    ProviderTimeoutError,
    StreamHandshakeRejected,
    StreamHandshakeTimeout,
    error_message,
)
from providers.models import ProviderConfig, UnifiedChatRequest
from tests.fixture_keys import fake_key
```

追加测试代码（完整）：

```python
# --------------------------------------------------------------------------- #
# 重试包装与三协议适配器接入
# --------------------------------------------------------------------------- #
def _provider(**overrides) -> ProviderConfig:
    """供应商配置测试基座（抄自 tests/test_providers.py 同名 helper）。"""
    base = {
        "id": "p1",
        "name": "测试站",
        "is_preset": False,
        "enabled": True,
        "base_url": "https://gw.example.com/v1",
        "api_key": fake_key("gw"),
        "protocol": "openai_chat",
        "models": [{"id": "m-1", "name": "M1"}],
    }
    base.update(overrides)
    return ProviderConfig.model_validate(base)


def _flaky_handshake_sse(calls: list, *, ok_frames: list):
    """构造"首次握手期超时、之后成功"的 _http_post_sse 替身工厂。

    每次调用把 (payload, headers, timeout, api_key) 快照进 calls，供
    "重试请求与首次逐字段一致"断言（鉴权头完整性专项，Review Focus #4）。
    """

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        calls.append(
            {"payload": payload, "headers": headers, "timeout": timeout, "api_key": api_key}
        )
        if len(calls) == 1:

            def gen_fail():
                raise StreamHandshakeTimeout("握手期超时（未收到任何响应字节）: read timed out")

                yield  # pragma: no cover —— 使其成为生成器函数，异常延迟到迭代时抛出

            return gen_fail()

        def gen_ok():
            yield from ok_frames

        return gen_ok()

    return fake_sse


def _midstream_timeout_sse(calls: list):
    """构造"首次即 mid-stream 超时"的替身：验证穿透不重试。"""

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        calls.append(
            {"payload": payload, "headers": headers, "timeout": timeout, "api_key": api_key}
        )

        def gen_fail():
            raise ProviderTimeoutError("请求超时: read timed out")

            yield  # pragma: no cover

        return gen_fail()

    return fake_sse


def _bind_metrics(monkeypatch) -> MetricsRegistry:
    """隔离进程级单例：重定向 providers.adapters 的 default_registry 到独立实例。"""
    reg = MetricsRegistry()
    monkeypatch.setattr("providers.adapters.default_registry", lambda: reg)
    return reg


_OPENAI_OK_FRAMES = [
    json.dumps({"choices": [{"delta": {"content": "ok"}}]}),
    "[DONE]",
]
_ANTHROPIC_OK_FRAMES = [
    json.dumps({"type": "content_block_delta", "delta": {"text": "ok"}}),
    json.dumps({"type": "message_stop"}),
]
_RESPONSES_OK_FRAMES = [
    json.dumps({"type": "response.output_text.delta", "delta": "ok"}),
    json.dumps({"type": "response.completed", "response": {}}),
]


def test_openai_chat_handshake_retry_recovers(monkeypatch):
    """openai_chat：首次握手超时 → 重试成功返回；两次请求逐字段一致；指标齐全。"""
    adapter = OpenAIChatAdapter(_provider(stream=True), "m-1")
    calls: list = []
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _flaky_handshake_sse(calls, ok_frames=_OPENAI_OK_FRAMES),
    )
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
    )
    assert resp.content == "ok"
    assert len(calls) == 2
    assert calls[0] == calls[1]  # 鉴权头/参数逐字段一致（Review Focus #4）
    assert reg.snapshot()["llm_handshake"] == {
        "handshake_timeout": 1,
        "retry_success": 1,
        "retry_fail": 0,
    }


def test_openai_chat_double_handshake_failure_raises(monkeypatch):
    """两次握手超时 => 上抛 StreamHandshakeTimeout；指标 handshake_timeout=2 + retry_fail=1。"""
    adapter = OpenAIChatAdapter(_provider(stream=True), "m-1")
    calls: list = []

    def always_fail(url, *, payload, headers, timeout, max_seconds, api_key=None):
        calls.append(
            {"payload": payload, "headers": headers, "timeout": timeout, "api_key": api_key}
        )

        def gen_fail():
            raise StreamHandshakeTimeout("握手期超时（未收到任何响应字节）: read timed out")

            yield  # pragma: no cover

        return gen_fail()

    monkeypatch.setattr("providers.adapters._http_post_sse", always_fail)
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    with pytest.raises(StreamHandshakeTimeout):
        adapter.chat(
            UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
        )
    assert len(calls) == 2
    assert reg.snapshot()["llm_handshake"] == {
        "handshake_timeout": 2,
        "retry_success": 0,
        "retry_fail": 1,
    }


def test_openai_chat_mid_stream_timeout_no_retry(monkeypatch):
    """mid-stream 超时（响应头已到后挂起）=> 原样上抛、零重试、零 retry_* 计数。"""
    adapter = OpenAIChatAdapter(_provider(stream=True), "m-1")
    calls: list = []
    monkeypatch.setattr("providers.adapters._http_post_sse", _midstream_timeout_sse(calls))
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    with pytest.raises(ProviderTimeoutError):
        adapter.chat(
            UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
        )
    assert len(calls) == 1  # 铁律：mid-stream 禁止重试
    assert reg.snapshot()["llm_handshake"] == {
        "handshake_timeout": 0,
        "retry_success": 0,
        "retry_fail": 0,
    }


def test_openai_chat_retry_disabled(monkeypatch):
    """RETRY_MAX=0 关闭重试：首次握手超时即上抛，只打 handshake_timeout、无 retry_fail。"""
    monkeypatch.setattr("config.settings.PROVIDER_HANDSHAKE_RETRY_MAX", 0)
    adapter = OpenAIChatAdapter(_provider(stream=True), "m-1")
    calls: list = []
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _flaky_handshake_sse(calls, ok_frames=_OPENAI_OK_FRAMES),
    )
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    with pytest.raises(StreamHandshakeTimeout):
        adapter.chat(
            UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
        )
    assert len(calls) == 1
    assert reg.snapshot()["llm_handshake"] == {
        "handshake_timeout": 1,
        "retry_success": 0,
        "retry_fail": 0,
    }


def test_anthropic_handshake_retry_recovers(monkeypatch):
    """anthropic 协议：握手重试同样生效（x-api-key 鉴权头一致性随 calls[0]==calls[1] 断言）。"""
    adapter = AnthropicAdapter(_provider(protocol="anthropic", stream=True), "m-1")
    calls: list = []
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _flaky_handshake_sse(calls, ok_frames=_ANTHROPIC_OK_FRAMES),
    )
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
    )
    assert resp.content == "ok"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert reg.snapshot()["llm_handshake"]["retry_success"] == 1


def test_responses_handshake_retry_recovers(monkeypatch):
    """responses 协议：握手重试同样生效。"""
    adapter = OpenAIResponsesAdapter(_provider(protocol="openai_responses", stream=True), "m-1")
    calls: list = []
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _flaky_handshake_sse(calls, ok_frames=_RESPONSES_OK_FRAMES),
    )
    reg = _bind_metrics(monkeypatch)
    monkeypatch.setattr("providers.adapters._HANDSHAKE_RETRY_BACKOFF_SECONDS", 0)
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1")
    )
    assert resp.content == "ok"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert reg.snapshot()["llm_handshake"]["retry_success"] == 1
```

**注意**：上方 `test_openai_chat_double_handshake_failure_raises` 与其余五个测试同批
落盘，无占位。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_provider_handshake_retry.py -v`
Expected: Task 1/2 的 9 个测试仍 PASS；Task 3 新增 6 个中 4 个 FAIL——
`test_openai_chat_handshake_retry_recovers` / `test_openai_chat_double_handshake_failure_raises`
/ `test_anthropic_handshake_retry_recovers` / `test_responses_handshake_retry_recovers`
（调用点尚未接入重试包装，首次 `StreamHandshakeTimeout` 直接上抛，`pytest.raises` 捕到
但 `len(calls) == 2` / `retry_success` 断言不满足）；`test_openai_chat_mid_stream_timeout_no_retry`
与 `test_openai_chat_retry_disabled` 可能已 PASS（其期望行为与现状穿透一致，属提前
到位的回归守卫）。

- [ ] **Step 3: 最小实现**

3a. `providers/adapters.py` 顶部：`from providers.errors import (...)` 块之前，按字母序加入：

```python
from audit.metrics import default_registry
```

3b. `_consume_stream` 函数定义结束后（其后是 `_raise_for_error_frame` / `_stream_fallback_eligible` 等模块级定义，插入位置以紧跟 `_consume_stream` 尾行为准），插入模块常量与新函数（完整）：

```python
# 握手期安全重试的固定短退避（秒）：对秒~分钟级链路抖动象征意义大于实际，
# 成本为零，仅避免对同一故障节点的瞬时重发冲击（不设配置，YAGNI）
_HANDSHAKE_RETRY_BACKOFF_SECONDS = 0.2


def _consume_stream_handshake_retry(
    build: Callable[[], Iterator[str]],
    *,
    extract_delta: Callable[[dict[str, Any]], str],
    terminal: Callable[[dict[str, Any]], bool],
    extract_usage: Callable[[dict[str, Any]], dict[str, Any] | None],
    extract_error: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """消费 SSE 流，仅对握手期超时安全重试（防重复计费铁律的唯一例外窗口）。

    - build 工厂闭包重建**全新 HTTP 请求**（绝不复用旧连接），捕获的
      payload / headers / api_key / 协议参数与首次请求完全一致（原样重发，
      鉴权头完整性由既有 _build_headers / api_key 透传保证）；
    - 仅 StreamHandshakeTimeout（未收到任何响应字节）触发重试——计费假设与
      免责锚点见 docs/superpowers/specs/2026-10-05-llm-handshake-retry-design.md
      §前提假设；mid-stream 超时 / 断连 / 错误帧 / 协议错误原样穿透；
    - 0.2s 固定短退避后重试；重试失败连接已在 _http_post_sse finally 关闭，
      退避期间不持有任何连接；
    - 事件打点 default_registry().record_llm_handshake：handshake_timeout
      （每次发生）/ retry_success（重试救回）/ retry_fail（开启重试且最后一次
      仍握手超时；RETRY_MAX=0 从未重试则不打）。
    """
    from config import settings

    attempts = 1 + max(settings.PROVIDER_HANDSHAKE_RETRY_MAX, 0)
    for attempt in range(attempts):
        try:
            content, usage = _consume_stream(
                build(),
                extract_delta=extract_delta,
                terminal=terminal,
                extract_usage=extract_usage,
                extract_error=extract_error,
            )
            if attempt > 0:
                default_registry().record_llm_handshake("retry_success")
            return content, usage
        except StreamHandshakeTimeout:
            default_registry().record_llm_handshake("handshake_timeout")
            if attempt >= attempts - 1:
                if attempts > 1:
                    default_registry().record_llm_handshake("retry_fail")
                raise
            logger.warning(
                "流式握手期超时（未收到任何响应字节），%.1fs 后安全重试（第 %d/%d 次）",
                _HANDSHAKE_RETRY_BACKOFF_SECONDS,
                attempt + 1,
                attempts - 1,
            )
            time.sleep(_HANDSHAKE_RETRY_BACKOFF_SECONDS)
    raise AssertionError("unreachable: 重试循环每轮要么返回要么上抛")
```

3c. 4 个流式调用点改造——统一变换规则：`_consume_stream(` →
`_consume_stream_handshake_retry(`；紧随其后的 `_http_post_sse(...)` 实参整体包成
`lambda: _http_post_sse(...)`；其余关键字实参与缩进逐字保留。逐处 after 代码：

**调用点 1/4——openai_chat 主路径**（锚文本 `content, usage = _consume_stream(` 首次出现处，当前约 527 行）：

```python
        try:
            content, usage = _consume_stream_handshake_retry(
                lambda: _http_post_sse(
                    url,
                    payload=payload,
                    headers=headers,
                    timeout=timeout,
                    max_seconds=max_seconds,
                    api_key=self.provider.api_key,
                ),
                extract_delta=_delta,
                terminal=lambda f: False,  # 终止帧 = [DONE]，_consume_stream 内建处理
                extract_usage=lambda f: f.get("usage"),
            )
        except ProviderError as exc:
```

（外层 `try/except ProviderError` 与 stream_options 回退判定逐字保留——
`StreamHandshakeTimeout` 是 `ProviderError` 子类会被此 except 捕获，但
`_stream_fallback_eligible` 型判定（`isinstance(exc, StreamHandshakeRejected)`）为
False 走 `raise` 重抛，行为等价穿透，无需改判定。）

**调用点 2/4——openai_chat stream_options 去参重试路径**（同函数内 `logger.info("网关不认 stream_options，去参数保流式重试")` 之后的第二个 `_consume_stream(`，当前约 549 行）：

```python
            content, usage = _consume_stream_handshake_retry(
                lambda: _http_post_sse(
                    url,
                    payload=payload,
                    headers=headers,
                    timeout=timeout,
                    max_seconds=max_seconds,
                    api_key=self.provider.api_key,
                ),
                extract_delta=_delta,
                terminal=lambda f: False,
                extract_usage=lambda f: f.get("usage"),
            )
```

**调用点 3/4——responses**（`OpenAIResponsesAdapter` 内，锚文本 `terminal=lambda f: f.get("type") == "response.completed"` 所在调用，当前约 737 行）：

```python
        content, usage = _consume_stream_handshake_retry(
            lambda: _http_post_sse(
                url,
                payload=payload,
                headers=self._build_headers(),
                timeout=timeout,
                max_seconds=settings.PROVIDER_STREAM_MAX_SECONDS,
                api_key=self.provider.api_key,
            ),
            extract_delta=lambda f: (
                (f.get("delta") or "") if f.get("type") == "response.output_text.delta" else ""
            ),
            terminal=lambda f: f.get("type") == "response.completed",
            extract_usage=lambda f: (f.get("response") or {}).get("usage"),
            extract_error=_extract_error,
        )
```

**调用点 4/4——anthropic**（`AnthropicAdapter` 内，锚文本 `terminal=lambda f: f.get("type") == "message_stop"` 所在调用，当前约 944 行）：

```python
        content, usage = _consume_stream_handshake_retry(
            lambda: _http_post_sse(
                url,
                payload=payload,
                headers=headers,
                timeout=timeout,
                max_seconds=settings.PROVIDER_STREAM_MAX_SECONDS,
            ),
            extract_delta=lambda f: (
                (f.get("delta") or {}).get("text") or ""
                if f.get("type") == "content_block_delta"
                else ""
            ),
            terminal=lambda f: f.get("type") == "message_stop",
            extract_usage=_extract_usage,
        )
```

**实现审查点（Review Focus #4/#5）**：四个闭包捕获的 `payload / headers /
timeout / api_key` 与首次请求为同一对象原样重发（openai_chat 去参路径的
`payload.pop("stream_options")` 发生在其 try/except 内、握手重试异常路径不触碰
payload）；`time.sleep` 位于 `_http_post_sse` 生成器异常上抛之后（其 finally 已
`conn.close()`），无连接跨退避存活。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_provider_handshake_retry.py -v`
Expected: 15 passed（Task 1 的 5 + Task 2 的 4 + Task 3 的 6：recovers / double_failure / mid_stream_no_retry / retry_disabled / anthropic / responses）。

- [ ] **Step 5: 全量门禁**

Run: `python -m black --check . && python -m ruff check . && python -m pytest -q`
Expected: 全绿。重点回归：`tests/test_providers.py` 全部既有流式测试（fake_sse
替身经 `_consume_stream_handshake_retry` 的 `build()` 间接调用，monkeypatch 模块属性
后闭包运行时解析到替身，行为不变）、编排层 `_llm_json` 相关测试（8 处测试桩格局未动）。

- [ ] **Step 6: Commit**

```bash
git add providers/adapters.py tests/test_provider_handshake_retry.py
git commit -m "feat(providers): 流式调用点接入握手期安全重试（工厂闭包重建请求 + 指标打点）"
```

---

## 红线自查记录（计划编写者已执行）

1. **规格覆盖**：spec §4.1 异常 → Task 1；§4.2 判定与预算 → Task 2；§4.3 包装与
   调用点 → Task 3；§4.4 配置 → Task 1；§4.5 指标 → Task 1+3；§风险矩阵各行 →
   Task 2/3 测试与既有行为守卫；§测试计划 9 组 → Task 1（1/2/8）、Task 2（3/4/5）、
   Task 3（3/6/7）；第 9 组全量回归 → 各任务 Step 5。无缺口。
2. **占位符扫描**：全文无 TBD / skip 占位 / "同 Task N"；Task 3 Step 1 的六个测试
   均为终版代码；无其他省略。
3. **类型一致性**：`_consume_stream_handshake_retry` 签名（Task 3 Produces）与
   spec §4.3、调用点关键字实参一致；`record_llm_handshake(kind)` 三值与 Task 1 测试、
   Task 3 断言一致；`StreamHandshakeTimeout` 名称在 errors/adapters/测试三处一致。
4. **Review Focus 落实**：#1 → Task 2 实现审查点；#2 → Task 2 测试；#3 → Task 3
   `test_openai_chat_retry_disabled`；#4 → Task 3 `calls[0] == calls[1]` 断言 ×3；
   #5 → Task 3 实现审查点。
