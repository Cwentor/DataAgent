# 评审报告｜全项代码评审：十九期能力边界扩展 M1-M7（2026-10）

## 评审元信息

- **评审对象**：十九期全项（M1-M7），固定点 `e5999f7..HEAD`（HEAD = 79d1629），44 提交、49 文件、+5645/-129；
- **需求对照物**：设计定稿 `docs/superpowers/specs/2026-10-02-capability-boundary-expansion-design.md`（200 行）、七份里程碑实施计划 `docs/plans/2026-10-0X-capability-boundary-m1..m7.md`、AGENTS.md 十九期铁律段；
- **方法**：双轴并行独立评审（Spec 轴代理 + Standards 轴代理，互不知晓），CRITICAL 由主评审复核事实链（MRO 继承、调用链、异常捕获范围三点静态验证）；
- **前序评审**：`20261003-pr-review-capability-boundary-m1-m5.md`（3 HIGH 已修复）、`20261003-security-review-capability-boundary-m1-m6.md`（0 高危结论）。本报告为第三次、覆盖 M1-M7 全项的独立评审，此前未被发现的 CRITICAL 见 Standards 轴 #1。

## 做得好的

- `test_sql_lift.py` 的执行等价断言（重编译后 sorted 行集判等）质量很高，26 条构造矩阵把"转译成功必须语义等价"落到了实处；`test_security_views.py` 的双保险交叉验证、`test_sandbox.py` 的 connect 旁路矩阵（别名绑定/内存库/合法 read_parquet 放行）覆盖扎实；
- 铁律执行到位：LLM SQL 永不直接执行、依赖方向（retrieval 不依赖 orchestrator）、沙箱 connect/database 封死（超出计划，连别名导入与内存库形态也封堵）、全部新 DSL 模型 `extra="forbid"`、web 前端审批卡全部 `textContent` 无 XSS 面；
- 范围纪律好：44 条提交中所有计划外提交均为缺陷修复、评审落盘与格式化，无功能性蔓延。

## Spec 轴（对照设计定稿 + 七份实施计划 + AGENTS.md 铁律）

### 需求覆盖总览

- M1（6 任务）覆盖完整；M2（4 任务）覆盖完整；M3（4 任务）覆盖完整；M5（4 任务）覆盖完整；
- M4 实现完整，T1 原实现曾遗漏设计明示的解析超时/长度上限防线（已由计划外 PR 评审修复补齐，现 `core/retrieval/sql_lift.py:37-38` 长度 10K/超时 5s 符合设计），T4 独立收口提交缺失；
- M6 实现基本完整，两处与设计原文有差距（见 (c)）；
- M7 五任务中 4/5 有落点（T1→1427420、T2→f600aad、T3→3ada056、T4→79d1629），**T5（全量收口 + 记忆更新）无任何 git 落点**，且设计 §8 的 M7 验收门「真实问法题库全量」未达成。

### (a) 缺失 / 半成品

- **[S-1] 设计 §7 新增评测指标三项全部未实现**（MED-HIGH）
  需求原句：「**新增评测指标**：答非所问率（探索层标注合规率）、假设标注率、越权拦截率（必须 100%）」（设计定稿 §7；§8 M7 验收门要求「§7 验收门全部通过」）。
  现状：`eval/eval_runner.py` 本期 diff 仅 9 行，`eval/intent_eval.py` 仅加 list_answer 分支与会话隔离；grep `答非所问|假设标注率|越权拦截率` 于 eval/ 无命中。三项指标无计算逻辑、无输出、无断言，"越权拦截率必须 100%" 作为硬验收指标不可复核。

- **[S-2] 设计 §7 题库第 2 层（复杂分析 10+ 条）与第 4 层（多轮追问链 3 轮+）未入 eval**（MED）
  需求原句：「**复杂分析问法（10+ 条）**：新客 cohort、退款漏斗、分组内 TopN、窗口累计、交叉表——期望行为 = L2 提升或 L3 审批后答对」「**多轮追问链（3 轮+）**」（设计定稿 §7 题库分层）；M7-T3「红线矩阵核对与**题库补全**」（M7 计划）。
  现状：`eval/golden_dataset.json` 本期仅新增 Q26/Q27 两条，无 cohort/漏斗/透视类 L2/L3 题；multi_turn 既有 2 条且各仅 2 轮；3ada056 只补 1 条写语句红线测试。三层同心圆的核心价值场景（L2 提升与 L3 审批后的复杂分析正确性）没有评测锚点。

