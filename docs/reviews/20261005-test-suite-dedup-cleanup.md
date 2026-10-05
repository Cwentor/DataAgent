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
