# 设计文档｜LLM 流式握手期超时安全重试（2026-10）

## 背景与动机

2026-10-04 诊断确认：ProviderTimeoutError（`请求超时: The read operation timed out`）
的根因是外部链路波动——中转站 aiaaa.cc 同一 payload 同一分钟窗口内实测 60s 挂起与
0.8s 返回交替，叠加本机代理 fake-ip TUN 全接管；不是代码或架构问题。流式改造
（2026-09-30 spec）已把超时语义从"整包 60s 读完"改为"块间不能长时间沉默"，但链路
完全死挂的窗口（一个字节都不来）依然会触发超时并整体降级离线模板。

现有 `_http_post_sse` 的 `except TimeoutError` 分支把两类性质完全不同的超时混为一谈：

1. **握手期超时**：连接建立、发送请求、等待响应头阶段挂起——服务端尚未输出任何
   字节，请求可能根本没到达上游（fake-ip / 中转死挂的典型形态）；
2. **mid-stream 超时**：响应头已到达、流已经开始流动后挂起——上游大概率已受理
   请求甚至开始推理。

前者重试一次是零风险的自救机会（波动窗口内下一次请求往往 0.8s 就返回）；后者重发
有重复计费风险（防重复计费铁律，`chat_text` docstring 已固化约束）。当前二者共用
`ProviderTimeoutError`，调用侧无法区分，只能整体降级。

## 目标与非目标

### 目标

- 握手期（收到任何响应字节之前）超时抛专用异常 `StreamHandshakeTimeout`，允许
  安全重试一次（默认），把链路瞬时挂起转化为可用性；
- 握手阶段使用独立的短超时预算（`PROVIDER_HANDSHAKE_TIMEOUT`，默认 30s），保证
  开启重试后最坏总延迟不高于现有单次 60s 上限；
- 三类事件（握手超时 / 重试成功 / 重试仍失败）计入 `/api/metrics` 可观测；
- 全程不触碰 mid-stream 防重复计费铁律，编排层、前端零改动。

### 非目标

- **mid-stream 重试**：任何收到响应字节之后的超时/断连/错误帧一律禁止重试（铁律）；
- **多供应商自动 failover**：本期不做（用户已裁决，另行立项）；
- **异步任务化 / 多轮小步拆解**：与本项目 DSL 契约架构冲突，明确否决；
- **非流式路径重试**：`_http_post` 整包读没有安全重试边界（响应头到达即可能已开始
  计费），且非主链路，本期不动；
- **编排层改动**：`_llm_json` 签名与其 8 处测试桩 monkeypatch 格局不动。

## 前提假设（计费边界锚点，免责声明）

> **假设**：上游中转 / LLM 服务对"TCP 连接建立但未返回任何响应字节"的请求，未启动
> 推理、不产生计费。本重试策略仅在此假设成立的前提下启用；若上游存在"收到请求即
> 计费（哪怕不返回数据）"的计费模式，必须通过 `PROVIDER_HANDSHAKE_RETRY_MAX=0`
> 关闭重试。
>
> 该假设与项目既有论证口径一致：`StreamHandshakeRejected`（握手 400 回退）注释
> 已确立"尚未产出任何 chunk，重发无重复计费风险"。切换供应商时应重新确认本假设。

## 架构设计

### 4.1 异常体系（providers/errors.py）

```python
class StreamHandshakeTimeout(ProviderTimeoutError):
    """流式握手期超时：连接建立 / 发送请求 / 等待响应头阶段挂起，
    未从 socket 收到任何响应字节。

    仅此异常允许触发握手安全重试（计费假设见 spec §前提假设）；
    mid-stream 超时永远是 ProviderTimeoutError 本类，结构上不可能误重试。
    扩展预留：未来若编排层需区分两种超时，可加 err.is_handshake 属性；
    本期上层无 code=="timeout" 分支区分（已核验），无需额外字段。
    """

    code = "timeout"
```

继承关系带来的兼容性（已逐项核验）：

- 上层所有 `except ProviderTimeoutError` 与 `exc_to_code` 文案映射零改动
  （`code` 保持 `"timeout"`）；
