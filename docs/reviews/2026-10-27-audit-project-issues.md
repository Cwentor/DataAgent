# 深度审计：全项目问题诊断（连接池 / 护栏 / 依赖 / 技术债）

- **日期**：2026-10-27
- **范围**：执行层（`exec/`）、编排层（`core/`）、服务层（`web/`）、依赖与工程面
- **手段**：静态审计（全仓扫描）+ 本地 Docker 容器实测（复用五后端矩阵的四个容器）
- **前置**：[五后端本地 Docker 实测矩阵](2026-10-27-audit-five-backend-docker-matrix.md)
- **轮次**：两轮。第一轮查「防线缺不缺」（§二~§八，已修）；第二轮查「防线是否仍然
  有效」——即既有机制在数据增长、双写失败、并发压力下会不会自己失效（§九，2 个 P1 已修）
- **结论**：工程质量基线良好。第一轮的 1 个 P0 + 3 个 P1 已修；第二轮另发现
  **2 个 P1**（扫描行缓存过时致熔断失效、审计双写不一致且无恢复手段）与 6 项 P2，
  其中 2 个 P1 与 3 项 P2 已修，其余记债。

## 一、结论摘要

| 级别 | 项 | 状态 |
|---|---|---|
| **P0** | 超时后连接脏归还到池（Hive 上确定性触发） | **已修**（`connstate` 打标 + `pool.discard`） |
| **P1** | 扫描行熔断在 ClickHouse / Hive 上失效，Hive 无任何兜底 | **已修**（Hive 补服务端会话超时；告警去重） |
| **P1** | `sqlglot` 无版本上界（SQL 转译核心依赖裸奔） | **已修**（`<31` + 升级流程注释） |
| **P1** | Hive 无账号体系（生产接入前置条件缺失） | 文档记录（代码不可解） |
| **P1** | 扫描行缓存无 TTL、key 不含后端 → 数据增长后熔断静默失效（§9.1） | **已修**（作用域键 + TTL + `clear_scan_cache`） |
| **P1** | 审计双写无一致性保证、无 JSONL→DB 重建工具，实测缺 21% 记录（§9.2） | **已修**（`scripts/audit_rebuild.py`，历史 3469 条已补齐） |
| **P2** | 审计失败无计数告警 | **已修**（`/api/metrics` 计数 + 阈值告警） |
| **P2** | Hive `ensure_db` 启动 9.4s、`nodes.py` 3301 行、限流仅覆盖登录、超时路径零测试覆盖 | 部分修（超时路径已补用例，其余记债） |
| **P2** | duckdb 预检使冷查询延迟翻倍、Hive 设计文档漂移、12 项配置未入 `.env.example`、SSE 线程无上限、审计写入全局串行、P0 修复无集成测试（§9.3） | 部分修（文档漂移、配置项、P0 集成测试已修，其余 3 项记债） |

修复过程中另外撞出三个**新的真实缺陷**（详见 §八），一并修掉：

1. Hive 服务端超时的错误文本是 `found TIMEDOUT`（无空格连写），原识别词
   `"timeout"` / `"timed out"` 都不匹配 → 超时被误归一为 `SqlExecutionError`；
2. Hive `run_query` 对无结果集语句（`SET` 赋值 / DDL）抛
   `TypeError: 'NoneType' object is not iterable`；
3. 审计写入有**两个**入口（`web/service.py::run_query` 与 `web/api.py::/api/agent/run`），
   各自实现了一份「吞失败」逻辑——只改一处会让改进只覆盖一半路径。

**这个 P0 的特殊性**：它不是"配置错了"或"版本漂移"，而是**防线设计里缺了一环**——
超时能被检测到、能被抛给调用方，但连接的状态没有被一并作废。四个外部后端的
`cancel()` 全是空操作（各有 spec 依据），于是"看门狗超时"与"查询真的停下来"之间
存在时间差，而连接池对这个时间差一无所知。

## 二、P0：超时后连接脏归还

### 2.1 现象

`web/service.py` 在请求结束时无条件归还连接：

```python
finally:
    if own_conn and pool is not None:
        pool.release(conn)          # web/service.py:491-493
```

而 `exec/pool.py::release` 也是无条件的：

```python
def release(self, conn: Any) -> None:
    if self._closed:
        conn.close()
        return
    self._queue.put(conn)           # exec/pool.py:57-62
```

`exec/engine/watchdog.py` 在超时后的处理是：

```python
worker.join(timeout_ms / 1000.0)
if worker.is_alive():
    try:
        adapter.cancel(conn)        # 四个外部后端此处均为 return None
    except Exception:
        pass
    worker.join(2.0)                # 硬等 2 秒，不等结果
    logger.error("查询超时被强制取消")
    raise QueryTimeoutError(...)    # 无论 worker 是否退出都抛
```

三者组合起来：**看门狗抛超时 → 调用方 finally 归还连接 → worker 线程仍在用这条
连接执行 → 下一个请求从池里拿到同一个连接对象。**

### 2.2 实证

