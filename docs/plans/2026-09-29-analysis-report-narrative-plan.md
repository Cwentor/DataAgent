# 分析报告叙述化修复 实施计划

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。

**Goal:** 让诊断分析报告在 LLM 可用时稳定产出叙述文本（根因：读超时 60s 硬编码误杀长文生成），在 LLM 不可用时兜底渲染达到"中文小节 + 人读表格 + 数值三分格式化"水平，且降级可感知。

**Architecture:** 三层修复——(1) providers 网关：`UnifiedChatRequest` 增加 `timeout` 字段全链路透传，六处 `_http_post(timeout=60)` 硬编码改为读 `settings.PROVIDER_TIMEOUT`，报告综合调用使用专用预算 `settings.SYNTHESIZER_TIMEOUT`（默认 180s）；(2) synthesize 链路：`_synthesize_with_llm` 返回 `(报告, 失败原因)`，失败时兜底报告头部注入降级标注；(3) 渲染层：`_summary_analyst_markdown` 防御式重写，嵌套结构表格化、`_metric_human` 三分格式化、小节标题中文回退。

**Tech Stack:** Python 3.12 / pydantic v2（`UnifiedChatRequest`）/ http.client（`_http_post`）/ pytest + monkeypatch（`_http_post` 打桩、`_resolve_llm` 打桩）。

**Spec:** `docs/superpowers/specs/2026-09-29-analysis-report-narrative-design.md`（执行者需同时读两者）。

## Global Constraints

- 所有新增/修改 Python 代码的 docstring 与行内注释使用简体中文（专有名词保留英文）；
- 严禁在报告中 repr 直出任何 dict/list（`{"baseline": ...}`、`week1 = {'gmv': ...}` 形态）；
- 现有测试锚点不可破坏：`66.93 万元`、`| 北京 |`、`report.count("### 两期对比与分省归因") == 1`；
- `black --check .`、`ruff check .`、`python -m pytest -q` 提交前全绿；
- 工作区已有 `web/static/js/stream-ui.js`、`web/static/style.css` 未提交改动属另一事项，**严禁 git add 这两个文件**；
- 工作分支：`feat/report-narrative-fix`（已存在，勿新建）。

## Review Focus

1. metrics 值为嵌套 dict/list（截图 `week1`/`log_decomp`/`province_delta_top` 形态）→ 必须表格化渲染，严禁 repr（Task 4 测试踩中）；
2. 比率键（`*_pct/*_rate/share/ratio`）→ 百分比一位小数，严禁误转万元（Task 4 测试踩中）；
3. summary title 为英文 id（step.id 或 Coder 自拟）→ 小节标题回退步骤中文 goal 或"归因分析（id）"（Task 4 测试踩中）；
4. 旧调用点不传 `timeout`（Planner/Coder/Router/探测）→ 行为不变：读超时回退 `settings.PROVIDER_TIMEOUT` 默认 60（Task 2 测试踩中）；
5. summary 结构畸形（`metrics=None`、行混合类型、非 dict 行）→ 降级渲染，不崩溃、不 dump（Task 4 测试踩中）。

---

### Task 1: 超时配置与请求模型签名层

**Files:**
- Modify: `config/settings.py`（`PROVIDER_TIMEOUT` 段之后，约 153 行）
- Modify: `providers/models.py:83-91`（`UnifiedChatRequest`）
- Modify: `providers/adapters.py:205-231`（`BaseAdapter.chat_text`）
- Modify: `providers/context.py:64-72`（`DispatchingAdapter.chat_text`）
- Modify: `providers/__init__.py:62-89`（`chat_text` 门面）
- Test: `tests/test_providers.py`（文件末尾追加）

**Interfaces:**
- Consumes: 无（首个任务）。
- Produces: `UnifiedChatRequest.timeout: int | None`；`BaseAdapter.chat_text(messages, *, model, json_mode, timeout)`；`providers.chat_text(client, messages, *, model, json_mode, timeout)`。Task 2 的适配器接线与 Task 3 的综合调用依赖这些签名。

- [ ] **Step 1: 写失败测试**

在 `tests/test_providers.py` 末尾追加（文件已 import `json`、`pytest`、`ProviderConfig` 相关与适配器类；若 `_provider()` 辅助不存在于文件中，参照同文件既有适配器测试的 provider 构造方式）：

```python
# --------------------------------------------------------------------------- #
# 超时透传（2026-09 报告叙述化修复）
# --------------------------------------------------------------------------- #
def test_unified_chat_request_accepts_timeout():
    from providers.models import UnifiedChatRequest

    req = UnifiedChatRequest(
        messages=[{"role": "user", "content": "x"}], model="m-1", timeout=180
    )
    assert req.timeout == 180
    default = UnifiedChatRequest(messages=[{"role": "user", "content": "x"}], model="m-1")
    assert default.timeout is None


def test_base_adapter_chat_text_carries_timeout(monkeypatch):
    from providers.adapters import OpenAIChatAdapter
    from providers.models import UnifiedChatResponse

    seen: dict = {}

    def fake_chat(self, request):
        seen["timeout"] = request.timeout
        return UnifiedChatResponse(content="ok")

    monkeypatch.setattr(OpenAIChatAdapter, "chat", fake_chat)
    adapter = OpenAIChatAdapter(_provider(), "m-1")
    text = adapter.chat_text(
        [{"role": "user", "content": "hi"}], json_mode=False, timeout=180
    )
    assert text == "ok"
    assert seen["timeout"] == 180


def test_chat_facade_forwards_timeout():
    from providers import chat_text

    seen: dict = {}

    class _Fake:
        def chat_text(self, messages, *, model=None, json_mode=True, timeout=None):
            seen["timeout"] = timeout
            return "ok"

    result = chat_text(_Fake(), [{"role": "user", "content": "x"}], timeout=180)
    assert result == "ok"
    assert seen["timeout"] == 180
```

