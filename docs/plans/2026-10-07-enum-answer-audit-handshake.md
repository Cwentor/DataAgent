# 枚举直答体验修复 + 编排审计闭环 + 握手重试落地 实施计划

> 本计划交 dev-executing-plans 逐任务执行；步骤用 `- [ ]` 勾选跟踪。

**Goal:** 实现四立项点——P1 枚举题全量直答（截断分流）、P2 降级水印文案分流、P3 编排链路审计闭环、P4 握手期超时安全重试落地。

**Architecture:** 三部分独立交付。P1+P2 改动集中在 `core/orchestrator/nodes.py` 综合层出口（ENUMERATION 预路由不走 LLM 叙述、确定性渲染行清单 + 中性水印）；P3 复用 `AuditStore` 扩展 `audit_log` 字段并接入 `/api/agent/run`，日志加可选文件 handler；P4 沿用既有 spec 实施。

**Tech Stack:** DuckDB、LangGraph、FastAPI、pytest、Pydantic。

**Spec:** `docs/superpowers/specs/2026-10-07-enum-answer-audit-handshake-design.md`（执行者需同时读规格与本计划）。

## Global Constraints

- LLM 产出的 SQL 永不直接执行，必须经提升闸门转译或治理执行；
- DSL 模型一律 `extra="forbid"`，Agent 只能产出契约内字段；
- 评测锚点 `AS_OF_DATE = 2025-12-31`，随机种子 42，确定性可复现；
- 提交前 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿；
- 新增意图词表枚举时同步 `intent_golden.json` 锚点；
- `answered_by` 取值须与 `state.answered_by` 枚举保持一致（P1 新增 `enumeration` 取值须在 `_degradation_banner` 与审计字段同步登记）；
- 审计写入失败绝不影响主链路（`try/except` + `logger.exception`，参考 `web/service.py:623`）；
- ENUMERATION 直答只走纯维度投影，不触及 comparison/top_n/fill_gaps/window 互斥组合（编译器显式抛 `CompileError`）。

## Review Focus

规格隐含、最可能咬到真实使用者的失效模式，各配测试挂到 owning task：

1. ENUMERATION 意图但含过滤线索（如"有退款的省份"）→ 走 `enumeration_dsl` 返回 None 时须拒答，不得全量枚举；
2. ENUMERATION 多维（"省份和品类"）→ 返回全量多维清单（字段序 = 词表提取序）；
3. ENUMERATION 结果集含极端宽行 → 每行宽度独立钳制，行数不截断；
4. 编排审计写入失败 → `/api/agent/run` 主链路不受影响，仅记日志；
5. 握手重试发生在 mid-stream 阶段（已收到响应字节）→ 严禁重试（防重复计费铁律）。

（RF1 由 `enumeration_dsl` 既有逻辑守护、RF5 由 spec 既有测试守护；RF2/RF3 已补测试至 Task 1、RF4 已补测试至 Task 5。）

---

# Part 1：枚举直答体验修复（P1 + P2）

## Task 1: ENUMERATION 路径确定性行清单渲染（P1 核心）

**Files:**
- Modify: `core/orchestrator/nodes.py:3100-3189`（`synthesize_node` 核心区段，LLM 综合判定入口）
- Create: `tests/core/test_enumeration_direct_answer.py`

**Interfaces:**
- Consumes: `classify_intent`（`core/orchestrator/intent.py:136`）、`IntentType.ENUMERATION`（`intent.py:158`）、`_preview_rows`（`nodes.py` 内）、`workspace_path`（`nodes.py:2837`）
- Produces: `_render_enumeration_rows(state: AgentState) -> str`（新函数，渲染全量维度取值清单）、`answered_by="enumeration"` 新取值（下游 Task 2 水印与 Task 4 审计字段消费）

- [ ] **Step 1: 写失败测试**