用 fake adapter（`cancel()` 空操作、查询阻塞 3s、`statement_timeout=300ms`）
复刻四个外部后端的行为，输出：

```
取到连接: <Conn A closed=False>
超时抛出: 耗时 2.32s
仍存活的 worker 线程: 1
归还后池内可用连接数: 1
下一个请求拿到: <Conn A closed=False>  —— 与超时连接同一对象? True
此刻 worker 是否仍在用同一连接执行: True
```

2.32s = 300ms 超时 + 2.0s `join` 硬等，与代码路径完全吻合。

### 2.3 触发条件：Hive 上是确定性的

四个外部后端的 `cancel()` 实现（均有注释与 spec 依据，不是遗漏）：

| 后端 | `cancel()` | 注释依据 |
|---|---|---|
| mysql | `return None` | 服务端 `max_execution_time` 已自限（spec D2） |
| postgres | `return None` | 服务端 `statement_timeout` 已自限（spec D3） |
| clickhouse | `return None` | 驱动无 cancel 方法（spec D5 退化路径） |
| hive | `return None` | 同步执行模型无可靠取消通道（spec D5） |

关键在于**客户端超时与服务端超时的相对时序**：

| 后端 | 客户端看门狗 | 客户端驱动层超时 | 服务端超时 | 超时后 worker 是否还在跑 |
|---|---|---|---|---|
| mysql | `QUERY_TIMEOUT_MS` | 无 | `max_execution_time = QUERY_TIMEOUT_MS` | 大概率同时退出（同值竞争） |
| postgres | `QUERY_TIMEOUT_MS` | 无 | `statement_timeout = QUERY_TIMEOUT_MS` | 同上 |
| clickhouse | `QUERY_TIMEOUT_MS` | `send_receive_timeout = QTM/1000 + 5s` | `max_execution_time = QTM/1000` | 同值竞争，网络半开时残留 |
| **hive** | `QUERY_TIMEOUT_MS` | **socket timeout = `QTM + 5s`** | **无强制超时** | **必然残留 ≥3s** |

Hive 的 `hive_adapter.py:169` 把 Thrift transport 的 socket 超时设为
`(QUERY_TIMEOUT_MS / 1000 + HIVE_TIMEOUT_EXTRA_SECONDS)`，其中
`HIVE_TIMEOUT_EXTRA_SECONDS = 5.0`。看门狗在 `QUERY_TIMEOUT_MS` 到点，
`join(2.0)` 后抛错，此时 worker 还要再等约 3 秒才会因 socket 超时退出。

**这 3 秒就是污染窗口**。窗口内任何并发请求 acquire 到这条连接，就会有两个线程
同时使用同一个 Thrift HTTP transport。

### 2.4 影响面

- **Hive**：确定性触发，每次超时都产生一条脏连接。Thrift HTTP transport 并发
  复用会串包（读到上一个查询的响应）。
- **MySQL / PostgreSQL**：同值竞争下大概率正常，但服务端超时未生效（账号无权限
  `SET SESSION`、PG 事务块内行为差异）、网络半开（TCP 黑洞，服务端已杀但客户端
  收不到）时同样触发。`pymysql` / `psycopg` 的并发使用会报
  `Commands out of sync` 或包序错乱。
- **ClickHouse**：HTTP 客户端相对无状态，风险最低，但 `conn` 内部共享 session。
- **放大因素**：`DB_POOL_SIZE` 默认 4、`MAX_CONCURRENT_QUERIES` 默认 4。池小，
  脏连接被复用的概率高；并发高时池满，请求会阻塞等这条脏连接归还。

### 2.5 为什么一直没被发现

超时路径**零测试覆盖**——`tests/` 下没有任何用例断言过"超时之后连接的状态"。
现有超时测试（`tests/engine/test_watchdog.py` 等）只验证"抛出了 `QueryTimeoutError`"，
不验证"连接是否还能用"。而生产里超时本身是低频事件，脏连接的下游报错会被
`engine_error` 归一化后当作"可自愈错误"重试掉，症状被吞掉了。

### 2.6 修复实现

三层改动，调用方零改动（5 个归还点不必逐个改，避免漏改）：

| 文件 | 改动 |
|---|---|
| `exec/engine/connstate.py`（新增） | 连接不可用标记：`mark_unusable` / `is_unusable` / `clear`。用 `WeakSet` 随对象回收自动清理；不可弱引用的 C 扩展连接退回 `id + 强引用`（池容量即上限，无泄漏） |
| `exec/engine/watchdog.py` | 超时分支无条件 `mark_unusable(conn)`。**即使 worker 已在 2 秒窗口内退出也打标**——连接上可能残留未读完的结果集（MySQL/PG 需 drain），重建一条连接是毫秒级代价，不值得赌 |
| `exec/pool.py` | `release()` 检查标记，命中即转 `discard()`；`discard()` 关闭连接、回收容量名额、**必要时补建一条唤醒排队者**（否则阻塞在 `acquire` 的等待者永远等不到归还，池会逐步缩容到 0） |

修复后复跑 §2.2 的原始复现：

