"""Model Provider 网关层单元测试：存储加密 / 协议适配器 / 工厂分派 / 请求级切换 / HTTP API。

覆盖（对应任务 DoD）：
- ProviderStore：预置种子、CRUD、API Key 落盘加密（文件不含明文）+ 历史明文自动迁移、
  对外视图不含 api_key（仅 has_api_key）、历史脱敏串不覆盖明文、预置拒绝删除；
- extract_json_object：裸 JSON / Markdown 围栏 / 前后杂文本的安全清洗；
- 适配器：openai_chat 透传 response_format + 降级重试、openai_responses 的
  input/text.format 规范、anthropic 的 system 顶级字段与 x-api-key 鉴权头；
  401 -> AuthenticationError、429 -> RateLimitError；
- ProviderFactory：显式分派 / 无 Key 供应商不参与默认分派 / 失效缓存；
- 请求级模型切换：ContextVar 绑定 -> DispatchingAdapter 转发目标供应商；
- HTTP API：未认证 401、CRUD 全流程（响应不含 api_key）、reveal 查看密钥、
  连通性探测业务失败 200+success=false。
"""

from __future__ import annotations

import json
import threading
from typing import ClassVar

import pytest

from providers.adapters import (
    _SSE_MAX_LINE_BYTES,
    AnthropicAdapter,
    OpenAIChatAdapter,
    OpenAIResponsesAdapter,
    _consume_stream,
    _http_post_sse,
    _iter_sse_payloads,
    extract_json_object,
)
from providers.context import (
    get_request_model,
    pop_request_model,
    set_request_model,
)
from providers.errors import (
    AuthenticationError,
    ProtocolError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    RateLimitError,
    StreamHandshakeRejected,
    error_message,
)
from providers.factory import ProviderFactory
from providers.models import ProviderConfig, UnifiedChatRequest
from providers.store import ProviderStore
from tests.fixture_keys import fake_key


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
        "api_key": fake_key("gw"),
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
def test_store_zero_presets_and_keyless_view(tmp_path):
    """零预置：新配置为空；对外视图不含 api_key（仅 has_api_key 布尔位）。"""
    store = ProviderStore(tmp_path / "providers.json")
    assert store.list_providers() == []  # 系统不预置任何供应商
    gw_key = fake_key("gw")
    store.create_provider(
        {"id": "gw", "name": "中转", "base_url": "https://gw/v1", "api_key": gw_key}
    )
    view = store.public_view(store.get_provider("gw"))
    assert "api_key" not in view and view["has_api_key"] is True
    assert gw_key not in json.dumps(view, ensure_ascii=False)


def test_store_prunes_legacy_presets_keeps_custom_with_backup(tmp_path):
    """零预置迁移：清掉全部历史预置（openai / 智谱），自定义条目保留。"""
    path = tmp_path / "providers.json"
    legacy = json.dumps(
        [
            {
                "id": "openai",
                "name": "OpenAI",
                "is_preset": True,
                "enabled": True,
                "base_url": "https://api.openai.com/v1",
                "protocol": "openai_chat",
                "models": [{"id": "gpt-4o"}],
                "api_key": "",
            },
            {
                "id": "zhipu",
                "name": "智谱",
                "is_preset": True,
                "enabled": True,
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "protocol": "openai_chat",
                "models": [{"id": "glm-4.5-flash"}],
                "api_key": "",
            },
            {
                "id": "p_legacy",
                "name": "旧中转",
                "is_preset": False,
                "enabled": True,
                "base_url": "https://legacy/v1",
                "protocol": "openai_chat",
                "models": [{"id": "m"}],
                "api_key": "",
            },
        ],
        ensure_ascii=False,
    )
    path.write_text(legacy, encoding="utf-8")
    store = ProviderStore(path)
    ids = {p.id for p in store.list_providers()}
    # 历史预置全部清理（零预置），自定义条目保留
    assert ids == {"p_legacy"}
    # 清理结果立即写回：磁盘文件不再含预置条目，自定义仍在
    persisted = {item["id"] for item in json.loads(path.read_text(encoding="utf-8"))}
    assert persisted == {"p_legacy"}
    backup = tmp_path / "providers.json.bak"
    backup_text = backup.read_text(encoding="utf-8")
    assert "openai" in backup_text and "zhipu" in backup_text


def test_store_key_encrypted_at_rest(tmp_path):
    """API Key 落盘加密：文件不含明文；reveal_key 可还原明文。"""
    path = tmp_path / "providers.json"
    store = ProviderStore(path)
    c1_key = fake_key("c1")
    store.create_provider(
        {"id": "c1", "name": "C1", "base_url": "https://c1/v1", "api_key": c1_key}
    )
    raw = path.read_text(encoding="utf-8")
    assert c1_key not in raw  # 落盘不含明文
    assert "enc1$" in raw  # 加密格式标记
    assert store.reveal_key("c1") == c1_key  # 受控查看可还原
    assert store.reveal_key("nope") is None


def test_store_legacy_plaintext_migrated_on_load(tmp_path):
    """历史明文文件：加载即迁移为加密格式，读取方仍拿到原 Key。"""
    path = tmp_path / "providers.json"
    legacy_key = fake_key("legacy")
    legacy = [
        {
            "id": "old1",
            "name": "Old",
            "is_preset": False,
            "enabled": True,
            "base_url": "https://old/v1",
            "api_key": legacy_key,
            "protocol": "openai_chat",
            "models": [],
            "custom_headers": {},
            "created_at": 0,
            "updated_at": 0,
        }
    ]
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    store = ProviderStore(path)
    assert store.reveal_key("old1") == legacy_key
    assert legacy_key not in path.read_text(encoding="utf-8")  # 已加密迁移


