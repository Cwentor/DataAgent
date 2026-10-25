# Agent 架构演进（十七期）M0-M4 实施计划

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。

**Goal:** 按 `docs/superpowers/specs/2026-09-24-agent-harness-evolution-design.md` 完成底座迁移（LangGraph + FastAPI 双引擎可回退）与 A 线（Plan Mode 审批门 + 自主性分级）、B 线（Subagent fan-out）落地，M4 收敛为单引擎。

**Architecture:** 三阶段双引擎迁移——自研 StateGraph 与 LangGraph 图并存于 `ORCHESTRATOR_ENGINE` 开关之后（节点/路由函数零改动复用）；ThreadingHTTPServer 与 FastAPI 并存于 `WEB_SERVER_ENGINE` 开关之后（端点路径/SSE 契约不变）；A 线用 `interrupt()` 泛化（clarify 与 plan_review 同一原语两个 kind）；B 线用四节点受限子图 + `Send` fan-out，native 引擎下降级为串行子任务循环。领域内核（agent/compiler/exec/security/semantic/core）零改动。

**Tech Stack:** Python 3.12（conda `dataagent`/`futurebi`）、pydantic v2、langgraph + langgraph-checkpoint-sqlite（精确 pin）、fastapi + uvicorn（已装）、httpx（TestClient）、DuckDB、pytest。

**Spec:** `docs/superpowers/specs/2026-09-24-agent-harness-evolution-design.md`（执行者需同时读本计划与该规格）。

## Global Constraints

- LLM 仅允许产出强契约受控 JSON（DSL），严禁直接生成裸 SQL；DSL 模型一律 `extra="forbid"`。
- `AgentState` 保持 `extra="forbid"`；新增字段（subagent 报告、自主性偏好、计划审批状态）一律在本文件对应任务的 `state.py` 契约登记处登记。
- 依赖方向铁律不变：orchestrator -> retrieval / sandbox / skills / agent；retrieval 不依赖 orchestrator；sandbox 不感知业务语义。
- langgraph 生态 0.x 迭代快：所有新增依赖**精确 pin**（`langgraph==x.y.z` 形式），升级走独立 PR + 全量回归门。
- 领域内核 4 个运行时依赖不变（pydantic / duckdb / python-dotenv / sqlglot）；新增依赖仅落在编排/交互层。
- 新增能力皆可降级：Plan Mode 审批门、subagent fan-out 在离线 / 无 Key / `ORCHESTRATOR_ENGINE="native"` 环境必须可关闭并回落现有单图路径。
- SSE 九类事件契约逐字节兼容（`plan_created / step_start / tool_start / tool_end / reflection / hitl_request / artifact_emit / done / error`）；事件总线（`core/orchestrator/events.py` contextvar 观察者）不切换到 LangGraph stream 模式。
- 状态合并语义保持：平移初期不引入 reducer；唯一例外是 B 线 `subagent_reports` 使用 append reducer（`Annotated[list, operator.add]`）。
- thread_id 约定：`f"{user_id}:{session_id}"`；子任务线程 `f"{user_id}:{session_id}:sub:{task_id}"`。
- SQLite 会话记忆（`agent/memory.py` SessionStore）与 SqliteSaver 图执行快照各司其职，互不替代。
- 评测锚点 `AS_OF_DATE = 2024-06-30`、随机种子 42，保证确定性可复现。
- Python docstring / 行内注释用简体中文（专有名词保留英文）；代码实体名保持英文。
- 每个任务提交前 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿（本任务新增测试 + 既有测试）。
- Git Commit 信息：Git 术语外统一简体中文。

## Review Focus

规格隐含、但单靠各任务测试不够显眼、最可能咬到真实使用者的失效模式（各条已挂到 owning task 的测试）：

1. **LangGraph resume 时节点从头重执行**：`interrupt()` 恢复会重放所在节点函数，若把 interrupt 放在含副作用的节点（planner LLM 调用、事件发射）中会造成重复规划 / 重复事件——预期行为：中断一律放在独立轻量 gate 节点（`clarify_gate` / `plan_gate`），gate 内无 LLM 调用、无事件发射（Task 4 / Task 10 测试踩中）。
2. **contextvar 观察者在 LangGraph 执行器线程中丢失**：`events.py` 观察者靠 contextvar 注入，LangGraph 可能在线程池中执行节点导致 SSE 全静默——预期行为：观察者经 `config.configurable` 显式传递、节点包装器在执行线程内重设 contextvar；parity 测试断言事件序列非空且逐类一致（Task 2 / Task 5 测试踩中）。
3. **recursion_limit 与 `iteration > 24` 语义差异**：LangGraph 超级步计数与自研 `state.iteration` 计数口径不同，可能提前终止或不终止——预期行为：初始 64 并用深度重规划用例锚定"两引擎都在护栏内终止且 `no_data_reason` 含迭代超限语义"（Task 5 测试踩中）。
4. **并发 Send 下 DuckDB 连接 / 单文件写锁竞争**：fan-out 并发子任务同时读库——预期行为：并发上限默认 4（`SUBAGENT_MAX_PARALLEL`）以内不抛锁异常、结果与串行执行等价（Task 15 测试踩中）。
5. **Checkpointer 挂起状态跨进程重启恢复**：服务重启后 clarify / plan_review 挂起必须仍可恢复，且 `resume_token` 属主校验不绕过——预期行为：重启后用原 token resume 成功续跑，错误 owner 的 resume 被拒（Task 8 测试踩中）。

## 环境与基线事实（执行者必读）

以下为编写本计划时实测的代码库事实，任务中的路径 / 符号均以此为据：

- 自研图：`core/orchestrator/graph.py`——`StateGraph`（L46）、`add_node(name, fn)`（L57）、`set_entry`（L65）、`add_edge`（L71）、`add_conditional_edges(from, router, map)`（L77）、`run(state)`（L87）、`resume(state)`（L133）；`iteration > 24` 护栏在 L93-102 / L156-158。
- 装配：`core/orchestrator/agent.py`——`build_graph(*, max_iterations=24)`（L60-98）、`run_agent(question, *, session_id, turn_id, trace_id, human_reply, resume_state, history_digest, on_event)`（L101-196）、`_run_agent_inner`（L199-223）；路由闭包 `route_from_clarify`（L71）/ `route_from_plan`（L75）/ `route_from_critic`（L83）。
- 六节点（`core/orchestrator/nodes.py`）：`clarify_node`（L185）/ `planner_node`（L313）/ `dsl_query_node`（L713）/ `code_exec_node`（L1165）/ `critic_node`（L1422）/ `synthesize_node`（L1892）。
- 状态：`core/orchestrator/state.py`——`AgentState`（L95-148，`extra="forbid"`，`apply(**updates)` 不可变更新 L139）、`PlanStep`（L37）、`Phase`（L26）。
- 事件：`core/orchestrator/events.py`——九类常量（L31-39）、contextvar `_observer`（L44）、`set_observer/reset_observer`（L57-64）、`emit_event`（L67-77）。观察者由 `web/runs.py` 的 `RunRegistry._observer(run)`（L212-221）注入，`AgentRun.append`（L70-83）在 `threading.Condition` 锁内写缓冲；注册表丢弃编排内置无 token 的 `hitl_request` 并补 token 版（L216-218）。
- Web：`web/server.py`——`ThreadingHTTPServer`（L39/L842）、`Handler`（L87）、`main()`（L831-852）、SSE 四件套 `_sse_open/_sse_write/_sse_ping/_sse_follow`（L670-704）、`POST /api/agent/run`（L511）、`GET /api/v1/agent/chat/stream`（L575）；`web/runs.py`——`AgentRun`（L43-157）、`RunRegistry.start/_execute`（L182-243，`run_id=f"run-{uuid4().hex[:16]}"`，同步 `run_agent` 跑后台线程）、`subscribe(run_id, cursor, write_frame, ...)`（L308-362，`after` 游标增量 + 空洞检测）、`resume / resume_by_token`（L367/L372）、`set_paused`（L263-271，token `f"hitl-{uuid4().hex[:16]}"`）。
- 认证：`auth/gateway.py`——`gateway.authenticate(headers) -> AuthContext`（L127/L32-46）；owner 判定 `owner = None if "admin" in ctx.roles else ctx.username`（server.py L618）。
- 配置：`config/settings.py`——模块级常量 + `os.getenv`（如 `SQL_SELF_HEAL_MAX_RETRIES` L62）；编排区在 L95-110；`STATE_STORE_DB`（L183）。
- 会话记忆：`agent/memory.py`（SessionStore，SQLite 经 `persistence/kvstore.py` 的 `SqliteKVStore`）；`web/runs.py` 的 `history_digest_for`（L484-508）。
- 工具注册中心：`tools/registry.py`——`ToolRegistry`（L38-115）、单例 `default_registry()`（L155）、`tool_definitions()`（L108）。
- 评测：`eval/eval_runner.py`——`--pipeline {oracle,agent}`（L312）、`evaluate_all(conn, pipeline)`（L249）、golden `eval/golden_dataset.json` 25 例（23 single + 2 multi_turn）。
- 测试基线：静态统计 44 个测试文件、约 647 个 `def test_`（规格中的"678"以执行日 `pytest --collect-only -q` 实测数为准，验收门是"全绿"不是具体数字）；编排器相关：`tests/test_orchestrator.py`、`tests/test_orchestrator_events.py`、`tests/test_agent.py`、`tests/test_agent_e2e.py`、`tests/test_agent_stream.py`、`tests/test_web_runs.py`。
- 依赖现状：`requirements.txt` 4 行（pydantic/duckdb/python-dotenv/sqlglot）；`requirements-dev.txt` 追加 pytest/black/ruff/numpy/pandas/pyarrow；fastapi 0.141.1 与 uvicorn 0.52.1 已装，langgraph **未安装**。注意：本机 miniconda 某些环境为 Python 3.14，langgraph 对其支持未验证——Task 1 第一步先确认环境解释器版本，不满足则回到 3.12 的 `dataagent`/`futurebi` 环境。

---

# M0 底座同构

**目标**：依赖引入 + AgentState 平移 + 六节点图 LangGraph 同构编译，`ORCHESTRATOR_ENGINE` 开关双引擎，native 仍为一等公民。

### Task 1: 依赖引入与版本锁定

**Files:**
- Modify: `requirements-dev.txt`
- Modify: `README.md`（关键技术选型章节）

**Interfaces:**
- Produces: 可 import 的 `langgraph`、`langgraph.checkpoint.sqlite`、`fastapi`、`uvicorn`、`httpx`；requirements-dev.txt 中的精确 pin 版本号（后续任务按此环境执行）。

- [ ] **Step 1: 确认环境解释器版本**

```bash
conda activate dataagent 2>/dev/null || conda activate futurebi
python --version
```

Expected: `Python 3.12.x`。若输出 3.13/3.14 且后续 `pip install langgraph` 报不支持，须先切换/重建 3.12 环境再继续（AGENTS.md 运行环境章节）。

- [ ] **Step 2: 安装并解析版本**

```bash
pip install langgraph langgraph-checkpoint-sqlite httpx
pip freeze | grep -iE "^(langgraph|langgraph-checkpoint|langgraph-sdk|langchain-core|fastapi|uvicorn|httpx)=="
```

Expected: 每个包都有确定版本号输出。记录输出原文（写入 requirements-dev.txt 用）。

- [ ] **Step 3: 精确 pin 写入 requirements-dev.txt**

在 `requirements-dev.txt` 末尾追加（`x.y.z` 用 Step 2 实测版本逐字替换，**严禁保留占位符提交**）：

```text
# Agent harness evolution (M0): orchestration & serving layer only —
# domain kernel deps in requirements.txt unchanged.
langgraph==x.y.z
langgraph-checkpoint-sqlite==x.y.z
httpx==x.y.z
```

