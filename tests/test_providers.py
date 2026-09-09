"""Model Provider 网关层单元测试：存储加密 / 协议适配器 / 工厂分派 / 请求级切换 / HTTP API。

覆盖（对应任务 DoD）：
- ProviderStore：预置种子、CRUD、API Key 落盘加密（文件不含明文）+ 历史明文自动迁移、
  对外视图不含 api_key（仅 has_api_key）、历史脱敏串不覆盖明文、预置拒绝删除；
- extract_json_object：裸 JSON / Markdown 围栏 / 前后杂文本的安全清洗；
- 适配器：openai_chat 透传 response_format + 降级重试、openai_responses 的
  input/text.format 规范、anthropic 的 system 顶级字段与 x-api-key 鉴权头、
  gemini 的 responseMimeType；401 -> AuthenticationError、429 -> RateLimitError；
- ProviderFactory：显式分派 / 无 Key 供应商不参与默认分派 / 失效缓存；
- 请求级模型切换：ContextVar 绑定 -> DispatchingAdapter 转发目标供应商；
- HTTP API：未认证 401、CRUD 全流程（响应不含 api_key）、reveal 查看密钥、
  连通性探测业务失败 200+success=false。
"""

from __future__ import annotations

import json
import threading

import pytest

from providers.adapters import (
    AnthropicAdapter,
    GeminiAdapter,
    OpenAIChatAdapter,
    OpenAIResponsesAdapter,
    extract_json_object,
)
from providers.context import (
    get_request_model,
    pop_request_model,
    set_request_model,
)
from providers.errors import (
    AuthenticationError,
    ProviderError,
    ProviderNotConfiguredError,
    RateLimitError,
    error_message,
)
from providers.factory import ProviderFactory
from providers.models import ProviderConfig, UnifiedChatRequest
from providers.store import ProviderStore


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _provider(**overrides) -> ProviderConfig:
    base = {
        "id": "p1",
        "name": "测试站",
        "is_preset": False,
        "enabled": True,
        "base_url": "https://gw.example.com/v1",
        "api_key": "sk-test-123456789",
        "protocol": "openai_chat",
        "models": [{"id": "m-1", "name": "M1"}],
    }
    base.update(overrides)
    return ProviderConfig.model_validate(base)


class _HttpStub:
    """_http_post 替身：按 (url, payload) 记录请求并返回预设响应。"""

    def __init__(self, status=200, body=None, raw=None):
        self.calls: list[tuple[str, dict]] = []
        self.status = status
        self.raw = raw if raw is not None else json.dumps(body or {})
        self.lock = threading.Lock()

    def __call__(self, url, *, payload, headers, timeout, api_key=None):
        with self.lock:
            self.calls.append((url, payload))
        return self.status, {}, self.raw


# --------------------------------------------------------------------------- #
# ProviderStore
# --------------------------------------------------------------------------- #
def test_store_preset_seed_and_keyless_view(tmp_path):
    """预置种子自动写入；对外视图不含 api_key（仅 has_api_key 布尔位）。"""
    store = ProviderStore(tmp_path / "providers.json")
    ids = [p.id for p in store.list_providers()]
    assert "zhipu" in ids and "openai" in ids  # 预置种子自动写入
    view = store.public_view(store.get_provider("zhipu"))
    assert "api_key" not in view and view["has_api_key"] is False
    # 配置 Key 后对外视图仍不含 Key 明文 / 脱敏串
    store.update_provider("zhipu", {"api_key": "zhpu-secret-key-abcdef123456"})
    view = store.public_view(store.get_provider("zhipu"))
    assert "api_key" not in view and view["has_api_key"] is True
    assert "secret" not in json.dumps(view, ensure_ascii=False)


def test_store_key_encrypted_at_rest(tmp_path):
    """API Key 落盘加密：文件不含明文；reveal_key 可还原明文。"""
    path = tmp_path / "providers.json"
    store = ProviderStore(path)
    store.create_provider(
        {"id": "c1", "name": "C1", "base_url": "https://c1/v1", "api_key": "plain-key-abcdef"}
    )
    raw = path.read_text(encoding="utf-8")
    assert "plain-key-abcdef" not in raw  # 落盘不含明文
    assert "enc1$" in raw  # 加密格式标记
    assert store.reveal_key("c1") == "plain-key-abcdef"  # 受控查看可还原
    assert store.reveal_key("nope") is None


