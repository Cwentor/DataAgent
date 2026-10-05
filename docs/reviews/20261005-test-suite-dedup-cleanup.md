# 评审报告｜测试套件清理：冗余单测合并与无效测试修复（2026-10）

## 背景与方法

对全量 1010 个离线单测（55 个文件）执行冗余审计：三个并行审计通道逐行对照源码
审查 13 个重点文件（471 个测试，占 47%），其余小文件抽样核对。冗余判定口径：删掉
后故障检测力不降才算冗余；**跨层路径守卫、协议矩阵守卫、分支枚举锚点均为有意冗余，
不清理**。本次清理遵循两条安全线：合并仅限"同函数同分支仅输入微差"的测试组；任何
"断言迁移"先落新位再删旧位，迁移后立刻跑所属文件与全量门禁。

## 清理总账

| 指标 | 清理前 | 清理后 | 变化 |
| ---- | ---- | ---- | ---- |
| pytest 收集用例 | 1012（含 2 live skip） | 993（含 2 live skip） | **-19 用例** |
| 测试函数（维护单元） | ~1010 | ~975 | 约 -35 |
| 全量门禁 | 1010 passed | 991 passed | 全绿 |

检测力守恒：所有合并均保留原断言全集（参数化展开后检测点不减）；删除项均为
超集测试已覆盖的纯重复；两处断言迁移（skills / orchestrator）以等价检测力落位。

## 逐文件明细

### 无效测试修复（1 项，非冗余而是零检测力）

- `test_orchestrator.py::test_resolve_step_inputs_follows_dependency_not_dict_order`：
  其输入 datasets 恰好只有本轮两键，"字典首尾"旧 bug 在该输入下首尾即正确答案——
  测试对自身 docstring 声称的回归 bug **零检测力**。删除（旧 bug 的有效守卫
  `..._ignores_stale_datasets_from_prior_round` 输入为超集，保留）。

### 完全重复删除（9 项）

| 删除项 | 保留的有效守卫 |
| ---- | ---- |
| `test_orchestrator.py::test_llm_second_round_clarification_hard_blocked` | 状态机测试 second 段（同路径、检测面更宽） |
| `test_orchestrator.py::test_exploration_allow_session_skips_repeated_gate` | `..._resume_sets_round_flag` 第二步（captured==1 断言同一短路） |
| `test_agent.py::test_heuristic_dimension_count_fallback_unknown_query` / `test_heuristic_uncovers_dim_count_fallback_without_metrics` | 并入拒绝参数化组 |
| `test_providers.py::test_base_adapter_chat_text_carries_timeout` | `test_dispatching_chat_text_timeout_end_to_end`（全链路覆盖同一断言点） |
| `test_tool_agent.py::test_llm_planner_picks_tool_and_executes` | `test_replan_done_terminates_and_synthesizes`（同 FakeLLM 脚本同路径，llm.calls 断言已并入） |
| `test_memory.py::test_resolve_inherit_still_works_after_reset_rule` | 并入 `test_resolve_inherit_single_province`（同输入，metrics 断言保留） |
| `test_web_auth.py::test_query_without_credentials_401` / `test_metrics_without_credentials_401` | web_api 同端点断言（已核实 401 产生点唯一：`web/api.py:87`，且两文件的 `_start_server` 均为同一 TestClient 桥接桩，非双引擎） |
| `test_web_api.py::test_schema_summary_requires_auth` | web_auth 版（断言超集） |
| `test_web_api.py::test_health_matches_stdlib_contract` | `test_health_field_set_frozen`（字段集锁）+ `/api/health` 冒烟全等 |
| `test_web.py::test_run_query_unknown_question` | `test_router.py::test_run_query_chitchat_rejects`（断言超集） |
| `test_remediation_fixes.py::test_route_normal_queries_not_blocked` | router 五分类/快速路径测试（同函数同分支同断言形状） |
| `test_synthesizer_rebuild.py` 三项（见下） | 断言迁移后删除 |

### 参数化合并（8 组，断言全集保留）