```python
# tests/core/test_enumeration_direct_answer.py
from core.orchestrator.state import AgentState
from core.orchestrator.nodes import _render_enumeration_rows, synthesize_node


def _enum_state(query: str, rows: list) -> AgentState:
    """构造 ENUMERATION 意图的 state，datasets 指向含 province 列的 parquet。"""
    return AgentState(
        session_id="s1",
        turn_id="t1",
        user_query=query,
        answered_by="heuristic",
        datasets={
            "dim": {
                "path": "_rows.parquet",
                "columns": ["province"],
                "rows": len(rows),
                "audit": {},
            }
        },
    )


def test_enumeration_renders_full_list_not_truncated(tmp_path, monkeypatch):
    """枚举题应返回全量清单，不被 preview[:20] 截断。"""
    import duckdb

    rows = [{"province": f"省份{i:02d}"} for i in range(34)]
    parquet = tmp_path / "_rows.parquet"
    duckdb.from_df(__import__("pandas").DataFrame(rows)).to_parquet(str(parquet))

    state = _enum_state("列举全部省份", rows)
    # monkeypatch workspace_path 指向 tmp_path
    from core.orchestrator import nodes as N

    monkeypatch.setattr(N, "workspace_path", lambda s: tmp_path)
    report = _render_enumeration_rows(state)
    # 全量 34 行，不应被截断到 20
    assert "省份33" in report
    assert report.count("省份") >= 34


def test_enumeration_multi_dim_full_list(tmp_path, monkeypatch):
    """RF2 多维枚举返回全量多维清单（字段序 = 词表提取序）。"""
    import duckdb
    import pandas

    rows = [{"province": f"省份{i}", "category": f"类目{i}"} for i in range(34)]
    parquet = tmp_path / "_rows.parquet"
    duckdb.from_df(pandas.DataFrame(rows)).to_parquet(str(parquet))
    state = _enum_state("省份和品类", rows)
    state.datasets["dim"]["columns"] = ["province", "category"]
    monkeypatch.setattr(N, "workspace_path", lambda s: tmp_path)
    report = _render_enumeration_rows(state)
    assert "省份33" in report and "类目33" in report


def test_enumeration_wide_row_clamped(tmp_path, monkeypatch):
    """RF3 极端宽行宽度钳制（40），行数不截断。"""
    import duckdb
    import pandas

    rows = [{"province": "x" * 200}]
    parquet = tmp_path / "_rows.parquet"
    duckdb.from_df(pandas.DataFrame(rows)).to_parquet(str(parquet))
    state = _enum_state("列举省份", rows)
    monkeypatch.setattr(N, "workspace_path", lambda s: tmp_path)
    report = _render_enumeration_rows(state)
    assert "x" * 200 not in report
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/core/test_enumeration_direct_answer.py::test_enumeration_renders_full_list_not_truncated -v`
Expected: FAIL, `AttributeError: module 'core.orchestrator.nodes' has no attribute '_render_enumeration_rows'`

- [ ] **Step 3: 最小实现**

在 `core/orchestrator/nodes.py` 的 `synthesize_node` 核心区段入口（`deduped_summaries or state.datasets` 判定之前，约 `nodes.py:3108`）插入 ENUMERATION 预路由直答分支：

```python
def _render_enumeration_rows(state: AgentState) -> str:
    """ENUMERATION 预路由直答：确定性渲染维度取值全量清单（不走 LLM 叙述）。

    纯名称清单无数值，绕过 grounding 口径风险低；行数不截断，每行宽度独立
    钳制防止极端宽行撑爆报告。
    """
    lines: list[str] = [f"## 维度取值清单：{state.user_query}", ""]
    for name, ref in state.datasets.items():
        preview = _preview_rows(str(workspace_path(state) / "inputs" / ref.get("path", "")))
        cols = [str(c) for c in ref.get("columns", [])]
        dims = [c for c in cols if c not in ("gmv", "orders", "buyers")]
        if not dims or not preview:
            continue
        width = 40  # 每行宽度独立钳制，行数不截断
        lines.append(f"**{name}**（{len(preview)} 个取值）")
        lines.append("")
        lines.append("| " + " | ".join(dims) + " |")
        lines.append("| " + " | ".join("---" for _ in dims) + " |")
        for row in preview:
            cells = [str(row[cols.index(d)])[:width] for d in dims]
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
```

在 `synthesize_node` 入口插入判定：

```python
    # ENUMERATION 预路由直答（P1）：不走 LLM 叙述，确定性渲染全量行清单
    from core.orchestrator.intent import IntentType, classify_intent

    if classify_intent(state.user_query).intent == IntentType.ENUMERATION:
        report = _render_enumeration_rows(state)
        events.emit_event(
            events.EVENT_ARTIFACT_EMIT,
            {"artifact": {"type": "markdown_report", "title": "维度取值清单", "content": report}},
        )
        return state.apply(report=report, answered_by="enumeration", phase="done")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/core/test_enumeration_direct_answer.py::test_enumeration_renders_full_list_not_truncated -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/core/test_enumeration_direct_answer.py
git commit -m "feat(orchestrator): ENUMERATION 预路由确定性渲染全量行清单（P1 截断分流）"

## Task 2: 降级水印文案分流（P2）

**Files:**
- Modify: `core/orchestrator/nodes.py:2988-3021`（`_degradation_banner` 分支判定）
- Create: `tests/core/test_enumeration_banner.py`

**Interfaces:**
- Consumes: `answered_by="enumeration"`（Task 1 产出）、`state.planner_llm_error`（`state.py:220`）
- Produces: `_degradation_banner` 新增 `enumeration` 分支中性文案（与 LLM 故障告警视觉可区分）

- [ ] **Step 1: 写失败测试**

```python
# tests/core/test_enumeration_banner.py
from core.orchestrator.state import AgentState
from core.orchestrator.nodes import _degradation_banner


