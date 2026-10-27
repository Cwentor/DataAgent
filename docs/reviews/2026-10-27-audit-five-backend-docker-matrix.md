# 深度审计：五后端本地 Docker 实测矩阵（连通性 / 实用性 / 调度链路稳定性）

- **日期**：2026-10-27
- **范围**：DuckDB / MySQL / PostgreSQL / ClickHouse / Hive 五个执行后端
- **手段**：本地 Docker Compose 拉起四个外部数据源容器，真实建仓后跑三档体检
- **配套代码**：`docker/docker-compose.backends.yml`、`scripts/backend_matrix.py`、
  [docker/README.md](../../docker/README.md)

## 一、结论

**可行，且已在本机实测跑通。** 五后端 × 四档检查全部 PASS，调度链路在后端切换下
未出现连接池泄漏、并发失败或状态污染。

三点必须同时成立才谈得上「可行」：

1. **配置单一来源**：ClickHouse 只读 profile 与 Hive `hive-site.xml` 直接复用 `.github/`
   下 CI 用的同一份文件，本地与 CI 不存在两套配置各自演化的问题；
2. **两账号分离落实到位**：建仓用可写账号、运行时用只读账号，且只读属性由
   adapter 的验证式加固（`harden`）在每次建连时 fail-closed 校验——不是靠约定；
3. **端口偏移**：四个后端一律不用默认端口（3307/5433/8124/10001）。本机 10000 已被
   百度网盘 `YunDetectService` 占用，用默认端口必然踩坑。

同时本次实测撞出一个**真实的既存缺陷**（审计库 schema 迁移把 `re.Match` 对象当列名），
已修复并补回归测试，见 §四。

## 二、实测环境

| 项 | 值 |
|---|---|
| Docker | 29.8.0 / Compose v5.5.1（Docker Desktop，Windows） |
| 容器 | mysql:8.0、postgres:16、clickhouse/clickhouse-server:24.8、apache/hive:4.0.0 |
| Python | 3.12.14（conda env `dataagent`） |
| 链路模式 | 强制确定性启发式（清空 LLM 供应商装配），排除外部模型抖动 |
| 轮次 | 串行 10 轮（3 个问句轮转）+ 并发 4 线程 × 2 批 |

## 三、结果矩阵

| 后端 | 就绪 | 建仓 | L1 连通性 | L2 实用性 | L3 链路稳定性 |
|---|---|---|---|---|---|
| duckdb | 0.0s | 32.8s（首次） | PASS 0.0s | PASS 1.9s | PASS 2.0s |
| mysql | 0.1s | 67.6s | PASS 0.0s | PASS 23.8s | PASS 1.4s |
| postgres | 0.2s | 64.5s | PASS 0.0s | PASS 23.5s | PASS 1.1s |
| clickhouse | 0.3s | 32.9s | PASS 0.2s | PASS 26.0s | PASS 2.9s |
| hive | 0.2s | 52.4s | PASS 0.2s | PASS 65.4s | PASS 19.3s |

**L1 连通性**（`connect_readonly()` 内含 harden + `SELECT 1`）：

| 后端 | 建连 | 查询 |
|---|---|---|
| duckdb | 20.6ms | 1.0ms |
| mysql | 24.5ms | 0.4ms |
| postgres | 9.9ms | 0.5ms |
| clickhouse | 181.9ms | 50.5ms |
| hive | 108.0ms | 86.0ms |

ClickHouse 建连 182ms 是 harden 要查一次 `system.settings` 做验证式校验；Hive 的
108ms 是 HiveServer2 的 HTTP 会话握手成本。都属预期，不是缺陷。

**L2 实用性**：目标后端与 DuckDB 跑同一批 golden DSL，结果集逐行一致（容差口径见
`tests/parity_utils.py`）+ `semantic.catalog_loader` 能从真实后端读出物理元数据。

| 后端 | parity 用例 | 目录刷新 |
|---|---|---|
| duckdb | golden oracle 全 PASS | — |
| mysql | 5 passed | 读到 `fact_table=order_detail` |
| postgres | 5 passed | 同上 |
| clickhouse | 6 passed | 同上（只读账号读 `system.columns`） |
| hive | 6 passed | 同上（逐表 `DESCRIBE`） |

**L3 调度链路稳定性**（`web.service.run_query` 完整链路）：

| 后端 | 串行 | 并发 | p50 | p95 | 连接池 | ensure_db |
|---|---|---|---|---|---|---|
| duckdb | 10/10 | 8/8 | 65.5ms | 73.0ms | 4/4 无泄漏 | 0.07s |
| mysql | 10/10 | 8/8 | 74.0ms | 109.0ms | 4/4 无泄漏 | 0.18s |
| postgres | 10/10 | 8/8 | 55.0ms | 84.0ms | 4/4 无泄漏 | 0.09s |
| clickhouse | 10/10 | 8/8 | 111.0ms | 117.0ms | 4/4 无泄漏 | 0.92s |
| hive | 10/10 | 8/8 | **722.5ms** | **787.0ms** | 4/4 无泄漏 | **9.42s** |

## 四、关键发现

### 4.1 缺陷（已修）：审计库 schema 迁移把 Match 对象当列名

`audit/store.py::_migrate_schema` 用 `_AUDIT_COLUMN_IDENTIFIER_RE.fullmatch(column)`
做标识符校验，却把返回的 `Match` 对象直接插进 DDL：

