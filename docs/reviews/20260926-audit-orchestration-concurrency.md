# 评审报告｜审计：编排层并发安全前置审计（2026-09）

> 归属：`docs/plans/2026-09-26-agent-harness-evolution-m0-m4.md` Task 2（M0 前置审计项）。
> 设计文档依据：`docs/superpowers/specs/2026-09-24-agent-harness-evolution-design.md` §8。

## 审计项一：事件总线（core/orchestrator/events.py）线程安全

**结论：contextvar 观察者线程隔离成立；异常吞并契约成立；但观察者只在 set_observer 的那个线程可见——LangGraph 池线程执行节点时事件会静默丢失，Task 4 必须用节点包装器显式传递。**

实测证据（`tests/test_orchestrator_events.py`）：

- `test_emit_event_observers_are_contextvar_isolated`：双线程各自 `set_observer` 并发各发射 50 次事件，双方收集器内容精确匹配、零串话（contextvar 语义天然保证，本测试将其固化为回归锚点）。
- `test_emit_event_swallows_observer_exception`：观察者抛 `RuntimeError` 时 `emit_event` 不反噬调用方（异常吞并 + 日志告警，L72-77）。
- 真实观察者契约为**单参数事件字典** `{"event", "timestamp", "payload"}`（`ObserverFn = Callable[[dict], None]`，L42）——实施计划中双参签名的假设按此修正（台账已记 Ruling）。

对后续任务的约束：

1. LangGraph 引擎（Task 4）不得依赖 contextvar 跨线程传播；节点包装器从 LangGraph `config["configurable"]["observer"]` 取观察者，并在节点执行线程内重设 contextvar。
2. 观察者的线程安全由消费端保证（`web/runs.py` `AgentRun.append` 在 `threading.Condition` 锁内写缓冲）——事件总线本身不加点锁，维持现状。

## 审计项二：exec 只读连接池在并发读下的安全性

**结论：安全。8 线程（2 倍池容量，覆盖"池满阻塞排队"路径）并发只读查询零错误，连接数封顶 4。**

实测证据（`.harness-plan/pool_audit.py`，一次性验证脚本不落仓）：

- 环境：`analytics_sandbox.duckdb`（`python -m mock.init_duckdb` 重建），`DB_POOL_SIZE=4`。
- 方法：8 个线程各执行 `select count(*) from fact_orders`，经 `exec.pool.default_pool()` acquire/release。
- 结果：`errors: []`，`ok reads: 8`，`pool size: 4`（未超容量）。

实现层面复核（`exec/pool.py`）：

- `ReadOnlyConnectionPool` 以 `queue.Queue + threading.Lock` 实现惰性创建与池满排队（L38-52），线程安全成立；
- DuckDB 只读连接（`read_only=True`，L48）并发读无写锁冲突——B 线 fan-out 并发上限 4（`SUBAGENT_MAX_PARALLEL=4`，Task 14）与池容量 4 对齐，是安全边界而非巧合，Task 14 不得把并发上限调高到超过 `DB_POOL_SIZE` 而不做二次审计。

## 遗留事项

- 无。两条审计结论均为"现状安全 + 约束传递"，未发现需在本任务内修复的缺陷。