fastapi / uvicorn 已存在于环境但未 pin，同样追加 `fastapi==x.y.z`、`uvicorn==x.y.z`（版本取 Step 2 输出）。

- [ ] **Step 4: 冒烟验证**

```bash
pip install -r requirements-dev.txt
python -c "from langgraph.graph import StateGraph, END; from langgraph.types import interrupt, Command, Send; from langgraph.checkpoint.sqlite import SqliteSaver; from langgraph.checkpoint.memory import MemorySaver; import fastapi, uvicorn, httpx; print('ok')"
```

Expected: `ok`。

- [ ] **Step 5: README 选型章节同步**

在 `README.md`「关键技术选型」对应小节追加一行（中文）：编排/交互层引入 LangGraph + FastAPI（精确 pin），领域内核 4 依赖不变；"零框架依赖"约束自此收敛为"领域内核零框架"。

- [ ] **Step 6: Commit**

```bash
git add requirements-dev.txt README.md
git commit -m "chore(deps): 引入 langgraph/fastapi/httpx 并精确 pin（M0 底座迁移前置）"
```

### Task 2: 前置审计——事件总线线程安全与 exec 并发读

**Files:**
- Create: `docs/reviews/<执行日YYYYMMDD>-audit-orchestration-concurrency.md`
- Test: `tests/test_orchestrator_events.py`（追加）

**Interfaces:**
- Produces: 审计结论两条（事件总线在多线程调用方下是否安全；exec 连接池在 ≤4 并发读下是否安全），作为 Task 4（LangGraph 节点包装器）与 Task 15（Send 并发上限）的设计依据。若审计否决现状，在本任务内修复（给出修复代码位）。

- [ ] **Step 1: 写观察者隔离性与并发发射的失败风险测试**

追加到 `tests/test_orchestrator_events.py`：

```python
import threading

def test_emit_event_observers_are_contextvar_isolated():
    """两个线程各自 set_observer 并发发射：观察者不得串话（A 线程的事件不得进 B 的收集器）。"""
    from core.orchestrator import events as ev

    box_a, box_b = [], []
    barrier = threading.Barrier(2)

    def worker(box, name):
        token = ev.set_observer(lambda event, payload: box.append((name, event)))
        try:
            barrier.wait()
            for _ in range(50):
                ev.emit_event(ev.EVENT_STEP_START, {"who": name})
        finally:
            ev.reset_observer(token)

    t1 = threading.Thread(target=worker, args=(box_a, "a"))
    t2 = threading.Thread(target=worker, args=(box_b, "b"))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert all(name == "a" for name, _ in box_a) and len(box_a) == 50
    assert all(name == "b" for name, _ in box_b) and len(box_b) == 50


def test_emit_event_swallows_observer_exception():
    """观察者抛异常不得打断编排节点（emit_event 契约：异常吞并）。"""
    from core.orchestrator import events as ev

    token = ev.set_observer(lambda event, payload: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        ev.emit_event(ev.EVENT_STEP_START, {})  # 不应抛出
    finally:
        ev.reset_observer(token)
```

- [ ] **Step 2: 跑测试确认现状**

Run: `python -m pytest tests/test_orchestrator_events.py -q`
Expected: PASS（contextvar 天然线程隔离；emit_event 已吞异常）。若 FAIL，说明现状比预期弱，先修 `events.py` 再继续（修复方向：emit_event 外层 try/except 保持、set_observer 保持 contextvar 语义不动）。

- [ ] **Step 3: 审计 exec 并发读**

写一次性验证脚本并执行（不落仓，结论写入审计文档）：

```bash
python - <<'PY'
import duckdb, threading
from mock.init_duckdb import *  # noqa: F401,F403 确保数仓文件存在
from config import settings
# 按 exec/ 实际连接获取入口替换下方 conn 工厂（执行者读 exec/ 包确认）
from exec import executor
errs = []
def read(i):
    try:
        conn = executor.acquire() if hasattr(executor, "acquire") else duckdb.connect(settings.DUCKDB_PATH, read_only=True)
        conn.execute("select count(*) from sales_orders").fetchone()
    except Exception as e:
        errs.append(repr(e))
ts = [threading.Thread(target=read, args=(i,)) for i in range(4)]
[t.start() for t in ts]; [t.join() for t in ts]
print("errors:", errs)
PY
```

Expected: `errors: []`（4 并发只读不触发 DuckDB 写锁冲突）。

- [ ] **Step 4: 审计结论落盘**

创建 `docs/reviews/<执行日YYYYMMDD>-audit-orchestration-concurrency.md`，首行 `# 评审报告｜审计：编排层并发安全前置审计（YYYY-MM）`，记录三件事：① contextvar 观察者线程隔离成立，但 **观察者只对 set_observer 的那个线程可见**——LangGraph 若在池线程执行节点，事件会静默丢失，结论：Task 4 的节点包装器必须经 `config.configurable` 显式传观察者并在执行线程内重设 contextvar；② exec 4 并发只读结论与依据；③ 对 Task 15 的约束：并发上限 `SUBAGENT_MAX_PARALLEL=4` 是安全边界。

- [ ] **Step 5: Commit**

```bash
git add tests/test_orchestrator_events.py docs/reviews/
git commit -m "test(orchestrator): 事件总线线程隔离与并发发射前置审计（M0）"
```

### Task 3: AgentState 平移为 LangGraph 可编译 schema

**Files:**
- Modify: 无（AgentState 本就是 Pydantic `extra="forbid"`，预期零改动）
- Test: `tests/test_langgraph_parity.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 langgraph 安装。
- Produces: 事实确认——`AgentState` 可直接作为 `langgraph.graph.StateGraph` 的 state schema 编译（后续 Task 4 依赖）。

- [ ] **Step 1: 写编译冒烟测试**

```python
"""双引擎等价性测试（M0-M4 全程作为回归锚点）。"""
from langgraph.graph import StateGraph as LGStateGraph, END

from core.orchestrator.state import AgentState


def _minimal_state(**overrides) -> AgentState:
    base = dict(user_query="上月销售额是多少", session_id="s1", turn_id="t1")
    base.update(overrides)
    return AgentState(**base)


def test_agentstate_compiles_as_langgraph_schema():
    g = LGStateGraph(AgentState)
    g.add_node("noop", lambda s: s)
    g.set_entry_point("noop")
    g.add_edge("noop", END)
    app = g.compile()
    out = app.invoke(_minimal_state(), {"configurable": {"thread_id": "t"}})
    assert out["user_query"] == "上月销售额是多少"


def test_agentstate_extra_forbid_unchanged():
    import pydantic
    try:
        _minimal_state(nonexistent_field="x")
        raise AssertionError("extra field must be rejected")
    except pydantic.ValidationError:
        pass
```

- [ ] **Step 2: 跑测试确认结果**

Run: `python -m pytest tests/test_langgraph_parity.py -q`
Expected: PASS。若第一条 FAIL（Pydantic 私有属性 / `apply` 方法导致 schema 推断失败），修法：给 `AgentState` 增加类注释排除不可序列化成员，或将 LangGraph schema 声明为 `class LGAgentState(AgentState): pass` 空子类——**不得**放松 `extra="forbid"`。

- [ ] **Step 3: Commit**

```bash
git add tests/test_langgraph_parity.py
git commit -m "test(orchestrator): AgentState 作为 LangGraph state schema 可编译验证（M0）"
```

### Task 4: 六节点图 LangGraph 同构编译 + interrupt 泛化（clarify）

**Files:**
- Create: `core/orchestrator/langgraph_engine.py`
- Modify: `core/orchestrator/agent.py`（仅把三个路由闭包提升为模块级函数，L71-84 原样搬移）
- Test: `tests/test_langgraph_parity.py`（追加）

**Interfaces:**
- Consumes: Task 2 审计结论（观察者经 config 传递）、Task 3（AgentState 可编译）、`nodes.py` 六节点原函数、`agent.py` 路由函数。
- Produces:
  - `build_langgraph_app(*, checkpointer=None) -> langgraph.compile.CompiledStateGraph`
  - `invoke_langgraph(state: AgentState, *, thread_id: str, observer: Callable[[str, dict], None] | None, recursion_limit: int | None = None) -> tuple[AgentState, dict | None]`——返回 `(终态或挂起态, 中断 payload 或 None)`；中断 payload 形如 `{"kind": "clarify", "clarification": <str|None>}`。
  - `resume_langgraph(state_before_resume: AgentState, resume_payload: dict, *, thread_id: str, observer) -> tuple[AgentState, dict | None]`——`resume_payload` 即上次中断返回的 payload（内含供 `Command(resume=...)` 使用的值）。

- [ ] **Step 1: 提升路由函数为模块级（原样搬移，不改语义）**

`agent.py` 中 `route_from_clarify` / `route_from_plan` / `route_from_critic` 从 `build_graph` 闭包内移到模块级（签名均 `(state: AgentState) -> str`，函数体逐行不动），`build_graph` 内改为引用模块级名字。跑 `python -m pytest tests/test_orchestrator.py tests/test_agent.py -q` 确认既有测试全绿。

- [ ] **Step 2: 写中断/恢复与观察者传递测试（先失败）**

追加到 `tests/test_langgraph_parity.py`：

```python
from core.orchestrator.langgraph_engine import (
    build_langgraph_app, invoke_langgraph, resume_langgraph,
)
from core.orchestrator import events as ev


def test_clarify_interrupt_and_resume_roundtrip(monkeypatch):
    """clarify 中断 -> Command(resume) 恢复：同一原语，gate 节点无副作用。"""
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")  # 强制确定性兜底路径（按 agent/ 实际开关名调整）
    events_seen = []
    state = _minimal_state(user_query="什么是销售额？")  # 触发 clarify 的提问按 nodes.py 启发式选定
    pending_payload = None
    thread_id = "u1:s1"
    for _ in range(6):
        state, pending = invoke_langgraph(
            state, thread_id=thread_id,
            observer=lambda e, p: events_seen.append(e),
        )
        if pending:
            pending_payload = pending
            break
    assert pending_payload is not None and pending_payload["kind"] == "clarify"
    assert state.phase == "clarify"
    # resume：human_reply 经 Command 注入，图从 clarify_gate 之后继续
    resumed, pending2 = resume_langgraph(
        state, {"kind": "clarify", "resume_value": "按 2024-06 口径"},
        thread_id=thread_id, observer=lambda e, p: events_seen.append(e),
    )
    assert pending2 is None
    assert resumed.phase in {"plan", "query", "analyze", "critique", "synthesize", "done"}


def test_observer_reaches_nodes_inside_langgraph_threads():
    """审计结论回归：观察者经 config.configurable 传递，节点在执行线程内也能 emit。"""
    events_seen = []
    state, _ = invoke_langgraph(
        _minimal_state(), thread_id="u1:s2",
        observer=lambda e, p: events_seen.append(e),
    )
    assert events_seen, "observer lost inside langgraph executor threads"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_langgraph_parity.py -q`
Expected: FAIL，`ModuleNotFoundError: core.orchestrator.langgraph_engine`。

- [ ] **Step 4: 实现 `core/orchestrator/langgraph_engine.py`**

```python
"""LangGraph 引擎适配层：六节点语义原样平移，节点/路由函数零改动复用。

设计纪律（规格 §3.2/§4.2）：
- 事件总线不动：节点内仍用 events.emit_event（contextvar 观察者）；
  观察者经 config["configurable"]["observer"] 显式传入执行线程（审计结论）。
- 中断泛化：clarify 与后续 plan_review 共用 interrupt() 原语，
  中断一律放独立轻量 gate 节点（节点重执行安全：gate 内无 LLM、无事件发射）。
- 状态合并语义：不引入 reducer，节点返回完整 AgentState。
"""
from typing import Callable

from langgraph.graph import StateGraph as LGStateGraph, END
from langgraph.types import Command, interrupt
from langgraph.checkpoint.memory import MemorySaver

