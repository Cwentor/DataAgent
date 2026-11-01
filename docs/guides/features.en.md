# Feature overview

This is the full DataAgent feature list, organized by evolution phase. The README keeps only the highlights; phase-level details live here. This file mirrors the Chinese [feature overview](features.md). For the phase index and design-doc entries see the [roadmap](roadmap.md) and the [docs index](../README.md).

## Foundation (Phases 1–13)

The ChatBI query-engine groundwork; every item below still runs in production today.

- **Semantic layer & contract (Phases 1, 8)**: semantic DSL with a field-whitelist catalog; Pydantic V2 + `extra="forbid"` strong contracts — fields, operators and aggregations are closed enums; multi-metric period-over-period, window functions, date gap-filling, grouped Top-N
- **Agent dual path (Phase 2)**: strictly validated LLM path plus a deterministic heuristic fallback; fully usable offline with no API key; safe degradation with a visible `degraded` flag when the LLM fails
- **Compilation semantics (Phases 3, 4)**: dual-window CTEs for period-over-period, multi-fact-table semantic model with cross-table ratio metrics
- **Permission system (Phases 5, 10)**: table / column / row-level RLS; unified auth (JWT + Session); server-side principal binding and pre-generation scope narrowing
- **Explanation & visualization (Phase 6)**: Chinese narrative explanation of the DSL and adaptive chart recommendation
- **Web service & workspace (Phases 7, 15)**: FastAPI + uvicorn service layer with query / orchestration / async-task endpoints; two-pane interactive workspace, nine SSE event types pushed in real time, task-DAG timeline, artifact canvas, HITL clarification interactions
- **Execution governance (Phase 11)**: read-only allowlist, timeout cancellation, scan-row circuit breaker, result-row cap, SQL self-healing, connection pool and query cache
- **Injection defense & hardening (Phase 12)**: read-only AST checks, login rate limiting, SQLite session offload, startup strict validation, observability
- **Agent capability deepening (Phase 13)**: observation-driven replanning (the dispatch trace is fed back to the planner under a hard Max-Steps budget), comparison questions decomposed into per-entity queries answered side by side, a reflection layer self-checking result sufficiency (with one bounded follow-up query when needed)
- **Observability (Phases 11, 12)**: audit snapshots, structured JSON logs (request_id threading, error/warning/info severity discipline), QPS and percentile metrics

## Enterprise upgrade layer (Phase 14, the `core/` packages)

The Data Agent capability layer on top of the core pipeline.

- **Graph orchestration (initial)**: StateGraph six nodes (clarify HITL, plan, governed retrieval, sandbox analysis, reflection/replan, synthesis); the plan is a step DAG
- **GuardrailAgent pre-execution audit**: compiled SQL goes through a sqlglot AST audit — cartesian products (comma / CROSS / JOIN without ON) and read-only violations are **REJECTED before execution** (the rejection reason feeds the self-healing context); unbounded output produces WARNING audit trails; verdicts are structured (check / severity / message / suggestion)
- **DataQAAgent result assertions**: four deterministic checks — empty result / NULL rate / negative values on non-negative metrics / uniqueness of aggregation dimension combinations — shared by both the orchestrator and the web retrieval paths; findings surface through `ParquetRef.audit` via SSE events, the critic's reflection view, a "data quality" section in the report, and structured logs (no false kills, no swallowed errors)
- **SchemaAgent dynamic profiling**: low-cardinality (≤30) string columns are probed for actual values and injected into the planning context ("allowed values: a|b|c"), so the planner stops inventing filter literals; process-level cache with graceful degradation, zero side effects offline
- **Sandbox code interpreter**: AST static guard (denylisted imports/calls/dunder escapes) + privilege-limited runner (module allowlist imports, workspace-restricted `open`) + pluggable backends (Docker `--net=none --cap-drop=ALL` for strong isolation, subprocess fallback); aggregation matrices capped at ≤100 rows to prevent data exfiltration
- **Attribution skill pack**: dimension entropy / information-gain drill-down, multiplicative log-chain metric decomposition trees (GMV = UV × CR × AOV), additive gap decomposition, DTW similarity, Holt-Winters anomaly detection, Shapley-value fair attribution — all cross-checked against known closed-form solutions
- **Data exchange protocol**: DSL results are PII-masked (column-name heuristics + value-shape regex, deterministic hash masking) and materialized as a `ParquetRef` (sha256-auditable); the sandbox has no network and no DB socket
- **Raw-SQL gateway**: string payloads / SQL keys / free-form SQL shapes are rejected at the gateway (`GuardrailViolation`) — the three lines of the "the LLM never emits raw SQL" defense