def test_store_masked_key_does_not_overwrite_plaintext(tmp_path):
    """兼容旧客户端：回传历史脱敏串 => 服务端保留原明文（不落垃圾 Key）。"""
    from providers.models import mask_api_key

    store = ProviderStore(tmp_path / "providers.json")
    c1_key = fake_key("c1")
    store.create_provider(
        {"id": "c1", "name": "C1", "base_url": "https://c1/v1", "api_key": c1_key}
    )
    store.update_provider("c1", {"api_key": mask_api_key(c1_key)})
    assert store.get_provider("c1").api_key == c1_key
    # 空串 / 缺省同样保留原值
    store.update_provider("c1", {"api_key": "", "name": "C1-renamed"})
    assert store.get_provider("c1").api_key == c1_key
    assert store.get_provider("c1").name == "C1-renamed"


def test_store_preset_delete_rejected_and_custom_deleted(tmp_path):
    store = ProviderStore(tmp_path / "providers.json")
    # 不存在的供应商删除返回 False；自定义供应商可删
    assert store.delete_provider("nope") is False
    store.create_provider({"id": "c2", "name": "C2", "base_url": "https://c2/v1"})
    assert store.delete_provider("c2") is True
    assert store.get_provider("c2") is None
    # 历史预置条目（is_preset=True）即使被外部注入也不可删
    store._providers["legacy_preset"] = ProviderConfig.model_validate(
        {
            "id": "legacy_preset",
            "name": "Legacy",
            "is_preset": True,
            "enabled": True,
            "base_url": "https://legacy/v1",
            "protocol": "openai_chat",
            "models": [],
        }
    )
    assert store.delete_provider("legacy_preset") is False


def test_store_update_with_models_list_dict_persists(tmp_path):
    """回归：PUT 更新提交 models（list[dict]）必须真正持久化。

    历史 bug：model_copy(update=...) 不重新校验字段，dict 条目混入实例后
    _normalize_models 访问 m.id 抛 AttributeError（HTTP 500），模型清单从未
    落盘 -> 模型切换器不收录该供应商（表现为"刷新后配置丢失"）。
    """
    path = tmp_path / "providers.json"
    store = ProviderStore(path)
    ark_key = fake_key("ark")
    store.create_provider(
        {"id": "c1", "name": "火山", "base_url": "https://ark/v1", "api_key": ark_key}
    )
    updated = store.update_provider(
        "c1",
        {
            "name": "火山改名",
            "models": [{"id": "doubao-seed-1-6", "name": "doubao-seed-1-6"}],
        },
    )
    assert updated is not None
    assert [m.id for m in updated.models] == ["doubao-seed-1-6"]
    # 重新实例化（模拟重启后从磁盘加载）：自定义供应商与模型仍在
    reloaded = ProviderStore(path)
    assert [m.id for m in reloaded.get_provider("c1").models] == ["doubao-seed-1-6"]
    assert reloaded.get_provider("c1").name == "火山改名"
    # 保存过的密钥与模型不被后续"仅改名称"的更新破坏（空 Key 保留原值）
    reloaded.update_provider("c1", {"name": "火山改名2"})
    assert reloaded.get_provider("c1").api_key == ark_key
    assert [m.id for m in reloaded.get_provider("c1").models] == ["doubao-seed-1-6"]
    # 非法契约字段 -> ValueError（API 层转 400），而非静默吞掉
    with pytest.raises(ValueError):
        reloaded.update_provider("c1", {"models": [{"unknown_field": 1}]})


def test_factory_choices_include_custom_provider_after_model_update(tmp_path):
    """回归：自定义供应商配置模型后必须进入模型切换器候选（choices）。"""
    store = ProviderStore(tmp_path / "providers.json")
    factory = ProviderFactory(store)
    store.create_provider({"id": "huoshan", "name": "火山", "base_url": "https://ark/v1"})
    # 尚未配置模型：不进入候选（没有可选模型，语义正确）
    assert all(c["provider_id"] != "huoshan" for c in factory.list_choices())
    # 配置模型后：立即进入候选
    store.update_provider("huoshan", {"models": [{"id": "doubao-seed-1-6"}]})
    choices = {c["provider_id"]: c for c in factory.list_choices()}
    assert "huoshan" in choices
    assert [m["id"] for m in choices["huoshan"]["models"]] == ["doubao-seed-1-6"]


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
    store.create_provider(
        {
            "id": "gw",
            "name": "中转",
            "base_url": "https://gw/v1",
            "api_key": fake_key("gw"),
            "models": [{"id": "gw-model"}],
        }
    )
    adapter = factory.resolve("gw", "gw-model")
    assert adapter.model_id == "gw-model"