| 文件 | 合并组 | 形态 |
| ---- | ---- | ---- |
| `test_agent.py` | 启发式拒绝 3→1（3 输入参数化） | 同一 PipelineError 分支 |
| `test_agent.py` | count 探查 4→1（含"有几个省"方言变体，4 参数集） | count_probe 同分支 |
| `test_agent.py` | 维度枚举 3→1 | 枚举分支 |
| `test_orchestrator.py` | `_insufficient_is_actionable` 四分支 4→1 | 分支矩阵参数化（域外/已覆盖/域内缺口/无进展） |
| `test_orchestrator.py` | 拒答文案二态 2→1 | LLM 配置状态参数化 |
| `test_orchestrator.py` | factoid 场景 3→1 | 同一次 run_agent 的三个观测面，四条独特断言全保留 |
| `test_degraded_fallback.py` | 降级水印 2→3 参数 | 增加 heuristic 第三分支用例（净 +1 用例） |
| `test_degraded_fallback.py` | 埋点发射 2→1×2 参数 | **必须参数化而非删一**：锁死 answered_by 枚举并集，源码遗漏任一值即失败 |
| `test_report_narrative.py` | `_render_table` 容器折叠 3→1×3 参数 | 比率/金额/计数三分类矩阵 |

### 断言迁移（3 项，先落后删）

- `"66.93 万元"` 人读断言：`test_synthesizer_rebuild::rejects_raw_json` →
  `llm_contract_output_marks_banner`（同输入同分支）；
- 维度池/状态过滤/两期衔接断言：`test_synthesizer_rebuild::carries_dimension_pool`
  → `test_orchestrator::carries_driver_factor_metrics`（同函数同输入）；
- share 归一负份额断言：`test_synthesizer_rebuild::region_attribution_math` →
  `test_skills::test_additive_decomposition_sums`（按 skills 数据重算为 -1.0，
  检测力等价）。

### 跨文件合并（1 项）

- `test_degraded_fallback.py::test_planner_clarify_round_limit_blocks_second_round`
  的 `answered_by in (degraded_confirmed, degraded_auto)` 独特断言迁入
  `test_orchestrator::test_degraded_second_round_ambiguity_answers_with_assumptions`
  （同路径：rounds=1、无 LLM、planner_node 带假设作答），原测试删除。

### 文档性标注（1 项）

- `test_web_api.py::test_agent_run_status_snapshot_contract` docstring 补充与
  `test_agent_stream.py::test_sse_run_status_endpoint` 的分工说明（本测试锚定
  关鉴权匿名属主配置路径，HTTP 层行为由后者覆盖）——消除后续审计的重复嫌疑。

## 审计建议被推翻的项（核实后不合并）

1. **router 两项**：`test_run_query_text2sql_has_intent` 与 `test_e2e_data_query_compiles`
   审计称"同一查询"，实核为**不同查询文本**（GMV 问句 vs 订单数问句），intent 旧值
   兼容断言独立有效；RAG 两条的 detected_intent 断言覆盖不对齐。均保留。
2. **web 401 双锚删除的前提核实**：确认 web_auth 的 `_start_server` 是 TestClient
   桥接桩（非独立 stdlib 服务器）、401 产生点唯一（`web/api.py:87`）后才执行删除——
   若为双引擎独立实现则属路径守卫，不可删。
3. **test_agent.py 拒绝组的词表锚点**："我想看一下数据"（无指标无维度）保留为独立
   参数用例，兼顾原 `uncovers_dim_count_fallback` 的"不误匹配 count_distinct"意图。

## 未处理项（超出"安全清理"范围，后续可立项）

1. **镜像实现改造**：`test_degraded_fallback.py` 两个测试手工复刻 `_plan_gate`
   clarify 恢复合并语义（实现漂移不报警）——需改造为真实恢复路径，属重写非清理，
   且它们是回执闭环唯一锚点，删除风险高。
2. **fixture 卫生**：`FakeLLM` 桩在 test_agent/test_tool_agent/test_synthesizer_rebuild
   三处重复定义、query payload 字面量在 test_orchestrator 出现 5 次等——不影响检测力，
   可提取共享 conftest。
3. **live 测试覆盖缺口**（前期终审遗留 Minor）：stream_options 去参路径无专项用例等，
   见 2026-10-05 握手重试评审报告。

## 验证