## Model provider gateway (Phase 16, `providers/`)

- Four protocol adapters — OpenAI Chat, OpenAI Responses, Anthropic, Google — with JSON-Mode smoothing
- API keys encrypted at rest, list responses never echo secrets
- Visual CRUD and connectivity probes in the workspace settings page (all providers are user-added, nothing preset)
- SSE orchestration supports request-level `provider_id` / `model_id` model switching

## Agent architecture evolution (Phase 17, `core/orchestrator/`)

- **Single LangGraph engine convergence**: six-node semantic port + checkpointer (`ORCHESTRATOR_CHECKPOINT_DB`, optional SQLite persistence)
- **Generalized interrupts with L1-L4 autonomy levels**: clarify / plan_review approval gates; L3 double-confirms model-authored sandbox code (high_risk); L4 passes through
- **Subagent fan-out**: constrained task cards + tool-whitelist execution-path trimming + a four-node subgraph + a hard budget cap
- **Self-healing error-context injection**: on replanning the latest failure summary (`error_context`) is injected into the Planner prompt — healing is targeted correction, not blind retry

## Intent routing convergence & honest fallback (Phase 18)

- **Clarification authority moves up to the Planner** (a `clarification` contract, with option-style clarifications — clicking a metric pill answers it)
- **Admission-based fallback**: diagnostics / cardinality ("how many provinces are there") / hard-anchored metrics are answered deterministically (with a note on the default definition); all other intents get an honest refusal (reason + recognized anchors + capability list, zero LLM calls)
- **L3 intent-DSL mismatch guard**: blocks "cardinality intent + amount aggregation" style mismatches before execution
- **Grounding retry loop**: reported numbers are verified against source values (unit-normalized tolerance); non-traceable values above threshold trigger one rewrite with correction instructions, otherwise rendering degrades deterministically
- **Intent vocabulary with a single source of truth**: `FieldMeta.aliases` — new field aliases take effect automatically on registration; off-target-answer evaluation runs in CI (`intent_eval`)

## Capability boundary expansion (Phase 19, three concentric layers)

The capability boundary is the governance-pipeline boundary (read-only + RLS + sensitive-data protection + resource caps), not the DSL contract's expressiveness. LLM-produced SQL is never executed directly.

- **L1 deterministic core**: the Planner emits DSL directly for routine queries
- **L2 lifting gate** (`core/retrieval/sql_lift.py`, sqlglot): beyond-contract SQL is lifted back into a DSL contract and recompiled by the deterministic compiler; a refusal to lift is not a refusal to answer — the precise rejection list feeds self-healing (≤2 rounds)
- **L3 exploration layer** (`core/retrieval/exploration.py`): non-liftable SQL, after a fourth approval gate (allow_once / allow_session / deny), executes under governance on a per-principal secure view (`security/views.py`: forbidden-column physical projection blocked + RLS baked in + connection hardened), with the report clearly labeled "exploration query output"
- **DSL expansion**: pure-dimension projections / HAVING / expression metrics, multi-dimensional enumeration
- **Tiered transparent answering**: honesty = no fabrication + explicit assumptions (the `assumptions` contract field, shown at the top of the report); second-round ambiguity turns into answering-with-assumptions; refusal is the last resort
- **Sandbox bypass sealed**: `connect/database` calls are permanently blocked — data access lives only in the execution layer; red-line matrix runs in CI (`boundary_eval`)

## Gmall warehouse & semantic SSOT (Phase 20)