> 说明：`_provider()` 为 `tests/test_providers.py` 既有辅助函数。分发代理（`DispatchingAdapter`）的全链路超时转发验证放在 Task 2（需适配器接线后 `_http_post` 才能捕获到真实超时值）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_providers.py -k "timeout" -v`
Expected: FAIL（`UnifiedChatRequest` 无 `timeout` 字段 / `chat_text()` got an unexpected keyword argument `'timeout'`）

- [ ] **Step 3: 最小实现**

`config/settings.py`：在 `PROVIDER_TIMEOUT` 定义（约 153 行）之后追加：

```python
# 报告综合（Synthesizer）专用读超时（秒）：长文生成耗时 > 常规调用，独立预算；
# 配置 <=0 视为非法，回落默认 180
SYNTHESIZER_TIMEOUT: int = int(os.getenv("SYNTHESIZER_TIMEOUT", "180"))
if SYNTHESIZER_TIMEOUT <= 0:
    SYNTHESIZER_TIMEOUT = 180
```

`providers/models.py` 的 `UnifiedChatRequest`（保留 `model_config` 与既有字段不动）：

```python
class UnifiedChatRequest(BaseModel):
    """适配器统一入参（协议无关的对话请求）。"""

    model_config = {"extra": "forbid"}

    messages: list[UnifiedMessage]
    model: str
    temperature: float | None = None
    response_format: ResponseFormat | None = None
    # 读超时覆盖（秒）：None = 网关默认（settings.PROVIDER_TIMEOUT）；
    # 报告综合等长文生成调用传更大预算（Task 2 起在适配器内接线生效）
    timeout: int | None = None
```

`providers/adapters.py` 的 `BaseAdapter.chat_text`（docstring 保留原有内容，末尾补一行超时说明）：

```python
    def chat_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        json_mode: bool = True,
        timeout: int | None = None,
    ) -> str:
        """旧形态便捷入口：messages 列表 -> 文本；默认启用 JSON Mode。

        供 agent 层既有调用点（LLMNL2DSL / LLMPlanner 等）透明切换，
        无需改动其内部消息构造逻辑。

        温度显式接线 settings.LLM_TEMPERATURE（缺省 0.0）：agent 层全部 LLM
        调用（规划/总结/反思/自愈）的温度由此统一控制，评测确定性锁定
        （eval_runner._lock_determinism）才能真正落到请求层。

        timeout：本次调用读超时覆盖（秒），None 时由适配器回退网关默认。
        """
        from config import settings

        resp = self.chat(
            UnifiedChatRequest(
                messages=[{"role": m["role"], "content": m["content"]} for m in messages],
                model=model or self.model_id,
                temperature=settings.LLM_TEMPERATURE,
                response_format={"type": "json_object"} if json_mode else None,
                timeout=timeout,
            )
        )
        return resp.content
```

`providers/context.py` 的 `DispatchingAdapter.chat_text`：

```python
    def chat_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        json_mode: bool = True,
        timeout: int | None = None,
    ) -> str:
        """兼容旧形态便捷入口：转发到当前请求的真实适配器。"""
        return self._resolve(model).chat_text(
            messages, model=model, json_mode=json_mode, timeout=timeout
        )
```

`providers/__init__.py` 的 `chat_text` 门面（签名与分发均加 `timeout`，docstring 补一句"timeout 为本次调用读超时覆盖（秒）"）：

```python
def chat_text(
    client: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    json_mode: bool = True,
    timeout: int | None = None,
) -> str:
    """统一对话入口，三形态透明分发：

    - Model Provider 适配器（``BaseAdapter``）走 UnifiedChatRequest 统一接口；
    - 请求感知分发代理（``DispatchingAdapter``）按请求上下文转发真实适配器；
    - 旧形态 client（测试桩 / 既有 OpenAICompatClient）走 ``chat(messages) -> str``。

    timeout：本次调用读超时覆盖（秒），None 时适配器回退网关默认
    （settings.PROVIDER_TIMEOUT）；报告综合等长文生成调用传更大预算。
    """
    if isinstance(client, (BaseAdapter, DispatchingAdapter)):
        return client.chat_text(messages, model=model, json_mode=json_mode, timeout=timeout)
    return client.chat(messages)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_providers.py -v`
Expected: 全部 PASS（含既有测试——旧调用点不传 timeout 行为不变）

- [ ] **Step 5: Commit**

```bash
git add config/settings.py providers/models.py providers/adapters.py providers/context.py providers/__init__.py tests/test_providers.py
git commit -m "feat(providers): 对话请求支持 timeout 透传，新增 SYNTHESIZER_TIMEOUT 配置"
```

---

### Task 2: 适配器 `_http_post` 超时接线

**Files:**
- Modify: `providers/adapters.py`（六处 `_http_post(..., timeout=60)`：行 305、422、521、568、620、664；及 `OpenAIChatAdapter._post:293-316`、`OpenAIResponsesAdapter._post:410-432` 签名）
- Test: `tests/test_providers.py`（文件末尾追加）

**Interfaces:**
- Consumes: Task 1 的 `UnifiedChatRequest.timeout`。
- Produces: 适配器行为契约——`chat()` 路径超时 = `request.timeout or settings.PROVIDER_TIMEOUT`；`test_connection()` 路径 = `settings.PROVIDER_TIMEOUT`。Task 3 依赖"综合调用超时可放宽到 180s"。

- [ ] **Step 1: 写失败测试**

`tests/test_providers.py` 末尾追加：

```python
def _timeout_stub(captured: dict):
    """捕获 timeout 实参并返回 OpenAI Chat 形态成功响应的 _http_post 替身。"""

    def stub(url, *, payload, headers, timeout, api_key=None):
        captured["timeout"] = timeout
        return 200, {}, json.dumps({"choices": [{"message": {"content": "ok"}}]})

    return stub