def test_request_scoped_model_dispatch(monkeypatch, tmp_path):
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
    store.create_provider(
        {
            "id": "proxy",
            "name": "中转",
            "base_url": "https://proxy/v1",
            "api_key": fake_key("proxy"),
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
    # 未绑定请求上下文 -> 默认分派（首位有 Key 的启用供应商 = primary）
    proxy.chat_text([{"role": "user", "content": "q"}])
    assert "primary-gw" in seen_urls[-1]
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
class _FakeSrv:
    def shutdown(self):
        pass

    def server_close(self):
        pass


def _start_test_client():
    """M4 单引擎：FastAPI TestClient 替代 ThreadingHTTPServer（无 Cookie 持久化）。"""
    from fastapi.testclient import TestClient

    from web.api import app as _app

    return _FakeSrv(), TestClient(_app, raise_server_exceptions=False)


def _client_req(client, method, path, payload=None, headers=None):
    resp = client.request(method, path, json=payload, headers=headers or {})
    return resp.status_code, resp.json()


_REQ_CLIENT = None


def _req(port, method, path, payload=None, headers=None):
    return _client_req(_REQ_CLIENT, method, path, payload, headers)


def _fresh_req(method, path, payload=None):
    """无 Cookie 的独立请求：http.client 旧语义（不持久化会话），
    未认证用例不得被同用例前序登录的 Set-Cookie 放行。"""
    from fastapi.testclient import TestClient

    from web.api import app as _app

    fresh = TestClient(_app, raise_server_exceptions=False)
    return _client_req(fresh, method, path, payload)


def test_providers_http_api_flow(tmp_path, monkeypatch):
    from providers.factory import reset_provider_factory
    from providers.store import reset_default_provider_store

    # 注入临时存储（测试隔离，绝不读写真实 providers.json）
    isolated = ProviderStore(tmp_path / "providers.json")
    reset_default_provider_store(isolated)
    global _REQ_CLIENT
    server, _REQ_CLIENT = _start_test_client()
    port = 0
    reset_provider_factory(ProviderFactory(isolated))
    import web.providers_api as api_mod

    monkeypatch.setattr(api_mod, "_store", lambda: isolated)
    monkeypatch.setattr(api_mod, "_factory", lambda: ProviderFactory(isolated))

    try:
        # 未认证 -> 401
        status, _ = _req(port, "GET", "/api/settings/providers")
        assert status == 401
        _, login = _req(
            port, "POST", "/api/auth/login", {"username": "admin", "password": "admin123"}
        )
        H = {"Authorization": "Bearer " + login["token"]}
        # 列表为零预置（系统不预置任何供应商，全部由用户添加）
        status, body = _req(port, "GET", "/api/settings/providers", headers=H)
        assert status == 200 and body["providers"] == []
        # 创建 gemini 协议 -> 400（控制面协议白名单：chat/responses/anthropic）
        status, body = _req(
            port,
            "POST",
            "/api/settings/providers",
            {
                "name": "Gemini 站",
                "base_url": "https://gai.googleapis.com",
                "protocol": "gemini",
                "models": [{"id": "gemini-1.5-pro"}],
            },
            headers=H,
        )
        assert status == 400 and "不支持的 API 协议" in body["error"]
        # 创建自定义供应商（openai_chat 协议）-> 200；响应不含 api_key
        http_api_key = fake_key("http-api")
        status, body = _req(
            port,
            "POST",
            "/api/settings/providers",
            {
                "name": "API测试站",
                "base_url": "https://api.test/v1",
                "protocol": "openai_chat",
                "api_key": http_api_key,
                "models": [{"id": "t-model"}],
            },
            headers=H,
        )
        assert status == 200
        created = body["provider"]
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
        assert isolated.get_provider(created["id"]).api_key == http_api_key
        # 查看密钥（reveal）：认证后返回明文，未认证 401，未知 id 404
        status, body = _req(
            port, "POST", f"/api/settings/providers/{created['id']}/reveal", headers=H
        )
        assert status == 200 and body["api_key"] == http_api_key
        status, _ = _fresh_req("POST", f"/api/settings/providers/{created['id']}/reveal")
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
        # 删除自定义 -> ok；删除不存在的供应商 -> 404
        status, body = _req(port, "DELETE", f"/api/settings/providers/{created['id']}", headers=H)
        assert status == 200 and body["ok"] is True
        status, body = _req(port, "DELETE", "/api/settings/providers/ghost", headers=H)
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        reset_default_provider_store(None)
        reset_provider_factory(None)


# --------------------------------------------------------------------------- #
# 超时透传（2026-09 报告叙述化修复）
# --------------------------------------------------------------------------- #
def test_unified_chat_request_accepts_timeout():
    from providers.models import UnifiedChatRequest

    req = UnifiedChatRequest(messages=[{"role": "user", "content": "x"}], model="m-1", timeout=180)
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
    text = adapter.chat_text([{"role": "user", "content": "hi"}], json_mode=False, timeout=180)
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
    adapter.chat(UnifiedChatRequest(messages=[{"role": "user", "content": "x"}], model="m-1"))
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
        UnifiedChatRequest(messages=[{"role": "user", "content": "x"}], model="m-1", timeout=180)
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
        UnifiedChatRequest(messages=[{"role": "user", "content": "x"}], model="m-1", timeout=180)
    )
    assert captured["timeout"] == 180


def test_openai_responses_chat_timeout_override(monkeypatch):
    from providers.adapters import OpenAIResponsesAdapter
    from providers.models import UnifiedChatRequest

    captured: dict = {}

    def stub(url, *, payload, headers, timeout, api_key=None):
        captured["timeout"] = timeout
        return (
            200,
            {},
            json.dumps({"output": [{"content": [{"type": "output_text", "text": "ok"}]}]}),
        )

    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIResponsesAdapter(_provider(), "m-1")
    adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "x"}], model="m-1", timeout=180)
    )
    assert captured["timeout"] == 180


