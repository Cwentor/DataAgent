"""本地 OpenAI 兼容 Chat Completions 模拟服务（用于端到端验证 LLM 路径）。

用法: python tools/mock_llm_server.py [端口]
仅用于离线验证协议正确性；真实业务请配置 LLM_BASE_URL 指向真实端点。
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from audit.logging import get_logger, setup_logging

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

_access_logger = get_logger("mock_llm.access")

EXPECTED_DSL = {
    "metrics": [
        {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"},
    ],
    "time_filter": {
        "granularity": "day",
        "range_type": "absolute",
        "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
    },
    "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
}


class Handler(BaseHTTPRequestHandler):
    """本地 Mock LLM 服务：返回受控 DSL JSON（带围栏，验证解析兼容性）。"""

    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        """处理 /chat/completions：校验 Bearer 鉴权后返回固定受控 JSON。"""
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception:
            return self._reply(400, {"error": {"message": "bad json"}})

        if not self.path.endswith("/chat/completions"):
            return self._reply(404, {"error": {"message": "not found"}})
        if "Authorization" not in self.headers or not self.headers["Authorization"].startswith(
            "Bearer "
        ):
            return self._reply(401, {"error": {"message": "missing bearer"}})

        # 模拟 LLM：返回受控 DSL JSON（用 Markdown 围栏包裹，顺带验证 agent 的围栏剥离）
        content = "```json\n" + json.dumps(EXPECTED_DSL) + "\n```"
        self._reply(
            200,
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": payload.get("model", "mock"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def log_message(self, fmt, *args):
        """结构化访问日志（Structured access logging）。"""
        status = str(args[1]) if len(args) > 1 else "-"
        size = str(args[2]) if len(args) > 2 else "-"
        _access_logger.info(
            fmt % args,
            extra={
                "event": "http_access",
                "method": self.command,
                "path": self.path,
                "client_ip": self.client_address[0],
                "status": status,
                "size": size,
            },
        )


if __name__ == "__main__":
    setup_logging()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"mock-llm listening on http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()