- 代码库中 `exc.code ==` 的消费点仅 `adapters.py:587/778` 两处，均判
  `"provider_error"`（openai_chat / responses 的 stream_options 回退资格），
  与 `"timeout"` 天然不匹配，不存在按 `"timeout"` 分支区分的逻辑；
- `_stream_fallback_eligible` 要求 `isinstance(exc, StreamHandshakeRejected)`，
  本异常不是其子类，恒为 False——回退资格绝不误触发（测试固化）。

### 4.2 握手期判定与独立超时预算（providers/adapters.py）

**判定口径（保守优先）**：握手完成标志（即讨论中的 `got_bytes`，下文代码变量为
`handshake_done`）置位时机 = `conn.getresponse()` 正常返回。
`getresponse()` 返回意味着已从 socket 读入响应头字节（状态行 + 头部）——按
"读到任意字节即进入 mid-stream 域"的保守口径，此后的一切超时（包括 200 后
body 挂起、首个 SSE 数据块等待超时）都归 mid-stream，禁止重试。TCP connect
成功本身不置位（fake-ip 场景 connect 成功只代表本地代理接受连接）。

> 保守口径的代价："200 响应头已到但 body 挂起"的场景放弃救援（上游 200 说明
> 已受理请求，重发计费风险真实存在）。对已知根因场景（链路死挂，连响应头都
> 拿不到）覆盖充分。

**超时预算**：

```python
from config import settings  # 函数内延迟导入（沿用现有模式）

handshake_timeout = min(
    max(settings.PROVIDER_HANDSHAKE_TIMEOUT, 1),
    timeout,                                   # 调用方预算（PROVIDER_TIMEOUT / SYNTHESIZER_TIMEOUT）
    max_seconds,                               # 流总预算
)
conn = conn_cls(parsed.hostname, parsed.port, timeout=handshake_timeout)
```

`conn` 构造的 timeout 参数覆盖 connect + `request()` 发送 + `getresponse()`
等待响应头整段握手期；循环体内现有的 `sock.settimeout(min(timeout, remaining))`
在首个数据块读取前自动切回常规块间空闲预算，无需额外处理。总预算
`remaining <= 0` 的既有检查保持不变（直接抛 `ProviderTimeoutError`，不进
`TimeoutError` 分支，天然不重试）。

**异常分流**：

```python
handshake_done = False
try:
    conn.request(...)
    resp = conn.getresponse()
    handshake_done = True
    ...  # 状态映射、readline 循环照旧
except TimeoutError as exc:  # socket.timeout（3.10+ 即 TimeoutError）
    if not handshake_done:
        raise StreamHandshakeTimeout(f"握手期超时（未收到任何响应字节）: {exc}") from exc
    raise ProviderTimeoutError(f"请求超时: {exc}") from exc
except (http.client.HTTPException, OSError) as exc:
    raise ProviderError(f"网络请求失败: {exc}", code="provider_error") from exc
finally:
    conn.close()
```

连接被拒 / 重置（HTTPException / OSError 非 TimeoutError 分支）不是超时，
不重试，行为与现状一致。

### 4.3 重试包装（providers/adapters.py）

新增共享包装函数，4 个流式调用点（openai_chat 主路径 / openai_chat
stream_options 去参路径 / anthropic / responses）统一改用：

```python
_HANDSHAKE_RETRY_BACKOFF_SECONDS = 0.2  # 固定短退避（模块常量，不设配置）

def _consume_stream_handshake_retry(
    build: Callable[[], Iterator[str]],
    # 以下四个参数与 _consume_stream 签名逐一同名透传，本处省略注解细节
    *,
    extract_delta, terminal, extract_usage, extract_error=None,
) -> tuple[str, dict[str, Any] | None]:
    """消费 SSE 流，仅对握手期超时安全重试（重建全新 HTTP 请求，绝不复用旧连接）。

    build 工厂闭包捕获的 payload / headers / api_key / 协议参数与首次请求
    完全一致（原样重发）；鉴权头完整性由既有 _build_headers / api_key 透传
    保证，评审须专项核对（借鉴流式改造批次 2 教训：桩测试断言捕获的真实入参）。
    0.2s 固定短退避：对秒~分钟级的链路抖动窗口象征意义大于实际，成本为零，
    仅为避免对同一故障节点的瞬时重发冲击。
    """
    attempts = 1 + max(settings.PROVIDER_HANDSHAKE_RETRY_MAX, 0)
    for attempt in range(attempts):
        try:
            content, usage = _consume_stream(build(), ...)
            if attempt > 0:
                default_registry().record_llm_handshake("retry_success")
            return content, usage
        except StreamHandshakeTimeout as exc:
            default_registry().record_llm_handshake("handshake_timeout")
            if attempt >= attempts - 1:
                raise
            logger.warning(
                "流式握手期超时（未收到任何响应字节），%.1fs 后安全重试"
                "（第 %d/%d 次）: %s",
                _HANDSHAKE_RETRY_BACKOFF_SECONDS, attempt + 1, attempts - 1, exc,
            )
            time.sleep(_HANDSHAKE_RETRY_BACKOFF_SECONDS)
    raise AssertionError("unreachable")  # 循环结构保证
```