# --------------------------------------------------------------------------- #
# 429 限流退避重试（2026-09 分层降级兜底：LLM_MAX_RETRIES 死配置接线）
# --------------------------------------------------------------------------- #
def test_chat_facade_retries_on_rate_limit(monkeypatch):
    """第一次 429、第二次成功 => 门面自动重试并返回结果。"""
    from config import settings
    from providers import RateLimitError, chat_text

    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr("providers.time.sleep", lambda _s: None)
    calls: list[int] = []

    class _Flaky:
        def chat_text(self, messages, *, model=None, json_mode=True, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise RateLimitError("配额超限或请求过于频繁（HTTP 429）")
            return "ok"

    assert chat_text(_Flaky(), [{"role": "user", "content": "x"}]) == "ok"
    assert len(calls) == 2


def test_chat_facade_rate_limit_exhausted_raises(monkeypatch):
    """重试耗尽仍 429 => 原样抛出（调用方走各自兜底）。"""
    from config import settings
    from providers import RateLimitError, chat_text

    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr("providers.time.sleep", lambda _s: None)
    calls: list[int] = []

    class _Always429:
        def chat_text(self, messages, *, model=None, json_mode=True, timeout=None):
            calls.append(1)
            raise RateLimitError("429")

    with pytest.raises(RateLimitError):
        chat_text(_Always429(), [{"role": "user", "content": "x"}])
    assert len(calls) == 2  # 首调 + 1 次重试


def test_chat_facade_no_retry_on_other_errors(monkeypatch):
    """非 429 异常（如 ProviderError）不重试，直接抛出。"""
    from config import settings
    from providers import ProviderError, chat_text

    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr("providers.time.sleep", lambda _s: None)
    calls: list[int] = []

    class _Broken:
        def chat_text(self, messages, *, model=None, json_mode=True, timeout=None):
            calls.append(1)
            raise ProviderError("网络请求失败")

    with pytest.raises(ProviderError):
        chat_text(_Broken(), [{"role": "user", "content": "x"}])
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# 流式契约（ProviderConfig.stream / 总时长上限）
# --------------------------------------------------------------------------- #
def test_provider_config_stream_defaults_false_and_roundtrips(tmp_path):
    cfg = _provider()
    assert cfg.stream is False  # 存量配置缺省关闭，行为不变
    cfg2 = _provider(stream=True)
    assert cfg2.stream is True
    # 序列化往返（public_view 透传给前端）
    dumped = cfg2.model_dump(mode="json")
    assert dumped["stream"] is True
    assert ProviderConfig.model_validate(dumped).stream is True


def test_settings_stream_max_seconds_exists():
    from config import settings

    assert isinstance(settings.PROVIDER_STREAM_MAX_SECONDS, int)
    assert settings.PROVIDER_STREAM_MAX_SECONDS > 0


# --------------------------------------------------------------------------- #
# SSE 流式（纯函数 / 读取器 / 聚合器）
# --------------------------------------------------------------------------- #
def test_iter_sse_payloads_ignores_noise_and_yields_data():
    lines = [
        ": keep-alive",
        "event: response.output_text.delta",
        'data: {"a": 1}',
        "",
        "data: [DONE]",
    ]
    assert list(_iter_sse_payloads(lines)) == ['{"a": 1}', "[DONE]"]


def test_consume_stream_aggregates_and_collects_usage():
    frames = [
        json.dumps({"choices": [{"delta": {"content": "he"}}]}),
        json.dumps({"choices": [{"delta": {"content": "llo"}}]}),
        json.dumps(
            {
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }
        ),
        "[DONE]",
    ]
    content, usage = _consume_stream(
        frames,
        extract_delta=lambda f: ((f.get("choices") or [{}])[0].get("delta") or {}).get("content")
        or "",
        terminal=lambda f: False,
        extract_usage=lambda f: f.get("usage"),
    )
    assert content == "hello"
    assert usage["total_tokens"] == 3


def test_consume_stream_eof_without_terminal_raises():
    # 流中途断连：EOF 先于终止帧 => ProviderError，绝不返回半截内容
    with pytest.raises(ProviderError):
        _consume_stream(
            [json.dumps({"choices": [{"delta": {"content": "half"}}]})],
            extract_delta=lambda f: ((f.get("choices") or [{}])[0].get("delta") or {}).get(
                "content"
            )
            or "",
            terminal=lambda f: False,
            extract_usage=lambda f: None,
        )


def test_consume_stream_empty_content_raises_protocol_error():
    # DeepSeek 系网关内容进 reasoning_content 的形态：content 为空必须报错走兜底
    frames = [json.dumps({"choices": [{"delta": {}}]}), "[DONE]"]
    with pytest.raises(ProtocolError):
        _consume_stream(
            frames,
            extract_delta=lambda f: ((f.get("choices") or [{}])[0].get("delta") or {}).get(
                "content"
            )
            or "",
            terminal=lambda f: False,
            extract_usage=lambda f: None,
        )


def test_consume_stream_error_frame_maps_429():
    frames = [json.dumps({"error": {"code": "429", "message": "rate limited"}})]
    with pytest.raises(RateLimitError):
        _consume_stream(
            frames,
            extract_delta=lambda f: "",
            terminal=lambda f: False,
            extract_usage=lambda f: None,
        )


def test_consume_stream_protocol_terminal_frame_collects_usage():
    frames = [
        json.dumps({"type": "response.output_text.delta", "delta": "ok"}),
        json.dumps({"type": "response.completed", "response": {"usage": {"total_tokens": 7}}}),
    ]
    content, usage = _consume_stream(
        frames,
        extract_delta=lambda f: (
            (f.get("delta") or "") if f.get("type") == "response.output_text.delta" else ""
        ),
        terminal=lambda f: f.get("type") == "response.completed",
        extract_usage=lambda f: f.get("response", {}).get("usage"),
    )
    assert content == "ok"
    assert usage["total_tokens"] == 7


class _SSEResponse:
    """SSE 读取器测试桩响应：按预置行序列逐行 readline。"""

    def __init__(self, lines, status=200):
        self.status = status
        self._lines = lines
        self._i = 0

    def readline(self, limit=-1):
        if self._i >= len(self._lines):
            return b""
        item = self._lines[self._i]
        self._i += 1
        return item.encode("utf-8")

    def read(self):
        return b'{"error": "bad request"}'

    def getheaders(self):
        return {}


class _SSEConnStub:
    """SSE 读取器测试桩连接：记录请求体/头，返回类属性 lines 组成的响应。"""

    lines: ClassVar[list[str]] = []
    last: ClassVar[dict | None] = None

    def __init__(self, host, port, timeout=None):
        self.host = host

    def request(self, method, path, body=None, headers=None):
        _SSEConnStub.last = {"body": json.loads(body), "headers": headers}

    def getresponse(self):
        return _SSEResponse(_SSEConnStub.lines)

    def close(self):
        pass


def test_http_post_sse_yields_data_lines_and_sends_stream_payload(monkeypatch):
    _SSEConnStub.lines = [": ping", 'data: {"x": 1}', "data: [DONE]", ""]
    monkeypatch.setattr("http.client.HTTPSConnection", _SSEConnStub)
    out = list(
        _http_post_sse(
            "https://gw.example.com/v1/chat/completions",
            payload={"stream": True, "messages": []},
            headers={"X-Custom": "1"},
            timeout=5,
            max_seconds=30,
            api_key="k-test",
        )
    )
    assert out == ['{"x": 1}', "[DONE]"]
    assert _SSEConnStub.last["body"]["stream"] is True
    assert _SSEConnStub.last["headers"]["Authorization"] == "Bearer k-test"
    assert _SSEConnStub.last["headers"]["Accept"] == "text/event-stream"


def test_http_post_sse_idle_timeout_maps_to_provider_timeout(monkeypatch):
    class IdleResp:
        status = 200

        def readline(self, limit=-1):
            raise TimeoutError("read timed out")

    class IdleConn(_SSEConnStub):
        def getresponse(self):
            return IdleResp()

    monkeypatch.setattr("http.client.HTTPSConnection", IdleConn)
    with pytest.raises(ProviderTimeoutError):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=1, max_seconds=30
            )
        )