```python
col = _AUDIT_COLUMN_IDENTIFIER_RE.fullmatch(column)
self._conn.execute(f'ALTER TABLE audit_log ADD COLUMN "{col}" {column_type}', ())
```

后果链：列名落成 `<re.Match object; span=(0, 11), match='answered_by'>` → 下次迁移
判定 `answered_by` 缺失 → 重复 ADD → `CatalogException: already exists` → **每条审计
记录写库失败**，只有 JSONL 侧有数据。异常被 `audit_write_failed` warning 吞掉，所以
一直没被发现。

本机 `logs/audit.duckdb` 里确实存在两个这样的畸形列（`answered_by`、
`planner_llm_error`），已随修复自愈（迁移时识别并 `DROP` 掉非法列名）。

修复：`col.group(0)` + 畸形列自愈清理。回归用例
`tests/audit/test_audit.py::test_migrate_schema_uses_matched_identifier_not_match_repr`
构造「旧表 + 畸形列」现场，断言迁移后列名合法且记录能落库。

> 这个缺陷只有在「同一进程反复写审计 + 表结构跨版本演进」时才会暴露，单测用
> 全新临时库跑不出来——是本次链路实测的直接收获。

### 4.2 短板：Hive 的链路延迟是其他后端的 7-13 倍

Hive p50 722ms vs 其他后端 55-111ms；`ensure_db` 9.42s vs 0.07-0.92s。原因清楚：

- `ensure_db` 走 `catalog_loader` 的物理元数据面，Hive 没有 `information_schema`
  统一视图，只能**逐表 `DESCRIBE`**——31 张表就是 31 次 HTTP 往返；
- 每次查询都要过 HiveServer2 的会话/作业提交，Tez local 模式下最轻的查询也是
  数百毫秒起步。

结论：Hive 能用，但**不适合交互式问数的主链路**，适合作为离线/批处理后端。若要做
生产接入，`ensure_db` 的元数据缓存是必须先解决的（当前每次启动都全量 DESCRIBE）。

### 4.3 已知设计边界（不是缺陷，但要知道）

- **ClickHouse / Hive 扫描行预检不可计量**：两者都没有 `EXPLAIN ANALYZE` 的行数面，
  `explain_scan_rows` 恒返回 `(0, False)`，实测日志里每轮都打「扫描行数预检不可计量」。
  真实预算由 LIMIT 硬上限 + 客户端超时 + 服务端 profile 兜底，不是无防护。
- **Hive 无账号体系**：`apache/hive:4.0.0` 单容器默认 `authentication=NONE`，两账号
  分离在本地无法落地（体检脚本用同一账号）。生产部署必须补 Kerberos/LDAP 或前置网关，
  这是 Hive 接入的实际前置条件，别在评估阶段忽略。

### 4.4 体检脚本自身的三个坑（已修，后续复用别再踩）

1. **`wait_ready` 用只读账号探活**：只读账号由建仓后的 provision 阶段创建，探活时还
   不存在，会把「账号未建」误报成「服务未起」。改为驱动级探活 + 建仓账号，并对
   `Access denied` 这类确定性错误立即失败而不是重试满超时。
2. **父进程 `DB_BACKEND` 污染子进程**：脚本切后端时写 `os.environ`，导致 pytest 子
   进程继承 `DB_BACKEND=mysql`，DuckDB 建库 fixture 用 MySQL 占位符 `%s` 灌数 →
   `Parser Error: syntax error at or near "%"`。改为只改 `settings` 属性 + 子进程从
   剥离后端变量的干净基线启动。
3. **parity 子进程不该带 `DB_BACKEND`**：parity 用例内部显式 `get_adapter("duckdb")`
   与目标后端 adapter，带 `DB_BACKEND` 只会污染 fixture。CI 的同名 job 也是这个口径。

## 五、复用方式

```bash
# 起容器（首次拉镜像约 1.5GB，Hive 最慢）
docker compose -f docker/docker-compose.backends.yml up -d

# 建仓 + 只读账号 + 三档体检 + 出矩阵
python scripts/backend_matrix.py

# 只重跑体检（容器数据已在）
python scripts/backend_matrix.py --skip-provision

# 单后端 / 加轮次
python scripts/backend_matrix.py --only hive --chain-rounds 20

# 收尾
docker compose -f docker/docker-compose.backends.yml down -v
```

矩阵落 `logs/backend_matrix.md`（人读）与 `logs/backend_matrix.json`（机读），
`logs/` 已在 gitignore 内。脚本任一档失败退出码 1，可直接挂进 CI 或本地预检。

## 六、遗留项

| 项 | 说明 | 优先级 |
|---|---|---|
| Hive 元数据缓存 | `ensure_db` 每次全量 DESCRIBE，9.4s；建议按表版本号缓存 | 中 |
| Hive 账号体系 | 本地/CI 单容器无认证，只读靠部署侧；生产需 Kerberos/LDAP | 高（生产前置） |
| 编排纳入 CI | 当前 CI 用 `services:` 逐个后端建 job；可考虑用本 compose 收敛为一个 job 矩阵 | 低 |
| Hive 链路压测 | 本次并发仅 4；HiveServer2 的并发作业能力未验证到上限 | 中 |