```
初始连接: <Conn 1 closed=False>
超时抛出
脏连接是否已关闭: True
下一个请求拿到: <Conn 2 closed=False>
与超时连接同一对象? False   ← 修复前为 True
池容量: 1/1
```

回归用例（`tests/exec/test_pool.py` + `tests/engine/test_watchdog.py`，共 6 条）：
标记后归还即废弃、废弃唤醒阻塞等待者、补建失败时容量退回、关闭后废弃不抛错、
超时打标、正常执行不打标（防止"每次查询都废弃连接"把池退化成直连）。

## 三、P1：三个防线缺口

### 3.1 扫描行熔断在 ClickHouse / Hive 上失效

`exec/guards.py:553-588` 的预检逻辑：`adapter.explain_scan_rows()` 返回
`(scanned, measurable)`；`measurable=False` 时跳过熔断、打一条 warning、**且不缓存**。

两个 adapter 的实现是硬编码常量：

```python
# clickhouse_adapter.py:260-262
def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
    return 0, False

# hive_adapter.py:274-276
def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
    return 0, False
```

后果有两层：

1. **熔断失效**：`MAX_SCAN_ROWS`（默认 1000 万）在这两个后端上完全不生效；
2. **日志噪音**：因为"不可计量就不缓存"（`guards.py:568-579` 的刻意设计，避免把
   "测不到"固化成"扫了 0 行"的假事实），**每次查询都会刷一条 warning**。

兜底能力并不对等：

| 后端 | 扫描行熔断 | 服务端兜底 | 实际防线 |
|---|---|---|---|
| duckdb | 有效（`EXPLAIN ANALYZE` 解析） | — | 熔断 + 超时 |
| mysql | 有效（`EXPLAIN` 解析） | 无 | 熔断 + 超时 |
| postgres | 有效（`EXPLAIN` actual 段） | 无 | 熔断 + 超时 |
| clickhouse | **失效** | `max_rows_to_read`（profile，harden 校验） | 服务端兜底 + 超时 |
| **hive** | **失效** | 无行数上限；**会话超时已补**（本次修复） | 会话超时 + 结果行数上限 + LIMIT |

Hive 的 adapter 注释自己承认了这一点："真实预算 = LIMIT 硬上限 + 超时"。

#### 3.1.1 实测：两条"看起来能补"的路都走不通

修复前先实测了两个候选方案，结论是**都不能用来伪造熔断能力**：

| 候选方案 | 实测结果（Hive 4.0.0 / ClickHouse 24.8） | 结论 |
|---|---|---|
| Hive `EXPLAIN` 解析 `Statistics: Num rows` | 计划输出**完全没有** Statistics 段——CBO 统计依赖 `ANALYZE TABLE` 显式收集，建仓链路未做 | 不可用 |
| ClickHouse `EXPLAIN ESTIMATE` | 可用（返回 `[db, table, parts, rows, marks]`），但只对单表有效；`count()` 返回空集，部分查询直接报错 | 不采用 |

ClickHouse 侧不采用的理由是**它已经不需要**：profile 里的 `max_rows_to_read` 是
服务端强制计数（真实扫描量，比客户端估算更准），harden 还会 fail-closed 校验它与
应用预算一致。加一层估算预检反而可能误熔断（估算偏差直接拒绝合法查询，比不熔断更糟）。

Hive 侧则发现了真正的缺口：**服务端根本没有超时**。

#### 3.1.2 实际修复：给 Hive 补服务端会话超时

实测 `hive.query.timeout.seconds` 默认 `0s`（服务端永不终止查询），且该参数
**可会话级 SET**：

```
SET hive.query.timeout.seconds      -> hive.query.timeout.seconds is undefined
SET hive.server2.query.timeout      -> hive.server2.query.timeout is undefined
SET hive.stats.autogather           -> hive.stats.autogather=true
```

设置后立竿见影（8 表笛卡尔积）：

```
SET hive.query.timeout.seconds=2
服务端终止 @ 2.39s  ->  Expected states: [RUNNING, FINISHED], but found TIMEDOUT
```

因此 `hive_adapter.harden` 增加会话超时加固（fail-closed，回读校验）：

```python
timeout_s = max(1, math.ceil(settings.QUERY_TIMEOUT_MS / 1000.0))
cur.execute(f"SET hive.query.timeout.seconds={timeout_s}")
cur.execute("SET hive.query.timeout.seconds")   # 回读校验，设不上就拒绝连接
```

收益有两层：

1. Hive 的查询第一次有了**服务端强制终止**，不再只靠客户端 socket 读超时
   （原先服务端对超预算查询一无所知，会一直跑到自己结束）；
2. 顺带压缩了 P0 的连接污染窗口——服务端会主动杀查询，worker 更容易在
   `join(2.0)` 内退出（P0 的 `connstate` 打标仍是必需的兜底，不因这个改动取消）。