def test_http_post_sse_total_limit_raises(monkeypatch):
    _SSEConnStub.lines = ['data: {"a": 1}', 'data: {"b": 2}']
    monkeypatch.setattr("http.client.HTTPSConnection", _SSEConnStub)
    with pytest.raises(ProviderTimeoutError):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=0
            )
        )


def test_http_post_sse_first_packet_401_maps(monkeypatch):
    class UnauthorizedResp:
        status = 401

        def read(self):
            return b"unauthorized"

        def readline(self, limit=-1):
            return b""

    class UnauthorizedConn(_SSEConnStub):
        def getresponse(self):
            return UnauthorizedResp()

    monkeypatch.setattr("http.client.HTTPSConnection", UnauthorizedConn)
    with pytest.raises(AuthenticationError):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=30
            )
        )


# --------------------------------------------------------------------------- #
# OpenAIChatAdapter 流式分支
# --------------------------------------------------------------------------- #
def test_openai_chat_stream_aggregates(monkeypatch):
    provider = _provider(stream=True)
    frames = [
        json.dumps({"choices": [{"delta": {"content": '{"ok"'}}]}),
        json.dumps({"choices": [{"delta": {"content": ": 1}"}}]}),
        json.dumps(
            {
                "choices": [],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            }
        ),
        "[DONE]",
    ]
    captured: dict = {}

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        captured["url"] = url
        captured["payload"] = payload
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = OpenAIChatAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["stream_options"] == {"include_usage": True}
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert resp.content == '{"ok": 1}'
    assert resp.parsed_json == {"ok": 1}
    assert resp.usage.total_tokens == 5


def test_openai_chat_stream_degrades_without_stream_options(monkeypatch):
    # Review Focus #3：网关不认 stream_options -> 去参保流式重试
    provider = _provider(stream=True)
    frames = [json.dumps({"choices": [{"delta": {"content": '{"a": 1}'}}]}), "[DONE]"]
    calls: list[dict] = []

    def flaky_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        calls.append(dict(payload))
        if "stream_options" in payload:
            raise StreamHandshakeRejected(
                "模型服务返回 HTTP 400: stream_options not supported", code="provider_error"
            )
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", flaky_sse)
    adapter = OpenAIChatAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert "stream_options" not in calls[-1]
    assert calls[-1]["stream"] is True  # 保流式，仅去参数
    assert resp.parsed_json == {"a": 1}


def test_openai_chat_stream_falls_back_to_non_stream_on_400(monkeypatch):
    # Review Focus #4：网关整体拒绝 stream -> 回退非流式重试一次
    provider = _provider(stream=True)

    stream_calls: list[int] = []

    def reject_stream(url, *, payload, headers, timeout, max_seconds, api_key=None):
        stream_calls.append(1)  # 证明流式桩确被先调用（先拒绝后回退）
        raise StreamHandshakeRejected(
            "模型服务返回 HTTP 400: stream is not supported", code="provider_error"
        )

    monkeypatch.setattr("providers.adapters._http_post_sse", reject_stream)
    stub = _HttpStub(body={"choices": [{"message": {"content": '{"ok": 1}'}}]})
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIChatAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert stream_calls  # 流式桩确被先调用（先拒绝后回退）
    assert stub.calls  # 确实走了非流式路径
    assert resp.parsed_json == {"ok": 1}