- **[S-3] M6-T2 审批状态机测试覆盖不完整，三枚审计事件无断言**（LOW-MED）
  需求原句：「挂起 exploration_approval（allow_once/allow_session/deny 三动作）」「审计事件三枚（lift_reject / exploration_approval / exploration_execute）」（M6 计划 Task M6-T2；设计 §6 第 5 条）。
  现状：`tests/test_orchestrator.py:1944-2038` 覆盖 L4 直通、L4+敏感列真中断后 deny、state 预置 allow_session 旁路；但 resume 值为 allow_once/allow_session 的**真中断恢复分支**无直接测试，L2 默认档挂起无显式用例，三枚审计事件（`nodes.py:533,599,670`）仅 emit 无测试捕获断言。（注意：此测试缺口正是 Standards 轴 CRITICAL #1 漏出的直接原因。）

- **[S-4] M4-T4 / M5-T4 / M7-T5 三个「全量收口」任务无独立提交，M7-T5「记忆更新」无落点**（LOW，疑似）
  需求原句：M4-T4 / M5-T4 / M7-T5 各计划任务清单（Commit `test: …全量收口`）。
  现状：`git log --grep="全量收口"` 仅命中 M3（a32e507）与 M6（f020020 附带）；79d1629 之后无任何提交。不排除验证已做但未留痕。

### (b) 范围蔓延

未发现功能性蔓延。计划外提交（ce62a05 clarify 挂起修复、3950475 PR 评审收口、d0dea57 临时 SKIP 已被 M3-T3 按计划移除、各 style/black 提交）均属缺陷修复与评审闭环，符合规范。附注一处代码异味：`core/retrieval/exploration.py:49` 用 `__import__("security.views", ...)` 动态调用 `build_secure_views`（同文件顶部已静态导入同模块其他符号），见 Standards 轴 MEDIUM #5。

### (c) 实现与需求不符

- **[S-5] 审批卡缺「EXPLAIN 行数预估」**（LOW-MED）
  需求原句：「前端审批卡（复用 plan_review UI）：SQL 摘要 + 触达表/视图 + **EXPLAIN 行数预估** + 敏感列提示」（设计 §3.5）。
  现状：审批载荷仅 `{sql, tables, sensitive}`（`core/orchestrator/nodes.py:511-516`；`web/api.py:573-578`），EXPLAIN 预检仅在执行层内部做，结果不回填审批卡。M6 计划已降格载荷但设计 §3.5 未显式修订。

- **[S-6] L4 自动档「越预算仍挂起」实现为「执行层熔断报错」**（LOW，疑似）
  需求原句：「L4 自动档边界：…**越预算仍挂起**」（设计 §3.5）。
  现状：`nodes.py:523-528` L4 且无敏感列即直通放行不预判预算；超预算时走执行层 EXPLAIN 熔断失败路径（`nodes.py:575-598`），非挂起人工审批。设计 §5 错误处理表自述「治理管道熔断：既有行为不变」，两节存在内部张力，实现可辩护，标疑似。

- **[S-7] 拒升自愈次数：设计文档「≤3 次」vs 实现/AGENTS.md「≤2 次」**（LOW）
  需求原句：「（复用既有 ≤3 次闭环）」（设计 §3.3）。现状：`nodes.py:478` `_LIFT_MAX_RETRIES = 2`，AGENTS.md 与 M4 计划均为 2 次。M4 计划与 AGENTS.md 一致，唯独设计文档从未同步修订（f600aad 只改状态行）。

- **[S-8] 「本会话允许」实际语义为「本轮允许」，跨轮不持久**（LOW）
  需求原句：「选项：**允许一次 / 本会话允许 / 拒绝**」（设计 §3.5、§12）。现状：`state.py:195-198` `exploration_allowed` 为轮内字段，未接 session store 跨轮持久化。M6 计划 Review Focus #5 已批准延后，安全复审已如实记录，但设计文档未修订此偏差。

- **[S-9] M7 安全复审文档防线数字与代码不符**（LOW）
  `docs/reviews/20261003-security-review-capability-boundary-m1-m6.md` §2.1 写「解析超时 3s + 长度上限 20K」；代码为 `_PARSE_TIMEOUT_S = 5.0`、`_MAX_SQL_LENGTH = 10_000`（`core/retrieval/sql_lift.py:37-38`）。

- **[S-10] 口径模糊锚点期望为 refusal，与设计 §7 通过条件字面不符**（LOW，疑似）
  需求原句：「断言：**要么发生选项式澄清、要么 assumptions 非空且报告可见**，两种都算通过」（设计 §7 题库第 3 层）。现状：`eval/intent_golden.json` 该题 `expect: "refusal"`，intent_eval 强制无 LLM 走兜底拒答；「补充假设作答断言」落在单测层（`tests/test_orchestrator.py:1671,1709`）。可辩护为「不静默猜」的证明，但 eval 中无 LLM 场景落点。

