# 十九期 M3 实施计划：Planner 双产出契约 + 分级透明作答

> 沿用 M1/M2 执行模式（本会话逐任务 TDD）；步骤用 `- [ ]` 勾选跟踪。
> 前置状态：HEAD @ d0dea57，920 测试绿、intent_eval 14/14。

**Goal:** LLM Planner 学会产出 M2 新形态（HAVING/表达式/投影）并输出口径假设（assumptions）；分级透明作答落地——澄清一轮后仍歧义不再拒答，改为选定合理口径 + 假设标注作答；确定性启发式补齐 HAVING/客单价表达式产出，移除 CI 的 Q26/Q27 SKIP 豁免。

**Architecture:** 提示词契约扩展（PLANNER_SYSTEM 广播新形态 + assumptions 字段 + 二轮澄清纪律）；AgentState 增 `assumptions`；planner 消费 clarification 在二轮时降级为确定性直答（`_degraded_parse` 增 assume 模式：多候选筛选维度转分组）；synthesize 报告头部呈现口径假设（LLM 与确定性双路）；启发式 `_metrics` 客单价改表达式形态 + HAVING 阈值解析。

**Tech Stack:** pydantic v2、既有 LangGraph 编排（零图结构变更）、agent 包启发式规则。

**Spec:** `docs/superpowers/specs/2026-10-02-capability-boundary-expansion-design.md` §3.6（双产出/分级透明）、§3.7（兜底不回退）、§8 M3 验收。

## Global Constraints

- 同 M1/M2 Global Constraints 全部适用；
- 分级透明红线：假设必须显式标注，**严禁静默猜口径**（spec §12 决策记录）；
- 兜底模式（无 LLM）行为不回退：确定性直答能力只增不减（spec §3.7）；
- M2 已知待办：d0dea57 的 `_is_contract_pending_case` SKIP 豁免在启发式接入后**必须移除**（eval harness 与 test_agent 同源单一事实源）。

## Review Focus

1. 二轮歧义带假设作答后，`answered_by` 水印必须如实（degraded_confirmed/degraded_auto 不变，报告含口径假设小节）——"带假设作答"不等于"静默降级"（M3-T2）；
2. assumptions 消费宽容：缺失/非字符串数组不得阻塞规划（对齐 intent 回传的宽容契约）（M3-T1）；
3. LLM 二轮仍返回 clarification 时必须被硬约束拦截（提示词是软约束，planner_node 消费层强制），严禁二轮澄清挂起死循环（M3-T2）；
4. 客单价口径变更（avg → gmv/orders 表达式）须全量回归——golden/评测/单测无旧形态残留依赖（M2 已核查仅 Q27 引用）（M3-T3）；
5. HAVING 阈值解析只作用于已解析出的金额类指标别名，问句无数值阈值时不产出 having（宁缺毋滥）（M3-T3）。

---

### Task M3-T1: Planner 契约扩展

**Files:** `core/orchestrator/prompts.py`（PLANNER_SYSTEM + planner_prompt）、`core/orchestrator/state.py`（assumptions 字段）、`core/orchestrator/nodes.py`（planner_node 消费）
**Test:** `tests/test_orchestrator.py`

- Produces: `AgentState.assumptions: list[str]`；planner JSON `assumptions` 字段消费；`planner_prompt(..., clarify_context: str | None)`

- [ ] Step 1 失败测试：state 含 assumptions 字段；planner_prompt(clarify_context=...) 注入二轮指令小节；planner_node 消费 assumptions（monkeypatch _llm_json 返回带 assumptions 的 payload → state.assumptions 非空）；非法 assumptions（非数组）宽容忽略不阻塞
- [ ] Step 2 确认失败
- [ ] Step 3 实现（提示词三处：输出契约加 assumptions、DSL 契约要点广播纯投影/HAVING/表达式规则、澄清纪律补"二轮后严禁再次澄清"；planner_prompt 注入 clarify_context；planner_node 透传轮次上下文 + 宽容消费 assumptions）
- [ ] Step 4 通过 + Commit `feat(orchestrator): Planner 契约扩展——assumptions 与 M2 新形态广播（十九期 M3）`

### Task M3-T2: 二轮歧义带假设作答 + 报告口径假设呈现

**Files:** `core/orchestrator/nodes.py`（planner_node clarification 二轮拦截；`_degraded_parse` 增 assume 模式；synthesize 呈现）、`core/orchestrator/prompts.py`（SYNTHESIZER_SYSTEM 口径假设指令）
**Test:** `tests/test_orchestrator.py`、`tests/test_degraded_fallback.py`

- Produces: `_degraded_parse(query, profile, enum_values, assume_on_ambiguity=False) -> (mode, payload, assumptions)`（返回三元组）；二轮歧义 → plan + assumptions；报告含口径假设小节

- [ ] Step 1 失败测试：degraded 二轮 clarify（rounds>=1 + pending 多候选）→ mode=plan + assumptions 非空 + 维度转分组；LLM 二轮返回 clarification → 不挂起、走 degraded assume 计划；确定性报告含"口径假设"小节
- [ ] Step 2 确认失败
- [ ] Step 3 实现（`_degraded_parse` 三元组 + pending 维度 assume 转分组；planner_node 二轮 clarification 硬拦截 → assume 计划；二轮 round-limit 拒答块替换为 assume 分支；synthesize 确定性路径头部注入 assumptions、SYNTHESIZER_SYSTEM 增"报告开头逐条呈现口径假设"纪律并把 assumptions 注入综合输入）
- [ ] Step 4 通过 + Commit `feat(orchestrator): 二轮歧义带假设作答与报告口径假设呈现（十九期 M3）`

### Task M3-T3: 启发式 HAVING/客单价表达式 + 移除 SKIP 豁免

**Files:** `agent/heuristic.py`（客单价分支 + HAVING 阈值解析）、`eval/eval_runner.py`（移除 `_is_contract_pending_case`）、`tests/test_agent.py`（移除同源豁免）
**Test:** `tests/test_agent.py`（恢复 `test_heuristic_covers_all_golden_questions` 全覆盖）

- [ ] Step 1 失败测试：启发式对 Q26/Q27 问题文本产出的 DSL 与 golden expected 相等（现有豁免测试移除豁免后即失败）；无数值阈值不产出 having
- [ ] Step 2 确认失败
- [ ] Step 3 实现：客单价 → gmv + orders + expression(aov=div) 三指标形态；HAVING 阈值正则（超过/大于/高于→gt，低于/小于→lt）作用于金额类指标别名
- [ ] Step 4 通过 + Commit `feat(agent): 启发式 HAVING 与客单价表达式产出，移除 CI SKIP 豁免（十九期 M3）`

### Task M3-T4: 口径模糊题库与全量收口

**Files:** `eval/intent_golden.json`、`tests/test_orchestrator.py`
**Test:** 全量 pytest / black / ruff / intent_eval / eval_runner（oracle 与 agent 均 27/27）

- [ ] Step 1 澄清-标注路由单测补全（二轮 assume 路由的状态机断言）+ 口径模糊离线锚点（clarify 挂起语义已有用例，补充假设作答断言）
- [ ] Step 2 全量质量门（含 agent 模式 eval 无 SKIP 全绿）
- [ ] Step 3 Commit `test(eval): M3 口径模糊题库与澄清-标注路由锚点；全量收口`
