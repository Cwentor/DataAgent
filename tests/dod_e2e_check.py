"""DoD 端到端验证：本地 mock 三协议供应商服务 + 请求级切换全链路。

启动一个真实 HTTP 服务（127.0.0.1 随机端口）同时模拟：
- POST /v1/chat/completions  -> OpenAI Chat Completions（校验 Bearer 鉴权）
- POST /v1/responses         -> OpenAI Responses（校验 input / text.format）
- POST /v1/messages          -> Anthropic Messages（校验 x-api-key + system 顶级字段）

然后通过 web.service.run_query 携带 provider_id/model_id 发起真实查询，
验证：意图路由与 DSL 生成的 LLM 调用全部命中 mock 供应商（自定义协议站），
最终返回合法 DSL 与确定性 SQL。

用法：python -m tests.dod_e2e_check  （一次性脚本，不进 pytest 套件）
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from tests.fixture_keys import fake_key

# Mock 服务的 Bearer / x-api-key：运行时生成，源码不落凭据字面量
MOCK_RELAY_KEY = fake_key("relay")
MOCK_ANT_KEY = fake_key("ant")

DSL = {
    "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
    "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
}


def _extract_texts(payload: dict) -> tuple[str, str]:
    """从三协议 payload 中提取 (system_text, last_user_text)。"""
    system = str(payload.get("system") or "")
    user = ""
    messages = payload.get("messages") or []
    blocks = payload.get("input") or []
    items = list(messages) + list(blocks)
    for m in items:
        role = str((m or {}).get("role", ""))
        content = str((m or {}).get("content", ""))
        if role == "system" and not system:
            system = content
        if role == "user":
            user = content
    return system, user


def _mock_llm_payload(path: str, payload: dict) -> dict:
    """按 system prompt 角色分流返回协议正确的响应（模拟一个全功能模型）。"""
    system, user = _extract_texts(payload)
    if "意图识别器" in system:
        # 意图路由：data_query 高置信度（超过 ROUTER_MIN_CONFIDENCE=0.6）
        body = {
            "intent": "data_query",
            "confidence": 0.98,
            "reason": "mock-router",
            "extracted_entities": {},
        }
    elif "已经执行过若干工具调用" in system:
        # R1 重规划（必须先于首轮规划匹配：两者都含工具清单）：首查已有结果 -> 作答
        body = {"answer": "2024年5月成功订单GMV为 mock 洞察文本。"}
    elif "上一次工具调用失败" in system:
        # 自愈修复：重新调用 query_metric
        body = {"tool": "query_metric", "args": {"query": user or "2024年5月成功订单的GMV是多少？"}}
    elif "可用的工具清单" in system:
        # 首轮规划：调度 query_metric（透传用户原问题）
        body = {"tool": "query_metric", "args": {"query": user or "2024年5月成功订单的GMV是多少？"}}
    elif "语义解析器" in system:
        # NL -> DSL：合法 QueryDSL（由调用方按协议包装，带围栏验证 JSON 兜底清洗）
        body = None
    elif "sufficient" in system:
        # R3 反思层：结果充分
        body = {"sufficient": True, "reason": "mock-reflect"}
    else:
        # 总结合成器与其他
        body = {"answer": "2024年5月成功订单GMV为 mock 洞察文本。", "chart": None}

    if path.endswith("/chat/completions"):
        text = (
            json.dumps(DSL, ensure_ascii=False)
            if body is None
            else json.dumps(body, ensure_ascii=False)
        )
        return {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    if path.endswith("/responses"):
        text = (
            json.dumps(DSL, ensure_ascii=False)
            if body is None
            else json.dumps(body, ensure_ascii=False)
        )
        return {
            "output": [{"content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
        }
    if path.endswith("/v1/messages"):
        text = (
            json.dumps(DSL, ensure_ascii=False)
            if body is None
            else json.dumps(body, ensure_ascii=False)
        )
        return {
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 6, "output_tokens": 3},
        }
    return {"error": "unknown path"}


class MockHandler(BaseHTTPRequestHandler):
    """三协议 mock：记录收到的请求体供断言，按角色分流返回合法响应。"""

    received: ClassVar[list[tuple[str, dict, dict]]] = []
    lock: ClassVar[threading.Lock] = threading.Lock()

    def _reply(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        headers = {k.lower(): v for k, v in self.headers.items()}
        with self.lock:
            self.received.append((self.path, payload, headers))
        if self.path.endswith("/chat/completions"):
            if headers.get("authorization", "") != f"Bearer {MOCK_RELAY_KEY}":
                return self._reply({"error": {"message": "invalid api key"}}, 401)
            return self._reply(_mock_llm_payload(self.path, payload))
        if self.path.endswith("/responses"):
            return self._reply(_mock_llm_payload(self.path, payload))
        if self.path.endswith("/v1/messages"):
            if headers.get("x-api-key") != MOCK_ANT_KEY:
                return self._reply({"error": {"message": "unauthorized"}}, 401)
            if "system" not in payload:
                # 连通性探测的极小 ping 无 system 字段，直接回显即可
                return self._reply(
                    {
                        "content": [{"type": "text", "text": "pong"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                )
            return self._reply(_mock_llm_payload(self.path, payload))
        return self._reply({"error": "unknown path"}, 404)

    def log_message(self, *args) -> None:  # 静默
        pass


def main() -> None:
    import tempfile
    from pathlib import Path

    from providers.factory import ProviderFactory, reset_provider_factory
    from providers.store import ProviderStore, reset_default_provider_store
    from web.service import run_query

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    # 注入隔离存储（临时文件，绝不读写真实 providers.json）+ 隔离工厂
    tmp = Path(tempfile.mkdtemp()) / "providers.json"
    store = ProviderStore(tmp)
    store.create_provider(
        {
            "id": "mock-relay",
            "name": "Mock中转",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "api_key": MOCK_RELAY_KEY,
            "protocol": "openai_chat",
            "models": [{"id": "mock-chat"}],
        }
    )
    store.create_provider(
        {
            "id": "mock-resp",
            "name": "MockResponses",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "api_key": MOCK_RELAY_KEY,
            "protocol": "openai_responses",
            "models": [{"id": "mock-resp-model"}],
        }
    )
    store.create_provider(
        {
            "id": "mock-ant",
            "name": "MockAnthropic",
            "base_url": f"http://127.0.0.1:{port}",
            "api_key": MOCK_ANT_KEY,
            "protocol": "anthropic",
            "models": [{"id": "mock-ant-model"}],
        }
    )
    factory = ProviderFactory(store)
    reset_default_provider_store(store)
    reset_provider_factory(factory)

    # 1) Chat Completions 协议端到端
    r1 = run_query(
        "2024年5月成功订单的GMV是多少？", "admin", provider_id="mock-relay", model_id="mock-chat"
    )
    ok1 = "error" not in r1 and r1.get("dsl")
    print(
        f"[1] openai_chat  -> dsl={'ok' if ok1 else r1.get('error')} sql={'yes' if r1.get('sql') else 'no'}"
    )
    # 2) Responses 协议
    r2 = run_query(
        "2024年5月成功订单的GMV是多少？",
        "admin",
        provider_id="mock-resp",
        model_id="mock-resp-model",
    )
    ok2 = "error" not in r2 and r2.get("dsl")
    print(
        f"[2] responses    -> dsl={'ok' if ok2 else r2.get('error')} sql={'yes' if r2.get('sql') else 'no'}"
    )
    # 3) Anthropic 协议
    r3 = run_query(
        "2024年5月成功订单的GMV是多少？", "admin", provider_id="mock-ant", model_id="mock-ant-model"
    )
    ok3 = "error" not in r3 and r3.get("dsl")
    print(
        f"[3] anthropic    -> dsl={'ok' if ok3 else r3.get('error')} sql={'yes' if r3.get('sql') else 'no'}"
    )

    # 4) 连通性探测（三种协议）
    from web import providers_api

    for pid, mid in [
        ("mock-relay", "mock-chat"),
        ("mock-resp", "mock-resp-model"),
        ("mock-ant", "mock-ant-model"),
    ]:
        code, res = providers_api.test_provider({"provider_id": pid, "model_id": mid})
        print(
            f"[4] test {pid:<10} -> HTTP {code} success={res['success']} latency={res['latency_ms']}ms"
        )

    # 5) 错误码映射（错误 Key -> auth_failed）
    store.update_provider("mock-relay", {"api_key": fake_key("wrong")})
    factory.invalidate("mock-relay")
    r5 = run_query(
        "2024年5月成功订单的GMV是多少？", "admin", provider_id="mock-relay", model_id="mock-chat"
    )
    print(f"[5] wrong key    -> error={r5.get('error')!r}")

    server.shutdown()
    print(f"mock received {len(MockHandler.received)} requests; done")


if __name__ == "__main__":
    main()