def test_openai_chat_timeout_defaults_to_settings(monkeypatch):
    from config import settings
    from providers.adapters import OpenAIChatAdapter
    from providers.models import UnifiedChatRequest

    captured: dict = {}
    monkeypatch.setattr(settings, "PROVIDER_TIMEOUT", 61)  # 与硬编码 60 区分
    monkeypatch.setattr("providers.adapters._http_post", _timeout_stub(captured))
    adapter = OpenAIChatAdapter(_provider(), "m-1")
    adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "x"}], model="m-1")
    )
    assert captured["timeout"] == 61


def test_dispatching_chat_text_timeout_end_to_end(monkeypatch, tmp_path):
    """分发代理全链路：chat_text(timeout=180) 经真实适配器落到 _http_post。"""
    from providers import factory as factory_mod
    from providers.context import dispatching_adapter, reset_dispatching_adapter

    store = ProviderStore(tmp_path / "providers.json")
    store.create_provider(
        {
            "id": "primary",
            "name": "A-Primary",
            "base_url": "https://primary-gw/v1",
            "api_key": fake_key("primary"),
            "models": [{"id": "p-default"}],
        }
    )
    isolated = ProviderFactory(store)
    monkeypatch.setattr(factory_mod, "_default_factory", isolated)
    reset_dispatching_adapter()
    captured: dict = {}

    def spy(url, *, payload, headers, timeout, api_key=None):
        captured["timeout"] = timeout
        return 200, {}, json.dumps({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("providers.adapters._http_post", spy)
    proxy = dispatching_adapter()
    proxy.chat_text([{"role": "user", "content": "q"}], json_mode=False, timeout=180)
    assert captured["timeout"] == 180


def test_openai_chat_timeout_request_override(monkeypatch):
    from providers.adapters import OpenAIChatAdapter
    from providers.models import UnifiedChatRequest

    captured: dict = {}
    monkeypatch.setattr("providers.adapters._http_post", _timeout_stub(captured))
    adapter = OpenAIChatAdapter(_provider(), "m-1")
    adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "x"}], model="m-1", timeout=180
        )
    )
    assert captured["timeout"] == 180


def test_anthropic_chat_timeout_override(monkeypatch):
    from providers.adapters import AnthropicAdapter
    from providers.models import UnifiedChatRequest

    captured: dict = {}

    def stub(url, *, payload, headers, timeout, api_key=None):
        captured["timeout"] = timeout
        return 200, {}, json.dumps({"content": [{"type": "text", "text": "ok"}]})

    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = AnthropicAdapter(_provider(), "m-1")
    adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "x"}], model="m-1", timeout=180
        )
    )
    assert captured["timeout"] == 180


def test_gemini_chat_timeout_override(monkeypatch):
    from providers.adapters import GeminiAdapter
    from providers.models import UnifiedChatRequest

    captured: dict = {}

    def stub(url, *, payload, headers, timeout, api_key=None):
        captured["timeout"] = timeout
        return 200, {}, json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        )

    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = GeminiAdapter(_provider(), "m-1")
    adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "x"}], model="m-1", timeout=180
        )
    )
    assert captured["timeout"] == 180


def test_openai_responses_chat_timeout_override(monkeypatch):
    from providers.adapters import OpenAIResponsesAdapter
    from providers.models import UnifiedChatRequest

    captured: dict = {}

    def stub(url, *, payload, headers, timeout, api_key=None):
        captured["timeout"] = timeout
        return 200, {}, json.dumps(
            {"output": [{"content": [{"type": "output_text", "text": "ok"}]}]}
        )

    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIResponsesAdapter(_provider(), "m-1")
    adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "x"}], model="m-1", timeout=180
        )
    )
    assert captured["timeout"] == 180
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_providers.py -k "timeout" -v`
Expected: 新增测试 FAIL——defaults 断言 captured 为 60 而非 61；override/分发代理断言 captured 为 60 而非 180（接线尚未实现）。

- [ ] **Step 3: 最小实现**

`providers/adapters.py`：

(a) `OpenAIChatAdapter.chat`（行 284）把请求超时传入 `_post`：

```python
        from config import settings

        # 读超时：请求级覆盖优先，回退网关默认（PROVIDER_TIMEOUT 配置面）
        timeout = request.timeout or settings.PROVIDER_TIMEOUT
        status, _, raw = self._post(base_payload, want_json=want_json, timeout=timeout)
```

(b) `OpenAIChatAdapter._post` 签名加 `timeout`（递归降级重试保持同一超时）：

```python
    def _post(
        self,
        payload: dict[str, Any],
        *,
        want_json: bool,
        timeout: int,
        attempt_native: bool = True,
    ) -> tuple[int, dict[str, str], str]:
        """发送请求；JSON 原生参数被拒（400）时去掉后重试一次（仅留 Prompt 约束）。"""
        body: dict[str, Any] = payload
        if want_json and attempt_native:
            body = {**payload, "response_format": {"type": "json_object"}}
        url = f"{self.provider.base_url.rstrip('/')}/chat/completions"
        headers = {**self._build_headers()}
        if self.provider.api_key:
            headers["Authorization"] = f"Bearer {self.provider.api_key}"
        try:
            return _http_post(url, payload=body, headers=headers, timeout=timeout, api_key=None)
        except ProviderError as exc:
            if (
                want_json
                and attempt_native
                and isinstance(exc, ProviderError)
                and exc.code == "provider_error"
                and any(h in str(exc).lower() for h in _UNSUPPORTED_JSON_HINTS)
            ):
                # 模型/中转不支持 response_format -> 降级：仅保留 Prompt 约束重试
                return self._post(payload, want_json=want_json, timeout=timeout, attempt_native=False)
            raise
