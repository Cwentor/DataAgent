# 环境配置

配置加载顺序为：进程环境变量优先，其次是项目根目录 `.env`。模板见 [../.env.example](../.env.example)。

| 分组 | 变量 | 作用 |
| --- | --- | --- |
| LLM | `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_TEMPERATURE`、`LLM_TIMEOUT`、`LLM_MAX_RETRIES` | OpenAI 兼容模型接入 |
| 执行层 | `QUERY_TIMEOUT_MS`、`MAX_SCAN_ROWS`、`MAX_RESULT_ROWS`、`SQL_SELF_HEAL_MAX_RETRIES` | 超时、扫描熔断、结果上限与自愈 |
| 澄清 | `CLARIFY_SLOT_TTL` | 多轮澄清槽位上下文 TTL（秒） |
| 审计 | `AUDIT_ENABLED`、`LOG_LEVEL` | 审计开关与日志级别 |
| 认证 | `AUTH_ENABLED`、`AUTH_STRICT`、`WEB_HOST` | HTTP 鉴权与服务绑定 |
| 默认身份 | `AUTH_DEFAULT_PRINCIPAL`、`AUTH_DEFAULT_USER`、`AUTH_DEFAULT_DISPLAY` | 鉴权关闭时的服务端默认身份 |
| JWT | `AUTH_JWT_SECRET`、`AUTH_JWT_ISSUER`、`AUTH_JWT_AUDIENCE`、`AUTH_JWT_TTL` | JWT 签名与有效期 |
| Session | `AUTH_SESSION_TTL`、`AUTH_SESSION_DB` | 会话有效期与可选 SQLite 共享存储 |
| 限流 | `AUTH_LOGIN_MAX_FAILURES`、`AUTH_LOGIN_BASE_SECONDS`、`AUTH_LOGIN_MAX_SECONDS` | 登录失败指数退避 |
| 供应商 | `PROVIDERS_ENC_SECRET` | 模型供应商 API Key 加密主密钥（缺省回退 `AUTH_JWT_SECRET`） |

## 生产注意事项

- 生产环境必须替换 `AUTH_JWT_SECRET`。
- `AUTH_STRICT=1` 或绑定非 localhost 地址时，启动会拒绝弱默认密钥与关闭鉴权。
- `AUTH_ENABLED=0` 也不会信任客户端传入的 principal，服务端仍使用 `AUTH_DEFAULT_*`。

## 模型供应商（providers）

多模型供应商网关的配置**不走 `.env`**，而是持久化于服务端 JSON 文件
`config/providers.json`（线程锁保护并发读写），推荐通过 Web 工作台的设置界面或
`/api/settings/providers` 端点管理（端点语义见 [API 参考](api.md)）：

- 预置供应商（智谱 / OpenAI / Anthropic / Gemini）首次加载自动写入，`is_preset=True`
  不可删除（可禁用 / 编辑）；支持 OpenAI Chat、OpenAI Responses、Anthropic、Gemini
  四种协议适配；
- API Key **落盘加密**（Encrypt-then-MAC，纯标准库实现）：文件内容不含明文，
  历史 JSON 中的明文 Key 会在加载时自动迁移为加密格式；
- API Key 只应通过工作台设置界面或 API 端点写入服务端加密存储；不要把任何可用凭据
  写入源码、示例、测试或仓库内任何文件；
- 加密主密钥取 `PROVIDERS_ENC_SECRET`（优先），缺省回退 `AUTH_JWT_SECRET`。
  **更换主密钥后已存密钥将无法解密**（校验失败即抛错，不静默降级），需在各供应商
  配置里重新填写 Key。