from config import settings
from core.orchestrator import events as ev
from core.orchestrator.agent import (
    route_from_clarify, route_from_plan, route_from_critic,
)
from core.orchestrator.nodes import (
    clarify_node, planner_node, dsl_query_node,
    code_exec_node, critic_node, synthesize_node,
)
from core.orchestrator.state import AgentState

ObserverFn = Callable[[str, dict], None]


def _observed(fn):
    """节点包装器：从 LangGraph config 取观察者，在本执行线程内重设 contextvar。

    LangGraph 可能在线程池中调度节点，contextvar 不跨线程——这是 M0 前置审计
    （docs/reviews/ 前置并发审计）确认的必须项，不是防御性冗余。
    """

    def wrapped(state: AgentState, config: dict) -> AgentState:
        observer = ((config or {}).get("configurable") or {}).get("observer")
        token = ev.set_observer(observer) if observer else None
        try:
            return fn(state)
        finally:
            if token is not None:
                ev.reset_observer(token)

    return wrapped


def _clarify_gate(state: AgentState) -> AgentState:
    """clarify 中断门：phase=clarify 时挂起等待人工回答，恢复后注入并转 plan。

    gate 无 LLM 调用、无事件发射——LangGraph resume 会从头重执行本节点，
    副作用放在这里会重复触发（Review Focus #1）。
    """
    if state.phase != "clarify":
        return state
    resume_value = interrupt({"kind": "clarify", "clarification": state.clarification})
    return state.apply(human_reply=resume_value, phase="plan")


def build_langgraph_app(*, checkpointer=None):
    saver = checkpointer if checkpointer is not None else MemorySaver()
    g = LGStateGraph(AgentState)
    g.add_node("clarify", _observed(clarify_node))
    g.add_node("clarify_gate", _observed(_clarify_gate))
    g.add_node("plan", _observed(planner_node))
    g.add_node("query", _observed(dsl_query_node))
    g.add_node("analyze", _observed(code_exec_node))
    g.add_node("critique", _observed(critic_node))
    g.add_node("synthesize", _observed(synthesize_node))
    g.set_entry_point("clarify")
    g.add_edge("clarify", "clarify_gate")
    g.add_conditional_edges("clarify_gate", route_from_clarify, {"plan": "plan"})
    g.add_conditional_edges("plan", route_from_plan,
                            {"query": "query", "analyze": "analyze", "critique": "critique"})
    g.add_edge("query", "analyze")
    g.add_edge("analyze", "critique")
    g.add_conditional_edges("critique", route_from_critic,
                            {"plan": "plan", "synthesize": "synthesize"})
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=saver)


def _config(thread_id: str, observer, recursion_limit: int | None) -> dict:
    return {
        "configurable": {"thread_id": thread_id, "observer": observer},
        "recursion_limit": recursion_limit or settings.ORCHESTRATOR_RECURSION_LIMIT,
    }


def _extract(result) -> tuple[AgentState, dict | None]:
    """invoke 结果 -> (AgentState, 中断 payload)。__interrupt__ 为 LangGraph 约定键。"""
    pending = None
    if isinstance(result, dict):
        interrupts = result.get("__interrupt__") or []
        if interrupts:
            pending = dict(interrupts[0].value)
    state = AgentState.model_validate(
        {k: v for k, v in result.items()
         if k not in {"__interrupt__", "__prev__"}})
    return state, pending


def invoke_langgraph(state, *, thread_id, observer=None, recursion_limit=None):
    result = build_langgraph_app().invoke(state, _config(thread_id, observer, recursion_limit))
    return _extract(result)


def resume_langgraph(state, resume_payload, *, thread_id, observer=None, recursion_limit=None):
    """resume_payload["resume_value"] 即 Command(resume=...) 注入值。"""
    result = build_langgraph_app().invoke(
        Command(resume=resume_payload["resume_value"]),
        _config(thread_id, observer, recursion_limit))
    return _extract(result)
```

同时在 `config/settings.py` 编排配置区（L95-110 附近）追加：

```python
# Agent harness evolution (M0)：双引擎开关与 LangGraph 超级步护栏。
# native = 自研 StateGraph（迁移前行为）；langgraph = 六节点同构编译。
ORCHESTRATOR_ENGINE: str = os.getenv("ORCHESTRATOR_ENGINE", "native")
ORCHESTRATOR_RECURSION_LIMIT: int = int(os.getenv("ORCHESTRATOR_RECURSION_LIMIT", "64"))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_langgraph_parity.py tests/test_orchestrator.py tests/test_agent.py -q`
Expected: 全部 PASS。clarify 触发语料若与 `clarify_node` 启发式不符，按 `nodes.py` L185-213 的实际触发条件改测试入参（改测试不改实现）。

- [ ] **Step 6: Commit**

```bash
git add core/orchestrator/langgraph_engine.py core/orchestrator/agent.py config/settings.py tests/test_langgraph_parity.py
git commit -m "feat(orchestrator): 六节点图 LangGraph 同构编译 + interrupt 泛化 clarify 门（M0）"
```

### Task 5: ORCHESTRATOR_ENGINE 双引擎开关 + 等价性验收

**Files:**
- Modify: `core/orchestrator/agent.py`（`run_agent` 引擎分派）
- Modify: `core/orchestrator/langgraph_engine.py`（护栏校准辅助）
- Test: `tests/test_langgraph_parity.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `invoke_langgraph / resume_langgraph`。
- Produces: `run_agent(...)` 按 `settings.ORCHESTRATOR_ENGINE` 分派，LangGraph 路径对外签名 / 返回类型 / 挂起契约（phase=clarify 的 AgentState）与 native 完全一致——web 层（`web/runs.py`）不感知引擎。

- [ ] **Step 1: 写双引擎事件逐类等价测试（先失败）**

```python
def test_native_and_langgraph_event_classes_equal(monkeypatch):
    """同一确定性兜底输入：双引擎事件类序列一致、终态 phase 一致。"""
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    from core.orchestrator.agent import run_agent

    def collect(engine):
        monkeypatch.setenv("ORCHESTRATOR_ENGINE", engine)
        seq = []
        out = run_agent("上月销售额是多少", session_id="s-parity",
                        on_event=lambda e, p: seq.append(e))
        return seq, getattr(out, "phase", None)

    seq_native, phase_native = collect("native")
    seq_lg, phase_lg = collect("langgraph")
    assert seq_native == seq_lg, f"event classes diverged: {seq_native} vs {seq_lg}"
    assert phase_native == phase_lg


def test_recursion_limit_anchors_termination(monkeypatch):
    """深度重规划场景：两引擎都在护栏内终止，且护栏语义等价（Review Focus #3）。"""
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    from core.orchestrator.agent import run_agent

    monkeypatch.setenv("ORCHESTRATOR_ENGINE", "langgraph")
    monkeypatch.setenv("ORCHESTRATOR_RECURSION_LIMIT", "8")
    out = run_agent("构造一个必然反复重规划的问题", session_id="s-limit",
                    on_event=lambda e, p: None)
    # 自研图护栏语义：超限置 phase=done 并在 no_data_reason/scratchpad 留痕
    assert out.phase == "done"
```

（两条测试的入参与 monkeypatch 开关名以 `agent/` 兜底路径实际实现为准调整；断言目标不变：事件逐类一致 + 护栏终止语义一致。）

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_langgraph_parity.py -q`
Expected: 新增两条 FAIL（`run_agent` 尚未分派引擎）。

- [ ] **Step 3: `run_agent` 引擎分派**

`agent.py` 的 `_run_agent_inner`（L199-223）改造为：

```python
def _run_agent_inner(question, *, session_id, turn_id, trace_id,
                     human_reply, resume_state, history_digest, on_event):
    if settings.ORCHESTRATOR_ENGINE == "langgraph":
        return _run_agent_langgraph_path(
            question, session_id=session_id, turn_id=turn_id, trace_id=trace_id,
            human_reply=human_reply, resume_state=resume_state,
            history_digest=history_digest, on_event=on_event)
    # —— native 原路径逐行保留 ——
```

`_run_agent_langgraph_path` 放在 `agent.py`：按 native 路径同样的方式组装初始 `AgentState`（user_query / session_id / turn_id / trace_id / history_digest），thread_id 取 `f"{session_id}"`（user 维度在 web 层传入时前缀化，见 Task 8），`human_reply`/`resume_state` 非空时走 `resume_langgraph`，否则循环 `invoke_langgraph`（clarify 挂起即返回 `AgentState`，与 native 挂起契约一致）；返回类型与 native 逐字段同构（`AgentState` 或 `AgentTrace`）。

- [ ] **Step 4: 跑测试确认通过 + 评测双跑**

```bash
python -m pytest tests/test_langgraph_parity.py -q
python -m eval.eval_runner                       # native 基线
ORCHESTRATOR_ENGINE=langgraph python -m eval.eval_runner
```

Expected: parity 测试 PASS；两次评测 oracle 25/25 通过（agent 模式在无 Key 环境为确定性兜底，两引擎得分一致）。任何分数差异：先查事件序列 diff，再查路由函数搬移，**不得**通过放宽评测断言收敛。

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/agent.py core/orchestrator/langgraph_engine.py config/settings.py tests/test_langgraph_parity.py
git commit -m "feat(orchestrator): ORCHESTRATOR_ENGINE 双引擎开关 + 双跑评测等价验收（M0）"
```

### M0 验收检查点

- [ ] `python -m pytest -q` 全绿（含新增 parity 测试）。
- [ ] `ORCHESTRATOR_ENGINE=native python -m eval.eval_runner` 与 `ORCHESTRATOR_ENGINE=langgraph python -m eval.eval_runner` 均 oracle 25/25。
- [ ] 双引擎事件类序列逐类 diff 为空（`test_native_and_langgraph_event_classes_equal`）。
- [ ] `ORCHESTRATOR_ENGINE=native`（默认）下全系统行为与本分支迁入前完全一致（不设 env 跑全量测试即证）。
- [ ] `ruff check .`、`black --check .` 通过。

---

# M1 交互升级

**目标**：FastAPI 替换 ThreadingHTTPServer（开关可回退）+ SSE 契约回归 + resume 端点 Command 化 + SqliteSaver 持久挂起。

### Task 6: FastAPI 应用骨架 + 认证依赖 + 基础端点平移

**Files:**
- Create: `web/api.py`
- Modify: `config/settings.py`（`WEB_SERVER_ENGINE` 开关）
- Test: `tests/test_web_api.py`（新建）

**Interfaces:**
- Consumes: `auth.gateway.authenticate`、`web/service.run_query`、`web/server.py` 既有端点语义（L148-205 分发表）。
- Produces: `web.api.app`（FastAPI 实例），已平移端点：`GET /api/health`、`GET /api/metrics`、`GET/POST /api/auth/*`、`POST /api/query`、`POST /api/query/async`、`GET /api/schema/summary`、`GET /api/settings/providers`、`GET /api/export/{export_id}`、`GET /api/tasks/{task_id}`、静态资源。响应 JSON 结构与 stdlib 实现逐字段一致（本任务测试锁定）。

- [ ] **Step 1: 写端点等价测试（先失败）**

```python
"""FastAPI 端点与 stdlib 实现的契约等价测试。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from web.api import app
    return TestClient(app, raise_server_exceptions=False)


def test_health_matches_stdlib_contract(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_field_set_frozen(client):
    """字段集快照锁：防止 FastAPI 平移时增删响应字段。

    本步骤执行时先起一次 stdlib 服务（WEB_SERVER_ENGINE=stdlib python -m web.server）
    并 `curl /api/health`，把实测字段集逐字写入 expected_fields，再跑本测试。
    """
    expected_fields = {"status"}  # ← 以 stdlib 实测字段集逐字修正
    assert set(client.get("/api/health").json()) == expected_fields


def test_query_endpoint_auth_enforced(client):
    # AUTH_ENABLED=1 下无凭据访问必须 401（与 stdlib `_authenticate` 行为一致）
    resp = client.post("/api/query", json={"query": "上月销售额"})
    assert resp.status_code == 401
```