扫描行预算在 Hive 上**仍然是不可达的**，这一点不做粉饰：EXPLAIN 无统计、
无服务端行数上限设置面，真实防线 = 会话超时 + `MAX_RESULT_ROWS` 结果行数硬上限 +
编译层 LIMIT。要覆盖扫描量必须靠部署侧资源池/配额，属于接入前置条件。

#### 3.1.3 告警去重

`exec/guards.py` 增加 `_warn_unmeasurable_once`：同一后端只 WARNING 一次，后续降为
DEBUG。可计量性对给定引擎是恒定的（ClickHouse / Hive 恒不可计量），逐查询重复
WARNING 会把日志淹掉、掩盖真正的异常。首次告警保留完整口径说明与文档指引。

### 3.2 `sqlglot` 无版本上界

`requirements.txt`：

```
sqlglot>=25.0          # 无上界
duckdb>=1.5.6,<2.0     # 有上界，且写了 5 行注释解释原因
```

`duckdb` 那段注释记录了 2026-10-22 的 CI 事故：2.0 起聚合走元数据快路径、
执行计划被折叠为单算子，`parse_scan_rows` 失去计量口径，熔断静默失效。

`sqlglot` 是 `compiler/` 里 SQL 转译的核心——所有方言渲染都经它。大版本变更
AST 结构或生成器行为，会**静默产出语法合法但语义错误的方言 SQL**，而且这种错误
不会被单测捕获（单测断言的是当前版本的输出）。同一次事故的两个角色，只补了一个。

**已修**：`sqlglot>=25.0,<31`（当前实装 30.21），并在 requirements.txt 写明升级
流程——独立 PR → 全量 pytest → 四个后端 parity → 人工核对各 renderer 产物 diff。

### 3.3 Hive 无账号体系

本地 / CI 的 Hive 单容器 `authentication=NONE`，两账号分离（可写建仓 + 只读查询）
在本地落不了地。这与 MySQL / PostgreSQL / ClickHouse 的"只读账号 + harden 验证式
加固"防线不对等：Hive 的只读性**只靠部署侧授权**，代码层没有任何校验手段。

**这是生产接入的前置条件，不是可延后的优化**：必须先补 Kerberos / LDAP，或前置
网关做认证与只读约束，才能谈接入。代码层面无解，故本文档记录为接入门槛，并已同步
到 [docker/README.md](../../docker/README.md) 的「生产接入前置条件」一节。

## 四、P2：技术债

| 项 | 事实 | 影响 |
|---|---|---|
| Hive `ensure_db` 慢 | 启动期 9.42s（31 张表逐表 `DESCRIBE`），其他后端 0.07-0.92s | 表数线性恶化；仅影响启动，不影响单请求 |
| 审计写失败无告警 | 原 `web/service.py` 与 `web/api.py` 各一份 `except: logger.exception(...)`，无计数器 | **已修**：抽 `write_audit_safely` 公共函数，失败进 `metrics.audit_write_failures`，首次带栈、之后每 100 次汇总 |
| `nodes.py` 过大 | 3301 行 / 68 个函数；`synthesize_node` 221 行、`_run_query_step` 179 行 | 改动风险与评审成本高（需独立设计文档，不在本次范围） |
| 限流覆盖不全 | 仅登录接口有 `LoginRateLimitError`；查询接口只有 `BoundedSemaphore` 闸门 | 无按用户配额，单用户可占满全部并发（需产品定义配额模型） |
| 超时路径无测试 | 原 `tests/` 无任何"超时后连接状态"断言 | **已修**：6 条回归用例覆盖打标/废弃/补位/唤醒 |

## 五、已验证健康的面

审计不只找问题，以下各项经实测确认**不是纸面配置**：

- **`harden` 是 fail-closed 真防线**：故意把 ClickHouse profile 的
  `max_execution_time=30` 与 `QUERY_TIMEOUT_MS=1500` 制造漂移，连接直接被拒：
  ```
  SecurityError: ClickHouse max_execution_time=30 与应用预算不一致（期望 1）
                 ——部署 profile 漂移，须与 users.d 同步
  ```
- **异常吞没极少**：全仓 `except Exception` 后 `pass`/`continue` 仅 2 处
  （`nodes.py:2542` 目录不可用不阻断渲染、`watchdog.py:44` cancel 失败仍须走超时分支），
  且都有注释说明为何必须吞。
- **密钥不落库**：`config/providers.json` 已在 `.gitignore:47`；`.env` 未被跟踪。
- **依赖管理规范**：`requirements-dev.txt` 中 langgraph / fastapi 等 0.x 快速迭代
  生态一律精确 pin，升级走独立 PR + 全量回归门。
- **CI 覆盖完整**：lint / test / 四个后端 parity（mysql / postgres / clickhouse / hive）
  + nightly。
- **鉴权链路完整**：JWT + 服务端会话（`SqliteSessionStore`）+ 登录限流，
  27 个 API 路由均经 `_require_auth`。

## 六、复现方法

### 6.1 P0 脏连接（无需容器）

