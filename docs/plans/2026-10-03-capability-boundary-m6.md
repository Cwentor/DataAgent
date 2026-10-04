# 十九期 M6 实施计划：探索层执行（L3 链路 + 审批门 + 前端审批卡）

> 沿用 M1-M5 执行模式（本会话逐任务 TDD）；步骤用 `- [ ]` 勾选跟踪。
> 前置：HEAD @ 066e0fa，967 测试绿。

**Goal:** 不可提升的 SQL 不再静默回落兜底，而是经**探索层审批门**（可视化选项）后在安全视图上受治理执行——"数据可达范围内都有力"的最后一环执行面。

**Architecture:** 新 `core/retrieval/exploration.py`：sqlglot 表名重写（基表 → sec_* 视图，越权表拒绝）+ TEMP 视图安装（read_only 连接可用）+ 复用 `execute_sql` 三护栏与 `export_to_parquet` PII 脱敏；编排接线：M4 的"自愈耗尽回落启发式"改为**保留 SQL 计划**交执行层审批（`maybe_interrupt` 第四类 `exploration`：allow_once / allow_session / deny；L4 无敏感列自动放行 + 水印，含敏感列仍挂起）；拒绝 → 诚实告知；批准 → 探索执行 + 报告"探索查询产出"标注。审计事件三枚（lift_reject / exploration_approval / exploration_execute）。

**Tech Stack:** sqlglot、DuckDB TEMP VIEW、langgraph interrupt、既有 exec 护栏与 export 管道、web/static 审批卡。

**Spec:** `docs/superpowers/specs/2026-10-02-capability-boundary-expansion-design.md` §3.5（审批门）、§4 案例 B、§6.5（L4 边界）、§8 M6 验收。

## Global Constraints

- 同 M1-M5 Global Constraints 全部适用；
- 探索 SQL **永不执行原文**：必须经表名重写到 sec_* 视图后由 `execute_sql` 护栏执行；
- "带假设作答 ≠ 静默降级"同样适用于探索层：报告必须醒目标注"探索查询产出"；
- 审批拒绝是诚实告知，不算错误。

## Review Focus

1. TEMP 视图在 read_only 连接上可用（生产连接池是 read_only——测试用 read_only 文件连接实证，内存连接不算数）（M6-T1）；
2. 越权表拒绝：SQL 引用无 sec_* 视图的基表 → 拒绝并精确指名（不静默改名或放行）（M6-T1）；
3. L4 自动放行边界：SQL 含 principal 禁列（文本级判定）→ 即使 L4 也必须挂起审批（M6-T2）；
4. 审批拒绝 → blocked 诚实报告（含替代问法），plan_steps 清空，不得带错误重试（M6-T2）；
5. "本会话允许"在本轮状态内生效（跨轮持久化经 session store 属延后 Minor，docstring 如实记录）（M6-T2）；
6. 探索结果集必须经 PII 脱敏导出（export_to_parquet 既有管道，不得绕行）（M6-T1）。

---

### Task M6-T1: 探索执行器（视图重写 + TEMP 视图 + 治理执行）

**Files:** Create `core/retrieval/exploration.py`；Test Create `tests/test_exploration.py`
**Interfaces:**
- Produces: `rewrite_sql_to_views(sql, principal) -> tuple[str | None, list[str]]`（重写后 SQL / 拒绝清单）；`execute_exploration_query(sql, principal, workspace, name, query, conn=None) -> ParquetRef`（audit 带 `exploration: True`）；`exploration_risk(sql, principal) -> tuple[bool, list[str]]`（含敏感列判定, 触达表）

- [ ] Step 1 失败测试：基表→sec_* 重写执行等价（对照原库直接执行）；read_only 文件连接上 TEMP 视图安装可用；越权表拒绝；含禁列 SQL 的敏感判定；护栏复用（超行数 ResultLimitExceeded）；导出 audit.exploration=True 且 PII 脱敏生效
- [ ] Step 2 确认失败
- [ ] Step 3 实现
- [ ] Step 4 通过 + Commit `feat(retrieval): 探索执行器——视图重写与治理执行（十九期 M6）`

### Task M6-T2: 编排接线 + 审批门 + 审计事件

**Files:** `core/orchestrator/nodes.py`（`_lift_plan_sql` 耗尽保留 SQL 计划；`dsl_query_node` sql 分支探索执行 + 审批门）、`core/orchestrator/autonomy.py`（exploration 直通动作）、`core/orchestrator/state.py`（`exploration_allowed` 字段）、`core/orchestrator/nodes.py`（synthesize 探索标注）
**Test:** `tests/test_orchestrator.py`（M4 耗尽测试按 M6 行为更新 + 审批状态机）

- [ ] Step 1 失败测试：拒升耗尽 → 保留 SQL 计划（answered_by=llm）+ lift_reject 事件；L2 下 sql 步骤执行 → 挂起 exploration_approval（allow_once/allow_session/deny 三动作）；deny → blocked 诚实报告；L4 无敏感列 → 自动放行水印；L4 含敏感列 → 仍挂起；allow_session 后同轮第二次 sql 步骤不再询问；探索数据集报告含"探索查询产出"标注
- [ ] Step 2 确认失败
- [ ] Step 3 实现（autonomy._DEFAULT_ACTIONS 增 exploration：{"action": "allow_once"}；dsl_query_node 分支：exploration_risk 判定 → maybe_interrupt(kind=exploration, sql 摘要/触达表/敏感提示) → 批准则 execute_exploration_query，dataset 带 exploration 标记；deny → blocked）
- [ ] Step 4 通过 + Commit `feat(orchestrator): 探索层编排接线与第四类审批门（十九期 M6）`

### Task M6-T3: 前端审批卡

**Files:** `web/static/*`（审批卡渲染）、`web/service.py`（resume 动作透传核对）
**Test:** 既有前端测试（如有）+ 手工冒烟说明

- [ ] Step 1 探查前端 plan_review/high_risk 卡实现 → 最小扩展 exploration 卡（SQL 摘要 + 触达表 + 三按钮 → resume JSON）
- [ ] Step 2 实现 + 全量回归
- [ ] Step 3 Commit `feat(web): 探索层审批卡——三按钮可视化选项（十九期 M6）`

### Task M6-T4: 全量收口

- [ ] Step 1 全量 pytest / black / ruff / intent_eval / eval_runner（无 Key 等效环境双模式 27/27）+ 端到端冒烟（L4 自动放行出报告）
- [ ] Step 2 Commit `test: M6 全量收口——探索层回归与质量门（十九期 M6）`