## Standards 轴

### CRITICAL

**#1 探索审批门（第四类 interrupt）在真实图执行路径上永远挂不起来**
**File:** `core/orchestrator/nodes.py:1421`（根因触发点 `nodes.py:528`）
**Issue:** `_execute_exploration_step`（nodes.py:528）调用 langgraph `interrupt(payload)` 抛出的 `GraphInterrupt` 继承链为 `GraphInterrupt → GraphBubbleUp → Exception`（已核实 MRO），被 `dsl_query_node` 包裹 `_run_query_step` 的通用异常兜底（`except Exception as exc:`，nodes.py:1421）捕获：步骤被标 `failed`、`error_context` 记录 `GraphInterrupt: …`，图继续跑 synthesize 直至 `phase="done"`。已实测复现：L2 + admin + SQL 步骤，plan_review approve 后恢复，`pending=None`、`final.phase="done"`、s1=`failed`。后果：审批卡（L2/L3 全部路径、L4 含敏感列路径）永远到不了用户，`deny` 能力整体失效——M6 核心交付端到端不可用；SQL 因执行发生在 interrupt 之后而未泄露，但人工审批硬边界形同虚设。L4 非敏感路径走 `maybe_interrupt`（`core/orchestrator/autonomy.py:48-50`，L4 分支直接返回默认动作不抛异常）不受影响，因此单测全绿而真实链路失效。测试盲区：探索审批测试（tests/test_orchestrator.py:1944-2025）全部单元级直调节点或 monkeypatch `interrupt`，无图级端到端验证。
**Fix:** 在 `dsl_query_node` 的异常处理最前面放行中断：

```python
from langgraph.errors import GraphInterrupt

except GraphInterrupt:
    raise
except Exception as exc:
    ...
```

同时补一条图级端到端测试（FakeLLM 产 SQL 计划 → L2 → plan_review approve → 断言 `pending["kind"]=="exploration"` 且图暂停 → deny/allow 两向恢复断言终态）。

### HIGH

**#2 只有 `<` 终点的时间条件被静默丢弃，提升后结果集静默扩大**
**File:** `core/retrieval/sql_lift.py:621-647`
**Issue:** `WHERE f.order_time < TIMESTAMP '2024-07-01'` 经闸门提升成功且 `time_filter=None`（实测复现）——`starts` 空字典导致 634 行循环体不执行，`ends` 里的上界条件无人认领、无拒升记录；DSL 重编译后变全域聚合，直接违背"宁拒升不错译"。同构的"只有起点"有拒升（有测试），终点方向漏网。
**Fix:** 按 `starts ∪ ends` 的字段并集循环配对，任一字段只有单边条件即 `rejections.append`；补 only-end 用例。

**#3 RIGHT/FULL JOIN 被误判为 inner 并提升成功**
**File:** `core/retrieval/sql_lift.py:473-474`
**Issue:** `actual_type = "left" if side == "LEFT" else "inner"` 把 `side="RIGHT"/"FULL"` 归入 inner（实测复现两条均 OK lifted）；`JOIN_RULES` 中 dim 表要求 `inner`（semantic/catalog.py:157-161），校验通过后重编译为 INNER JOIN——dim 孤儿行的保留/丢弃语义不同，聚合行集不等价，属"转译成功但语义错译"。
**Fix:** 仅 `side` 为空（纯 inner）才允许映射为 `inner`；`RIGHT`/`FULL` 一律拒升，补两条拒升用例。

**#4 敏感判定可被列名大小写绕过，L4 自动放行硬边界可击穿**
**File:** `core/retrieval/exploration.py:91-93`
**Issue:** `col_name in policy.forbidden_columns` 为区分大小写精确匹配，而 DuckDB 标识符大小写不敏感——`SELECT DISCOUNT_AMOUNT` 对 restricted 判 `False`（实测复现），"含敏感列必须挂人工审批"的硬边界（AGENTS.md 铁律）可被任意大小写变化击穿，审批留痕缺失。数据面由 sec_* 视图禁列物理投影兜底（执行报列不存在，无泄露），故定 HIGH 而非 CRITICAL。
**Fix:** `col_name.lower() in policy.forbidden_columns`（规范化两侧），补大写变体用例。

### MEDIUM