def test_openai_chat_stream_eof_propagates_without_fallback(monkeypatch):
    # 断连不是"网关拒绝流式"：不得触发非流式回退（避免重复计费长请求）
    provider = _provider(stream=True)

    def eof_stream(url, *, payload, headers, timeout, max_seconds, api_key=None):
        return iter([json.dumps({"choices": [{"delta": {"content": "half"}}]})])

    monkeypatch.setattr("providers.adapters._http_post_sse", eof_stream)
    stub = _HttpStub(body={"choices": [{"message": {"content": "{}"}}]})
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIChatAdapter(provider, "m-1")
    with pytest.raises(ProviderError):
        adapter.chat(UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1"))
    assert not stub.calls


# --------------------------------------------------------------------------- #
# OpenAIResponsesAdapter 流式分支
# --------------------------------------------------------------------------- #
def test_openai_responses_stream_aggregates(monkeypatch):
    provider = _provider(protocol="openai_responses", stream=True)
    frames = [
        json.dumps({"type": "response.output_text.delta", "delta": '{"r": '}),
        json.dumps({"type": "response.output_text.delta", "delta": "2}"}),
        json.dumps(
            {
                "type": "response.completed",
                "response": {"usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}},
            }
        ),
    ]
    captured: dict = {}

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        captured["url"] = url
        captured["payload"] = payload
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = OpenAIResponsesAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert captured["url"].endswith("/responses")
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["text"]["format"] == {"type": "json_object"}
    assert resp.content == '{"r": 2}'
    assert resp.parsed_json == {"r": 2}
    assert resp.usage.total_tokens == 3


def test_openai_responses_stream_falls_back_to_non_stream_on_400(monkeypatch):
    provider = _provider(protocol="openai_responses", stream=True)

    stream_calls: list[int] = []

    def reject_stream(url, *, payload, headers, timeout, max_seconds, api_key=None):
        stream_calls.append(1)  # 证明流式桩确被先调用（先拒绝后回退）
        raise StreamHandshakeRejected(
            "模型服务返回 HTTP 400: streaming not supported here", code="provider_error"
        )

    monkeypatch.setattr("providers.adapters._http_post_sse", reject_stream)
    stub = _HttpStub(
        body={
            "output": [{"content": [{"type": "output_text", "text": '{"r": 2}'}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    )
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIResponsesAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert stream_calls  # 流式桩确被先调用（先拒绝后回退）
    assert stub.calls
    assert "stream" not in stub.calls[0][1]  # 回退请求不带 stream 参数
    assert resp.parsed_json == {"r": 2}


# --------------------------------------------------------------------------- #
# AnthropicAdapter 流式分支
# --------------------------------------------------------------------------- #
def test_anthropic_stream_aggregates_and_merges_usage(monkeypatch):
    provider = _provider(protocol="anthropic", stream=True)
    frames = [
        json.dumps({"type": "message_start", "message": {"usage": {"input_tokens": 4}}}),
        json.dumps({"type": "content_block_delta", "delta": {"text": '{"a": '}}),
        json.dumps({"type": "content_block_delta", "delta": {"text": "1}"}}),
        json.dumps({"type": "message_delta", "usage": {"output_tokens": 6}}),
        json.dumps({"type": "message_stop"}),
    ]
    captured: dict = {}

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        captured["url"] = url
        captured["payload"] = payload
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = AnthropicAdapter(provider, "claude-x")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "system", "content": "你是助手"}, {"role": "user", "content": "hi"}],
            model="claude-x",
            response_format={"type": "json_object"},
        )
    )
    assert captured["url"].endswith("/v1/messages")
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["system"].startswith("你是助手")  # system 顶级字段照旧
    assert resp.content == '{"a": 1}'
    assert resp.parsed_json == {"a": 1}
    assert resp.usage.prompt_tokens == 4
    assert resp.usage.completion_tokens == 6
    assert resp.usage.total_tokens == 10


def test_anthropic_stream_falls_back_to_non_stream_on_400(monkeypatch):
    provider = _provider(protocol="anthropic", stream=True)

    stream_calls: list[int] = []

    def reject_stream(url, *, payload, headers, timeout, max_seconds, api_key=None):
        stream_calls.append(1)  # 证明流式桩确被先调用（先拒绝后回退）
        raise StreamHandshakeRejected(
            "模型服务返回 HTTP 400: streaming is not supported", code="provider_error"
        )

    monkeypatch.setattr("providers.adapters._http_post_sse", reject_stream)
    stub = _HttpStub(
        body={
            "content": [{"type": "text", "text": '{"a": 1}'}],
            "usage": {"input_tokens": 4, "output_tokens": 6},
        }
    )
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = AnthropicAdapter(provider, "claude-x")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-x",
            response_format={"type": "json_object"},
        )
    )
    assert stream_calls  # 流式桩确被先调用（先拒绝后回退）
    assert stub.calls
    assert "stream" not in stub.calls[0][1]
    assert resp.parsed_json == {"a": 1}


# --------------------------------------------------------------------------- #
# 流式鉴权头（修复轮）：三协议流式调用必须与非流式路径同源鉴权
# --------------------------------------------------------------------------- #
def test_openai_chat_stream_passes_api_key(monkeypatch):
    # openai_chat 流式：api_key 经 _http_post_sse 参数透传（网关 401 防线）
    provider = _provider(stream=True)
    frames = [json.dumps({"choices": [{"delta": {"content": '{"ok": 1}'}}]}), "[DONE]"]
    captured: dict = {}

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        captured["api_key"] = api_key
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = OpenAIChatAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert resp.parsed_json == {"ok": 1}
    assert captured["api_key"] == provider.api_key
    assert captured["api_key"] is not None  # 区分"传了/没传"


def test_openai_responses_stream_passes_api_key(monkeypatch):
    # openai_responses 流式：同 openai_chat 的 api_key 透传要求
    provider = _provider(protocol="openai_responses", stream=True)
    frames = [
        json.dumps({"type": "response.output_text.delta", "delta": '{"r": 2}'}),
        json.dumps({"type": "response.completed", "response": {"usage": {"total_tokens": 1}}}),
    ]
    captured: dict = {}

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        captured["api_key"] = api_key
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = OpenAIResponsesAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert resp.parsed_json == {"r": 2}
    assert captured["api_key"] == provider.api_key
    assert captured["api_key"] is not None


def test_anthropic_stream_passes_auth_headers(monkeypatch):
    # anthropic 流式：headers 内联 x-api-key + anthropic-version（api_key 参数保持 None）
    provider = _provider(protocol="anthropic", stream=True)
    frames = [
        json.dumps({"type": "content_block_delta", "delta": {"text": '{"a": 1}'}}),
        json.dumps({"type": "message_stop"}),
    ]
    captured: dict = {}

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        captured["headers"] = headers
        captured["api_key"] = api_key
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = AnthropicAdapter(provider, "claude-x")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-x",
            response_format={"type": "json_object"},
        )
    )
    assert resp.parsed_json == {"a": 1}
    assert captured["headers"]["x-api-key"] == provider.api_key
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["api_key"] is None


# --------------------------------------------------------------------------- #
# store 加载防毒 + gemini 移除回归
# --------------------------------------------------------------------------- #
def test_store_skips_invalid_protocol_entry_with_warning(tmp_path, caplog):
    # Review Focus #5：存量文件含 gemini 等未知协议条目 -> 跳过并告警，不拒载
    import logging

    raw = [
        {
            "id": "bad",
            "name": "坏条目",
            "protocol": "gemini",
            "base_url": "https://x.example.com",
        },
        {
            "id": "ok",
            "name": "好条目",
            "protocol": "openai_chat",
            "base_url": "https://y.example.com",
        },
    ]
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="providers.store"):
        store = ProviderStore(path)
    assert [p.id for p in store.list_providers()] == ["ok"]