- **Gmall e-commerce warehouse migration**: a reverse-parsed data generator (seed-table extraction / session behavior-chain simulation / event JSON logs / parsed into 4 behavior fact tables); the transaction domain anchors on the `order_detail` wide table + traffic-domain `QUERY_DOMAINS` anchors, with CompileError on cross-domain queries
- **Single source of semantic truth**: `config/semantic.json` is the only static source of business facts — fields / labels / aliases / join rules / metric definitions / paid filter / default windows / reflection concepts / enum labels / region mapping / dimension member seeds all live in that JSON; `semantic/catalog.py` reads it directly at import time, and adding business mappings in hand-written code is forbidden
- **Enumeration pre-routing with deterministic rendering**: ENUMERATION intents are pre-routed at the orchestration entry into a deterministic rendering path — a full row listing (truncated with a split when above threshold), bypassing the LLM entirely: zero hallucination, zero latency; a degradation watermark distinguishes "enumeration pre-routed" from "LLM failure fallback"
- **Audit & logging extensions**: orchestration audit snapshots gain `answered_by` / `planner_llm_error`; structured logs optionally persist via `RotatingFileHandler` (`AUDIT_LOG_FILE`)
- **Engineering hygiene**: test directories mirror source packages one-to-one; safe streaming-handshake timeout retries; report view turn navigation and anchor locating

## Multiple execution backends (Phases 21–25)

`DB_BACKEND=duckdb|mysql|postgres|clickhouse|hive` (duckdb by default, zero breakage) — five backends, one contract.

- **Engine abstraction layer** (`exec/engine/`): the `EngineAdapter` protocol pushes connection / execution / cancellation / plan parsing / error normalization / connection hardening / bulk load / physical metadata down to each engine; `DialectRenderer` (`compiler/dialect/`) carries dialect differences (identifier quoting, date truncation and shifting, HAVING aliases, gap-filling spine, string escaping). Adding a backend means registering one line in each, with zero changes to the guardrail framework or consumers
- **Warehouse builds**: all five backends reuse the DuckDB DDL transpiled at runtime with row-identical source data; Hive builds via CSV staging + `LOAD DATA` (PyHive HTTP transport)
- **Physical metadata plane (Phase 24)**: `physical_columns()` reads the catalog's physical columns live per backend (`information_schema.columns` for DuckDB/MySQL/PG, `system.columns` for ClickHouse, per-table `DESCRIBE` parsing for Hive); type normalization converges in `exec/engine/type_map.py`, where unknown types fail closed instead of silently degrading
- **Per-backend specifics**:
  - **MySQL**: three read-only defense lines (read-only account + session READ ONLY + dialect denylist), `max_execution_time` timeouts, EXPLAIN ANALYZE scan breaker, Monday-aligned WEEKDAY weeks
  - **PostgreSQL**: `statement_timeout` (SQLSTATE 57014 normalization), `default_transaction_read_only` hardening, native date_trunc / generate_series
  - **ClickHouse**: account-profile budgets (readonly=1), verification-style hardening (reads `system.settings` back, fail-closed comparison), `numbers(N)` + addDays/addMonths/addYears spine, text-normalized driver errors
  - **Hive**: no session-level read-only / budget / cancel surface — client read timeouts + verification-style harden, `posexplode` gap-filling spine, backtick-quoted internal identifiers, fail-closed DESCRIBE partition parsing
- **Service-level CI gates**: one parity job per non-default backend — full golden-DSL cross-backend parity + week-boundary / recursive gap-filling / comparison pairing / connection-pool reuse cases + warehouse build smoke; golden evaluation stays anchored on the DuckDB dialect regardless of engine

## Interactive architecture diagram (architecture-ui)

A ReactFlow layered interactive canvas in the `architecture-ui/` subproject, built and deployed automatically by the Pages workflow. Seven main-chain layers plus two side groups (cross-cutting support / quality assurance); node and edge semantics align with [Architecture & design](architecture.md). Supports layer visibility toggles, upstream/downstream highlighting and vertical/horizontal layouts.

## Cross-cutting capabilities at a glance

| Capability | Entry |
| --- | --- |
| Unified auth (JWT + Session + login rate limiting) | `auth/` |
| Table / column / row-level RLS and secure views | `security/` |
| Audit snapshots, structured logs, metrics (/api/metrics) | `audit/` |
| Session / slot / rate-limit SQLite offload | `persistence/` |
| Golden evaluation (oracle / agent dual mode) + intent eval + boundary eval | `eval/` |
| Deterministic Gmall mock warehouse (seed 42, anchor 2025-12-31) | `mock/` |
| Five-backend local Docker test matrix | `scripts/backend_matrix.py` |