```

同文件 `OpenAIChatAdapter.test_connection`（行 327）的 `self._post(payload, want_json=False)` 改为 `self._post(payload, want_json=False, timeout=settings.PROVIDER_TIMEOUT)`（函数内补 `from config import settings`）。

(c) `OpenAIResponsesAdapter.chat`（行 395）与 `_post`（行 410-432）：与 (a)(b) 同构改造——`chat` 内计算 `timeout` 传入；`_post` 加 `timeout` 关键字参数，行 422 `timeout=timeout`，递归行 431 `timeout=timeout`；`test_connection`（行 443）传 `timeout=settings.PROVIDER_TIMEOUT`。

(d) `AnthropicAdapter.chat`（行 521 之前）加：

```python
        from config import settings

        # 读超时：请求级覆盖优先，回退网关默认（PROVIDER_TIMEOUT 配置面）
        timeout = request.timeout or settings.PROVIDER_TIMEOUT
        status, _, raw = _http_post(url, payload=payload, headers=headers, timeout=timeout)
```

`test_connection`（行 568）改为 `timeout=settings.PROVIDER_TIMEOUT`（函数内补 import）。

(e) `GeminiAdapter.chat`（行 620 之前）与 `test_connection`（行 664）：与 (d) 同构改造。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_providers.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add providers/adapters.py tests/test_providers.py
git commit -m "feat(providers): 适配器读超时接入 PROVIDER_TIMEOUT 配置与请求级覆盖"
```

---

### Task 3: 综合链路失败原因透出 + 长超时 + 降级标注

**Files:**
- Modify: `core/orchestrator/nodes.py:1891-1935`（`_synthesize_with_llm`）、`1957-1968`（`_degraded_report` 内 chat_text）、`2235-2297`（`synthesize_node` 综合与兜底段）
- Test: `tests/test_synthesizer_rebuild.py`（文件末尾追加）

**Interfaces:**
- Consumes: Task 1/2 的 `chat_text(..., timeout=...)`。
- Produces: `_synthesize_with_llm(state, material, extra_instruction=None) -> tuple[str | None, str | None]`——成功 `(文本, None)`，LLM 未配置 `(None, None)`（离线常态不算降级），调用失败 `(None, 原因摘要)`。Task 4 依赖"兜底报告头部可能带降级标注"的拼接顺序（标注在最前）。

- [ ] **Step 1: 写失败测试**

`tests/test_synthesizer_rebuild.py` 末尾追加：

```python
# --------------------------------------------------------------------------- #
# 2026-09 报告叙述化：LLM 综合失败 => 降级可见（禁止静默落兜底）
# --------------------------------------------------------------------------- #
class _TimeoutLLM:
    """模拟网关读超时的 LLM 桩（ProviderTimeoutError 为网关标准错误）。"""

    def chat(self, messages):
        from providers import ProviderTimeoutError

        raise ProviderTimeoutError("请求超时: The read operation timed out")


def test_synthesize_timeout_falls_back_with_banner(tmp_path, monkeypatch):
    """LLM 综合超时 => 兜底报告头部带降级标注与失败原因，数值仍人读渲染。"""
    from config import settings
    from core.orchestrator import nodes as orch_nodes
    from core.orchestrator.nodes import synthesize_node

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch_nodes, "_resolve_llm", lambda: _TimeoutLLM())
    out = synthesize_node(_summary_state(tmp_path))
    assert "本次报告由确定性模板生成" in out.report
    assert "ProviderTimeoutError" in out.report
    assert "66.93 万元" in out.report  # 兜底渲染仍走人读格式


def test_synthesize_no_llm_means_no_banner(tmp_path, monkeypatch):
    """LLM 未配置（离线常态）=> 不打降级标注（未尝试综合不算失败）。"""
    from config import settings
    from core.orchestrator import nodes as orch_nodes
    from core.orchestrator.nodes import synthesize_node

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch_nodes, "_resolve_llm", lambda: None)
    out = synthesize_node(_summary_state(tmp_path))
    assert "本次报告由确定性模板生成" not in out.report
    assert "66.93 万元" in out.report


def test_synthesize_llm_contract_output_marks_banner(tmp_path, monkeypatch):
    """LLM 回吐反契约 JSON => 同样视为综合失败，兜底报告带标注。"""
    import json as _json

    from config import settings
    from core.orchestrator import nodes as orch_nodes
    from core.orchestrator.nodes import synthesize_node

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    raw = _json.dumps({"baseline": 669300.0, "current": 616800.0}, ensure_ascii=False)
    monkeypatch.setattr(orch_nodes, "_resolve_llm", lambda: _FakeLLM(raw))
    out = synthesize_node(_summary_state(tmp_path))
    assert "本次报告由确定性模板生成" in out.report
    assert "反契约" in out.report
    assert '{"baseline"' not in out.report
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_synthesizer_rebuild.py -k "banner" -v`
Expected: FAIL（`out.report` 中无"本次报告由确定性模板生成"——当前实现静默落兜底）

- [ ] **Step 3: 最小实现**

(a) `core/orchestrator/nodes.py` 的 `_synthesize_with_llm`（整函数替换）：

```python
def _synthesize_with_llm(
    state: AgentState, material: str, extra_instruction: str | None = None
) -> tuple[str | None, str | None]:
    """LLM 商业分析师综合（审计修复 R1）；失败返回 (None, 失败原因) 走确定性兜底。

    返回 (report, failure_reason)：
    - 成功 => (报告文本, None)；
    - LLM 未配置 => (None, None)——离线/测试为常态，不计为降级；
    - 调用失败/空输出/反契约 => (None, 原因摘要)——原因用于兜底报告
      头部的降级标注（降级不可静默，2026-09 报告叙述化修复）。

    读超时使用 SYNTHESIZER_TIMEOUT 专用预算：报告为长文生成，
    常规 60s 网关默认会误杀（根因见设计文档）。
    ``extra_instruction``：Grounding 定向重写的修正指令（二期）——拼在
    user_prompt 末尾，仅约束本次调用。
    """
    from config import settings

    llm = _resolve_llm()
    if llm is None:
        return None, None
    user_prompt = (
        f"# 用户问题\n{state.user_query}\n\n# 上游分析材料（唯一数据事实来源）\n{material}\n\n"
        "# 现在，按四段式结构输出最终分析报告（Markdown）。"
    )
    if extra_instruction:
        user_prompt += "\n\n" + extra_instruction
    try:
        from agent.agent import extract_json
        from providers import chat_text

        text = chat_text(
            llm,
            [
                {"role": "system", "content": SYNTHESIZER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=False,
            timeout=settings.SYNTHESIZER_TIMEOUT,
        )
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"[:200]
        logger.warning(
            "LLM 商业分析师综合失败，走确定性分析师兜底",
            extra={"error": reason},
        )
        return None, reason
    if not text or not text.strip():
        logger.warning("LLM 商业分析师综合返回空文本，走确定性分析师兜底")
        return None, "空输出"
    stripped = text.strip()
    # 反契约输出防御：LLM 若仍回 JSON（被 extract_json 成功解析），视为失败走兜底
    if stripped.startswith("{") or stripped.startswith("["):
        if extract_json(stripped) is not None:
            logger.warning("LLM 综合输出反契约（JSON 形态），走确定性分析师兜底")
            return None, "输出反契约（JSON 形态）"
    return stripped, None
```