def test_gemini_protocol_removed_from_contract():
    from providers.models import ApiProtocol

    assert "GEMINI" not in ApiProtocol.__members__
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        _provider(protocol="gemini")


def test_gemini_adapter_removed_from_package():
    import providers

    assert not hasattr(providers, "GeminiAdapter")
    assert "GeminiAdapter" not in providers.__all__


def test_http_post_sse_overlong_line_without_newline_raises(monkeypatch):
    # 无换行慢滴流：单行达到行字节上限且无换行 => ProviderError 熔断，防内存无界增长
    class DripResp:
        status = 200

        def readline(self, limit=-1):
            return b"x" * _SSE_MAX_LINE_BYTES  # 恰为行上限且不以 b"\n" 结尾

    class DripConn(_SSEConnStub):
        def getresponse(self):
            return DripResp()

    monkeypatch.setattr("http.client.HTTPSConnection", DripConn)
    with pytest.raises(ProviderError, match="SSE 行超长"):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=1
            )
        )


# --------------------------------------------------------------------------- #
# 回退判定收紧（PR1）：仅握手阶段 400 可回退，mid-stream 永不重发
# --------------------------------------------------------------------------- #
def test_http_post_sse_handshake_400_raises_dedicated_type(monkeypatch):
    """首包 400 必须抛 StreamHandshakeRejected（回退资格判定的唯一依据）。"""

    class BadRequestResp:
        status = 400

        def read(self):
            return b"stream is not supported"

        def readline(self, limit=-1):
            return b""

    class BadRequestConn(_SSEConnStub):
        def getresponse(self):
            return BadRequestResp()

    monkeypatch.setattr("http.client.HTTPSConnection", BadRequestConn)
    with pytest.raises(StreamHandshakeRejected):
        list(
            _http_post_sse(
                "https://gw.example.com/x", payload={}, headers={}, timeout=5, max_seconds=30
            )
        )


def _midstream_fail_sse(frame: str):
    """构造 mid-stream 错误桩：先产出半个 delta，再抛文本含 400/stream 特征的错误帧。"""

    def _stub(url, *, payload, headers, timeout, max_seconds, api_key=None):
        yield frame
        raise ProviderError(
            "流式响应错误帧: HTTP 400 stream response_format not supported",
            code="provider_error",
        )

    return _stub


def test_midstream_error_never_falls_back_openai_chat(monkeypatch):
    # 事项 5 回归：mid-stream 错误文本巧合含 "HTTP 400"/"stream" 不得触发非流式重发
    provider = _provider(stream=True)
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _midstream_fail_sse(json.dumps({"choices": [{"delta": {"content": "he"}}]})),
    )
    stub = _HttpStub(body={"choices": [{"message": {"content": "{}"}}]})
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIChatAdapter(provider, "m-1")
    with pytest.raises(ProviderError):
        adapter.chat(UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1"))
    assert not stub.calls  # 绝不重发


def test_midstream_error_never_falls_back_openai_responses(monkeypatch):
    provider = _provider(protocol="openai_responses", stream=True)
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _midstream_fail_sse(json.dumps({"type": "response.output_text.delta", "delta": "he"})),
    )
    stub = _HttpStub(body={"output": [{"content": [{"type": "output_text", "text": "{}"}]}]})
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIResponsesAdapter(provider, "m-1")
    with pytest.raises(ProviderError):
        adapter.chat(UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1"))
    assert not stub.calls


def test_midstream_error_never_falls_back_anthropic(monkeypatch):
    provider = _provider(protocol="anthropic", stream=True)
    monkeypatch.setattr(
        "providers.adapters._http_post_sse",
        _midstream_fail_sse(json.dumps({"type": "content_block_delta", "delta": {"text": "he"}})),
    )
    stub = _HttpStub(body={"content": [{"type": "text", "text": "{}"}]})
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = AnthropicAdapter(provider, "claude-x")
    with pytest.raises(ProviderError):
        adapter.chat(
            UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="claude-x")
        )
    assert not stub.calls


@pytest.mark.parametrize(
    "exc",
    [
        AuthenticationError("鉴权失败（HTTP 401）"),
        ProviderError("模型服务返回 HTTP 403: forbidden", code="provider_error"),
        ProviderError("模型服务返回 HTTP 500: internal error", code="provider_error"),
        ProviderTimeoutError("请求超时: read timed out"),
    ],
    ids=["401", "403", "500", "timeout"],
)
def test_stream_non_handshake400_never_falls_back(monkeypatch, exc):
    """事项 7 矩阵：非握手 400（401/403/500/超时）三协议一律不回退非流式。"""
    cases = [
        (
            OpenAIChatAdapter,
            _provider(stream=True),
            "m-1",
            {"output": None},
        ),
        (
            OpenAIResponsesAdapter,
            _provider(protocol="openai_responses", stream=True),
            "m-1",
            None,
        ),
        (
            AnthropicAdapter,
            _provider(protocol="anthropic", stream=True),
            "claude-x",
            None,
        ),
    ]
    for adapter_cls, provider, model_id, _ in cases:

        def reject(url, *, payload, headers, timeout, max_seconds, api_key=None, _exc=exc):
            raise _exc

        monkeypatch.setattr("providers.adapters._http_post_sse", reject)
        stub = _HttpStub(body={"choices": [{"message": {"content": "{}"}}]})
        monkeypatch.setattr("providers.adapters._http_post", stub)
        adapter = adapter_cls(provider, model_id)
        with pytest.raises(ProviderError):
            adapter.chat(
                UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model=model_id)
            )
        assert not stub.calls, f"{adapter_cls.__name__} 在 {type(exc).__name__} 下误回退"