```python
# 见 §2.2：fake adapter，cancel 空操作 + 查询阻塞 3s
pool = ReadOnlyConnectionPool(SlowAdapter(), max_connections=1)
conn = pool.acquire()
try:
    run_with_timeout(SlowAdapter(), conn, "SELECT 1", 300)
except QueryTimeoutError:
    pass
pool.release(conn)                    # 复刻 web/service.py 的 finally
assert pool.acquire() is conn         # True —— 同一对象，worker 仍在使用
```

### 6.2 P1 熔断失效

```python
from exec.engine import get_adapter
for b in ("clickhouse", "hive"):
    assert get_adapter(b).explain_scan_rows(None, "SELECT 1", 30000) == (0, False)
```

### 6.3 全量矩阵

```bash
docker compose -f docker/docker-compose.backends.yml up -d
python scripts/backend_matrix.py
docker compose -f docker/docker-compose.backends.yml down -v
```

## 七、修复执行记录

| 顺序 | 项 | 实际改动 | 验证 |
|---|---|---|---|
| 1 | P0 脏连接 | 新增 `exec/engine/connstate.py`；`watchdog` 超时打标；`pool.release` 自动分流 + `discard` 补位 | 原始复现脚本：脏连接关闭、新连接 Conn 2、容量 1/1；6 条新用例 |
| 2 | P1 Hive 超时 | `hive_adapter.harden` 加 `SET hive.query.timeout.seconds` + 回读 fail-closed | 真实 Hive：回读 `=2`，笛卡尔积 2.47s 被服务端终止 |
| 3 | P1 告警去重 | `guards._warn_unmeasurable_once` + `reset_unmeasurable_warnings` | 新用例断言同一后端只 1 条 WARNING |
| 4 | P1 依赖上界 | `requirements.txt`：`sqlglot>=25.0,<31` + 升级流程注释 | — |
| 5 | P2 审计告警 | `metrics.record_audit_write_failure` + `write_audit_safely` 公共函数（两处入口统一） | 新用例：失败计数 +1、`/api/metrics` 可见 |
| 6 | 附带缺陷 | Hive `run_query` 容忍无结果集；`engine_error` 识别 `TIMEDOUT` | 真实 Hive 复验 + 2 条新用例 |

**未做**：`nodes.py` 拆分（3301 行，需独立设计文档与多期实施）、Hive 元数据缓存
（需先量化表数增长曲线）、查询接口按用户配额（需产品定义配额模型）、Hive 账号体系
（部署侧前置条件，代码不可解）。

## 八、修复过程中新发现的缺陷

这三个都是"动手改才暴露"的，静态审计看不出来：

### 8.1 Hive 服务端超时的错误文本漏识别

`engine_error` 原本只认 `"timeout"` 与 `"timed out"`，而 HiveServer2 会话超时的
真实文本是：

```
Expected states: [RUNNING, FINISHED], but found TIMEDOUT
```

`TIMEDOUT` 是连写的，两个关键词都不匹配 → 超时被归一成 `SqlExecutionError`，
自愈层会把它当普通引擎错误重试（语义完全错位）。已在识别词里补 `"timedout"`。

**这个缺陷只在"给 Hive 配了服务端超时"之后才会出现**——原来的实现里服务端永不超时，
所以错误文本从没机会被观察到。修 A 才暴露 B。

### 8.2 Hive `run_query` 对无结果集语句崩溃

```python
columns = [str(d[0]) for d in cur.description]   # description 为 None -> TypeError
```

`SET` 赋值 / DDL / `USE` 都没有结果集。加固语句与运维脚本会走这条路径。改为
`description` 为空时直接返回 `([], [])`（同时避免 `fetchall()` 抛
`ProgrammingError: No result set`）。

### 8.3 审计写入有两个入口，各自实现了一份吞失败逻辑

`web/service.py::run_query` 与 `web/api.py::/api/agent/run` 各写了一遍
`try: store.write(...) except: logger.exception(...)`。修复时先只改了前者，跑测试
才发现后者的失败完全没有被计数——**这类重复实现会让任何"可观测性改进"只覆盖一半
路径**。已抽成 `web/service.py::write_audit_safely`，两处共用。

## 九、第二轮审查：防线是否仍然有效

第一轮问的是「防线缺不缺」，第二轮问的是**「已有防线在时间维度上还成不成立」**——
即数据增长、双写失败、并发压力之后，那些看起来在工作里的机制会不会自己失效。

结论是：**会，而且两处都失效得很安静**。这一轮的发现都是"能跑、测试全绿、
但护栏已经不再保护任何东西"的类型。

### 9.1 P1：扫描行缓存无 TTL，数据增长后熔断静默失效

`exec/guards.py` 的缓存键只 hash SQL 文本：

```python
def _scan_cache_key(sql: str) -> str:
    normalized = " ".join(sql.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
```

三个问题叠在一起：

1. **没有 TTL**：缓存一旦写入就永久有效，只有条目数超过 `MAX_SCAN_CACHE_SIZE`
   （默认 512）才整体 `clear()`；
2. **key 不含数据版本**：数据从 1000 行涨到 200 万行，缓存里仍是 1000；
3. **key 不含后端/库**：同一进程串多后端（体检脚本正是如此）会跨引擎复用同一份
   扫描行数。