def test_banner_enumeration_is_neutral():
    """枚举预路由直答：中性文案，非故障告警。"""
    state = AgentState(session_id="s1", turn_id="t1", user_query="列举全部省份", answered_by="enumeration")
    banner = _degradation_banner(state)
    assert "确定性路径直答" in banner
    assert "暂不可用" not in banner


def test_banner_llm_failure_is_alert():
    """真 LLM 故障降级：告警文案带原因，与枚举中性路径视觉可区分。"""
    state = AgentState(
        session_id="s1",
        turn_id="t1",
        user_query="GMV",
        answered_by="heuristic",
        planner_llm_error="timeout",
    )
    banner = _degradation_banner(state)
    assert "暂不可用" in banner
    assert "原因：timeout" in banner
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/core/test_enumeration_banner.py::test_banner_enumeration_is_neutral -v`
Expected: FAIL, `assert "确定性路径直答" in banner` 失败（当前返回空串或 heuristic 文案）

- [ ] **Step 3: 最小实现**

在 `_degradation_banner` 的 `degraded_auto` 分支之后、`heuristic` 分支之前插入 `enumeration` 分支：

```python
    if state.answered_by == "enumeration":
        # 枚举预路由直答（P2）：LLM 根本没被调用，非故障降级，中性文案
        return (
            "> ℹ️ **枚举类问题按确定性路径直答**，未调用 LLM 规划；"
            "以下为维度取值全量清单。\n\n"
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/core/test_enumeration_banner.py -v`
Expected: PASS（两个用例均通过）

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator/nodes.py tests/core/test_enumeration_banner.py
git commit -m "feat(orchestrator): 降级水印区分枚举预路由直答与 LLM 故障（P2 文案分流）"
```

## Task 3: golden 重固化 + 枚举评测用例（34 行全量断言）

**Files:**
- Modify: `eval/intent_golden.json`（枚举类意图锚点，同步登记）
- Modify: `eval/` 评测用例（枚举类意图 34 行全量断言）

**Interfaces:**
- Consumes: Task 1 产出的 `answered_by="enumeration"` 路径
- Produces: 重固化后的 golden 快照（ENUMERATION 全量断言）

- [ ] **Step 1: 跑 golden 重建确认变化**

Run: `python -m eval.rebuild_golden`
Expected: 输出 ENUMERATION 相关用例的快照变化（从截断 20 行变为全量 34 行），无报错

- [ ] **Step 2: 同步 intent_golden.json 锚点**

新增/核对枚举类意图评测用例锚点，确保 `intent_golden.json` 的 ENUMERATION 取值与 `semantic/catalog` 的 `FieldMeta.aliases` 一致（AGENTS.md 铁律：意图词表以 catalog 别名为单一事实源）。

- [ ] **Step 3: 新增枚举类意图 34 行全量断言用例**

在评测用例中新增：枚举类意图"列举全部省份"的 golden 断言 = 全量 34 行（不截断），断言 `answered_by == "enumeration"`。

- [ ] **Step 4: 跑评测确认通过**

Run: `python -m eval.eval_runner --pipeline agent`
Expected: ENUMERATION 用例 PASS，全量 34 行断言通过

- [ ] **Step 5: Commit**

```bash
git add eval/
git commit -m "test(eval): 枚举类意图 golden 重固化 + 34 行全量断言（P1 验收）"

---

# Part 2：编排链路审计闭环（P3）

## Task 4: audit_log 表扩展字段（answered_by / planner_llm_error）

**Files:**
- Modify: `audit/record.py:19-43`（`AuditRecord` 字段定义）
- Modify: `audit/store.py:25-77`（`_AUDIT_COLUMN_TYPES` / `_AUDIT_DDL` / `_INSERT_SQL` 三处同步）
- Modify: `audit/store.py:225-246`（`_insert_db` 参数列表）
- Modify: `tests/test_audit.py`（一致性断言同步）

**Interfaces:**
- Consumes: `AuditRecord` 既有字段
- Produces: `AuditRecord.answered_by`、`AuditRecord.planner_llm_error` 两个新字段（下游 Task 5 消费）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_audit.py 新增（追加到既有一致性断言文件）
def test_audit_columns_ddl_consistent_after_extension():
    """_AUDIT_COLUMN_TYPES 与 _AUDIT_DDL 列清单一致（新增 answered_by / planner_llm_error 后仍守护）。"""
    import re

    from audit.store import _AUDIT_COLUMN_TYPES, _AUDIT_DDL

    ddl_cols = set(re.findall(r"^\s+(\w+)\s+\w+", _AUDIT_DDL, flags=re.MULTILINE))
    assert set(_AUDIT_COLUMN_TYPES) == ddl_cols
    assert "answered_by" in _AUDIT_COLUMN_TYPES
    assert "planner_llm_error" in _AUDIT_COLUMN_TYPES


def test_audit_record_serializes_orchestration_fields():
    """AuditRecord 序列化包含编排特有字段。"""
    from audit.record import AuditRecord

    rec = AuditRecord(request_id="r1", prompt="列举省份", answered_by="enumeration", planner_llm_error=None)
    d = rec.to_dict()
    assert d["answered_by"] == "enumeration"
    assert d["planner_llm_error"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_audit.py::test_audit_columns_ddl_consistent_after_extension -v`
Expected: FAIL，`"answered_by" not in _AUDIT_COLUMN_TYPES`

- [ ] **Step 3: 最小实现**

`audit/record.py` 的 `AuditRecord` 在 `routing_reason` 之后、`created_at` 之前插入两个字段：

```python
    # 编排特有字段（P3）：编排裁决方与 LLM 规划失败原因
    answered_by: str | None = None
    planner_llm_error: str | None = None
```

`audit/store.py` 三处同步（DDL 与 _AUDIT_COLUMN_TYPES 必须逐列一致，由既有一致性断言守护）：

```python
# _AUDIT_COLUMN_TYPES 追加（store.py:43 之后）
    "answered_by": "VARCHAR",
    "planner_llm_error": "VARCHAR",
```

```python
# _AUDIT_DDL 追加（CREATE TABLE 内，created_at 之前）
    answered_by            VARCHAR,
    planner_llm_error      VARCHAR,
```

```python
# _INSERT_SQL 追加两个占位符
# INSERT INTO audit_log (...) VALUES (?, ?, ?, ..., ?, ?)  # 末尾追加 answered_by, planner_llm_error
```

`_insert_db` 的参数列表（`store.py:225-246`）末尾追加：

```python
                    record.answered_by,
                    record.planner_llm_error,
```

`_migrate_schema`（`store.py:164`）自动迁移旧表补齐新列（既有机制，幂等不丢历史数据）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_audit.py -v`
Expected: PASS（一致性断言 + 序列化断言均通过）

- [ ] **Step 5: Commit**

```bash
git add audit/record.py audit/store.py tests/test_audit.py
git commit -m "feat(audit): audit_log 扩展 answered_by/planner_llm_error 编排字段（P3）"
```

## Task 5: /api/agent/run 接入编排审计写入

**Files:**
- Modify: `web/api.py:512-593`（`post_agent_run`，done 路径返回前接入审计）
- Modify: `web/service.py:214-225`（`_default_audit_store` 供编排路由复用，若已共享则跳过）

**Interfaces:**
- Consumes: `AuditStore.write`（Task 4 扩展后的 `AuditRecord`）、`_default_audit_store`（`web/service.py:214`）
- Produces: 编排请求的 `audit.jsonl` 落盘记录（answered_by / planner_llm_error / detected_intent 可查）

- [ ] **Step 1: 写失败测试**

```python
# tests/web/test_agent_audit.py
from fastapi.testclient import TestClient
from web.server import app  # 或实际 app 入口


def test_agent_run_writes_audit_record(tmp_path, monkeypatch):
    """/api/agent/run done 路径落 audit.jsonl，含 answered_by 字段。"""
    jsonl = tmp_path / "audit.jsonl"
    # monkeypatch _default_audit_store 返回指向 tmp_path 的 AuditStore
    ...
    resp = client.post("/api/agent/run", json={"query": "列举全部省份"}, headers={"Authorization": "Bearer <token>"})
    assert resp.status_code == 200
    lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    import json

    rec = json.loads(lines[0])
    assert rec["answered_by"] == "enumeration"
    assert rec["prompt"] == "列举全部省份"


def test_agent_run_audit_failure_does_not_break_main(monkeypatch):
    """RF4 审计写入失败时 /api/agent/run 仍返回 200，不影响主链路。"""
    from audit.store import AuditStore

    def _boom(self, record):
        raise OSError("disk full")

    monkeypatch.setattr(AuditStore, "write", _boom)
    resp = client.post("/api/agent/run", json={"query": "列举全部省份"}, headers={"Authorization": "Bearer <token>"})
    assert resp.status_code == 200
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/web/test_agent_audit.py::test_agent_run_writes_audit_record -v`
Expected: FAIL，`audit.jsonl` 不存在或无记录（/api/agent/run 当前不落审计）

- [ ] **Step 3: 最小实现**

在 `post_agent_run` 的 done 路径返回前（`result_dict` 构造之后、`return Response` 之前，约 `api.py:587`）插入审计写入，参照查询管道 `web/service.py:619-624` 模式：

```python
    result_dict = result.to_dict()
    result_dict["auth"] = ctx.to_dict()
    # 编排审计（P3）：落 audit.jsonl，失败不影响主链路
    store = _default_audit_store()
    if store is not None and isinstance(result, AgentState):
        try:
            store.write(AuditRecord(
                request_id=request.headers.get("X-Request-ID") or _uuid.uuid4().hex,
                session_id=ctx.session_id or ctx.username,
                user=ctx.username,
                prompt=query,
                detected_intent=result.intent_type or result_dict.get("detected_intent"),
                answered_by=result.answered_by or result_dict.get("answered_by"),
                planner_llm_error=result.planner_llm_error or result_dict.get("planner_llm_error"),
            ))
        except Exception:
            logger.exception("audit_write_failed", extra={"event": "audit_write_failed"})
```

需在 `api.py` 顶部补 import：`from audit.record import AuditRecord`、`from web.service import _default_audit_store`（若已存在则跳过）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/web/test_agent_audit.py::test_agent_run_writes_audit_record -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/api.py tests/web/test_agent_audit.py
git commit -m "feat(web): /api/agent/run 编排路由接入审计写入（P3 审计闭环）"
```

## Task 6: 结构化日志文件 handler（可选 RotatingFileHandler）

**Files:**
- Modify: `audit/logging.py:76-84`（`setup_logging` 加可选文件 handler）
- Modify: `config/settings.py`（新增 `AUDIT_LOG_FILE` 配置项，默认 None）
- Modify: `web/server.py`（启动时传 `log_file` 给 `setup_logging`）
- Create: `tests/test_logging_file_handler.py`

**Interfaces:**
- Consumes: `setup_logging(level, log_file)` 签名扩展
- Produces: 日志可选落盘（配置开关，默认关闭）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_logging_file_handler.py
import logging
import logging.handlers


def test_setup_logging_writes_file_when_enabled(tmp_path):
    """配置 log_file 时日志落盘。"""
    from audit.logging import setup_logging

    log_file = tmp_path / "app.log"
    root = logging.getLogger()
    # 清理既有 handler 保证幂等测试
    for h in list(root.handlers):
        root.removeHandler(h)
    setup_logging(log_file=str(log_file))
    logging.getLogger("test").info("hello-audit")
    for h in list(root.handlers):
        h.flush()
    assert log_file.exists()
    assert "hello-audit" in log_file.read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_logging_file_handler.py::test_setup_logging_writes_file_when_enabled -v`
Expected: FAIL，`setup_logging` 不接受 `log_file` 参数（TypeError）

- [ ] **Step 3: 最小实现**

`audit/logging.py` 的 `setup_logging` 签名扩展：

```python
def setup_logging(level: int = logging.INFO, log_file: str | None = None) -> None:
    """把根 logger 配成结构化 JSON 输出（幂等）。log_file 非空时追加文件 handler。"""
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    if log_file:
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(JsonFormatter())
        root.addHandler(fh)
    root.setLevel(level)
```

`config/settings.py` 新增配置项（默认 None，即默认关闭）：

```python
AUDIT_LOG_FILE: str | None = None  # 编排审计结构化日志文件路径，None=仅 stderr
```

`web/server.py` 启动时传 `log_file`：

```python
setup_logging(log_file=settings.AUDIT_LOG_FILE)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_logging_file_handler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add audit/logging.py config/settings.py web/server.py tests/test_logging_file_handler.py
git commit -m "feat(audit): 结构化日志可选 RotatingFileHandler 落盘（P3 可观测性）"

---

# Part 3：握手期超时安全重试落地（P4）

## Task 7: 握手重试实施（沿用既有 spec）

**Files:**
- Modify: `providers/errors.py`（`StreamHandshakeTimeout` 专用异常，spec §4.1）
- Modify: `providers/` 网关 `_http_post_sse`（握手期独立短预算 + 安全重试，spec §4.2-§4.3）
- Modify: `config/settings.py`（`PROVIDER_HANDSHAKE_TIMEOUT` / `PROVIDER_HANDSHAKE_RETRY_MAX`，spec §4.2）
- Modify: `audit/` 指标（三类事件计入 `/api/metrics`，spec §4.4）
- Create: `tests/providers/test_handshake_retry.py`

**Interfaces:**
- Consumes: 既有 spec `docs/superpowers/specs/2026-10-05-llm-handshake-retry-design.md`（§4 架构设计全套）
- Produces: 握手期超时安全重试能力 + 三类事件指标

**说明**：本任务实施既有 spec，详细设计见 spec §4（异常体系 / 握手预算 / 重试策略 / 指标）。执行者按 spec §4 逐项落地，关键锚点如下。

- [ ] **Step 1: 写失败测试（握手超时专用异常 + 重试配置）**

```python
# tests/providers/test_handshake_retry.py
from providers.errors import StreamHandshakeTimeout
from config import settings


def test_handshake_timeout_exception_distinct():
    """握手期超时抛专用异常，区别于 ProviderTimeoutError。"""
    exc = StreamHandshakeTimeout("handshake failed")
    assert isinstance(exc, Exception)
    assert "handshake" in str(exc)


def test_handshake_retry_config_present():
    """握手重试配置项存在且可关闭。"""
    assert settings.PROVIDER_HANDSHAKE_TIMEOUT > 0
    assert settings.PROVIDER_HANDSHAKE_RETRY_MAX >= 0  # 0=关闭重试
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/providers/test_handshake_retry.py -v`
Expected: FAIL，`ModuleNotFoundError: cannot import name 'StreamHandshakeTimeout'`

- [ ] **Step 3: 按 spec §4 实施**

按 spec §4.1-§4.4 落地（异常体系 / 握手独立短预算 / 安全重试一次 / 三类事件指标）。关键约束（spec §目标/非目标）：
- 握手期（收到任何响应字节之前）超时抛 `StreamHandshakeTimeout`，允许安全重试一次；
- 握手阶段独立短预算 `PROVIDER_HANDSHAKE_TIMEOUT`（默认 30s），保证最坏总延迟不高于现有单次 60s；
- mid-stream（已收到响应字节后）严禁重试（防重复计费铁律）；
- 三类事件（握手超时 / 重试成功 / 重试仍失败）计入 `/api/metrics`；
- 编排层、前端零改动。

计费边界假设与免责口径沿用 spec §前提假设（切换供应商时须重新确认）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/providers/test_handshake_retry.py -v`
Expected: PASS

Run: `python -m pytest tests/providers/ -v`
Expected: 既有 providers 测试全绿（mid-stream 不重试回归守护）

- [ ] **Step 5: Commit**

```bash
git add providers/ config/settings.py audit/ tests/providers/test_handshake_retry.py
git commit -m "feat(providers): 握手期超时安全重试落地（P4 既有 spec 实施）"
```

---

## 移交执行

1. 本计划 7 个任务，P1+P2 合成小期（Task 1-3）、P3 独立立项（Task 4-6）、P4 既有 spec 实施（Task 7）。任务间接口：Task 1 产出 `answered_by="enumeration"` 与 `_render_enumeration_rows`，Task 2 消费该取值出水印，Task 4 审计字段登记该取值，Task 5 编排路由落盘该取值——Task 1 是 2/4/5 的前置。

2. **推荐执行方式：本会话逐任务**（最快最省）。依据：设计已由计划与规格承载，任务间接口依赖集中在 Task 1（P1+P2+P3 共享 `answered_by="enumeration"` 取值），出错代价中等，独立性可接受。如需隔离工作区，执行时按 dev-git-worktrees 创建。

3. 执行交 dev-executing-plans；测试纪律见 dev-tdd。每个任务自带一轮测试周期（写失败测试→跑确认失败→最小实现→跑确认通过→提交），逐任务过评审门。
```
```
```