(b) `_degraded_report` 内 `chat_text(...)` 调用（行 1961-1968 附近）补 `timeout=settings.SYNTHESIZER_TIMEOUT`（函数已 import settings）。

(c) `synthesize_node` 综合段（行 2235-2283）改为双返回值适配，Grounding 重试失败时记录原因：

```python
    # LLM 商业分析师综合（R1）；素材 = 去重后的 summary 产物 + 数据集预览
    llm_report: str | None = None
    synthesize_failure: str | None = None
    if deduped_summaries or state.datasets:
        material = _analysis_material(state, include_trace=bool(state.error_context.errors))
        if material:
            llm_report, synthesize_failure = _synthesize_with_llm(state, material)
            if llm_report:
                # （Grounding 定向重试闭环保持现状逻辑，仅把两处
                #  _synthesize_with_llm 调用改为 tuple 解包）
```

其中行 2261-2263 的重试调用改为：

```python
                    retry_report, _retry_reason = _synthesize_with_llm(
                        state, material, extra_instruction=retry_instruction
                    )
```

行 2278-2283 的 `if not retry_ok:` 分支在 `llm_report = None` 后追加一行：

```python
                        synthesize_failure = "数值溯源重写后仍超阈值"
```

(d) 兜底路径头部（行 2294-2300）在 heuristic banner 之后插入降级标注：

```python
    # ---- 确定性分析师兜底（无 LLM / LLM 输出反契约）---- #
    lines: list[str] = []
    banner = _degradation_banner(state)
    if banner:
        lines.append(banner.rstrip("\n"))
        lines.append("")
    # 降级不可静默：曾尝试 LLM 综合且失败时，头部明示渲染方式与原因
    if synthesize_failure:
        lines.append(
            "> ⚠️ **本次报告由确定性模板生成**：LLM 商业分析师综合不可用"
            f"（原因：{synthesize_failure}）。以下数值均来自真实查询结果，叙述深度有限。"
        )
        lines.append("")
    lines.append(f"## 分析报告：{state.user_query}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_synthesizer_rebuild.py tests/test_orchestrator.py tests/test_orchestrator_events.py -v`
Expected: 全部 PASS（既有 `test_synthesize_rejects_raw_json_llm_output` 因新增 banner 不断言其排除项，仍通过）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_synthesizer_rebuild.py
git commit -m "feat(orchestrator): LLM 综合失败原因透出与兜底报告降级标注，综合调用启用专用超时预算"
```

---

### Task 4: 兜底渲染人话化重写

**Files:**
- Modify: `core/orchestrator/nodes.py:1806-1854`（`_summary_analyst_markdown`/`_COUNT_METRIC_KEYS`/`_metric_human` 重写）、`1857-1888`（`_render_table` 格式化扩展）、`2301-2304`（调用点传 state + 渲染隔离）
- Test: `tests/test_report_narrative.py`（新建）

**Interfaces:**
- Consumes: Task 3 的降级标注（拼接顺序：banner → 降级标注 → `## 分析报告：` → 各小节）。
- Produces: `_summary_analyst_markdown(summary, state=None) -> list[str]`；`_section_title(summary, state=None) -> str`；`_metrics_lines(metrics) -> list[str]`；`_metric_label(key) -> str`；`_metric_human(value, key="") -> str`（表格单元格复用同函数）。供同文件 synthesize 兜底路径调用。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_report_narrative.py`：

```python
"""报告叙述化修复（2026-09）回归锚点。

- 确定性渲染：嵌套结构表格化、数值三分格式化、小节标题中文化——严禁 repr dump；
- 小节标题：中文直用 / 英文 id 回退步骤 goal / 无匹配回退"归因分析（id）"；
- 渲染隔离：畸形 summary 降级为占位文案，不拖垮整份报告。
"""

from __future__ import annotations

from core.orchestrator.state import AgentState, Artifact, PlanStep


# --------------------------------------------------------------------------- #
# 截图同款形态的状态构造：英文 title + 嵌套 metrics
# --------------------------------------------------------------------------- #
def _diag_state() -> AgentState:
    return AgentState(
        session_id="nar",
        turn_id="t1",
        trace_id="tr1",
        user_query="分析一下 2024 年 5 月第二周比第一周 GMV 下滑的原因，按地区定位",
        plan_steps=[
            PlanStep(
                id="factor_decomposition",
                goal="乘法因子分解：定位量跌还是价跌",
                kind="analyze",
            ),
            PlanStep(
                id="region_drilldown",
                goal="地区维度下钻：定位主要下滑省份",
                kind="analyze",
            ),
        ],
        artifacts=[
            Artifact(
                kind="summary",
                name="s1",
                payload={
                    "summary": {
                        "title": "factor_decomposition",
                        "findings": ["GMV 从 61.69 万元 变至 41.02 万元，下滑 -33.5%"],
                        "metrics": {
                            "week1": {"gmv": 616872.81, "orders": 44.0, "buyers": 39.0},
                            "week2": {"gmv": 410248.48, "orders": 32.0, "buyers": 30.0},
                            "gmv_change_pct": -0.3347,
                            "orders_per_buyer": 1.1282,
                            "volume_vs_price": "量跌为主",
                            "province_delta_top": [
                                {
                                    "province": "北京",
                                    "gmv_w1": 112791.59,
                                    "gmv_w2": 10140.62,
                                    "gmv_delta": -107150.97,
                                },
                                {
                                    "province": "湖北",
                                    "gmv_w1": 124300.98,
                                    "gmv_w2": 78142.17,
                                    "gmv_delta": -46158.81,
                                },
                            ],
                        },
                        "table": {},
                    }
                },
            )
        ],
    )