命中缓存即跳过预检，直接用旧行数比阈值。实证（临时 DuckDB，同一 SQL）：

```
1) 数据 1000 行，预算 10000 -> 通过（scan_rows=1000），已缓存 1 条
   已灌入 200 万行（模拟数据增长）
2) 真实扫描 200 万，预算 10000 -> 未熔断（用缓存 scan_rows=1000）
3) 清缓存后同一 SQL -> 熔断（证明第 2 步放行来自过时缓存）
```

**这是与 duckdb 2.0 事故完全相同的失败模式**——`requirements.txt` 里为那次事故
写了五行注释（"熔断静默失效"），但缓存这条路径上同样的事又发生了一遍，且更隐蔽：
版本事故至少会打 warning（不可计量），缓存过时连 warning 都没有，日志一片干净。

加重因素：`_SCAN_CACHE` 没有对外清理接口，数据更新后**除了重启进程没有别的办法**。

#### 9.1.1 修复实现

| 改动 | 内容 |
|---|---|
| 缓存键加作用域 | `_scan_cache_key(sql, scope)`，`scope = _adapter_scope(adapter)` = 后端名 + 数据源标识（外部后端 `host:port/database`，DuckDB 取文件路径）。同一 SQL 在不同引擎/库上不再串用 |
| 条目带时间戳 + TTL | 值从 `int` 改为 `(scan_rows, cached_at)`；读取时 `now - cached_at > SCAN_CACHE_TTL_SECONDS` 即视为未命中。新增配置 `SCAN_CACHE_TTL_SECONDS`（默认 300s，0 = 不过期） |
| 手动失效出口 | 新增 `clear_scan_cache()`；`mock/init_duckdb.py` 重建数据后、`scripts/backend_matrix.py` 切后端时自动调用 |

TTL 取值权衡：预检本身等于把查询**真的执行一遍**（见 §9.3），所以 TTL 太短会明显拖慢；数据量通常以小时/天级变化，300s 足以覆盖同一会话内的重复预检，又不至于让过时值长期生效。

修复后复跑同一实验（TTL 设为 1s 便于观察）：

```
1) 1000 行，预算 10000 -> 通过（scan_rows=1000），已缓存
   已灌入 200 万行
2) TTL 内 -> 仍用缓存（scan_rows=1000），符合设计
3) TTL 过期后重新预检 -> 熔断（修复生效）
```

回归用例 3 条：跨后端作用域隔离、TTL 过期返回未命中、`clear_scan_cache` 生效。

### 9.2 P1：审计双写不一致，且没有恢复手段

`AuditStore.write` 是两个独立通道的顺序写：

```python
if self.jsonl_path is not None:
    self._append_jsonl(record)   # 先写，失败即抛
if self.db_path is not None:
    self._insert_db(record)      # 后写，失败时 JSONL 已落盘
```

DB 写失败时 JSONL 已经写成功 → **两通道不一致**。第一轮我给它加了计数
（`audit_write_failures`，现在能看见了），但数据仍然不一致，而且 `scripts/` 下
**没有任何 JSONL → DuckDB 的回灌工具**。

本机实测：

```
JSONL 行数: 16461
DuckDB 行数: 12992
差额: 3469  <- 21% 的记录在库里查不到，且永久不可恢复
```

这 3469 条正是畸形列 bug 期间的记录。审计的用途是追溯与合规，21% 的缺口不是
小数——现在的状态是"能报警，但修不了"。

#### 9.2.1 修复实现

**不加死信队列，改为承认「JSONL 是权威源、DB 是派生物」。** 顺序写决定了 DB 成功
必然意味着 JSONL 已成功，反之不成立——所以 JSONL 天然是超集，只要能从它重建，
就不需要额外的死信通道。

| 改动 | 内容 |
|---|---|
| `AuditStore.write_db_many` | 批量**仅**写 DB 通道：一次跨进程锁 + 一次连接 + `executemany`。逐条走 `_insert_db` 的话每条都要开关连接，3469 条要几分钟 |
| `AuditStore.existing_request_ids` | 批量查已存在的 `request_id`（分块 500），用于算差集；表不存在时返回空集而非抛错 |
| `_record_params` 抽取 | 单条写入与批量回灌共用同一份字段映射——两处各写一遍，加字段时必然漏一处（本项目已有 §8.3 的前车之鉴） |
| `scripts/audit_rebuild.py` | 读 JSONL → 按 `request_id` 去重 → 批量回灌 → 复核行数。支持 `--dry-run` / `--since` / `--batch-size` |

本机实际回灌结果：

```
可解析记录 : 16461
库内已有   : 12992
待回灌     : 3469
回灌完成   : 写入 3469 条，库内行数 12992 -> 16461
（再次运行）待回灌 : 0 → 无需回灌（两通道已一致）
```

历史缺口已补齐（16461 = 16461），且重复执行幂等。回归用例 5 条，含 CLI 端到端与
`--dry-run` 不写库。