认证用例与 `tests/test_web_auth.py` **逐条一一对应**：打开该文件，把每个用例的 arrange（凭据构造、登录请求体、Cookie/JWT 通道）与 assert 原样搬到 `TestClient` 版本，仅替换传输层（`self.send`/urllib → `client.post/get`），用例名加 `test_api_` 前缀。不得另造凭据体系——stdlib 测试里的用户名/密码/JWT 构造函数是唯一事实来源（涉及 `auth/gateway.py` L127 的 `authenticate` 双通道语义，凭造假代码极易写错字段名）。

登录/鉴权用例直接复用 `tests/test_web_auth.py` 的既有 fixture 与凭据构造（打开该文件照抄构造方式，改调 `TestClient`），**逐条对应** stdlib 已有鉴权测试迁移断言，不是另起炉灶。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_web_api.py -q`
Expected: FAIL，`ImportError: web.api`。

- [ ] **Step 3: 实现 `web/api.py`（基础端点）**

骨架与认证依赖：

```python
"""FastAPI 应用：端点路径/认证语义/响应结构逐字段对齐 stdlib 实现（web/server.py）。

迁移纪律：每个端点先在 stdlib Handler 中找到对应处理函数，响应字段集照抄；
本文件不做任何"顺手改进"。
"""
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse

from auth.gateway import gateway
from config import settings

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def require_auth(request: Request):
    """认证依赖：与 server.py `_authenticate`（L129-138）语义逐行对齐。"""
    ctx = gateway.authenticate(request.headers)
    if ctx is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return ctx


@app.get("/api/health")
def health():
    # 响应构造与 stdlib Handler._get_health 同源：把 server.py 中构造该 dict 的
    # 代码提为模块级纯函数（如 _health_payload()），stdlib 与本端点共同调用，
    # 保证字段集永不漂移（test_health_field_set_frozen 由此获得稳定性）。
    return _health_payload()
```

其余端点逐个平移：处理逻辑直接调用与 stdlib 相同的底层函数（`web/service.run_query`、metrics 收集器、providers 设置模块、export 存储），同步 `def`（FastAPI 自动进线程池，LLM 与 DuckDB 阻塞 IO 安全）。静态资源用 `StaticFiles` 挂载 `/static`，`/` 与 `/login` 返回与 stdlib 相同的 HTML 文件。

- [ ] **Step 4: server 入口开关**

`web/server.py` 的 `main()`（L831）改为：

```python
def main() -> None:
    issues = _startup_security_issues()
    if issues:
        raise SystemExit("; ".join(issues))
    ensure_db()
    if settings.WEB_SERVER_ENGINE == "fastapi":
        import uvicorn
        uvicorn.run("web.api:app", host=_host(), port=_port(sys.argv), log_level="warning")
        return
    ThreadingHTTPServer((_host(), _port(sys.argv)), Handler).serve_forever()
```

`config/settings.py` 追加：

```python
# Agent harness evolution (M1)：HTTP 引擎开关。stdlib = ThreadingHTTPServer（回退路径）。
WEB_SERVER_ENGINE: str = os.getenv("WEB_SERVER_ENGINE", "stdlib")
```

- [ ] **Step 5: 跑测试确认通过**

```bash
python -m pytest tests/test_web_api.py tests/test_web.py tests/test_web_auth.py -q
WEB_SERVER_ENGINE=fastapi python -m web.server 8000 &
curl -s http://127.0.0.1:8000/api/health   # 另一终端
```

Expected: 测试全绿；health 返回 200 JSON；`python -m web.server 8000` 用法不变。

- [ ] **Step 6: Commit**

```bash
git add web/api.py web/server.py config/settings.py tests/test_web_api.py
git commit -m "feat(web): FastAPI 骨架 + 基础端点平移 + WEB_SERVER_ENGINE 开关（M1）"
```

### Task 7: Agent 端点平移 + SSE StreamingResponse

**Files:**
- Modify: `web/api.py`
- Test: `tests/test_web_api.py`（追加）

**Interfaces:**
- Consumes: Task 6 的 app 骨架、`web/runs.py` 的 `default_run_registry()`（`start/subscribe/resume/run_status`）、`AgentProtocol.buildStreamUrl` 对应的服务端查询参数（`run_id` / `after` / `resume_token` / `human_reply`）。
- Produces: `POST /api/agent/run`、`GET /api/v1/agent/runs/{run_id}`、`GET /api/v1/agent/chat/stream`（SSE）。SSE 帧格式 `data: <json>\n\n`、注释心跳 `: ping\n\n`、响应头 `X-Run-Id`、`after` 游标增量重放语义——与 stdlib `_sse_*`（L670-704）逐字节一致。

- [ ] **Step 1: 写 SSE 帧格式与游标重放测试（先失败）**

```python
def test_agent_stream_frame_format_and_cursor(client, monkeypatch):
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    owner_headers = {}  # AUTH_ENABLED=0 下免认证；=1 时复用 test_web_auth fixture 登录
    r = client.post("/api/agent/run", json={"query": "上月销售额是多少"},
                    headers=owner_headers)
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    frames = []
    with client.stream("GET", f"/api/v1/agent/chat/stream?run_id={run_id}",
                       headers=owner_headers) as resp:
        assert resp.headers.get("x-run-id") == run_id
        for line in resp.iter_lines():
            frames.append(line)
    data_lines = [f for f in frames if f.startswith("data: ")]
    assert data_lines, "expected at least one SSE data frame"
    # 终帧必须是 done 事件（json 解析代替子串匹配，避免格式假设）
    import json as _json
    last = _json.loads(data_lines[-1][6:])
    assert last["event"] == "done"
    # after 游标：after=最后一帧 seq 只回增量，不整轮重发
    last_seq = max(_json.loads(f[6:]).get("seq", 0) for f in data_lines)
    replay = client.get(f"/api/v1/agent/chat/stream?run_id={run_id}&after={last_seq}",
                        headers=owner_headers)
    body = replay.text
    assert f'"seq": {last_seq}' not in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_web_api.py -q`
Expected: 新增用例 FAIL（404，端点未平移）。

- [ ] **Step 3: 实现三个端点**

```python
from fastapi.responses import StreamingResponse
from web.runs import default_run_registry


@app.post("/api/agent/run")
def post_agent_run(payload: dict, request: Request, ctx=Depends(require_auth)):
    # 属主语义照抄 server.py L618：owner = None if "admin" in ctx.roles else ctx.username
    registry = default_run_registry()
    run_id, handle = registry.start(
        query=payload["query"], principal=ctx.principal, owner=owner,
        session_id=payload.get("session_id") or ctx.session_id, ...)
    return JSONResponse({"run_id": run_id, ...})  # 字段集照抄 stdlib _post_agent_run 响应


@app.get("/api/v1/agent/chat/stream")
def agent_chat_stream(request: Request, ctx=Depends(require_auth)):
    registry = default_run_registry()
    run_id = request.query_params.get("run_id")
    after = int(request.query_params.get("after") or 0)
    # resume 参数（resume_token/human_reply）与 stdlib L630-652 语义一致；Task 8 接 Command 化
    def gen():
        yield from registry.subscribe(run_id, after, write_frame_collector, owner=owner, ...)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-Run-Id": run_id, "Cache-Control": "no-cache"})
```

`registry.subscribe` 现有签名以 `write_frame(frame_text)` 回调输出帧——用 `queue.SimpleQueue` 桥接到生成器（subscribe 在工作线程写队列，生成器在线程内 `get` 后 yield），帧文本构造复用 `web/runs.py` 现有帧格式化函数，**不自写格式化**。`GET /api/v1/agent/runs/{run_id}` 同法平移 `_get_agent_run_status`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_web_api.py tests/test_web_runs.py tests/test_agent_stream.py -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add web/api.py tests/test_web_api.py
git commit -m "feat(web): agent 端点平移 + SSE StreamingResponse 帧格式与游标重放（M1）"
```

### Task 8: resume 端点 Command 化 + SqliteSaver 持久挂起

**Files:**
- Modify: `core/orchestrator/langgraph_engine.py`（checkpointer 装配、thread_id 规范）
- Modify: `web/api.py`（stream 端点 resume 分支）、`web/runs.py`（LangGraph 引擎下暂停态登记）
- Modify: `config/settings.py`（`ORCHESTRATOR_CHECKPOINT_DB`）
- Test: `tests/test_langgraph_parity.py`、`tests/test_web_api.py`（追加）

**Interfaces:**
- Consumes: Task 4/5 引擎适配、`web/runs.py` 暂停/恢复契约（`set_paused` / `take_resume_state` / `resume_by_token`）。
- Produces:
  - `langgraph_engine.get_checkpointer() -> SqliteSaver | MemorySaver`（配置 `ORCHESTRATOR_CHECKPOINT_DB` 时用 SqliteSaver，否则 MemorySaver）。
  - thread_id 规范落地：`f"{user_id}:{session_id}"`（web 层传 `user_id=ctx.principal`）。
  - web resume 语义：LangGraph 引擎下挂起状态由 Checkpointer 持久化，`run_registry` 的 token 机制保留（属主校验不变），resume 调 `resume_langgraph` 而非重发 query。

- [ ] **Step 1: 写重启恢复测试（先失败）**

```python
def test_checkpointer_survives_process_restart(tmp_path, monkeypatch):
    """服务重启后 clarify 挂起仍在：同 thread_id 新 app 实例可恢复（Review Focus #5）。"""
    from langgraph.checkpoint.sqlite import SqliteSaver
    db = tmp_path / "ckpt.sqlite"
    saver = SqliteSaver(sqlite3.connect(str(db), check_same_thread=False))
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    state = _minimal_state(user_query="什么是销售额？")
    state, pending = invoke_langgraph(state, thread_id="u9:restart",
                                      observer=None, checkpointer=saver)
    assert pending and pending["kind"] == "clarify"
    # —— 模拟重启：全新 app 实例 + 同一 sqlite 文件 ——
    saver2 = SqliteSaver(sqlite3.connect(str(db), check_same_thread=False))
    resumed, pending2 = resume_langgraph(
        state, {"kind": "clarify", "resume_value": "按 2024-06 口径"},
        thread_id="u9:restart", observer=None, checkpointer=saver2)
    assert pending2 is None and resumed.phase != "clarify"
```

（`invoke_langgraph / resume_langgraph` 因此增加 `checkpointer=None` 关键字参数——默认 `get_checkpointer()`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_langgraph_parity.py -q`
Expected: 新增用例 FAIL（无 checkpointer 参数）。

- [ ] **Step 3: 实现**

`langgraph_engine.py`：

```python
import sqlite3

def get_checkpointer():
    """ORCHESTRATOR_CHECKPOINT_DB 配置时持久化（长任务断点续航），否则进程内。"""
    db = settings.ORCHESTRATOR_CHECKPOINT_DB
    if not db:
        return MemorySaver()
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(str(db), check_same_thread=False))
```

`config/settings.py` 追加 `ORCHESTRATOR_CHECKPOINT_DB: str | None = os.getenv("ORCHESTRATOR_CHECKPOINT_DB") or None`。
`web/api.py` stream 端点：请求带 `resume_token` + `human_reply` 时按 stdlib L630-652 语义校验 token 属主后，调 `registry.resume(...)`；`web/runs.py` 的 LangGraph 分支 resume 改调 `resume_langgraph`（token 领取/属主校验逻辑不动），并在 `_execute` 的 `run_agent` 调用处传 `user_id` 使 thread_id 合规（`f"{principal}:{session_id}"`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_langgraph_parity.py tests/test_web_api.py tests/test_web_runs.py -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/langgraph_engine.py web/api.py web/runs.py config/settings.py tests/
git commit -m "feat(web): resume Command 化 + SqliteSaver 挂起跨重启恢复（M1）"
```