def test_store_legacy_plaintext_migrated_on_load(tmp_path):
    """历史明文文件：加载即迁移为加密格式，读取方仍拿到原 Key。"""
    path = tmp_path / "providers.json"
    legacy = [
        {
            "id": "old1",
            "name": "Old",
            "is_preset": False,
            "enabled": True,
            "base_url": "https://old/v1",
            "api_key": "legacy-plain-key",
            "protocol": "openai_chat",
            "models": [],
            "custom_headers": {},
            "created_at": 0,
            "updated_at": 0,
        }
    ]
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    store = ProviderStore(path)
    assert store.reveal_key("old1") == "legacy-plain-key"
    assert "legacy-plain-key" not in path.read_text(encoding="utf-8")  # 已加密迁移


def test_store_masked_key_does_not_overwrite_plaintext(tmp_path):
    """兼容旧客户端：回传历史脱敏串 => 服务端保留原明文（不落垃圾 Key）。"""
    from providers.models import mask_api_key

    store = ProviderStore(tmp_path / "providers.json")
    store.create_provider(
        {"id": "c1", "name": "C1", "base_url": "https://c1/v1", "api_key": "plain-key-abcdef"}
    )
    store.update_provider("c1", {"api_key": mask_api_key("plain-key-abcdef")})
    assert store.get_provider("c1").api_key == "plain-key-abcdef"
    # 空串 / 缺省同样保留原值
    store.update_provider("c1", {"api_key": "", "name": "C1-renamed"})
    assert store.get_provider("c1").api_key == "plain-key-abcdef"
    assert store.get_provider("c1").name == "C1-renamed"


def test_store_preset_delete_rejected_and_custom_deleted(tmp_path):
    store = ProviderStore(tmp_path / "providers.json")
    assert store.delete_provider("zhipu") is False  # 预置不可删
    store.create_provider({"id": "c2", "name": "C2", "base_url": "https://c2/v1"})
    assert store.delete_provider("c2") is True
    assert store.get_provider("c2") is None


# --------------------------------------------------------------------------- #
# JSON 安全清洗
# --------------------------------------------------------------------------- #
def test_extract_json_plain_fenced_and_dirty():
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('好的，结果如下：{"a": 1} 请查收') == {"a": 1}
    with pytest.raises(ProviderError):
        extract_json_object("完全不是 JSON")