其余异常（mid-stream `ProviderTimeoutError`、断连 `ProviderError`、错误帧、
`ProtocolError`、`StreamHandshakeRejected`）原样穿透，行为与现状逐字节一致。

调用点改造形态（以 openai_chat 主路径为例）：

```python
content, usage = _consume_stream_handshake_retry(
    lambda: _http_post_sse(url, payload=payload, headers=headers,
                           timeout=timeout, max_seconds=max_seconds,
                           api_key=self.provider.api_key),
    extract_delta=_delta,
    terminal=lambda f: False,
    extract_usage=lambda f: f.get("usage"),
)
```

### 4.4 配置（config/settings.py）

```python
# 流式握手期超时（秒）：连接建立+发送请求+等待响应头的独立预算，
# 与调用方 timeout、流总预算取 min；握手挂起无需硬等满 PROVIDER_TIMEOUT。
PROVIDER_HANDSHAKE_TIMEOUT: int = int(os.getenv("PROVIDER_HANDSHAKE_TIMEOUT", "30"))

# 握手期超时安全重试次数：仅限未收到任何响应字节的超时（防重复计费假设
# 见 spec）；<=0 关闭重试。
PROVIDER_HANDSHAKE_RETRY_MAX: int = int(os.getenv("PROVIDER_HANDSHAKE_RETRY_MAX", "1"))
```

`PROVIDER_HANDSHAKE_RETRY_MAX <= 0` 时 attempts=1，行为退化为"握手超时即上抛"
（仍抛 `StreamHandshakeTimeout`，仍打 `handshake_timeout` 计数）。握手超时配置
无 `<=0` 守卫陷阱（运行时 `max(..., 1)` 钳制，非法值退化为 1s）。

### 4.5 指标埋点（audit/metrics.py + providers/adapters.py）

`MetricsRegistry` 新增：

```python
# LLM 流式握手重试事件（2026-10）：handshake_timeout=握手期超时发生次数 /
# retry_success=重试救回次数 / retry_fail=重试后仍失败次数
self._llm_handshake: Counter[str] = Counter()

def record_llm_handshake(self, kind: str) -> None:
    """登记一次 LLM 流式握手重试事件（handshake_timeout / retry_success / retry_fail）。"""
    with self._lock:
        self._llm_handshake[kind] += 1
```

`snapshot()` 导出 `"llm_handshake": dict(self._llm_handshake)`。

**providers 层直接打点的理由**（分层惯例说明）：项目现状是底层包
（exec / retrieval）不直接依赖 audit，打点归编排侧——原因是那些事件有业务语义
需要编排层翻译。但握手重试是纯传输层事件，"重试成功"对编排层完全不可见（异常被
providers 吞掉后正常返回），强行让编排层转译不存在的信息是伪分层。且
`audit/metrics.py` 自身纯标准库零依赖、零业务语义，`core/orchestrator` 直接
import `audit.logging` / `audit.metrics` 已是项目既有先例，providers→audit.metrics
不引入循环导入（audit 不依赖 providers，已核验）。

## 风险矩阵