### Task 9: SSE 九类事件逐字节契约回归

**Files:**
- Test: `tests/test_sse_contract.py`（新建）

**Interfaces:**
- Consumes: Task 6/7 的 FastAPI 端点、stdlib 实现作为 oracle。
- Produces: 契约快照测试——FastAPI 与 stdlib 两引擎对同一确定性输入的 SSE 帧序列逐字节一致（含心跳帧格式），作为 M4 删除 stdlib 服务前的最终护栏。

- [ ] **Step 1: 写双服务帧级 diff 测试**

```python
"""SSE 契约回归：FastAPI vs stdlib 双实现帧级 diff（M1 验收门 / M4 删除前护栏）。"""
import json


def _collect_frames_sse(engine_env, monkeypatch, query="上月销售额是多少"):
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    monkeypatch.setenv("ORCHESTRATOR_ENGINE", "langgraph")
    if engine_env == "fastapi":
        from fastapi.testclient import TestClient
        from web.api import app
        c = TestClient(app)
        run_id = c.post("/api/agent/run", json={"query": query}).json()["run_id"]
        text = c.get(f"/api/v1/agent/chat/stream?run_id={run_id}").text
    else:
        # 起线程跑 stdlib Handler 对应 registry（复用 tests/test_agent_stream.py 的进程内采集法）
        from web.runs import default_run_registry
        registry = default_run_registry()
        frames = []
        run_id = registry.start(query=query, ...).run_id
        registry.subscribe(run_id, 0, lambda frame: frames.append(frame), owner=None, ...)
        text = "".join(frames)
    return [l for l in text.splitlines() if l.startswith("data: ")]


def test_sse_frames_identical_across_servers(monkeypatch):
    fast = _collect_frames_sse("fastapi", monkeypatch)
    std = _collect_frames_sse("stdlib", monkeypatch)
    # 事件类序列一致；data 载荷去掉 run_id/seq/时间戳后逐帧 JSON 相等
    ev = lambda frames: [json.loads(f[6:])["event"] for f in frames]
    assert ev(fast) == ev(std)
    def norm(frames):
        out = []
        for f in frames:
            d = json.loads(f[6:])
            d.pop("run_id", None); d.pop("seq", None); d.pop("timestamp", None)
            out.append(d)
        return out
    assert norm(fast) == norm(std)
```

（stdlib 侧进程内采集法以 `tests/test_agent_stream.py` 现有写法为准照抄，避免起真实端口。）

- [ ] **Step 2: 跑测试**

Run: `python -m pytest tests/test_sse_contract.py -q`
Expected: PASS。FAIL 时逐帧打印 diff 定位（几乎必然是某端点响应字段照抄不全，回 Task 6/7 修）。

- [ ] **Step 3: Commit**

```bash
git add tests/test_sse_contract.py
git commit -m "test(web): SSE 九类事件双服务帧级契约回归（M1）"
```

### M1 验收检查点

- [ ] `WEB_SERVER_ENGINE=fastapi python -m web.server 8000` 启动，`python -m web.server 8000` 用法兼容；前端手动 smoke：登录 → 提问 → 流式输出 → HITL 澄清 → 恢复，全程可用。
- [ ] `tests/test_web_api.py` + `tests/test_sse_contract.py` 全绿；SSE 帧级 diff 为空。
- [ ] clarify 挂起经服务重启后仍可恢复（`test_checkpointer_survives_process_restart`）。
- [ ] `WEB_SERVER_ENGINE=stdlib` 一键回退仍全绿。
- [ ] 全量 `pytest -q` + `ruff` + `black --check` 全绿。

---

# M2 A 线：Plan Mode 审批门 + 自主性分级

**目标**：`maybe_interrupt` 帮助函数 + plan_review 审批门三选一 + `hitl_request` kind 扩展（向后兼容）+ L1-L4 自主性分级 + 前端审批卡片与侧栏设置。

### Task 10: 自主性分级内核 + plan_review 审批门（后端）

**Files:**
- Create: `core/orchestrator/autonomy.py`
- Modify: `core/orchestrator/state.py`（契约登记新字段）、`core/orchestrator/langgraph_engine.py`（plan_gate 节点）、`config/settings.py`
- Test: `tests/test_autonomy.py`（新建）

**Interfaces:**
- Consumes: `langgraph.types.interrupt / Command`、`AgentState.apply`。
- Produces:
  - `AUTONOMY_LEVELS = ("L1", "L2", "L3", "L4")`；`AutonomyPolicy`（Pydantic，`extra="forbid"`，字段 `level`）。
  - `maybe_interrupt(state: AgentState, payload: dict, *, trigger: str) -> dict`——按 `state.autonomy_level` 分流：L1-L3 调 `langgraph interrupt()`（以异常形式挂起，本函数不返回）；L4 先 `emit_event(EVENT_HITL_REQUEST, {**payload, "auto_resolved": True})` 降级为通知事件，再返回默认 resume 值直通。
  - `AgentState` 新增契约字段：`autonomy_level: Literal["L1","L2","L3","L4"] = "L2"`、`plan_edit_instruction: str | None = None`。
  - plan_gate 中断 payload：`{"kind": "plan_review", "plan_steps": [...model_dump...], "summary": str}`；resume 值 `{"action": "approve"|"reject", "instruction": str|None}`（edit 动作在 gate 内转译：`{"action": "edit", ...}` → 置 `plan_edit_instruction`、phase 回 `"plan"` 重新规划，与 error_context 同一注入位）。

- [ ] **Step 1: 写 L1-L4 行为矩阵测试（先失败）**

```python
"""自主性分级 L1-L4 行为矩阵 + plan_review 三分支。"""
import pytest

from core.orchestrator.autonomy import AUTONOMY_LEVELS, maybe_interrupt
from core.orchestrator.state import AgentState


def _state(level):
    return AgentState(user_query="q", session_id="s", autonomy_level=level)


def test_levels_are_the_documented_four():
    assert AUTONOMY_LEVELS == ("L1", "L2", "L3", "L4")


def test_l2_default_in_state_contract():
    assert _state("L2").autonomy_level == "L2"  # 日常交互默认档


def test_extra_forbid_on_policy():
    import pydantic
    from core.orchestrator.autonomy import AutonomyPolicy
    with pytest.raises(pydantic.ValidationError):
        AutonomyPolicy(level="L5")
```

plan_review 三分支测试放 `tests/test_langgraph_parity.py`（需要图运行时）。用 monkeypatch 把 planner_node 固定为两步计划（不依赖 LLM 与兜底启发式的产物形态），再分别用三种 resume 值驱动：

```python
def _patch_two_step_plan(monkeypatch):
    """把 planner 固定为两步计划（query+analyze），plan_review 必触发。"""
    from core.orchestrator.state import PlanStep
    steps = [PlanStep(id="p1", kind="query", goal="取上月销售额"),
             PlanStep(id="p2", kind="analyze", goal="环比归因")]
    calls = []
    def fake_planner(state):
        calls.append(1)
        return state.apply(plan_steps=steps, phase="plan")
    monkeypatch.setattr("core.orchestrator.langgraph_engine.planner_node", fake_planner)
    return calls  # (PlanStep 必填字段以 state.py 实际契约为准补全)


def test_plan_review_approve_branch(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    _patch_two_step_plan(monkeypatch)
    state, pending = invoke_langgraph(_minimal_state(), thread_id="u1:pr1", observer=None)
    assert pending and pending["kind"] == "plan_review"
    resumed, pending2 = resume_langgraph(
        state, {"kind": "plan_review", "resume_value": {"action": "approve", "instruction": None}},
        thread_id="u1:pr1", observer=None)
    assert pending2 is None
    assert resumed.phase in {"query", "analyze", "critique", "synthesize", "done"}


def test_plan_review_reject_branch(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    _patch_two_step_plan(monkeypatch)
    state, pending = invoke_langgraph(_minimal_state(), thread_id="u1:pr2", observer=None)
    resumed, _ = resume_langgraph(
        state, {"kind": "plan_review", "resume_value": {"action": "reject", "instruction": None}},
        thread_id="u1:pr2", observer=None)
    assert resumed.phase == "done" and resumed.artifacts == []
    assert "拒绝" in (resumed.no_data_reason or "")


def test_plan_review_edit_branch_replans(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    calls = _patch_two_step_plan(monkeypatch)
    state, pending = invoke_langgraph(_minimal_state(), thread_id="u1:pr3", observer=None)
    resumed, pending2 = resume_langgraph(
        state, {"kind": "plan_review", "resume_value": {"action": "edit", "instruction": "只看华东区域"}},
        thread_id="u1:pr3", observer=None)
    assert pending2 is not None  # 重规划后再次进入审批门
    assert len(calls) == 2       # planner 被再次调用（重规划发生）
    assert resumed.plan_edit_instruction == "只看华东区域"
```

（LangGraph 挂起后同一 thread_id 的 resume 依赖 checkpointer——`invoke_langgraph/resume_langgraph` 默认走 Task 8 的 `get_checkpointer()`；M0 阶段本测试在 Task 8 落地前以 `checkpointer=MemorySaver()` 显式传入。）

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_autonomy.py -q`
Expected: FAIL，`ImportError: core.orchestrator.autonomy`。

- [ ] **Step 3: 实现 `core/orchestrator/autonomy.py` + plan_gate**

```python
"""自主性分级（规格 §5.2）：单一图结构 + maybe_interrupt 帮助函数。

L1 每步确认 / L2 计划确认（默认）/ L3 高危确认 / L4 全自动（降级为通知事件）。
真中断用 langgraph interrupt()；直通档返回默认 resume 值，图结构不分叉。
"""
from typing import Literal

from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict

from core.orchestrator import events as ev
from core.orchestrator.state import AgentState

AUTONOMY_LEVELS = ("L1", "L2", "L3", "L4")


class AutonomyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Literal["L1", "L2", "L3", "L4"] = "L2"


_DEFAULT_ACTIONS = {
    "plan_review": {"action": "approve", "instruction": None},
    "high_risk": {"action": "approve", "instruction": None},
    "step_confirm": {"action": "approve", "instruction": None},
}


def maybe_interrupt(state: AgentState, payload: dict, *, trigger: str):
    """返回 None 表示已发生真中断（调用方不再前进）；返回 dict 为直通默认值。

    L4：审批门降级为 hitl_request 富化载荷通知（auto_resolved=True），零中断。
    """
    level = state.autonomy_level
    if level == "L4":
        ev.emit_event(ev.EVENT_HITL_REQUEST, {**payload, "auto_resolved": True})
        return _DEFAULT_ACTIONS[trigger]
    return interrupt(payload)  # L1/L2/L3 真中断；档位差异由 gate 触发条件承载
```

`langgraph_engine.py` 增加节点（挂接在 plan 之后、条件边之前）：

```python
def _plan_gate(state: AgentState) -> AgentState:
    """plan_review 审批门（规格 §5.1）：多步 DAG 或含 analyze 步骤才触发；L1 强制触发。

    phase 约定：approve 后保持 planner 产出的 phase（供 route_from_plan 分流）；
    edit 置 phase="plan"（回 planner 重规划）；reject 置 phase="done"。
    三种去向由 route_from_plan_gate 条件边分流，不得混用 route_from_plan。
    """
    multi_step = len(state.plan_steps) > 1 or any(s.kind == "analyze" for s in state.plan_steps)
    forced = state.autonomy_level == "L1"
    if state.phase != "plan" or not (multi_step or forced):
        return state
    resume = maybe_interrupt(state, {
        "kind": "plan_review",
        "plan_steps": [s.model_dump() for s in state.plan_steps],
        "summary": state.plan_steps[0].goal if state.plan_steps else "",
    }, trigger="plan_review")
    if resume["action"] == "reject":
        return state.apply(phase="done", no_data_reason="用户拒绝了分析计划，未执行任何查询")
    if resume["action"] == "edit":
        return state.apply(plan_edit_instruction=resume.get("instruction"),
                           phase="plan")  # 回 plan 重规划，指令随提示词注入（planner_node 读取）
    return state  # approve：保持 phase，交 route_from_plan 分流


