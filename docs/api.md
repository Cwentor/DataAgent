# Web UI 与 API

## 启动服务

```bash
python -m web.server 8000
```

默认地址：`http://127.0.0.1:8000`。

## 端点

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/health` | GET | 健康检查 |
| `/api/metrics` | GET | QPS、分位数、意图/动作分布等进程内指标 |
| `/api/auth/login` | POST | 用户名/口令换取 JWT 与 Session |
| `/api/auth/logout` | POST | 吊销服务端 Session |
| `/api/auth/me` | GET | 返回当前身份 |
| `/api/query` | POST | 执行受保护的数据查询 |
| `/api/agent/run` | POST | Data Agent 同步编排（多步分析 + 沙箱 + HITL 恢复） |
| `/api/v1/agent/chat/stream` | GET | Data Agent SSE 流式编排（AgentStreamEvent 事件流） |
| `/static/` | GET | 双栏工作台前端 |

## 查询示例

登录：

```bash
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"analyst","password":"analyst123"}'
```

查询：

```bash
curl -X POST http://127.0.0.1:8000/api/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"query":"各品类成功订单的GMV分布？"}'
```

`/api/query` 的响应包含 DSL、SQL、列、行、解释与可视化建议；principal 不接受客户端传入值，由服务端身份映射决定。

## SSE 流式 Data Agent 会话

`GET /api/v1/agent/chat/stream` 以 `text/event-stream` 推送编排全过程，每帧为
`data: <AgentStreamEvent JSON>\n\n`。事件类型（与前端 `web/static/js/protocol.js` 契约一致）：

| 事件 | 载荷要点 | 前端消费 |
| --- | --- | --- |
| `plan_created` | `plan: [{id,title,kind,status}]` | 任务 DAG 时间线 |
| `step_start` | `step_id / step_title` | 图节点进度指示 |
| `tool_start` / `tool_end` | `tool: {name, input, output, duration_ms, error}` | 工具手风琴（DSL/沙箱代码展开） |
| `reflection` | `reflection: {observation, decision, reason}` | 反思/自愈节点 |
| `hitl_request` | `hitl: {question, resume_token}` | 澄清交互卡（可点击答复） |
| `artifact_emit` | `artifact: {type, title, content}` | 右栏画布（报告/图表/代码/数据表） |
| `done` / `error` | `report` / `error` | 终态收尾（状态灯复位） |

查询参数：`query`（必填）、`human_reply` + `resume_token`（HITL 恢复）、
`provider_id` + `model_id`（请求级模型切换）。鉴权与 `/api/query` 一致
（Bearer JWT / 会话 Cookie）；编排异常收敛为 `error` 事件，不中断 HTTP 流。
