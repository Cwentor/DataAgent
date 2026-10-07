# 环境配置

配置加载顺序为：进程环境变量优先，其次是项目根目录 `.env`。模板见 [../.env.example](../.env.example)。

| 分组 | 变量 | 作用 |
| --- | --- | --- |
| 路径与锚点 | `DB_PATH`、`WORKSPACE_ROOT`、`AS_OF_DATE`、`DATA_DOMAIN_END`、`APPLOG_DIR` | 数仓路径（默认 `analytics_sandbox.duckdb`）、沙箱工作区根目录（默认 `logs/workspaces`）、评测与时间锚点（`AS_OF_DATE=2025-12-31`，`DATA_DOMAIN_END` 为时间域守卫上界）、埋点日志目录（Gmall 埋点 JSON 日志落盘路径，运行时自动创建） |
| LLM | `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_TEMPERATURE`、`LLM_TIMEOUT`、`LLM_MAX_RETRIES`、`PROVIDER_HANDSHAKE_TIMEOUT`、`PROVIDER_HANDSHAKE_RETRY_MAX`、`PROVIDER_STREAM_MAX_SECONDS` | OpenAI 兼容模型接入；握手期独立预算与安全重试（首字节到达前专用超时与重试上限）、流式调用整体最大时长 |
| 执行层 | `QUERY_TIMEOUT_MS`、`MAX_SCAN_ROWS`、`MAX_RESULT_ROWS`、`SQL_SELF_HEAL_MAX_RETRIES`（默认 3） | 超时、扫描熔断、结果上限与自愈 |
| 资源与缓存 | `DB_POOL_SIZE`、`MAX_CONCURRENT_QUERIES`、`QUERY_CACHE_ENABLED`（默认关）、`QUERY_CACHE_TTL_SECONDS`、`QUERY_CACHE_MAX_ENTRIES`、`MAX_SCAN_CACHE_SIZE`、`SANDBOX_BACKEND`（默认 subprocess） | 连接池容量、全局并发闸、按主体隔离的查询结果缓存与扫描预检缓存；沙箱执行后端（`auto` = Docker 优先探测并缓存，`docker` = 显式要求容器强隔离，不可用如实降级并留日志） |
| 澄清 | `CLARIFY_SLOT_TTL` | 多轮澄清槽位上下文 TTL（秒） |
| 会话记忆 | `SESSION_MEMORY_TTL`、`SESSION_MEMORY_MAX_SESSIONS`、`SESSION_MEMORY_HISTORY_TURNS` | 多轮会话记忆的过期、容量与携带轮数 |
| 编排与 Agent | `MAX_AGENT_STEPS`、`AGENT_REFLECTION_ENABLED`、`AGENT_DEFAULT_AUTONOMY`（默认 L2）、`ORCHESTRATOR_RECURSION_LIMIT`、`ORCHESTRATOR_CHECKPOINT_DB`、`SUBAGENT_MAX_PARALLEL`、`SUBAGENT_REDISPATCH_MAX`、`SYNTHESIZER_TIMEOUT` | 单轮工具链路步数与反思层开关；编排链路自主性分级默认档（L1 每步计划确认 / L2 计划确认 / L3 计划确认 + 沙箱代码执行高危确认 / L4 全自动通知）、迭代上限、checkpointer SQLite 落盘路径（空则内存）、Subagent 并发与重派上限、综合报告合成超时（秒） |
| 意图路由 | `ROUTER_MIN_CONFIDENCE`、`ROUTER_LLM_TIMEOUT`、`ROUTER_LLM_MODEL` | 五分类意图路由的 LLM 置信阈值、超时与独立模型 |
| 异步任务 | `ASYNC_TASK_MAX_WORKERS`、`ASYNC_TASK_HISTORY`、`ASYNC_TASK_TTL_SECONDS` | 异步查询线程池与任务快照保留 |
| 流式 Run | `AGENT_RUN_MAX_RUNS`、`AGENT_RUN_MAX_EVENTS`、`AGENT_RUN_TTL_SECONDS`、`AGENT_RUN_KEEPALIVE_SECONDS`、`AGENT_RUN_IDLE_TIMEOUT_SECONDS` | SSE 编排 run 注册表的容量、事件缓冲、TTL 与保活 |
| 审计 | `AUDIT_ENABLED`、`LOG_LEVEL`、`AUDIT_LOG_FILE`、`AUDIT_LOG_PATH`、`AUDIT_DB_PATH`、`AUDIT_DIR` | 审计开关、日志级别；结构化日志可选落盘文件（配置路径后启用 `RotatingFileHandler` 轮转落盘，留空则仅输出到 stderr）、审计 JSONL 日志路径、审计 DuckDB 数据库路径、审计目录 |
| 认证 | `AUTH_ENABLED`、`AUTH_STRICT`、`WEB_HOST` | HTTP 鉴权与服务绑定 |
| 默认身份 | `AUTH_DEFAULT_PRINCIPAL`、`AUTH_DEFAULT_USER`、`AUTH_DEFAULT_DISPLAY` | 鉴权关闭时的服务端默认身份 |
| JWT | `AUTH_JWT_SECRET`、`AUTH_JWT_ISSUER`、`AUTH_JWT_AUDIENCE`、`AUTH_JWT_TTL` | JWT 签名与有效期 |
| Session | `AUTH_SESSION_TTL`、`AUTH_SESSION_DB` | 会话有效期与可选 SQLite 共享存储 |
| 限流 | `AUTH_LOGIN_MAX_FAILURES`、`AUTH_LOGIN_BASE_SECONDS`、`AUTH_LOGIN_MAX_SECONDS` | 登录失败指数退避 |
| 状态存储 | `STATE_STORE_DB` | 可选 SQLite KV 后端：Session / 澄清槽位 / 登录限流状态外置（多 worker 共享），空则进程内 |
| 供应商 | `PROVIDERS_ENC_SECRET`、`PROVIDER_TIMEOUT` | 模型供应商 API Key 加密主密钥（缺省回退 `AUTH_JWT_SECRET`）与供应商调用超时 |