**#5 "allow_session" 实际语义是"本轮"，与前端文案承诺不符**
**File:** `core/orchestrator/nodes.py:565` + `core/orchestrator/agent.py:295`
**Issue:** AgentState 每轮由 `run_agent` 新建且不带 `exploration_allowed`（默认 False），跨轮即重置；而前端按钮文案"本会话允许"（web/static/js/stream-ui.js）与字段 description 均承诺"本会话"。用户下一轮会再次被弹卡，行为与 UI 承诺不符（方向偏严格，无泄露）。与 Spec 轴 [S-8] 同源，此处为代码-UI 契约面。
**Fix:** 二选一对齐：把该标记持久化到 session 级存储（persistence KV），或文案与字段统一改为"本轮允许"。

**#6 怪异动态导入**
**File:** `core/retrieval/exploration.py:49`
**Issue:** `__import__("security.views", fromlist=["build_secure_views"]).build_secure_views(...)`——同文件顶部已静态导入同模块，字符串动态导入绕开静态分析、IDE 跳转与重构工具。
**Fix:** 顶部并入 `from security.views import build_secure_views`，直接调用。

### LOW

**#7 TEMP 视图改写依赖固定前缀字面量，两处字符串耦合**
**File:** `security/views.py:199`
**Issue:** `ddl.replace("CREATE OR REPLACE VIEW", "CREATE OR REPLACE TEMP VIEW", 1)` 依赖 `build_secure_views` 的固定前缀，改一处即静默失效。
**Fix:** `build_secure_views` 直接输出 TEMP 标志或抽公共常量。

### 疑似坏味道（判断题）

- `core/retrieval/exploration.py:95-98`（疑似）：触达表收集把 `for table in catalog.ALIASES` 作外层循环，但循环体内 `meta_col = catalog.COLUMNS.get(col_name)` 只依赖列名、与 `table` 无关——同一列被重复判定 N 张表次。建议化简为按 `meta_col.table` 归属，并对 `forbidden_columns` 匹配做同一处规范化（与 HIGH #4 联动）。
- `core/orchestrator/nodes.py:1421` 宽 `except Exception` 与既有 DSL 自愈兜底共用一个捕获点：一旦某步新增会抛控制流异常的路径就被连带吞掉（本次即 GraphInterrupt）。修复 CRITICAL #1 后建议评估把"控制流异常 vs 业务异常"分层，避免下一个 interrupt 类机制再踩同一坑。

### 测试覆盖提示（非缺陷级别）

- 探索审批门缺**图级端到端**挂起-恢复测试（deny/allow 两向 + allow_session 跨轮语义）——CRITICAL #1 正是从该缺口漏出的，建议修复时一并补齐；
- `test_sql_lift.py` 缺三类负例：只有 `<` 终点（HIGH #2）、RIGHT/FULL JOIN（HIGH #3）、HAVING 与 DISTINCT 投影组合边界；
- `test_exploration.py` 缺敏感判定大小写变体（HIGH #4）与带引号标识符变体；写语句红线锚点（test_exploration.py:121-134）断言异常类型三选一偏宽，可收窄为 `UnsafeSqlError`。

### 规范核对结论

铁律「LLM SQL 永不直接执行」「依赖方向」「沙箱 connect/database 封死」「中文注释」「extra="forbid" + catalog 白名单」「评测锚点」均通过；「严禁隐式吞错」在 `dsl_query_node` 处被 CRITICAL #1 击穿（GraphInterrupt 被当作业务失败吞掉），其余 except 均有记录不静默。

## 结论：能不能合

- **Spec 轴**：6 条（缺失/半成品 4、不符 6 项中 4 项为文档-实现同步问题）；
- **Standards 轴**：🔴 1 / 🟠 3 / 🟡 2 / ⚪ 1，另有疑似坏味道 2 条；
- **判定**：**不能合，立即修。**

CRITICAL #1（GraphInterrupt 被 `dsl_query_node` 通用异常兜底吞掉）使 M6 探索审批门——本期核心交付——在真实图执行路径上端到端不可用，deny 硬边界失效；它之所以能通过 974 条测试与两轮前序评审，是因为探索审批测试全部为单元级直调或 mock interrupt，缺图级端到端验证。修复本身一行（`except GraphInterrupt: raise`），但必须同时补图级端到端测试防回归。

修复优先级建议：
1. 🔴 #1 + 图级端到端测试（合并阻塞项）；
2. 🟠 #2/#3/#4（提升闸门与敏感判定正确性——三者都是"静默出错"型，与"宁拒升不错译"的设计承诺直接冲突，建议随 #1 一批修复）；
3. 🟡 #5/#6 与 Spec 轴 [S-1]/[S-2]（评测指标与题库为 M7 验收门明文要求，属验收收口欠账，建议在十九期正式关闭前补齐）；
4. ⚪ #7 与文档同步类 [S-7]/[S-9]（可留 TODO）。
