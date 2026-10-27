# 本地 Docker 后端编排

在单机上拉起 MySQL / PostgreSQL / ClickHouse / Hive 四个外部数据源容器，用于本地复现
CI 的服务级门禁、验证后端连通性与调度链路稳定性。DuckDB 是进程内文件库，不需要容器。

## 快速开始

```bash
# 1. 起四个后端容器（首次需拉镜像，Hive 约 1.5GB）
docker compose -f docker/docker-compose.backends.yml up -d

# 2. 一键建仓 + 建只读账号 + 三档体检 + 出报告
python scripts/backend_matrix.py --skip-wait

# 3. 收尾（含卷，清空数据）
docker compose -f docker/docker-compose.backends.yml down -v
```

`scripts/backend_matrix.py --up` 会把第 1、2 步合并执行。

## 端口（一律偏移，不占默认端口）

| 服务 | 宿主机端口 | 容器端口 | 说明 |
|---|---|---|---|
| MySQL | 3307 | 3306 | 默认 3306 易与本机既有 MySQL 冲突 |
| PostgreSQL | 5433 | 5432 | 同上 |
| ClickHouse (HTTP) | 8124 | 8123 | |
| ClickHouse (Native) | 9001 | 9000 | |
| Hive (HiveServer2 HTTP) | 10001 | 10000 | **本机 10000 常被百度网盘 YunDetectService 占用** |

端口可用环境变量覆盖（`MYSQL_HOST_PORT` / `POSTGRES_HOST_PORT` /
`CLICKHOUSE_HOST_PORT` / `CLICKHOUSE_NATIVE_PORT` / `HIVE_HOST_PORT`），但改端口后
`scripts/backend_matrix.py` 里的 `HOST_PORTS` 必须同步——两处是同一套约定。

## 账号（两账号分离，本地演示口径）

| 后端 | 建仓账号 | 运行时只读账号 | 只读如何保证 |
|---|---|---|---|
| MySQL | root / root | dataagent_ro / ro_pwd | `GRANT SELECT ON gmall.*` |
| PostgreSQL | postgres / root | dataagent_ro / ro_pwd | `GRANT SELECT ON ALL TABLES` + `USAGE ON SCHEMA` |
| ClickHouse | default / ch_local_pwd | dataagent_ro / ch_ro_pwd | `readonly=1` profile（服务端固化，客户端不可改） |
| Hive | hive（无密码） | hive（同账号） | **无账号体系**——单容器 `authentication=NONE`，只读靠部署侧，属已知缺口 |

密码是本地/CI 演示值，禁止复用到生产。生产按各期 spec 的部署要求收窄授权。

## 生产接入前置条件

本地这套编排是**演示口径**，直接搬生产会留下防线空洞。接入前必须补齐：

| 项 | 为什么本地能省、生产不能省 |
|---|---|
| **Hive 认证（Kerberos / LDAP 或前置网关）** | 本地单容器 `authentication=NONE`，只读完全靠"没人写"；生产必须把"两账号分离 + 只读"变成可强制的机制。代码层无校验手段（HiveServer2 无会话级只读设置面），这是硬前置 |
| **Hive 扫描量上限（资源池 / 配额）** | Hive 拿不到扫描行数（`EXPLAIN` 无统计，需 `ANALYZE TABLE`），`explain_scan_rows` 恒返回不可计量。会话超时（`hive.query.timeout.seconds`，由 adapter 的 harden 自动设置）只能限时间，不能限读取量 |
| **ClickHouse 授权收窄到 `gmall.*`** | 本地 `GRANT SELECT ON *.*`；生产应收窄并走两账号分离 |
| **审计落库可观测** | 看 `/api/metrics` 的 `audit_write_failures`，非零即代表审计写不进库（只有 JSONL）。这条不是部署项，但接入时最容易漏 |

详细论证与实测数据见
[2026-10-27 问题诊断审计](../docs/reviews/2026-10-27-audit-project-issues.md)。

## 配置文件来源

ClickHouse 的只读 profile 与 Hive 的 `hive-site.xml` **直接复用 `.github/` 下的文件**
（`../.github/clickhouse/users.d/dataagent_ro.xml`、`../.github/hive/hive-site.xml`）——
CI 与本地是同一份配置，避免两处漂移。改预算（`max_rows_to_read` /
`max_execution_time`）必须与 `config/settings.py` 的 `MAX_SCAN_ROWS` /
`QUERY_TIMEOUT_MS` 同步，否则 adapter 的验证式加固会 fail-closed 拒绝连接。

## 已知坑（都已在编排里处理，改配置时别踩回去）

1. **Hive `/tmp/hive` 权限**：Hadoop 权限检查失败会进 60s 重试循环。CI 用
   `docker exec chmod 1777`，本地由体检脚本的 `wait_ready` 重试覆盖；手工排查时
   可执行 `docker exec dataagent-hive bash -c "mkdir -p /tmp/hive && chmod 1777 /tmp/hive"`。
2. **Hive `hive-site.xml` 必须整份**：单文件挂载是整体替换，只写 transport 四项会把
   `warehouse.dir` 打回 `/user/hive/warehouse` 导致建表失败。
3. **ClickHouse `users.d` 只能单文件挂载**：整目录 `:ro` 会让 entrypoint 写
   `default-user.xml` 失败并禁用 default 用户网络访问。
4. **ClickHouse 只读账号必须用 SQL 建**：`users_xml` 存储对 GRANT 只读，XML 里定义的
   用户无法授权。故账号创建放在 `clickhouse/initdb/00-gmall-and-ro-user.sql`。
5. **Hive staging 双向映射**：`LOAD DATA LOCAL INPATH` 的 LOCAL 是 hiveserver2 进程视角
   的路径，宿主机 `build/hive_staging` 与容器 `/opt/dataagent_staging` 必须是同一份内容。

## 体检脚本

`scripts/backend_matrix.py` 跑三档检查，任一失败退出码 1：

- **L1 连通性**：`connect_readonly()`（内含验证式加固）+ `SELECT 1`。
- **L2 实用性**：`tests/test_<backend>_backend_parity.py`（golden 全量 DSL 双后端一致性）
  + `semantic.catalog_loader` 物理元数据冒烟。
- **L3 调度链路稳定性**：经 `web.service.run_query` 走完整链路（意图路由 → 多工具 Agent
  → 编译 → 池化执行 → 结果组装），串行 N 轮 + 并发 M 线程，断言成功率、连接池不泄漏。

L3 强制清空 `LLM_API_KEY` 走确定性启发式——有 key 时延迟与结果都不确定，稳定性测量
失去意义。

报告落 `logs/backend_matrix.json`（机读）与 `logs/backend_matrix.md`（人读矩阵，可用
`--report` 改路径）。实测结论与遗留项见
[docs/reviews/2026-10-27-audit-five-backend-docker-matrix.md](../docs/reviews/2026-10-27-audit-five-backend-docker-matrix.md)。