- 全量：`python -m pytest -q` → **991 passed, 2 skipped**（约 113s）；
- 格式：`black --check .` / `ruff check .` 全绿（ruff 顺带清理 5 处删测试后的残留 import）；
- 离线确定性不受影响：无新增外部依赖，live 测试默认跳过行为不变。

---

# 二轮：全量补审与收尾清理（同日追加）

首轮审计覆盖 13 个重点文件（471 测试，47%）；二轮对剩余 41 个文件（483 测试）
全量补审（三个并行通道，吸取首轮两处误报教训：输入与断言逐字比对、疑者不删）。

## 补审结论

剩余 483 个测试中约 13 个参与冗余（**2.7%**），比重点文件区（清理前 5%~18%）
低一个量级——按域单文件组织（一模块一文件）的测试天然冗余率低。三个审计通道
均报告大量"有意冗余"核实记录：SSE/事件面四文件为四层各锚一层（事件源契约/
注册表缓冲/HTTP 桥/双实现帧 diff）、sql_lift 30 条输入各异矩阵、沙箱 AST 18
形态参数化、JWT 双攻击向量、内存/SQLite 双实现锚定等，均为设计上的守卫不清理。

## 二轮清理明细（净 -2 用例、-4 函数）

| 类型 | 项目 | 依据 |
| ---- | ---- | ---- |
| 无效断言修复 | `test_retrieval.py::test_mask_column_name_heuristics` 的 `assert out[0][0] == out[0][0]`（恒真自比较，注释意图"同值同掩码"） | 输入第二行邮箱改为同值，断言改为 `out[0][0] == out[1][0]`——测试恢复其声称的检测力 |
| 完全重复删除 | `test_compiler.py::test_having_requires_grouping_and_metrics` | 两分支输入与 M2 互斥矩阵 case1/case2 逐字一致，矩阵断言更强（含 match） |
| 分支级删除 | `test_compiler.py::test_expression_metric_contract_rejections` 的"ref 未声明"分支 | 与矩阵 case4 逐字一致；其余 3 分支（op 白名单/ref 指 ratio/lit NaN）为独立守卫保留 |
| 完全重复删除 | `test_present.py::test_viz_pie_for_few_categories` | 与 `test_viz_pie_without_y_signal_unaffected` 同输入同断言（rows 无 y 列，两测触发完全相同分支组合） |
| 分支级删除 | `test_exec.py::test_unsafe_sql_allows_comment_and_with` 的 WITH 段 | 与 `allows_normal_statements` 同 SQL 逐字重复，注释剥离独有价值保留 |
| 参数化合并 | `test_sandbox.py` 静态守卫入口拦截 2→1×2 参数（importlib/open 两威胁形态） | 同一 `static_check` 分支，参数 id 保留威胁语义 |
| 参数化合并 | `test_sandbox.py` title 非法形态 2→1×2 参数（dict 直传/'{'前缀串） | 锚定 `_bootstrap` 校验的两个 or 条件子句，metrics 指引断言两形态共享 |

## 二轮裁决（核实后保留，不清理）

1. **security/scope 单点权限测试**（`test_restricted_denies_refund_table` 等）：
   虽被 `test_rls_adversarial_matrix_restricted` 循环覆盖（alias 差异不参与权限
   判定，已核实 `guard.referenced_fields`），但作为"表级/列级"小节的直接文档
   锚点各仅 5 行，scope 版另承载"纵深防御仍生效"主题落点——保留。
2. **`build_messages` 内容断言**与 `build_system_prompt` 测试重叠（直通返回）：
   保留前者独有的消息结构契约断言，内容断言仅 3 行，清理收益为零。
3. **exec 字面量分号双验证**：输入非逐字相同（多一列 + WHERE），不满足安全
   合并标准，保留双验证。
4. **附带观察**（非冗余）：`test_agent_stream.py` 两个 SSE helper 结构重复、
   `test_retrieval` 恒真断言的姊妹缺口已由 `test_mask_preserves_groupability`
   正确覆盖——前者归 fixture 卫生。

## 二轮验证

- 全量：`python -m pytest -q` → **989 passed, 2 skipped**（约 103s）；
- black / ruff 全绿；
- 两轮累计：1010 → 989 离线用例（-21），函数数约 -39，检测力守恒（每处合并
  保留断言全集、断言迁移先落后删、无效断言修复为有效）。