| 场景 | 是否重试 | 计费风险 |
| ---- | ---- | ---- |
| 连接建立 / 发送请求 / 等待响应头超时（握手期，未收到任何字节） | 最多 1 次 | 假设成立时为零（见§前提假设） |
| 响应头到达后任何超时（含 200 后 body 挂起、首块等待、块间空闲） | 不重试 | 存在，禁止重试（铁律） |
| mid-stream 断连 / 错误帧 / 协议错误 | 不重试（行为与现状一致） | 存在，禁止重试 |
| HTTP 4xx / 5xx（含握手 400 `StreamHandshakeRejected`） | 不进本分支，按原有映射/回退逻辑 | 不变 |
| 流总预算（300s）耗尽 | 不重试（直接 `ProviderTimeoutError`） | 不变 |

## 错误处理与兼容性

- **最坏延迟核算**：握手挂起且重试也挂起时 30s + 0.2s + 30s = 60.2s，与现有单次
  60s 降级基本持平，用户等待不膨胀；`PROVIDER_HANDSHAKE_TIMEOUT` 与调用方预算取
  min 保证不会突破 `SYNTHESIZER_TIMEOUT` 等更短调用方预算；
- **重试成功**：对用户与编排层完全透明（无感），仅 warning 日志 + 指标留痕；
- **重试失败**：上抛 `StreamHandshakeTimeout`（code="timeout"），走既有
  `_llm_json` 捕获降级 → `planner_llm_error` / `_LLM_JSON_LAST_ERROR` →
  `_degradation_banner` 透出原因链路，前端文案与水印零改动；
- **异常类型判定矩阵**：现有 `except ProviderTimeoutError` 捕获面、
  `_stream_fallback_eligible` 回退资格、双协议错误帧识别均不受新异常影响。

## 测试计划（tests/test_provider_handshake_retry.py 新建）

1. **异常体系**：`StreamHandshakeTimeout` 是 `ProviderTimeoutError` 子类且
   `code == "timeout"`；不是 `StreamHandshakeRejected` 子类；
   `_stream_fallback_eligible(实例)` 恒 False；
2. **握手期判定**（假连接对象注入，控制 getresponse / readline 行为）：
   - connect / getresponse 阶段 TimeoutError → `StreamHandshakeTimeout`；
   - 重试一次后成功返回 content，指标记录 `handshake_timeout` + `retry_success`；
   - 两次握手超时 → 上抛 `StreamHandshakeTimeout`，指标记录 `retry_fail`；
3. **mid-stream 禁重试**：getresponse 成功后首行 readline 超时 / 块间超时 →
   `ProviderTimeoutError` 原样上抛、零重试、零 `retry_*` 计数；
4. **预算语义**：`PROVIDER_HANDSHAKE_TIMEOUT > 调用方 timeout` 时取 min；
   握手期耗时计入流总预算 remaining；
5. **配置开关**：`RETRY_MAX=0` 时不重试直接上抛（仍计 `handshake_timeout`）；
6. **三协议参数化**：openai_chat（主路径 + stream_options 去参路径）、anthropic、
   responses 各至少覆盖握手重试成功与 mid-stream 不重试各一例；
7. **鉴权头专项**（流式改造教训）：桩断言重试请求捕获的 headers / api_key /
   payload 与首次完全一致；
8. **指标接口**：`record_llm_handshake` 锁内计数 + `snapshot()["llm_handshake"]`
   导出（仿 `test_degraded_fallback.py` 指标测试模式）；
9. **回归**：全量 `python -m pytest -q` 绿——重点确认 8 处 `_llm_json`
   monkeypatch 测试桩与流式改造既有测试无回归。

## 改动面清单

| 文件 | 改动 | 规模 |
| ---- | ---- | ---- |
| `providers/errors.py` | 新增 `StreamHandshakeTimeout`（含 docstring 扩展预留注释） | ~15 行 |
| `providers/adapters.py` | `_http_post_sse` 握手分流 + `_consume_stream_handshake_retry` + 4 调用点改造 | ~70 行 |
| `config/settings.py` | 新增 2 项配置 | ~8 行 |
| `audit/metrics.py` | 新增 `record_llm_handshake` + snapshot 导出 | ~12 行 |
| `tests/test_provider_handshake_retry.py` | 新建（上述 9 组用例） | ~300 行 |
| `docs/superpowers/specs/` | 本设计文档 | — |

不改动：编排层（core/orchestrator）、web 层、前端、非流式 `_http_post` 路径、
`_llm_json` 及其测试桩格局。