### 9.3 P2：六项

| 项 | 事实 | 影响 | 状态 |
|---|---|---|---|
| **duckdb 预检让冷查询延迟翻倍** | `explain_scan_rows` 走 `EXPLAIN ANALYZE`，**会真的执行查询**。同一查询：直接执行 33.8ms / 预检 29.3ms ≈ 2x；小查询 1.0ms vs 1.8ms（2.78x） | 缓存命中才免，但问数场景 SQL 很少完全重复 → 每个新查询都要等两遍 | 设计已出（见 §9.5） |
| **Hive 设计文档与实现漂移** | design D4 写「harden 校验 `SET -v` 输出」，实际是 `current_user()` 回读；本轮又加了 `hive.query.timeout.seconds` | 读文档的人会按错的验证面排查 | **已修**（见 9.3.1） |
| **12 项运行时配置未入 `.env.example`** | `AGENT_RUN_*`（5 项）、`ASYNC_TASK_*`（3 项）、`SESSION_MEMORY_*`（3 项）、`MAX_SCAN_CACHE_SIZE` | 运维不知道这些参数存在，也就不会去调 | **已修**（见 9.3.1） |
| **SSE 每连接一个线程** | `web/api.py:746` 直接 `Thread(target=_pump, daemon=True).start()`，无并发上限 | 客户端大量连接时线程数线性增长；FastAPI 场景本可用 async 生成器 | 待修（见 §9.5） |
| **审计写入是全局串行点** | `AuditStore._lock` 覆盖 JSONL 追加 + DuckDB 插入，每次查询末尾都过这把锁 | 高并发下审计写入成为吞吐瓶颈 | 待修（见 §9.5） |
| **本轮 P0 修复只有单测** | parity / 矩阵都不测超时场景 | 真实后端"超时后连接是否被废弃"无 CI 验证——正是原缺陷能潜伏那么久的原因 | **已修**（见 9.3.2） |

#### 9.3.1 已修的两项

**文档漂移**：`docs/designs/2026-10-27-hive-backend-design.md` 的 D4 / D5 按实现重写，
并在两节末尾各加一个引用块标注「2026-10-27 审计修订」，说明改了什么、为什么改——
避免下次读文档的人以为修订块是原始设计的一部分。

| 决策 | 原文档 | 改为（与实现一致） |
|---|---|---|
| D4 加固验证面 | harden 校验 `SET -v` 输出中 `hive.security.authorization.enabled` | 会话超时 `SET hive.query.timeout.seconds` + 回读确认生效；身份侧只做 `current_user()` 回读 |
| D5 超时来源 | 客户端 socket + 部署侧 `hive.server2.query.timeout` | 实测服务端默认 0s（等于无超时），改为 harden 每次建连都 SET，fail-closed |

**配置项缺失**：`.env.example` 补 12 项（`MAX_SCAN_CACHE_SIZE`、`SCAN_CACHE_TTL_SECONDS`、
`AGENT_RUN_*` 5 项、`ASYNC_TASK_*` 3 项、`SESSION_MEMORY_*` 3 项），并逐项与
`config/settings.py` 的默认值核对一致（第一版凭印象写错了 7 项，已按源码改正并加校验）。

#### 9.3.2 P0 修复的集成测试（第三项已修）

原问题不是「没测」，而是**测的位置不对**：单测覆盖了「watchdog 打标」与
「`pool.release` 分流」两段，却没有任何用例跑真实驱动——而原缺陷恰恰只在真实
后端上确定性复现（四个外部后端的 `cancel()` 全是空操作，worker 线程会继续阻塞
在同一条连接上）。用 mock 连接测这段，等于只验了自己写的标记函数。

新增三层覆盖：

| 层 | 位置 | 验什么 |
|---|---|---|
| 无容器兜底 | `tests/exec/test_pool.py::test_timeout_through_pool_discards_connection` | `execute_sql` 超时 → 打标 → 归还即废弃 → 下次拿到**新**连接（DuckDB，默认 CI 就跑） |
| 真实后端 ×4 | `tests/test_{dual,pg,clickhouse,hive}_backend_parity.py::test_timeout_discards_connection` | 同一条链路的真驱动版本；矩阵 L2 跑 parity 文件时自动带上 |
| 变异验证 | 手工（见下） | 把 `watchdog.mark_unusable` 换成空操作，用例按预期失败——证明断言不是空转 |

四个后端实测（`statement_timeout_ms=300`，慢查询各自 3 秒）：

```
MySQL       SELECT SLEEP(3)                                 1 passed in 4.19s
PostgreSQL  SELECT pg_sleep(3)                              1 passed in 3.55s
ClickHouse  SELECT sleep(3)                                 1 passed in 3.88s
Hive        SELECT reflect('java.lang.Thread','sleep',3000)  1 passed in 3.15s
```

变异验证（把打标改回空操作，模拟 P0 修复被回退）：

```
$ python -c "import exec.engine.watchdog as wd; wd.mark_unusable = lambda c: None; ..."
变异后按预期失败 -> 超时后连接未被标记为不可复用
```