def route_from_plan_gate(state: AgentState) -> str:
    """plan_gate 条件边路由：approve 交原 route_from_plan；edit 回 plan；reject 收敛。"""
    if state.phase == "plan" and state.plan_edit_instruction:
        return "plan"
    if state.phase == "done":
        return "plan_gate_end"
    return route_from_plan(state)
```

`build_langgraph_app` 中插入：`g.add_node("plan_gate", _observed(_plan_gate))`，边改为 `plan → plan_gate → {query, analyze, critique, plan, plan_gate_end}`（条件边用 `route_from_plan_gate`；`plan_gate_end` 是直连 `END` 的空节点 `g.add_node("plan_gate_end", lambda s: s)`——LangGraph 条件边目标必须是节点，不能直接 END 字符串混用路由）。图结构仍是单图，clarify 与 plan_review 共用 interrupt 原语。`state.py` 登记两字段（`autonomy_level` / `plan_edit_instruction`），`planner_node` 提示词组装处追加：`plan_edit_instruction` 非空时作为用户修改指令注入（与 `error_context` 同一注入位）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_autonomy.py tests/test_langgraph_parity.py tests/test_orchestrator.py -q`
Expected: 全绿；native 引擎下 plan_gate 不存在（plan 直达条件边），`ORCHESTRATOR_ENGINE=native` 时审批门自动关闭——即"新增能力皆可降级"在引擎维度的体现。

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/autonomy.py core/orchestrator/state.py core/orchestrator/langgraph_engine.py config/settings.py tests/test_autonomy.py tests/test_langgraph_parity.py
git commit -m "feat(orchestrator): 自主性分级 L1-L4 + plan_review 审批门三分支（M2）"
```

### Task 11: hitl_request kind 扩展 + resume action 贯通 web 层

**Files:**
- Modify: `web/runs.py`（观察者补 token 时透传 payload.kind）、`web/api.py`（resume 请求体接受 action/instruction）
- Test: `tests/test_web_runs.py`、`tests/test_web_api.py`（追加）

**Interfaces:**
- Consumes: Task 10 plan_gate、`AgentRun.set_paused`。
- Produces: SSE `hitl_request` 事件 payload 含 `kind: "clarify" | "plan_review"`（旧前端忽略不崩溃）；resume 请求支持 `{"action": "approve"|"edit"|"reject", "instruction": ...}`（`human_reply` 形式继续兼容）。

- [ ] **Step 1: 写 payload 透传与 action resume 测试（先失败）**

两条用例共用一个挂起驱动 helper（放在测试文件顶部，arrange 写法照抄 `tests/test_web_runs.py` 既有 registry 直驱用例——该文件已有"start → 等待挂起 → 领取 token"的成熟模式，仅替换触发语料为多步 plan 问题）：

```python
def _start_and_wait_plan_review(monkeypatch):
    """驱动一条 run 到 plan_review 挂起，返回 (registry, run_id, resume_token)。

    实现方式 = 照抄 tests/test_web_runs.py 中「registry.start → 轮询等待挂起
    → 领取 resume_token」的既有用例主体，仅两处改动：
    ① 触发语料换成多步问题（如"对比华东与华北上月销售额并归因差异"）；
    ② 等待条件改为事件缓冲出现 kind=plan_review 的 hitl_request。
    函数名/参数以该文件实际写法为准移植，本 helper 不重复实现等待轮子。
    """
    raise NotImplementedError("按 docstring 步骤从 tests/test_web_runs.py 移植 arrange 主体")
```

```python
def test_hitl_payload_kind_passthrough(monkeypatch):
    """编排内置 plan_review 中断：注册表补 token 版必须保留 kind 字段。"""
    registry, run_id, token = _start_and_wait_plan_review(monkeypatch)
    run = registry.get(run_id)
    hitl = [e for _, e in run._events if e["event"] == "hitl_request"][-1]
    assert hitl["payload"]["kind"] == "plan_review"
    assert hitl["payload"]["hitl"]["resume_token"] == token


def test_resume_with_action_edit_triggers_replan(monkeypatch):
    registry, run_id, token = _start_and_wait_plan_review(monkeypatch)
    registry.resume(run_id, {"action": "edit", "instruction": "只看华东区域"},
                    owner=None)
    # 轮询 run 状态到重新挂起或完成（照抄既有等待写法），断言事件缓冲中
    # plan_created 在 edit 之后再次出现 = 重规划发生，而非直接终止
    run = registry.get(run_id)
    kinds = [e["event"] for _, e in run._events]
    assert kinds.count("plan_created") >= 2
```

- [ ] **Step 2: 跑测试确认失败 → 实现**

`web/runs.py` `_observer`（L216-218）丢弃内置 hitl_request 处，改为：保留其 payload，构造补 token 事件时 `payload = {**orig_payload, "hitl": {"resume_token": token, ...}}`（`kind` 随 payload 自然透传）。`web/api.py` / stdlib `web/server.py` resume 入口：body 含 `action` 时转译为 resume 值 `{"action": ..., "instruction": ...}` 传 `resume_langgraph`（Task 8 的 Command 路径），缺省 `action="approve"` 保持 clarify 的 `human_reply` 行为不变。

- [ ] **Step 3: 跑测试确认通过 + 向后兼容验证**

Run: `python -m pytest tests/test_web_runs.py tests/test_web_api.py -q`；另跑 `tests/test_agent_stream.py`（clarify 旧式 `human_reply` resume 不回归）。
Expected: 全绿。

- [ ] **Step 4: Commit**

```bash
git add web/runs.py web/api.py web/server.py tests/
git commit -m "feat(web): hitl_request kind 扩展透传 + resume action 贯通（M2）"
```

### Task 12: 前端审批卡片 + 侧栏自主性设置

**Files:**
- Modify: `web/static/js/stream.js`（`hitl_request` 分派按 kind）、`web/static/app.js`（审批卡片渲染 + 三按钮 resume 提交）
- Modify: `web/static/js/sidebar-ui.js`（自主性设置下拉，值随会话持久化）

**Interfaces:**
- Consumes: Task 11 的 `kind` 字段与 resume action 语义。
- Produces: kind=plan_review 时渲染「分析计划审批卡片」（步骤列表 + 每步目标 + 批准/修改/拒绝三操作）；侧栏可设 L1-L4，写入 run 请求体 `autonomy_level`。

- [ ] **Step 1: 流处理按 kind 分派**

`stream.js` / `app.js` 中现有 hitl_request 处理分支前增加 kind 判断：

```javascript
if (ev.event === "hitl_request") {
  var kind = (p.kind) || "clarify"; // 旧服务端无 kind：回退 clarify 卡片，向后兼容
  if (kind === "plan_review") { renderPlanReviewCard(threadId, p); return; }
  renderClarifyCard(threadId, p); // 现有实现抽为此函数，行为不变
}
```

`renderPlanReviewCard`：按 `p.plan_steps` 渲染有序步骤（`goal` 文本 + kind 徽标），三个按钮分别提交：

```javascript
// 批准 / 拒绝 / 修改（弹出输入框取 instruction）
submitPlanAction(threadId, { action: "approve" });
submitPlanAction(threadId, { action: "reject" });
submitPlanAction(threadId, { action: "edit", instruction: input.value });
```

`submitPlanAction` 复用现有 resume 请求通道（`resume_token` + 新增 `action`/`instruction` 字段）。

- [ ] **Step 2: 侧栏自主性设置**

`sidebar-ui.js` 会话菜单追加四档下拉（L1 每步确认 / L2 计划确认 / L3 高危确认 / L4 全自动），选择写入 `localStorage`（key `agent.autonomy.<threadId>`），发起 run 时并入请求体 `autonomy_level`。

- [ ] **Step 3: 手动验收清单（前端无测试基建，验收走 webapp-testing 清单）**

```bash
ORCHESTRATOR_ENGINE=langgraph WEB_SERVER_ENGINE=fastapi python -m web.server 8000
```

- [ ] 多步问题 → 审批卡片出现，步骤与 planner 产物一致；
- [ ] 批准 → 执行推进到 done；拒绝 → 报告区显示"用户拒绝了分析计划"；修改+指令 → 重规划且新计划体现指令；
- [ ] L4 下同一问题零卡片、时间线出现计划通知事件；L1 下每步前出现确认卡；
- [ ] 旧字段路径（clarify 澄清卡）回归正常；刷新页面设置保留。

- [ ] **Step 4: Commit**

```bash
git add web/static/js/stream.js web/static/app.js web/static/js/sidebar-ui.js
git commit -m "feat(web): 分析计划审批卡片 + 侧栏自主性分级设置（M2）"
```

### M2 验收检查点

- [ ] `tests/test_autonomy.py` L1-L4 矩阵全绿；plan_review 三分支（approve/edit/reject）测试全绿。
- [ ] SSE `hitl_request` payload 带 `kind` 且旧式 clarify 流程回归全绿（向后兼容）。
- [ ] `ORCHESTRATOR_ENGINE=native` 下审批门整体关闭、全量测试全绿（降级路径）。
- [ ] 前端手动清单（Step 3）全部通过；`ruff` / `black --check` / 全量 `pytest -q` 全绿。

---

# M3 B 线：Subagent 运行时与 fan-out 编排

**目标**：`SubagentTask`/`SubagentReport` 契约 + 四节点受限子图 + 预算硬顶 + `Send` fan-out（并行归因/多假设/对比升级三模式接口）+ critique 充分性检查 + 子任务轨迹汇入主 SSE 流。

### Task 13: Subagent 契约模型 + 四节点受限子图（确定性降级）

**Files:**
- Create: `core/orchestrator/subagent.py`
- Modify: `core/orchestrator/state.py`（登记 `subagent_reports`）
- Test: `tests/test_subagent.py`（新建）

**Interfaces:**
- Consumes: `nodes.py` 的 `planner_node/dsl_query_node/code_exec_node`（子图复用主图节点函数与全部防御栈）、`AgentState.apply`。
- Produces:
  - `SubagentTask`（Pydantic，`extra="forbid"`）：`task_id: str`、`goal: str`、`context_slice: dict`（schema digest 片段 / 过滤条件 / 上轮 DSL 引用）、`allowed_tools: list[str]`（`default_registry().tool_names()` 子集，越集构造即抛 `ValueError`）、`budget: SubagentBudget`。
  - `SubagentBudget`（`extra="forbid"`）：`max_steps: int = 6`、`timeout_seconds: float = 120.0`。
  - `SubagentReport`（`extra="forbid"`）：`task_id`、`status: Literal["done","failed","timeout"]`、`findings: list[str]`、`artifacts: list[Artifact]`、`audit: dict`（与 ParquetRef.audit 同源同格式 `{guard, qa}`）、`metrics: dict`。
  - `run_subagent(task: SubagentTask, *, thread_id: str, observer) -> SubagentReport`——四节点子图 `plan_task → query → analyze → summarize`（无 LLM 时降级为确定性单步取数）；子图强制 L4（零中断）；`AgentState` 新增 `subagent_reports: list[SubagentReport] = []`（LangGraph 侧 `Annotated[list[SubagentReport], operator.add]` append reducer，native 侧普通 list）。

- [ ] **Step 1: 写契约与降级测试（先失败）**

```python
"""Subagent 契约：白名单 / 预算 / 确定性降级（离线可运行可单测）。"""
import pytest
from pydantic import ValidationError

from core.orchestrator.subagent import (
    SubagentBudget, SubagentReport, SubagentTask, run_subagent,
)
from tools.registry import default_registry