def _render_report(monkeypatch, tmp_path, state: AgentState) -> str:
    from config import settings
    from core.orchestrator import nodes as orch_nodes
    from core.orchestrator.nodes import synthesize_node

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(orch_nodes, "_resolve_llm", lambda: None)
    return synthesize_node(state).report


def test_fallback_render_no_repr_dump(tmp_path, monkeypatch):
    """嵌套 metrics 渲染为表格，严禁 repr 与长浮点直出。"""
    report = _render_report(monkeypatch, tmp_path, _diag_state())
    assert "{'gmv'" not in report  # 禁 dict repr
    assert "616872.81" not in report  # 禁长浮点直出（必须舍入/万元化）
    assert "1.1282051282051282" not in report
    assert "41.02 万元" in report  # week2 gmv 万元化（对比表）
    assert "-33.5%" in report  # 比率百分比化（严禁 "-0.33 万元" 类错乱）
    assert "| 北京 |" in report  # province_delta_top 表格化
    assert "-10.72 万元" in report  # gmv_delta 万元化


def test_fallback_render_section_title_falls_back_to_step_goal(tmp_path, monkeypatch):
    """英文 title（step.id）=> 小节标题回退计划步骤中文 goal。"""
    report = _render_report(monkeypatch, tmp_path, _diag_state())
    assert "### 乘法因子分解：定位量跌还是价跌" in report
    assert "### factor_decomposition" not in report


def test_section_title_rules():
    """_section_title 三级回退：中文直用 / id 匹配 goal / 无匹配括注 id。"""
    from core.orchestrator.nodes import _section_title

    state = _diag_state()
    assert _section_title({"title": "驱动因子分解"}, state) == "驱动因子分解"
    assert (
        _section_title({"title": "region_drilldown"}, state)
        == "地区维度下钻：定位主要下滑省份"
    )
    assert _section_title({"title": "coder_custom_name"}, state) == "归因分析（coder_custom_name）"
    assert _section_title({}, None) == "归因分析"


def test_metric_human_type_discipline():
    """数值三分：比率百分比、计数原值、金额万元、未知数值舍入、字符串原样。"""
    from core.orchestrator.nodes import _metric_human

    assert _metric_human(-0.3347, "gmv_change_pct") == "-33.5%"
    assert _metric_human(-0.65, "share") == "-65.0%"
    assert _metric_human(616872.81, "gmv") == "61.69 万元"
    assert _metric_human(44.0, "orders") == "44"
    assert _metric_human(1.1282, "orders_per_buyer") == "1.13"  # 未知比值：舍入原样
    assert _metric_human("量跌为主", "volume_vs_price") == "量跌为主"