def test_responses_handshake_400_json_hint_falls_back(monkeypatch):
    # 事项 6：responses 回退判定补齐 JSON hints——报文无 "stream" 字样也回退
    provider = _provider(protocol="openai_responses", stream=True)

    def reject_json_mode(url, *, payload, headers, timeout, max_seconds, api_key=None):
        raise StreamHandshakeRejected(
            "模型服务返回 HTTP 400: text.format not supported", code="provider_error"
        )

    monkeypatch.setattr("providers.adapters._http_post_sse", reject_json_mode)
    stub = _HttpStub(
        body={
            "output": [{"content": [{"type": "output_text", "text": '{"r": 2}'}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    )
    monkeypatch.setattr("providers.adapters._http_post", stub)
    adapter = OpenAIResponsesAdapter(provider, "m-1")
    resp = adapter.chat(
        UnifiedChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="m-1",
            response_format={"type": "json_object"},
        )
    )
    assert stub.calls
    assert resp.parsed_json == {"r": 2}


# --------------------------------------------------------------------------- #
# SSE 解析协议对齐（PR2）：空 data 行忽略 / responses 错误帧识别 / 行上限
# --------------------------------------------------------------------------- #
def test_iter_sse_payloads_skips_empty_data_lines():
    # 事项 1：SSE 规范中空 data: 字段（仅冒号或纯空白）应忽略，不产出空负载
    lines = ["data:", "data: ", "data:\t", 'data: {"a": 1}', "data: [DONE]"]
    assert list(_iter_sse_payloads(lines)) == ['{"a": 1}', "[DONE]"]


def test_sse_line_limit_is_256kb():
    # 事项 8：行上限从 64KB 放宽到 256KB——合法大 payload 单行（带换行）不被误杀
    assert _SSE_MAX_LINE_BYTES == 256 * 1024


def test_consume_stream_extract_error_responses_error_event():
    # 事项 3：responses 协议 {"type": "error"} 事件须识别为错误帧而非"断连"
    frames = [
        json.dumps({"type": "response.output_text.delta", "delta": "he"}),
        json.dumps({"type": "error", "code": "server_error", "message": "boom"}),
    ]
    with pytest.raises(ProviderError, match="boom"):

        def _extract_error(frame):
            if frame.get("type") == "error":
                return frame
            if frame.get("type") == "response.failed":
                return (frame.get("response") or {}).get("error") or frame
            return None

        _consume_stream(
            frames,
            extract_delta=lambda f: (
                (f.get("delta") or "") if f.get("type") == "response.output_text.delta" else ""
            ),
            terminal=lambda f: f.get("type") == "response.completed",
            extract_usage=lambda f: None,
            extract_error=_extract_error,
        )


def test_consume_stream_extract_error_response_failed_maps_429():
    frames = [
        json.dumps({"type": "response.output_text.delta", "delta": "he"}),
        json.dumps(
            {
                "type": "response.failed",
                "response": {"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
            }
        ),
    ]

    def _extract_error(frame):
        if frame.get("type") == "error":
            return frame
        if frame.get("type") == "response.failed":
            return (frame.get("response") or {}).get("error") or frame
        return None

    with pytest.raises(RateLimitError):
        _consume_stream(
            frames,
            extract_delta=lambda f: (
                (f.get("delta") or "") if f.get("type") == "response.output_text.delta" else ""
            ),
            terminal=lambda f: f.get("type") == "response.completed",
            extract_usage=lambda f: None,
            extract_error=_extract_error,
        )


def test_openai_responses_stream_error_event_raises_not_disconnect(monkeypatch):
    # 适配器级回归：responses 流中段错误事件不再归因为"中途断连"
    provider = _provider(protocol="openai_responses", stream=True)
    frames = [
        json.dumps({"type": "response.output_text.delta", "delta": "he"}),
        json.dumps({"type": "error", "code": "server_error", "message": "upstream blew up"}),
    ]

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = OpenAIResponsesAdapter(provider, "m-1")
    with pytest.raises(ProviderError, match="upstream blew up"):
        adapter.chat(UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="m-1"))


# --------------------------------------------------------------------------- #
# 配置防呆与 usage 契约一致（PR3）
# --------------------------------------------------------------------------- #
def test_settings_stream_max_seconds_guards_nonpositive(monkeypatch):
    # 事项 2：env 误配 0/负数时回落安全默认 300（仿 SYNTHESIZER_TIMEOUT 先例）
    import importlib

    from config import settings as settings_mod

    for bad in ("0", "-5"):
        monkeypatch.setenv("PROVIDER_STREAM_MAX_SECONDS", bad)
        try:
            importlib.reload(settings_mod)
            assert settings_mod.PROVIDER_STREAM_MAX_SECONDS == 300
        finally:
            monkeypatch.delenv("PROVIDER_STREAM_MAX_SECONDS")
            importlib.reload(settings_mod)


def test_anthropic_stream_usage_none_when_absent(monkeypatch):
    # 事项 4：流式未收到 usage 帧 => usage=None（与非流式空 usage 语义一致）
    provider = _provider(protocol="anthropic", stream=True)
    frames = [
        json.dumps({"type": "content_block_delta", "delta": {"text": '{"a": 1}'}}),
        json.dumps({"type": "message_stop"}),
    ]

    def fake_sse(url, *, payload, headers, timeout, max_seconds, api_key=None):
        return iter(frames)

    monkeypatch.setattr("providers.adapters._http_post_sse", fake_sse)
    adapter = AnthropicAdapter(provider, "claude-x")
    resp = adapter.chat(
        UnifiedChatRequest(messages=[{"role": "user", "content": "hi"}], model="claude-x")
    )
    assert resp.content == '{"a": 1}'
    assert resp.usage is None  # 本测试焦点：无 usage 帧 => None
    assert resp.parsed_json is None  # 请求未启用 json_mode，解析关闭属预期