慢查询的选型不是随手写的：**不拿笛卡尔积做首选**——Hive `hive.mapred.mode=strict`
会直接拒绝交叉连接，且 `execute_sql` 前置的 GuardrailAgent 会把笛卡尔积升级为
执行前熔断（`tests/test_exec_audit.py` 已钉住这一点）。Hive 无 sleep 内建函数，
故用 `reflect` 调 java `Thread.sleep`（不启 Tez 作业），并保留交叉连接作候选
回退——`tests/parity_utils.py::SLOW_SQLS` 的列表语义就是「前一条被后端拒绝就换
下一条」，失败原因不会被误报成超时失败。

顺带修掉一个让矩阵**不可信**的缺陷：`check_usefulness` 调 pytest 时未传
`--basetemp`，临时目录落在系统 `%TEMP%`，pytest 清理上一轮残留时文件数（62）
超过批量删除保护阈值（50）被拦，退出码变 1——而用例全绿，表现为「L2 假失败」。
ClickHouse 一档实测复现（`[safe-delete]...count:62,threshold:50` + 全点号输出 +
rc=1），补 `--basetemp=logs/.pytest_parity_<backend>` 后复跑 L2 PASS。

### 9.4 复现方法

```python
# 9.1 缓存过时（无需容器，临时库即可）
#   1. 建 1000 行的表，execute_sql(..., max_scan_rows=10000) -> 通过并缓存
#   2. 灌到 200 万行
#   3. 同一 SQL 再执行 -> 不熔断；_SCAN_CACHE.clear() 后同一 SQL -> 熔断

# 9.2 双写差额
python - <<'PY'
import duckdb
from config import settings
n_jsonl = sum(1 for _ in open(settings.AUDIT_DIR / "audit.jsonl", encoding="utf-8"))
c = duckdb.connect(str(settings.AUDIT_DB_PATH), read_only=True)
print(n_jsonl, c.execute("SELECT count(*) FROM audit_log").fetchone()[0])
PY
```

### 9.5 处置建议（更新于 2026-10-27）

| 优先级 | 项 | 建议改法 | 状态 |
|---|---|---|---|
| 高 | 9.1 缓存过时 | 键加 `adapter.name` + 库名；加 TTL（按 `settings` 可配）或数据版本号；暴露 `clear_scan_cache()` 供运维/建仓脚本调用 | **已实施**（§9.1.1） |
| 高 | 9.2 审计恢复 | 新增 `scripts/audit_rebuild.py`：从 JSONL 回灌 DuckDB（按 `request_id` 幂等去重），先补齐历史 3469 条 | **已实施**（§9.2.1）；「DB 失败落死信文件」**未采纳**，改为 JSONL 即权威源 |
| 中 | 9.3 预检开销 | 先用 `EXPLAIN`（不执行）估算初筛，仅当估算接近阈值时才做 `EXPLAIN ANALYZE` | **已出设计**：[两段式设计](../designs/2026-10-27-scan-precheck-two-stage-design.md)（未实施） |
| 中 | 9.3 文档漂移 | 更新 Hive design D4 的验证面清单 | **已完成**（§9.3.1） |
| 低 | 其余 | 配置项补进 `.env.example`；SSE 改 async；审计锁按通道拆分 | 配置项**已完成**；SSE 与审计锁待修 |

## 十、给运维的检查清单

接入或升级任一后端前，按这张表核对（每一条都是本次审计中真实踩过的）：

| 检查项 | 怎么验 |
|---|---|
| 客户端超时与服务端超时**不能同值竞争** | 客户端应短于服务端，或服务端必须存在且生效；同值时看门狗会先抛、worker 后退出 |
| Hive 会话超时是否生效 | `SET hive.query.timeout.seconds` 回读不应是 `is undefined`（harden 已 fail-closed 校验） |
| ClickHouse profile 预算与 `MAX_SCAN_ROWS` 一致 | `adapter.harden` 会 fail-closed 拒绝漂移 |
| 审计落库是否真的在写 | 看 `/api/metrics` 的 `audit_write_failures`，非零即代表 JSONL 有、库无 |
| **数据量大幅增长后熔断是否还有效** | 扫描行缓存有 TTL（`SCAN_CACHE_TTL_SECONDS`，默认 300s，§9.1）：超过 TTL 会重新预检；重建数据后可主动 `clear_scan_cache()` 立即失效 |
| **超时后连接是否被废弃** | `pytest tests/test_<backend>_backend_parity.py -k timeout`（需 `*_TEST_*` 环境变量，§9.3.2）：断言链为「超时 → 打标 → 归还即废弃 → 下次拿到新连接」 |
| **审计两通道是否对得上** | `wc -l logs/audit.jsonl` 与 `SELECT count(*) FROM audit_log` 对比；不一致就跑 `python scripts/audit_rebuild.py` 幂等回灌（§9.2） |
| 端口是否与宿主冲突 | 本机 10000 被百度网盘占用，故 compose 偏移到 10001 |
