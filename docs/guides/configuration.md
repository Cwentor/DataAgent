# 环境配置

配置加载顺序为：进程环境变量优先，其次是项目根目录 `.env`。模板见 [../../.env.example](../../.env.example)。

| 分组 | 变量 | 作用 |
| --- | --- | --- |
| 路径与锚点 | `DB_PATH`、`WORKSPACE_ROOT`、`AS_OF_DATE`、`DATA_DOMAIN_END`、`APPLOG_DIR` | 数仓路径（默认 `analytics_sandbox.duckdb`）、沙箱工作区根目录（默认 `logs/workspaces`）、评测与时间锚点（`AS_OF_DATE=2025-12-31`，`DATA_DOMAIN_END` 为时间域守卫上界）、埋点日志目录（Gmall 埋点 JSON 日志落盘路径，运行时自动创建） |
| LLM | `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_TEMPERATURE`、`LLM_TIMEOUT`、`LLM_MAX_RETRIES`、`PROVIDER_HANDSHAKE_TIMEOUT`、`PROVIDER_HANDSHAKE_RETRY_MAX`、`PROVIDER_STREAM_MAX_SECONDS` | OpenAI 兼容模型接入；握手期独立预算与安全重试（首字节到达前专用超时与重试上限）、流式调用整体最大时长 |
| 执行层 | `QUERY_TIMEOUT_MS`、`MAX_SCAN_ROWS`、`MAX_RESULT_ROWS`、`SQL_SELF_HEAL_MAX_RETRIES`（默认 3） | 超时、扫描熔断、结果上限与自愈 |
| 资源与缓存 | `DB_POOL_SIZE`、`DB_POOL_DISCARD_GRACE_MS`（默认 5000）、`MAX_CONCURRENT_QUERIES`、`QUERY_CACHE_ENABLED`（默认关）、`QUERY_CACHE_TTL_SECONDS`、`QUERY_CACHE_MAX_ENTRIES`、`MAX_SCAN_CACHE_SIZE`、`SCAN_CACHE_TTL_SECONDS`、`SCAN_PRECHECK_TWO_STAGE`（默认开）、`SCAN_PRECHECK_SAFETY_FACTOR`（默认 32）、`SCAN_PRECHECK_AUDIT_RATE`（默认 0.01）、`SANDBOX_BACKEND`（默认 subprocess） | 连接池容量与废弃连接延迟关闭宽限期、全局并发闸、按主体隔离的查询结果缓存与扫描预检缓存（容量/TTL）、扫描行预检两段式开关与安全系数/复核采样比例；沙箱执行后端（`auto` = Docker 优先探测并缓存，`docker` = 显式要求容器强隔离，不可用如实降级并留日志） |
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

## 连接池废弃与延迟关闭（`DB_POOL_DISCARD_GRACE_MS`）

超时/中断后的连接**不复用**，由 `pool.discard` 回收容量名额并补建一条新连接。这里
有两个顺序陷阱，都已在 `exec/pool.py` 里固定：

- **先建新、后关旧**：若先 `close()` 旧连接，操作系统会立刻把该 fd 号分配给新连接，
  而超时后仍阻塞在旧连接上的 worker 线程会抢读新连接的启动报文，把这条健康连接读坏。
  2026-10-27 Linux CI 的 `postgres-parity` 正是这么红的（`lost synchronization with
  server`，新连接建不起来、`pool.size` 掉到 0）。建新时旧 fd 仍被占用，OS 必然分配
  不同号码，竞争从根上消失。
- **关闭再推迟一个宽限期**：外部后端的 `adapter.cancel` 多为空操作，worker 退出时机
  不可控（靠服务端超时兜底，秒级到分钟级不等），晚一点关能进一步压小 fd 被复用的窗口。

`DB_POOL_DISCARD_GRACE_MS` 默认 `5000`（毫秒），覆盖看门狗 `join(2.0)` 窗口并留余量；
置 `0` 退回"立刻关闭"。待关连接有上限（`_MAX_DEFERRED = 64`），宽限期内高频废弃时
超限的最早连接会被立刻关掉，fd 占用不会无界堆积；池 `close()` 会排空队列，进程退出
不泄漏。

> 这条加固是**引擎无关**的：PG 已补 `psycopg.Connection.cancel()` 真取消，但
> MySQL / ClickHouse / Hive 的 `cancel` 仍是空操作，同一窗口都存在。故修复放在池层
> 而不是各 adapter——否则每加一个后端就要重赌一次驱动的关闭行为。

## 扫描行预检两段式（`SCAN_PRECHECK_*`）

DuckDB 的 `EXPLAIN ANALYZE` 会把查询**真的执行一遍**，所以"先计量后放行"的预检
等于让每条冷查询跑两遍。两段式改成：先 `EXPLAIN` 拿估算，估算足够小就跳过实测，
否则再走 `EXPLAIN ANALYZE`（设计见
[../designs/2026-10-27-scan-precheck-two-stage-design.md](../designs/2026-10-27-scan-precheck-two-stage-design.md)）。

- **估算只做"能否跳过实测"的判据，绝不做熔断值**：估算最大高估实测 750×，直接比
  阈值会误杀合法查询；熔断判定一律用实测值。
- **`SCAN_PRECHECK_SAFETY_FACTOR` 怎么调**：放行条件是 `est × SF < MAX_SCAN_ROWS`。
  调低 SF 提高跳过率但压缩安全边际，调高反之。用
  `python scripts/scan_precheck_calibration.py` 在**你的库**上重新校准，别照搬默认值。
  当前库（35 条 golden + 38 条合成过滤族）实测：

  | `MAX_SCAN_ROWS` | SF=4 | SF=8 | SF=32（默认） | SF=64 |
  |---|---|---|---|---|
  | 10,000,000（默认） | 35/35 | 35/35 | **35/35** | 35/35 |
  | 1,000,000 | 35/35 | 35/35 | **35/35** | 35/35 |
  | 100,000 | 35/35 | 32/35 | **8/35** | 0/35 |

  即：默认阈值下加大 SF 不损失跳过率（安全边际白送）；只有把阈值收紧到十万级才需要
  在"跳过率"与"安全边际"之间权衡。
- **`SCAN_PRECHECK_AUDIT_RATE`**：跳过路径不产生实测值，SF 是否还够用在运行期不可
  观测。该比例命中时强制走实测、按实测熔断，并记录 `est`/`actual` 偏差（超 SF 记
  `WARNING: scan_precheck_estimate_drift`）。生产建议保持 0.01；排障可临时设 1.0。
- **`SCAN_PRECHECK_TWO_STAGE=0`**：完全回退旧行为（只发 `EXPLAIN ANALYZE`），
  调用序列、缓存写入与告警行为与改造前逐点一致，用于定位"是不是预检判据的问题"。
- `count(*)` 这类走 DuckDB 元数据快路径的查询**恒走实测**（计划里只有
  `COLUMN_DATA_SCAN ~1 row`，那个 1 行是"输出 1 行"不是"扫 1 行"），属预期。

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