def test_malformed_summary_degrades_without_crash(tmp_path, monkeypatch):
    """畸形 summary（metrics=None、行混合类型）=> 渲染不崩溃、不 dump。"""
    state = AgentState(
        session_id="nar2",
        turn_id="t1",
        trace_id="tr2",
        user_query="畸形产物渲染",
        artifacts=[
            Artifact(
                kind="summary",
                name="s1",
                payload={
                    "summary": {
                        "title": "weird_step",
                        "metrics": None,
                        "findings": ["只有一条结论"],
                        "table": {"columns": ["k", "v"], "rows": [["a", 1], "not-a-row"]},
                    }
                },
            )
        ],
    )
    report = _render_report(monkeypatch, tmp_path, state)
    assert "只有一条结论" in report
    assert "{" not in report  # 不 dump
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_report_narrative.py -v`
Expected: FAIL（当前实现 repr dump：`{'gmv'` 出现在报告中；英文小节标题存在）

- [ ] **Step 3: 最小实现**

(a) `core/orchestrator/nodes.py` 顶部 import 区确认有 `import re`（若无需补）。

(b) 用以下内容替换行 1843-1854（`_COUNT_METRIC_KEYS` 常量与 `_metric_human`）：

```python
# 指标渲染分类 token（键/列名小写包含匹配；判定顺序：比率 -> 计数 -> 金额）。
# 2026-09 报告叙述化修复：此前仅排除计数键，比率键 gmv_change_pct 被误转
# "万元"（-0.00 万元）；未知数值键一律舍入原样，严禁臆断单位。
_RATIO_TOKENS = ("pct", "rate", "share", "ratio", "gain")
_COUNT_METRIC_KEYS = ("orders", "buyers", "count", "users", "quantity", "qty")
_MONEY_TOKENS = ("gmv", "aov", "amount", "revenue", "baseline", "current", "delta", "value")

# 高频指标键 -> 中文标签（渲染层兜底映射；语义目录 COLUMNS 能命中的优先查目录）
_METRIC_LABELS: dict[str, str] = {
    "baseline": "基期",
    "current": "现期",
    "delta": "变化量",
    "gmv": "GMV",
    "orders": "订单数",
    "buyers": "买家数",
    "orders_per_buyer": "人均订单数",
    "aov": "客单价",
    "gmv_change_pct": "GMV 环比",
    "change_rate": "变化率",
    "share": "贡献占比",
    "contribution_share": "贡献占比",
    "volume_vs_price": "量价定性",
    "week1": "第 1 周",
    "week2": "第 2 周",
    "factor": "因子",
    "province": "省份",
    "log_decomp": "对数贡献分解",
}


def _metric_label(key: str) -> str:
    """指标键 -> 中文标签：语义目录命中优先，渲染层映射兜底，未知键原样。"""
    try:
        from semantic.catalog import COLUMNS

        meta = COLUMNS.get(key)
        if meta is not None and meta.label:
            return meta.label
    except Exception:  # 目录不可用不阻断渲染
        pass
    return _METRIC_LABELS.get(key, key)


def _is_ratio_key(key: str) -> bool:
    """键/列名是否为比率类（pct/rate/share/ratio/gain）。"""
    lowered = str(key).lower()
    return any(token in lowered for token in _RATIO_TOKENS)


def _fmt_count(value: Any) -> str:
    """计数值渲染：整数值不带小数，浮点保留两位去尾零，非数值原样。"""
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return str(value)
    if fval.is_integer():
        return str(int(fval))
    return f"{fval:.2f}".rstrip("0").rstrip(".")


def _metric_human(value: Any, key: str = "") -> str:
    """指标值人读化：比率百分比化、计数原值、金额万元化、未知数值舍入原样。

    key 为空或未命中任何分类 token 时：数值按两位舍入输出（不带单位），
    字符串原样——宁可无单位，不可错单位。
    """
    if _is_ratio_key(key):
        return _fmt_pct(value)
    lowered = str(key).lower()
    if any(token in lowered for token in _COUNT_METRIC_KEYS):
        return _fmt_count(value)
    if any(token in lowered for token in _MONEY_TOKENS):
        return _fmt_wan(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _fmt_count(value)
    return str(value)
```

(c) 用以下内容替换 `_summary_analyst_markdown`（行 1806-1840，函数体在 `_fmt_scalar_answer` 与 `_metric_human` 之间，注意保留前后其余函数不动）：

```python
def _summary_analyst_markdown(
    summary: dict[str, Any], state: AgentState | None = None
) -> list[str]:
    """沙箱 summary -> 分析师叙述（数值人读化，禁 raw dict 直出）。

    2026-09 报告叙述化修复：
    - 小节标题人读化（_section_title：中文直用 / 英文 id 回退步骤 goal）；
    - metrics 嵌套结构（dict/list）一律表格化渲染，严禁 repr 直出；
    - 数值三分格式化（比率百分比 / 计数原值 / 金额万元，未知数值舍入）。
    顶层 table 行按首列是因子名（factor）还是维度取值自动分流渲染；
    extra.gain_table（维度信息增益全景）另起小节渲染。
    """
    lines: list[str] = []
    findings = summary.get("findings") or []
    metrics = summary.get("metrics") or {}
    lines.append(f"### {_section_title(summary, state)}")
    for f in findings:
        lines.append(f"- {f}")
    if metrics:
        lines.extend(_metrics_lines(metrics))
    table = summary.get("table") or {}
    columns = [str(c) for c in (table.get("columns") or [])]
    if columns and columns[0] == "factor":
        lines.append("")
        lines.append("**驱动因子分解（GMV = 买家数 × 人均订单数 × 客单价）**")
        lines.extend(_render_table(table, limit=10, currency=False))
    elif columns:
        lines.extend(_render_table(table, limit=10))
    gain_table = (summary.get("extra") or {}).get("gain_table") or {}
    if gain_table.get("rows"):
        lines.append("")
        lines.append("**维度信息增益全景（候选维度扫描结果）**")
        lines.extend(_render_table(gain_table, limit=10, currency=False))
    return lines


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _section_title(summary: dict[str, Any], state: AgentState | None = None) -> str:
    """小节标题人读化三级回退：

    1. title 含中文 => 直接使用（内置模板/Coder 守约场景）；
    2. title 为英文（step.id 或 Coder 自拟）=> 按 id 匹配计划步骤取中文 goal；
    3. 仍无法确定 => "归因分析（<原 title>）"（保留可追溯性）；空 => "归因分析"。
    """
    title = str(summary.get("title") or "").strip()
    if title and _CJK_RE.search(title):
        return title
    if title and state is not None:
        for step in state.plan_steps:
            if step.id == title and (step.goal or "").strip():
                return step.goal.strip()
    return f"归因分析（{title}）" if title else "归因分析"


def _metrics_lines(metrics: dict[str, Any]) -> list[str]:
    """metrics -> 人读行：标量单行；dict 值合并对比表；list 值表格化。

    严禁任何嵌套结构 repr 直出（2026-09 报告叙述化修复根因项）。
    """
    lines: list[str] = []
    scalars: dict[str, Any] = {}
    dicts: dict[str, dict[str, Any]] = {}
    lists: dict[str, list[Any]] = {}
    for key, val in metrics.items():
        if isinstance(val, dict):
            dicts[key] = val
        elif isinstance(val, list):
            lists[key] = val
        else:
            scalars[key] = val
    if scalars:
        rendered = "；".join(
            f"{_metric_label(k)} = {_metric_human(v, k)}" for k, v in scalars.items()
        )
        lines.append(f"- 关键指标：{rendered}")
    if dicts:
        # 同构 dict 值（week1/week2 形态）合并为对比表：行=组名、列=子键并集
        union: list[str] = []
        for d in dicts.values():
            for k in d:
                if k not in union:
                    union.append(k)
        header = ["组"] + [_metric_label(c) for c in union]
        lines.append("")
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join([" --- "] * len(header)) + "|")
        for group, d in dicts.items():
            cells = [_metric_label(group)] + [
                _metric_human(d[c], c) if c in d else "—" for c in union
            ]
            lines.append("| " + " | ".join(str(x) for x in cells) + " |")
    for name, rows in lists.items():
        dict_rows = [r for r in rows if isinstance(r, dict)]
        if dict_rows:
            union = []
            for r in dict_rows:
                for k in r:
                    if k not in union:
                        union.append(k)
            lines.append("")
            lines.append(f"**{_metric_label(name)}**")
            lines.append("| " + " | ".join(_metric_label(c) for c in union) + " |")
            lines.append("|" + "|".join([" --- "] * len(union)) + "|")
            for r in dict_rows[:10]:
                cells = [_metric_human(r[c], c) for c in union if c in r]
                lines.append("| " + " | ".join(str(x) for x in cells) + " |")
            if len(dict_rows) > 10:
                lines.append(f"（仅列示前 10 行，共 {len(dict_rows)} 行）")
        elif rows:
            lines.append(f"- {_metric_label(name)}：{'、'.join(str(x) for x in rows)}")
    return lines
```

(d) `_render_table` 的行展开与单元格格式化段（行 1872-1885）替换为畸形行防御 + 按列名分类（保留函数签名与 `currency` 语义——`currency=False` 时因子表数值不做万元化）：

```python
    for row in rows[:limit]:
        if isinstance(row, dict):
            values = [row.get(c) for c in cols]
        elif isinstance(row, (list, tuple)):
            values = list(row)
        else:
            continue  # 畸形行（str/None 等）：跳过，严禁逐字符拆解
        cells: list[str] = []
        for c, v in zip(cols, values, strict=False):  # 行长不齐时容忍截断
            if _is_ratio_key(c):
                cells.append(_fmt_pct(v))
            elif currency and any(t in c.lower() for t in _MONEY_TOKENS):
                cells.append(_fmt_wan(v))
            elif not currency and any(t in c.lower() for t in _COUNT_METRIC_KEYS):
                cells.append(_fmt_count(v))
            else:
                # currency=True 未知列 / currency=False 其余列：浮点舍入原样
                cells.append(_fmt_count(v) if isinstance(v, (int, float)) else str(v))
        lines.append("| " + " | ".join(cells) + " |")
```

(e) `synthesize_node` 兜底渲染调用点（行 2301-2304）替换为（渲染隔离）：

```python
    for summary in deduped_summaries:
        try:
            lines.extend(_summary_analyst_markdown(summary, state))
        except Exception as exc:  # 单产物渲染失败不拖垮整份报告，严禁回退 raw dump
            logger.warning(
                "summary 产物渲染失败，小节降级",
                extra={"error": f"{type(exc).__name__}: {exc}"[:200]},
            )
            lines.append("### 归因分析")
            lines.append("- （该分析产物无法渲染，原始文件已留存工作区）")
        lines.append("")
        lines.append("")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_report_narrative.py tests/test_synthesizer_rebuild.py tests/test_orchestrator.py tests/test_orchestrator_events.py -v`
Expected: 全部 PASS（含既有锚点 `66.93 万元`、`| 北京 |`、`### 两期对比与分省归因`）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/test_report_narrative.py
git commit -m "feat(orchestrator): 兜底渲染人话化——嵌套结构表格化、数值三分格式化、小节标题中文回退"
```

---

### Task 5: Coder 提示词 summary 纪律（双保险）

**Files:**
- Modify: `core/orchestrator/prompts.py:102-124`（`CODER_SYSTEM`）
- Test: `tests/test_synthesizer_rebuild.py`（文件末尾追加）

**Interfaces:**
- Consumes: 无。
- Produces: `CODER_SYSTEM` 含 summary 结构纪律（软约束；渲染层 Task 4 为硬防御）。

- [ ] **Step 1: 写失败测试**

`tests/test_synthesizer_rebuild.py` 末尾追加：

```python
def test_coder_prompt_summary_discipline():
    """Coder 提示词必须约束 summary 结构：中文 title、metrics 标量化。"""
    from core.orchestrator.prompts import CODER_SYSTEM

    assert "title 必须为简体中文业务短语" in CODER_SYSTEM
    assert "metrics 只放标量" in CODER_SYSTEM
    assert "明细矩阵一律放 table 参数" in CODER_SYSTEM
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_synthesizer_rebuild.py::test_coder_prompt_summary_discipline -v`
Expected: FAIL

- [ ] **Step 3: 最小实现**

`core/orchestrator/prompts.py` 的 `CODER_SYSTEM`，在"# table 结构"段之后插入：

```
# summary 结构纪律（违反会被报告层降级渲染）
- save_summary 的 title 必须为简体中文业务短语（如"驱动因子分解"），严禁英文 id；
- metrics 只放标量（数值/字符串），严禁嵌套 dict/list；
- 明细矩阵一律放 table 参数（columns + rows）。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_synthesizer_rebuild.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/prompts.py tests/test_synthesizer_rebuild.py
git commit -m "feat(orchestrator): Coder 提示词补充 save_summary 结构纪律（中文 title/标量 metrics）"
```

---

### Task 6: 全量回归与真实链路验证

**Files:**
- 无新改动（验证任务）；若回归暴露问题，修复归属对应 Task 的文件。

**Interfaces:**
- Consumes: Task 1-5 全部产出。
- Produces: 全绿验证记录；真实供应商链路验证结论（写入提交说明或留档）。

- [ ] **Step 1: 格式与静态检查**

Run: `black --check . && ruff check .`
Expected: 全绿；若有格式问题先 `black .` 与 `ruff check --fix .` 后复查。

- [ ] **Step 2: 全量测试**

Run: `python -m pytest -q`
Expected: 全绿（基线 755+ 用例，无新增失败）。

- [ ] **Step 3: 真实链路验证（有可用供应商时执行，否则记录跳过）**

Run: `python C:/Users/cwt15/AppData/Local/Temp/diag_synth.py`
Expected: 诊断脚本原复现 `ProviderTimeoutError`；验证 `resolve_default_client` 正常且 `chat_text` 若仍超时说明需调大 `SYNTHESIZER_TIMEOUT`。随后（可选）用真实综合素材确认：`chat_text(..., timeout=settings.SYNTHESIZER_TIMEOUT)` 能返回四段式叙述文本，且 `grounding_review` 不整体否决（不可溯源 >3 时按现状降级——该行为不属本分支修复范围，仅记录结论）。

- [ ] **Step 4: 收尾提交（若 Step 1 有格式修复）**

```bash
git add -u
git commit -m "style: black/ruff 格式化（报告叙述化修复收尾）"
```