def test_task_rejects_unknown_tool():
    with pytest.raises(ValueError):
        SubagentTask(task_id="t1", goal="g", context_slice={},
                     allowed_tools=["__definitely_not_registered__"], budget=SubagentBudget())


def test_task_tool_subset_must_be_registry_subset():
    registered = default_registry().tool_names()
    if registered:  # 白名单约束：允许注册工具的子集
        task = SubagentTask(task_id="t2", goal="g", context_slice={},
                            allowed_tools=registered[:1], budget=SubagentBudget())
        assert task.allowed_tools == registered[:1]


def test_report_extra_forbid_and_status_literal():
    with pytest.raises(ValidationError):
        SubagentReport(task_id="t", status="crashed", findings=[], artifacts=[], audit={}, metrics={})
    report = SubagentReport(task_id="t", status="done", findings=["f"], artifacts=[], audit={"guard": "ok", "qa": []}, metrics={})
    assert report.status == "done"


def test_subagent_deterministic_without_llm(monkeypatch):
    """无 LLM：降级确定性单步取数，报告 audit 与 ParquetRef 同构（{guard, qa}）。"""
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    task = SubagentTask(task_id="t3", goal="上月销售额", context_slice={},
                        allowed_tools=[], budget=SubagentBudget(max_steps=6, timeout_seconds=60))
    report = run_subagent(task, thread_id="u1:s1:sub:t3", observer=None)
    assert report.status in {"done", "failed", "timeout"}
    assert report.task_id == "t3"
    assert set(report.audit) == {"guard", "qa"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_subagent.py -q`
Expected: FAIL，`ImportError`。

- [ ] **Step 3: 实现 `core/orchestrator/subagent.py`**

核心结构（四节点子图复用主图节点函数，包裹任务卡上下文）：

```python
"""Subagent 运行时（规格 §6）：受限任务卡 + 四节点受限子图 + 预算硬顶。

刻意不做 clarify（不能反问用户）、不做 synthesize（报告权在父图）。
子图强制 L4 自主性（零中断）；无 LLM 降级为确定性单步取数。
"""
import operator
import threading
import time
from typing import Annotated, Literal

from langgraph.graph import StateGraph as LGStateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, ConfigDict

from core.orchestrator.state import AgentState, Artifact
from tools.registry import default_registry


def _task_id_tagged(observer, task_id: str):
    """子任务事件标签器：payload 统一补 task_id 后转发主观察者（Task 15 汇流依赖）。"""
    if observer is None:
        return None
    def tagged(event: str, payload: dict) -> None:
        observer(event, {**payload, "task_id": task_id})
    return tagged


class SubagentBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_steps: int = 6
    timeout_seconds: float = 120.0


class SubagentTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    goal: str
    context_slice: dict = {}
    allowed_tools: list[str] = []
    budget: SubagentBudget = SubagentBudget()

    def model_post_init(self, __context):
        registered = set(default_registry().tool_names())
        unknown = set(self.allowed_tools) - registered
        if unknown:
            raise ValueError(f"allowed_tools 越过注册中心白名单: {sorted(unknown)}")


class SubagentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    status: Literal["done", "failed", "timeout"] = "done"
    findings: list[str] = []
    artifacts: list[Artifact] = []
    audit: dict = {}   # 与 ParquetRef.audit 同源同格式：{guard, qa}
    metrics: dict = {}


class SubagentState(AgentState):
    """子图状态：继承 AgentState 契约，append reducer 仅作用于报告通道。"""
    subagent_reports: Annotated[list[SubagentReport], operator.add] = []


def _subagent_summarize(state: SubagentState) -> SubagentState:
    """summarize 替身：压缩 scratchpad 为 findings 候选；报告权在父图（规格 §6.2）。"""
    findings = [f"{i + 1}. {line}" for i, line in enumerate(list(state.scratchpad)[-10:])]
    return state.apply(scratchpad=findings, phase="done")


def _build_subagent_app():
    """四节点受限子图：plan_task -> query -> analyze -> summarize，节点函数复用主图。"""
    from core.orchestrator.nodes import dsl_query_node, code_exec_node, planner_node
    g = LGStateGraph(SubagentState)
    g.add_node("plan_task", _observed(planner_node))
    g.add_node("query", _observed(dsl_query_node))
    g.add_node("analyze", _observed(code_exec_node))
    g.add_node("summarize", _observed(_subagent_summarize))
    g.set_entry_point("plan_task")
    g.add_edge("plan_task", "query")
    g.add_edge("query", "analyze")
    g.add_edge("analyze", "summarize")
    g.add_edge("summarize", END)
    return g.compile(checkpointer=MemorySaver())


def run_subagent(task: SubagentTask, *, thread_id: str, observer=None) -> SubagentReport:
    """预算硬顶：步数经 recursion_limit（LangGraph 原生超步护栏）、超时经看门狗线程。

    超时后本线程放弃等待但不强杀工作线程（Python 无安全强杀）——残留执行
    由 exec/ 自身的 statement_timeout / 扫描熔断兜底回收，结果被丢弃。
    """
    app = _build_subagent_app()
    state = AgentState(user_query=task.goal, session_id=thread_id,
                       turn_id=task.task_id, autonomy_level="L4",  # 子图强制 L4：零中断
                       **({"history_digest": task.context_slice.get("digest", "")}
                          if task.context_slice else {}))
    config = {
        "configurable": {"thread_id": thread_id,
                         "observer": _task_id_tagged(observer, task.task_id)},  # Task 15 汇流
        "recursion_limit": task.budget.max_steps + 1,  # 四节点 + 余量；超限 GraphRecursionError
    }
    box: dict = {}

    def _run() -> None:
        try:
            box["result"] = app.invoke(state, config)
        except Exception as exc:  # GraphRecursionError / 节点异常 / exec 看门狗超时
            box["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(task.budget.timeout_seconds)
    if worker.is_alive():
        return SubagentReport(task_id=task.task_id, status="timeout",
                              findings=[], artifacts=[], audit={"guard": "ok", "qa": []}, metrics={})
    if "error" in box:
        # 失败不炸主图（规格 §6.4）：转 failed 报告，由父图 critique 决定重派
        return SubagentReport(task_id=task.task_id, status="failed",
                              findings=[f"subagent failed: {box['error']}"],
                              artifacts=[], audit={"guard": "ok", "qa": []}, metrics={})
    final = box["result"]
    return SubagentReport(
        task_id=task.task_id, status="done",
        findings=list(final.get("scratchpad") or [])[-10:],
        artifacts=list(final.get("artifacts") or []),
        audit=_last_artifact_audit(final),  # 从 datasets 的 ParquetRef.audit 提取，{guard, qa} 同源
        metrics={"budget_max_steps": task.budget.max_steps},
    )
```

（`_observed` / `_task_id_tagged` 复用 Task 4 的节点包装器模式与 Task 15 的 task_id 标签器；`_last_artifact_audit(state)` 从 `state["datasets"]` 中最后一个 `ParquetRef.audit` 提取 `{guard, qa}`，无产物时返回 `{"guard": "ok", "qa": []}`。节点函数本体零改动，仅入参适配对照 `nodes.py`。）

（子图编译用 `SubagentState` schema、`build_langgraph_app` 同法装配 `plan_task/query/analyze/summarize` 四节点；`summarize` 只把本子图 scratchpad 压缩为 `findings`，不产出面向用户的报告。节点级实现细节执行者对照 `nodes.py` 各函数入参适配，但**节点函数本体零改动**。）`state.py` 登记父图字段：`subagent_reports: list[SubagentReport] = []`（native 契约），LangGraph 主图 schema 侧用带 reducer 的同名字段声明。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_subagent.py -q`
Expected: 全绿（含无 LLM 降级路径）。

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/subagent.py core/orchestrator/state.py tests/test_subagent.py
git commit -m "feat(orchestrator): Subagent 契约 + 四节点受限子图 + 确定性降级（M3）"
```

### Task 14: Send fan-out 并行编排 + 失败/重派语义

**Files:**
- Modify: `core/orchestrator/langgraph_engine.py`（fan-out 路由 + worker 节点）、`core/orchestrator/autonomy.py`（L3 高危确认钩子）
- Test: `tests/test_fanout.py`（新建）

**Interfaces:**
- Consumes: Task 13 `SubagentTask/run_subagent/SubagentReport`。
- Produces:
  - `run_fanout(tasks: list[SubagentTask], *, thread_id: str, observer=None) -> list[SubagentReport]`——Send 并行执行 + 报告按 `task_id` 排序返回（顺序无关确定性的规范化出口）。
  - `fanout_tasks(state: AgentState) -> list[Send] | str`——plan 产出多子任务 DAG 时返回 `[Send("subagent_worker", task), ...]`，否则回退普通路由。
  - **三种 fan-out 编排模式（规格 §6.3）共用同一 `PlanStep.fanout_tasks` 通道，由 planner 产出的任务卡字段 `mode` 区分**：`"attribution"`（并行归因 map-reduce：按维度拆分 → Send × N → reducer 汇总 → critique 充分性检查 → synthesize）、`"hypothesis"`（多假设并行验证：候选假设并行取证据 → critique 裁决）、`"comparison"`（对比型升级：原串行分解转并行子任务 + 汇总）。三模式在本任务中只要求通道与执行语义正确（测试用 attribution 模式构造），planner 提示词对三模式的产出引导在 Task 16 评测扩展后按用例回填。
  - 并发上限 `settings.SUBAGENT_MAX_PARALLEL`（默认 4）；失败语义：单失败回传 failed 报告，critique 充分性检查决定重派（≤1 轮）或降级单线程补做，重派仍失败则在报告层如实披露缺口。
  - native 引擎降级：`ORCHESTRATOR_ENGINE="native"` 时 fan-out 退化为串行 for 循环调 `run_subagent`。

- [ ] **Step 1: 写顺序无关确定性 + 失败语义测试（先失败）**

```python
"""fan-out：Send 并行结果合并顺序无关、失败不炸主图、并发上限护栏。"""
import threading

from core.orchestrator.subagent import SubagentBudget, SubagentReport, SubagentTask


def _task(i):
    return SubagentTask(task_id=f"t{i}", goal=f"区域{i}异动归因", context_slice={},
                        allowed_tools=[], budget=SubagentBudget())


def test_parallel_merge_is_order_independent(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    # 经 langgraph Send 并行跑 3 个子任务，合并后按 task_id 排序断言（种子固定，两次跑结果逐字段相等）
    from core.orchestrator.langgraph_engine import run_fanout
    r1 = run_fanout([_task(i) for i in range(3)], thread_id="u1:f1")
    r2 = run_fanout([_task(i) for i in range(3)], thread_id="u1:f2")
    key = lambda rs: [(r.task_id, r.status, tuple(r.findings)) for r in sorted(rs, key=lambda x: x.task_id)]
    assert key(r1) == key(r2)


def test_single_failure_does_not_kill_fanout(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_ENABLED", "0")
    from core.orchestrator.langgraph_engine import run_fanout
    reports = run_fanout([_task(0), _task(1)], thread_id="u1:f3")
    assert len(reports) == 2 and all(isinstance(r, SubagentReport) for r in reports)
    statuses = {r.status for r in reports}
    assert statuses <= {"done", "failed", "timeout"}


def test_concurrency_cap_enforced(monkeypatch):
    """并发闸：同时在途的 run_subagent 峰值不超过 SUBAGENT_MAX_PARALLEL。"""
    monkeypatch.setenv("SUBAGENT_MAX_PARALLEL", "2")
    import core.orchestrator.langgraph_engine as lge
    counter = {"active": 0, "peak": 0}
    lock = threading.Lock()
    real = lge.run_subagent

    def counted(task, **kwargs):
        with lock:
            counter["active"] += 1
            counter["peak"] = max(counter["peak"], counter["active"])
        try:
            return real(task, **kwargs)
        finally:
            with lock:
                counter["active"] -= 1

    monkeypatch.setattr(lge, "run_subagent", counted)
    lge.run_fanout([_task(i) for i in range(6)], thread_id="u1:f4")
    assert counter["peak"] <= 2
```

- [ ] **Step 2: 跑测试确认失败 → 实现**

`config/settings.py` 追加：

```python
SUBAGENT_MAX_PARALLEL: int = int(os.getenv("SUBAGENT_MAX_PARALLEL", "4"))
SUBAGENT_REDISPATCH_MAX: int = 1  # critique 充分性不足时的重派上限（规格 §6.4）
```

`langgraph_engine.py`：

```python
parallel_gate = threading.BoundedSemaphore(settings.SUBAGENT_MAX_PARALLEL)

def _subagent_worker(task: SubagentTask, config: dict) -> dict:
    observer = ((config or {}).get("configurable") or {}).get("observer")
    with parallel_gate:  # 并发上限护栏（审计结论：DuckDB 单文件写锁安全边界）
        report = run_subagent(task, thread_id=f"{config['configurable']['thread_id']}:sub:{task.task_id}",
                              observer=_task_id_tagged(observer, task.task_id))
    return {"subagent_reports": [report]}
```

fan-out 路由：`plan` 后检测归因类 DAG（`plan_steps` 携带 `fanout` 标记，由 planner 产出并经 PlanStep 契约字段 `fanout_tasks: list[dict] | None = None` 登记——**契约登记在 state.py 的 PlanStep**）时返回 `[Send("subagent_worker", SubagentTask.model_validate(t)) for t in step.fanout_tasks]`（`step` 为携带标记的 PlanStep）。critique 充分性检查追加在 `critic_node` 出口路径（新函数 `check_subreport_sufficiency(reports, plan) -> list[SubagentTask] | None`，不足则构造重派任务卡，重派上限 `SUBAGENT_REDISPATCH_MAX`，仍不足则在 synthesize 报告的 limitations 小节如实披露）。native 引擎：`agent.py` native 路径对同一 `fanout_tasks` 标记走串行 `for task in tasks: run_subagent(...)`。

- [ ] **Step 3: 跑测试确认通过**

Run: `python -m pytest tests/test_fanout.py tests/test_subagent.py tests/test_langgraph_parity.py -q`
Expected: 全绿。

- [ ] **Step 4: Commit**

```bash
git add core/orchestrator/langgraph_engine.py core/orchestrator/autonomy.py core/orchestrator/state.py config/settings.py tests/test_fanout.py
git commit -m "feat(orchestrator): Send fan-out 并行编排 + 失败重派语义 + 并发上限护栏（M3）"
```

### Task 15: 子任务轨迹汇入主 SSE 流 + run manifest

**Files:**
- Modify: `core/orchestrator/subagent.py`（子任务事件带 `task_id`）
- Modify: `web/static/js/stream.js`、`web/static/app.js`（时间线按子任务分组）

**Interfaces:**
- Consumes: Task 14 worker 的事件流。
- Produces: 子任务 `tool_start/tool_end/step_start` 事件带 `task_id` 汇入主 SSE 流；done 事件 payload 增 `manifest`（全部子任务轨迹摘要），前端时间线按 task_id 分组展示。

- [ ] **Step 1: 事件带 task_id + 前端分组**

`subagent.py` 子图观察器包装：`_task_id_tagged(observer, task_id)` 把 payload 统一补 `task_id` 后转发主 observer。前端时间线渲染遇 `payload.task_id` 时挂到该子任务分组节点（新增轻量分组容器，样式随既有时间线）。done 事件 payload 增 `manifest: [{task_id, status, steps, duration_ms}]`。

- [ ] **Step 2: 验证**

```bash
python -m pytest tests/test_subagent.py tests/test_fanout.py tests/test_sse_contract.py -q
# SSE 契约回归必须仍全绿（manifest 是 done payload 的增量字段，旧前端忽略不崩溃——契约测试快照更新）
```

手动：触发并行归因问题，确认时间线出现子任务分组与 manifest；前端旧路径（无 task_id 事件）渲染不回归。

- [ ] **Step 3: Commit**

```bash
git add core/orchestrator/subagent.py web/static/js/stream.js web/static/app.js tests/
git commit -m "feat(web): 子任务轨迹 task_id 汇流 + run manifest（M3）"
```

### M3 验收检查点

- [ ] `tests/test_subagent.py` + `tests/test_fanout.py` 全绿：白名单越集拒绝、预算硬顶、无 LLM 降级、顺序无关确定性、单失败不炸主图、并发上限。
- [ ] `ORCHESTRATOR_ENGINE=native` 下 fan-out 串行降级路径全绿。
- [ ] SSE 契约回归（含 manifest 增量字段）全绿；前端子任务分组手动验证通过。
- [ ] 全量 `pytest -q` + `ruff` + `black --check` 全绿。

---

# M4 验收收敛

**目标**：双跑对比报告 + 评测扩展 + 删除旧引擎与开关 + 文档全面更新。

### Task 16: 评测扩展（Golden 多步用例）+ 双跑对比报告

**Files:**
- Modify: `eval/golden_dataset.json`（新增 2-3 个并行归因类多步用例）
- Create: `docs/reviews/<执行日YYYYMMDD>-readiness-harness-migration-parity.md`

**Interfaces:**
- Consumes: Task 14 fan-out、`eval/eval_runner.py` 双模式。
- Produces: golden 28 例（25 + 3）、双跑对比报告（迁移验收门证据）。

- [ ] **Step 1: 新增多步 golden 用例**

在 `eval/golden_dataset.json` 按既有用例结构追加 3 个并行归因类多步用例（问题文本要求多维度拆分归因，oracle 侧 DSL 组合与既有条目同构，锚点 `AS_OF_DATE=2024-06-30` 不变；agent 侧 expected 覆盖多步 plan_steps 断言）。样例结构照抄文件内既有 multi_turn / 复合条目，**不引入新 DSL 字段**。

- [ ] **Step 2: 双跑对比报告**

```bash
ORCHESTRATOR_ENGINE=native python -m eval.eval_runner --pipeline oracle
ORCHESTRATOR_ENGINE=langgraph python -m eval.eval_runner --pipeline oracle
ORCHESTRATOR_ENGINE=native python -m eval.eval_runner --pipeline agent
ORCHESTRATOR_ENGINE=langgraph python -m eval.eval_runner --pipeline agent
```

报告落盘 `docs/reviews/<执行日YYYYMMDD>-readiness-harness-migration-parity.md`（首行 `# 评审报告｜生产就绪度：十七期底座迁移双跑对比（YYYY-MM）`）：四组分数对照表、SSE 事件 diff 结论（引用 Task 9 测试）、护栏校准结论（引用 Task 5 测试）、遗留风险。验收标准：双引擎全模式 **零回退**。

- [ ] **Step 3: Commit**

```bash
git add eval/golden_dataset.json docs/reviews/
git commit -m "test(eval): golden 扩展并行归因多步用例 + 双跑对比就绪度报告（M4）"
```

### Task 17: 删除旧引擎与开关，单引擎收敛

**Files:**
- Delete: `core/orchestrator/graph.py`（自研 StateGraph，节点/路由函数已迁至 langgraph_engine 复用）
- Modify: `core/orchestrator/agent.py`（删除 native 分派与 `_run_agent_inner` native 路径）、`config/settings.py`（删 `ORCHESTRATOR_ENGINE`；`WEB_SERVER_ENGINE` 同步删除并删 stdlib Handler 路径）、`web/server.py`（缩为 uvicorn 启动入口 + 保留安全预检/ensure_db）
- Test: 全量回归

**Interfaces:**
- Consumes: Task 16 双跑全绿（删除前置条件，**不满足则禁止执行本任务**）。
- Produces: 单引擎代码路径；`ORCHESTRATOR_ENGINE`/`WEB_SERVER_ENGINE` 环境变量不再被读取（残留设置无害但无效，README 注明）。

- [ ] **Step 1: 删除前确认验收前置**

Run: `python -m pytest -q` 全绿 + Task 16 报告双跑零回退。任一不满足即停止本任务。

- [ ] **Step 2: 删除与收敛**

- 删除 `core/orchestrator/graph.py`；`grep -rn "from core.orchestrator.graph import\|ORCHESTRATOR_ENGINE\|WEB_SERVER_ENGINE\|ThreadingHTTPServer" --include="*.py"` 清空全部引用（`web/server.py` 保留 `main()` 入口，内部改为无条件 `uvicorn.run("web.api:app", ...)`，`python -m web.server 8000` 用法不变）。
- `tests/` 中 native 专属分支测试（如按开关跳过的用例）同步删除，parity 测试改名为单引擎契约测试保留。
- `events.py` / `web/runs.py` 中为双引擎并存而写的分支（如 engine 判断）收敛为单路径。

- [ ] **Step 3: 全量回归**

```bash
python -m pytest -q
python -m eval.eval_runner && python -m eval.eval_runner --pipeline agent
black --check . && ruff check .
```

Expected: 全绿；评测 28 例双模式通过。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(orchestrator): 删除旧引擎与双引擎开关，LangGraph+FastAPI 单引擎收敛（M4）"
```

### Task 18: 文档全面更新（roadmap 十七期）

**Files:**
- Modify: `README.md`（选型哲学章节：从"零框架依赖"改写为"领域内核零框架（4 依赖不变）+ 编排/交互层拥抱成熟框架"）、目录结构章节（新增 `core/orchestrator/langgraph_engine.py`、`autonomy.py`、`subagent.py`、`web/api.py`）
- Modify: `AGENTS.md`（依赖方向铁律措辞同步：编排层框架依赖白名单 langgraph/fastapi；常用命令不变）
- Modify: `docs/superpowers/specs/2026-09-24-agent-harness-evolution-design.md`（文首追加实施状态小节：M0-M4 完成记录 + 计划文档链接）

- [ ] **Step 1: 按上述三处更新文档**（内容以实际落地代码为准，逐节核对不写愿景）。
- [ ] **Step 2: 终验**

```bash
python -m pytest -q && black --check . && ruff check .
python -m web.server 8000  # 冒烟：登录/提问/审批门/自主性切换/fan-out 演示路径
```

- [ ] **Step 3: Commit**

```bash
git add README.md AGENTS.md docs/superpowers/specs/
git commit -m "docs(specs): 十七期 Agent 架构演进 M0-M4 实施完成——单引擎收敛与文档同步"
```

### M4 验收检查点（= 规格迁移验收门）

- [ ] 全量单测全绿（含 golden 扩展后评测锚点）；`eval_runner` oracle + agent 双模式 28 例零回退。
- [ ] 旧引擎与开关已删除，`grep` 无残留引用；`python -m web.server 8000` 用法兼容。
- [ ] SSE 九类事件契约回归测试保持全绿（删除期最后护栏）。
- [ ] README / AGENTS.md / 规格文档三方一致；`docs/reviews/` 双跑对比报告在档。
- [ ] `black --check .`、`ruff check .` 全绿。

---

## 里程碑依赖与执行顺序

```
Task 1 → Task 2 → Task 3 → Task 4 → Task 5        （M0，串行）
Task 5 → Task 6 → Task 7 → Task 8 → Task 9        （M1，串行）
Task 9 → {Task 10 → Task 11 → Task 12} ∥ {Task 13 → Task 14 → Task 15}   （M2 ∥ M3 可并行）
全部 → Task 16 → Task 17 → Task 18                 （M4，串行；Task 17 前置 Task 16 全绿）
```

## 移交执行建议

任务间接口依赖较重（Task 4/5 的引擎适配被 M1-M3 全线消费；Task 13/14 契约贯穿 B 线），出错代价高（删除旧引擎不可逆），建议**本会话逐任务执行**（dev-executing-plans）+ 每个里程碑验收检查点处停下来人工确认一次；M2 与 M3 如需并行，拆两个 git worktree（dev-git-worktrees）分别推进，M4 合并后统一收敛。