## 生产注意事项

- 生产环境必须替换 `AUTH_JWT_SECRET`。
- `AUTH_STRICT=1` 或绑定非 localhost 地址时，启动会拒绝弱默认密钥与关闭鉴权。
- `AUTH_ENABLED=0` 也不会信任客户端传入的 principal，服务端仍使用 `AUTH_DEFAULT_*`。

## 模型供应商（providers）

多模型供应商网关的配置**不走 `.env`**，而是持久化于服务端 JSON 文件
`config/providers.json`（线程锁保护并发读写），推荐通过 Web 工作台的设置界面或
`/api/settings/providers` 端点管理（端点语义见 [API 参考](api.md)）：

- 供应商**全部由用户自行添加，无预置条目**（历史版本写入的预置条目会在加载时自动清理
  并生成 `.bak` 备份）；控制面（设置界面 / `/api/settings/providers`）接受 OpenAI Chat、
  OpenAI Responses、Anthropic 三种协议，适配层另含 Gemini 协议适配（控制面暂不放通）；
- API Key **落盘加密**（Encrypt-then-MAC，纯标准库实现）：文件内容不含明文，
  历史 JSON 中的明文 Key 会在加载时自动迁移为加密格式；
- API Key 只应通过工作台设置界面或 API 端点写入服务端加密存储；不要把任何可用凭据
  写入源码、示例、测试或仓库内任何文件；
- 加密主密钥取 `PROVIDERS_ENC_SECRET`（优先），缺省回退 `AUTH_JWT_SECRET`。
  **更换主密钥后已存密钥将无法解密**（校验失败即抛错，不静默降级），需在各供应商
  配置里重新填写 Key。