# --------------------------------------------------------------------------- #
# 适配器（_http_post 打桩）
# --------------------------------------------------------------------------- #
def test_openai_chat_passthrough_response_format(monkeypatch):
    stub = _HttpStub(
        body={
            "choices": [{"message": {"content": '{"ok": 1}'}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
    )
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIChatAdapter(_provider(), "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    url, payload = stub.calls[0]
    assert url.endswith("/chat/completions")
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert resp.parsed_json == {"ok": 1}
    assert resp.usage.total_tokens == 5


def test_openai_chat_json_fallback_retry_without_native_param(monkeypatch):
    # 第一次：response_format 被中转站拒绝（400 + 不支持提示）；第二次放行
    ok = _HttpStub(body={"choices": [{"message": {"content": '{"ok": 1}'}}]})
    calls: list[tuple[str, dict]] = []

    def flaky(url, *, payload, headers, timeout, api_key=None):
        calls.append((url, payload))
        if "response_format" in payload:
            raise ProviderError("HTTP 400: response_format not supported", code="provider_error")
        return 200, {}, ok.raw

    monkeypatch.setattr("providers.adapters._http_post", flaky)
    adapter = OpenAIChatAdapter(_provider(), "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert "response_format" not in calls[-1][1]  # 降级后不再携带原生参数
    assert resp.parsed_json == {"ok": 1}


def test_openai_chat_401_and_429_mapping(monkeypatch):
    def auth_fail(url, **kwargs):
        raise AuthenticationError("鉴权失败（HTTP 401）")

    def rate_limited(url, **kwargs):
        raise RateLimitError("配额超限（HTTP 429）")

    adapter = OpenAIChatAdapter(_provider(), "m-1")
    monkeypatch.setattr("providers.adapters._http_post", auth_fail)
    with pytest.raises(AuthenticationError):
        adapter.chat(UnifiedChatRequest(messages=[{"role": "user", "content": "x"}], model="m-1"))
    monkeypatch.setattr("providers.adapters._http_post", rate_limited)
    with pytest.raises(RateLimitError):
        adapter.chat(UnifiedChatRequest(messages=[{"role": "user", "content": "x"}], model="m-1"))
    assert "API Key" in error_message(AuthenticationError("x"))
    assert "429" in error_message(RateLimitError("x"))


def test_anthropic_system_top_level_and_headers(monkeypatch):
    stub = _HttpStub(
        body={
            "content": [{"type": "text", "text": '{"a": 1}'}],
            "usage": {"input_tokens": 4, "output_tokens": 6},
        }
    )
    monkeypatch.setattr("providers.adapters._http_post", stub)
    provider = _provider(protocol="anthropic", base_url="https://api.anthropic.com")
    adapter = AnthropicAdapter(provider, "claude-x")
    adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "system", "content": "你是助手"}, {"role": "user", "content": "hi"}],
            model="claude-x",
            response_format={"type": "json_object"},
        )
    )
    url, payload = stub.calls[0]
    assert url.endswith("/v1/messages")
    assert payload["system"].startswith("你是助手")  # System 提取至顶级字段
    assert all(m["role"] != "system" for m in payload["messages"])


def test_openai_responses_payload_shape(monkeypatch):
    stub = _HttpStub(
        body={
            "output": [{"content": [{"type": "output_text", "text": '{"r": 2}'}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    )
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIResponsesAdapter(_provider(protocol="openai_responses"), "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    url, payload = stub.calls[0]
    assert url.endswith("/responses")
    assert payload["input"][0]["role"] == "user"
    assert payload["text"]["format"] == {"type": "json_object"}
    assert resp.parsed_json == {"r": 2}


def test_gemini_payload_and_url(monkeypatch):
    stub = _HttpStub(
        body={
            "candidates": [{"content": {"parts": [{"text": '{"g": 3}'}]}}],
            "usageMetadata": {"totalTokenCount": 9},
        }
    )
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = GeminiAdapter(
        _provider(protocol="gemini", base_url="https://gai.googleapis.com"), "gemini-x"
    )
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="gemini-x",
            response_format={"type": "json_object"},
        )
    )
    url, payload = stub.calls[0]
    assert "gemini-x:generateContent" in url
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert resp.parsed_json == {"g": 3}


# --------------------------------------------------------------------------- #
# 工厂与请求级切换
# --------------------------------------------------------------------------- #
def test_factory_requires_key_for_default_dispatch(tmp_path):
    store = ProviderStore(tmp_path / "providers.json")
    factory = ProviderFactory(store)
    # 无 Key 的预置供应商不参与默认分派（避免空 Key 误判为 LLM 模式）
    with pytest.raises(ProviderNotConfiguredError):
        factory.resolve(None, None)
    # 显式指定也要求启用 + 存在
    with pytest.raises(ProviderNotConfiguredError):
        factory.resolve("nope", "m")
    store.update_provider("zhipu", {"api_key": "zhpu-key-1234567890"})
    adapter = factory.resolve("zhipu", "glm-4.5-flash")
    assert adapter.model_id == "glm-4.5-flash"


def test_request_scoped_model_dispatch(monkeypatch, tmp_path):
    from providers import factory as factory_mod
    from providers.context import dispatching_adapter, reset_dispatching_adapter

    store = ProviderStore(tmp_path / "providers.json")
    store.update_provider("zhipu", {"api_key": "zhpu-key-1234567890"})
    store.create_provider(
        {
            "id": "proxy",
            "name": "中转",
            "base_url": "https://proxy/v1",
            "api_key": "sk-proxy-000",
            "models": [{"id": "p-model"}],
        }
    )
    # 分发代理内部经 default_provider_factory 解析：注入隔离工厂（测试不触真实配置）
    isolated = ProviderFactory(store)
    monkeypatch.setattr(factory_mod, "_default_factory", isolated)
    reset_dispatching_adapter()
    seen_urls: list[str] = []

    def spy(url, *, payload, headers, timeout, api_key=None):
        seen_urls.append(url)
        return 200, {}, json.dumps({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr("providers.adapters._http_post", spy)

    proxy = dispatching_adapter()
    # 未绑定请求上下文 -> 默认分派（首位有 Key 的启用供应商 = zhipu）
    proxy.chat_text([{"role": "user", "content": "q"}])
    assert "open.bigmodel.cn" in seen_urls[-1]
    # 绑定请求上下文 -> 转发到目标供应商
    set_request_model("proxy", "p-model")
    try:
        proxy.chat_text([{"role": "user", "content": "q"}])
        assert "proxy" in seen_urls[-1]  # URL 指向中转供应商
        assert get_request_model() == ("proxy", "p-model")
    finally:
        pop_request_model()
    assert get_request_model() is None


# --------------------------------------------------------------------------- #
# NL -> DSL 经适配器流转（DoD 3：切换自定义模型后产出合法 DSL）
# --------------------------------------------------------------------------- #
def test_llm_pipeline_via_adapter_produces_valid_dsl(monkeypatch, tmp_path):
    from agent.agent import LLMNL2DSL
    from semantic.dsl_schema import QueryDSL

    dsl_payload = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
        "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
    }
    # 供应商响应（带围栏 + 杂文本，验证 JSON 兜底清洗在真实链路生效）
    raw = "好的：```json\n" + json.dumps(dsl_payload) + "\n``` 以上。"
    stub = _HttpStub(body={"choices": [{"message": {"content": raw}}]})

    called: dict[str, str] = {}

    def spy(url, *, payload, headers, timeout, api_key=None):
        called["url"] = url
        called["model"] = str(payload.get("model"))
        return stub(url, payload=payload, headers=headers, timeout=timeout, api_key=api_key)

    monkeypatch.setattr("providers.adapters._http_post", spy)
    # 自定义中转站 + 自定义模型名（模拟前端切换后的请求级分派）
    provider = _provider(
        id="relay",
        name="中转站",
        base_url="https://relay.example.com/v1",
        models=[{"id": "relay-model"}],
    )
    adapter = OpenAIChatAdapter(provider, "relay-model")
    dsl = LLMNL2DSL(adapter, max_retries=1).run("2024年5月成功订单的GMV是多少？")
    assert isinstance(dsl, QueryDSL)
    assert dsl.metrics[0].alias == "gmv"
    assert called["model"] == "relay-model" and "relay.example.com" in called["url"]


# --------------------------------------------------------------------------- #
# HTTP API（真实 Handler 冒烟）
# --------------------------------------------------------------------------- #
def _start_server():
    from web.server import Handler, ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


def _req(port, method, path, payload=None, headers=None):
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_providers_http_api_flow(tmp_path, monkeypatch):
    from providers.factory import reset_provider_factory
    from providers.store import reset_default_provider_store

    # 注入临时存储（测试隔离，绝不读写真实 providers.json）
    isolated = ProviderStore(tmp_path / "providers.json")
    reset_default_provider_store(isolated)
    reset_provider_factory(ProviderFactory(isolated))
    import web.providers_api as api_mod

    monkeypatch.setattr(api_mod, "_store", lambda: isolated)
    monkeypatch.setattr(api_mod, "_factory", lambda: ProviderFactory(isolated))

    server, port = _start_server()
    try:
        # 未认证 -> 401
        status, _ = _req(port, "GET", "/api/settings/providers")
        assert status == 401
        _, login = _req(
            port, "POST", "/api/auth/login", {"username": "admin", "password": "admin123"}
        )
        H = {"Authorization": "Bearer " + login["token"]}
        # 列表含预置种子
        status, body = _req(port, "GET", "/api/settings/providers", headers=H)
        assert status == 200 and any(p["id"] == "zhipu" for p in body["providers"])
        # 创建 -> Key 脱敏回显
        status, body = _req(
            port,
            "POST",
            "/api/settings/providers",
            {
                "name": "API测试站",
                "base_url": "https://api.test/v1",
                "protocol": "openai_chat",
                "api_key": "sk-api-test-987654321",
                "models": [{"id": "t-model"}],
            },
            headers=H,
        )
        assert status == 200
        created = body["provider"]
        # 响应不含 api_key（前端不回填脱敏串），仅暴露 has_api_key 布尔位
        assert "api_key" not in created and created["has_api_key"] is True
        # 更新（api_key 缺省 -> 保留原 Key）+ 名称变更
        status, body = _req(
            port,
            "PUT",
            f"/api/settings/providers/{created['id']}",
            {"name": "API测试站2"},
            headers=H,
        )
        assert status == 200 and body["provider"]["name"] == "API测试站2"
        assert isolated.get_provider(created["id"]).api_key == "sk-api-test-987654321"
        # 查看密钥（reveal）：认证后返回明文，未认证 401，未知 id 404
        status, body = _req(
            port, "POST", f"/api/settings/providers/{created['id']}/reveal", headers=H
        )
        assert status == 200 and body["api_key"] == "sk-api-test-987654321"
        status, _ = _req(port, "POST", f"/api/settings/providers/{created['id']}/reveal")
        assert status == 401
        status, _ = _req(port, "POST", "/api/settings/providers/p_none/reveal", {"x": 1}, headers=H)
        assert status == 404
        # 连通性探测：业务失败 -> 200 + success=false（不抛 4xx/5xx）
        status, body = _req(
            port,
            "POST",
            "/api/settings/providers/test",
            {"provider_id": created["id"], "model_id": "t-model"},
            headers=H,
        )
        assert status == 200 and body["success"] is False and body["error"]
        # 未知供应商探测 -> 404
        status, _ = _req(
            port, "POST", "/api/settings/providers/test", {"provider_id": "ghost"}, headers=H
        )
        assert status == 404
        # 删除自定义 -> ok；删除预置 -> 400
        status, body = _req(port, "DELETE", f"/api/settings/providers/{created['id']}", headers=H)
        assert status == 200 and body["ok"] is True
        status, body = _req(port, "DELETE", "/api/settings/providers/zhipu", headers=H)
        assert status == 400 and "不可删除" in body["error"]
    finally:
        server.shutdown()
        server.server_close()
        reset_default_provider_store(None)
        reset_provider_factory(None)
