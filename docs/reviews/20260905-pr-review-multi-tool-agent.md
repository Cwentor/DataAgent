# 评审报告｜PR 评审：多工具编排（Multi-Tool Agent）升级（2025-06）

> 评审日期：2025-06（会话内实时评审） · 评审方式：逐文件通读 + 缺陷实机复现 + 全量测试/静态检查验证
> 评审范围（本次未提交改动，共 407 insertions / 91 deletions）：
> - 新增：`tools/`（base / registry / builtins×6）、`agent/tool_agent.py`、`tests/test_tools.py`、`tests/test_tool_agent.py`
> - 修改：`web/service.py`、`web/server.py`、`exec/pool.py`、`audit/metrics.py`、`audit/record.py`、`config/settings.py`、`agent/heuristic.py`、`agent/glossary.py`、`present/viz.py`、`web/static/*`、`pyproject.toml`、`tests/test_web.py`

---

## 0. 四大排查维度预检结论（Non-Negotiable Checkpoints）

| 排查维度 | 结论 | 依据（文件:行号） |
|---|---|---|
| ① DSL 契约是否被绕过 | ✅ **未绕过** | 三个数据工具全部经 `tools/builtins/_query_core.py:64-105 run_guarded_query`：NL→DSL（`agent/pipeline.py:43-51`）→ `apply_policy` → `compile_sql` → `execute_sql`；趋势工具的规范化产物是 `QueryDSL.model_copy`（`trend_analysis_tool.py:209/228/241`），仍受 `extra="forbid"` 契约约束。**全 diff 无一处手写/LLM 裸 SQL** |
| ② 时间代数与补零是否复用 | ✅ **复用编译器** | 对比窗口由编译器 `compiler/sql_compiler.py:88-107 _shift_window` 生成、补零由 `sql_compiler.py:555-621 _compile_with_fill_gaps` 生成；趋势工具仅做语义规范化且正确遵守编译器互斥契约（comparison 剔时间维度 `trend_analysis_tool.py:182-188`、fill_gaps WEEK→DAY 降级 `:224-226`、comparison 与 fill_gaps/top_n 互斥 `:173-175,214-216`）。仅"默认窗口常量"存在双实现（见明细表 P2-7） |
| ③ RLS 行级权限强继承 | ✅ 数据生成侧完整 / ⚠ 产物获取侧缺口 | `agent/pipeline.py:51`（NL 路径内部 apply_policy）、`_query_core.py:123`（外部 DSL 路径显式 apply_policy）、`_query_core.py:151`（**自愈重写后的 DSL 重新过守卫，防权限逃逸**）。导出**数据**生成受 RLS 约束；但下载端点无属主校验（见红线 P1-3） |
| ④ 只读与熔断穿透 | ✅ 无穿透 / ⚠ 并发闸有条件旁路 | 所有 SQL 100% 经 `exec/guards.py execute_sql`（只读语句白名单 + 表函数黑名单、线程看门狗超时 `conn.interrupt()`、EXPLAIN ANALYZE 扫描行预检、LIMIT 硬上限），工具层无直连查询；但 conn 未注入时走第二连接池、绕过 web 层并发闸（见 P1-2，仅 LLM 规划模式下可达） |
| ⑤ 自愈闭环（Self-Correction）是否存活 | ✅ 存活且增强 | 原 `web/service.py _execute_with_self_heal` **原样迁移**至 `_query_core.py:126-156`（语义等价：CompileError/SqlExecutionError → rewrite_dsl → 重新 apply_policy → 重试 `SQL_SELF_HEAL_MAX_RETRIES` 次，失败 `record_self_heal_failure` + 透传原始报错）；外层叠加工具级自愈 `tool_agent.py:505-521`（受 Max Steps 3~5 与永久错误短路 `:578-586` 约束）；`BaseTool.run` 永不裸抛（`tools/base.py:137-142`）→ 错误结构化进 `AgentResult.error` → `web/service.py:281-285` 映射友好话术与熔断打点。原有 3 个自愈单测保持绿色 |
| ⑥ 澄清机制冲突 | ✅ 确定性模式无冲突 / ⚠ LLM 模式断裂 | `agent/router.py:85-92` 前置 `detect_clarifications` 与 `tool_agent.py:203` 的二次检测幂等一致（同函数同输入，router 已过滤则 planner 必为空），无"抢跑"/死循环；但 LLMPlanner 的 clarify 输出绕过 P0-5 槽位回填（见 P1-5） |
| ⑦ 依赖膨胀 | ✅ 零膨胀 | `requirements.txt` / `requirements-dev.txt` 零改动；`pyproject.toml` 仅 +`tools*` 包登记；无 LangChain/LlamaIndex 等框架；registry+base 合计 ~340 行标准库 + Pydantic |
| ⑧ 目录污染 | ✅ 本次增量克制 | 仅 `tools/{base,registry,builtins/}` 与 `agent/tool_agent.py` 扁平增量，无空壳多层接口；历史遗留 `tools/_p0_1_*.py` 系上一次提交（07b3377）引入，非本次新增（见 P2-15） |

---

## 1. 冲突风险定级

## 🔴 **存在破坏性缺陷，需打回修复后合并**

**核心破坏点（唯一 P0）**：`agent/tool_agent.py:640` 工厂函数构造 `LLMPlanner` 时参数错位，导致**只要配置了 `LLM_API_KEY`（即所有真实生产部署），`default_tool_agent()` 必然抛 `TypeError`，全部问答 100% 崩溃**（RAG 分支 `web/service.py:232` 与 TEXT2SQL 分支 `web/service.py:249` 均调用该工厂，异常被 `run_query` 外层 `except` 吞成"系统繁忙，请稍后重试"——即 LLM 模式下整个产品静默不可用）。

> 实机复现证据（futurebi 环境运行输出）：
> ```
> REPRO TypeError: LLMPlanner.__init__() got multiple values for argument 'max_retries'
> default_tool_agent CRASH: LLMPlanner.__init__() got multiple values for argument 'max_retries'
> ```

除该 P0 外，整体架构方向正确、安全链路继承完整、无过度工程，修复清单（§5）落地后可达"低风险可合并"。

---

## 2. 冲突与退化明细表

| # | 模块/文件 | 原有机制/原有代码 | 新改动实现方式 | 冲突与退化性质 | 定级 |
|---|---|---|---|---|---|
| 1 | `agent/tool_agent.py:640`（vs `:233` 构造签名） | —（新增工厂） | `LLMPlanner(client, registry, max_retries=...)`，但签名是 `__init__(self, client, max_retries=2)`，`registry` 误占 `max_retries` 位 → TypeError | **功能破坏**：LLM 模式全链路崩溃；测试因直接以正确签名构造 LLMPlanner（`tests/test_tool_agent.py:152`）而未拦截 | 🔴 P0 |
| 2 | `exec/pool.py:88-103` vs `web/service.py:96-107` | web 层唯一默认只读池 `_default_db_pool()` + `_query_gate` 并发闸（P0-6） | 工具层另立第二单例池 `default_pool()`；`_query_core.py:57-61 _acquire_conn` 在 conn 未注入时使用 | **重复造轮子 + 资源治理旁路**：同一 DuckDB 文件双池；RAG 分支（`service.py:232`，不传 conn、不持闸）在 LLM 规划器把口径问题调度到数据工具时，查询绕过 `MAX_CONCURRENT_QUERIES` 并发闸（exec/guards 四护栏不受影响） | 🟠 P1 |
| 3 | `tools/builtins/_export_store.py:62-80` + `web/server.py:301-324` | 多租户 RLS 体系（数据按 principal 隔离） | 导出 meta 不记录属主；`_get_export` 仅 `_authenticate()` 不校验属主 | **产物获取越权（IDOR）**：任意已登录用户持 32-hex id 可下载他人导出文件（uuid4 不可枚举、实际可利用性低，但导出内容的租户隔离在下载端点失效；数据生成本身仍受 RLS 约束） | 🟠 P1 |
| 4 | `tools/builtins/query_metric_tool.py:25-27`、`trend_analysis_tool.py:49`、`export_report_tool.py:45`、`explain_glossary_tool.py:24` | `tools/base.py:83-91` 自述：principal 属 ToolContext，**不属于 LLM 可声明的入参** | `principal` 同时暴露在 4 个工具的 args_schema 中；执行时 `ctx.principal or validated_args.principal`（`query_metric_tool.py:46`） | **纵深防御违例**：ctx.principal 为 None 时 LLM 声明的 principal 生效。现网 web 链路服务端强制绑定 principal（`web/server.py:271-290`）故当前不可利用，但契约自相矛盾且留提权后门 | 🟠 P1 |
| 5 | `agent/tool_agent.py:262-269, 485-488` vs `web/service.py:214-227` | P0-5 澄清槽位回填：CLARIFY 时写 `slot_store`，用户短语回答合并回原问题 | LLMPlanner 的 `{"clarify": ...}` 输出经 `ToolAgent.run` 早退；TEXT2SQL 分支既不写 slot_store 也不透出 `result["clarifications"]` | **机制断裂**：LLM 规划路径的反问不进槽位回填闭环，用户短语回答被当作全新问题（确定性模式无此问题） | 🟠 P1 |
| 6 | `tools/builtins/trend_analysis_tool.py:57-59` | —（新增参数） | `window_days` 声明于 args_schema 但 `execute()` 全程未引用 | **死参数/契约欺骗**：LLM 传入 `window_days=90` 被静默忽略 | 🟡 P2 |
| 7 | `agent/heuristic.py:77-88` vs `tools/builtins/trend_analysis_tool.py:266-288` | 启发式内置默认窗口（yoy=12月 / mom=6月） | 趋势工具 `_default_window` 重新硬编码同一组默认值 | **重复造轮子（轻度）**：两处口径当前一致，未来漂移风险；应提取共享常量 | 🟡 P2 |
| 8 | `agent/tool_agent.py:318` | Planner 持有 `self.registry`（构造注入） | `LLMPlanner.correct` 改用全局 `default_registry()` 查工具 | **一致性缺陷**：定制注册中心（测试隔离/插件场景）下自愈修复查错注册表 | 🟡 P2 |
| 9 | `agent/tool_agent.py:138` | `AgentResult.intent` 字段 | 从未赋值，恒为默认 `"text2sql"`；RAG 调度后 `to_dict()`/审计意图失真（`tests/test_tool_agent.py:329` 反而断言了该错误行为） | **可观测性失真** | 🟡 P2 |
| 10 | `agent/tool_agent.py:624` + `config/settings.py:60-61` | — | `_agent_lock = None` 死变量（工厂非线程安全，良性）；`MAX_AGENT_STEPS` 环境变量未做 3~5 钳制，越界配置 → 工厂 `ValueError` → 服务整体"系统繁忙" | **配置健壮性** | 🟡 P2 |
| 11 | `agent/tool_agent.py:547` | `run()` 文档承诺"不抛异常"（`:471`） | `_execute_once` 内 `registry.get_tool(call.tool)` 的 `UnknownToolError` 未被调度循环捕获 | **防御性缺口**（当前规划器路径不可达，但契约承诺被打破） | 🟡 P2 |
| 12 | `tools/builtins/export_report_tool.py:75-86` | — | 前置查询失败（prior.success=False）时落到 `elif query` 以同一 query **再执行一次**受控查询 | **重复执行/资源浪费**（几乎必然二次失败） | 🟡 P2 |
| 13 | `tools/builtins/export_report_tool.py:169-194` + `web/server.py:301-324` | — | CSV 导出未做公式注入转义（`=HYPERLINK(...)` 等）；下载响应缺 `X-Content-Type-Options: nosniff`；导出目录无 TTL/清理（`logs/exports` 无限增长，服务级测试每次运行遗留 9 组文件） | **安全加固项** | 🟡 P2 |
| 14 | `tools/_p0_1_e2e.py` / `_p0_1_pen.py` / `_p0_1_verify.py`、`AGENTS.md` | tools/ 原定位"本地工具（mock LLM 服务端等）" | 本次将 tools/ 升格为生产包（`pyproject.toml` +`tools*`），但历史一次性验证脚本仍留在包内；AGENTS.md 目录说明未更新 | **目录卫生/文档漂移**（`_p0_1_*` 非本次引入，系 07b3377 遗留） | 🟡 P2 |

**明确"未退化"的项（易误判，专门核验过）**：
- `_json_safe` 从 `web/service.py` 迁至 `_query_core.py:172-181`，原实现删除——是**迁移**而非双实现 ✅
- SQL 自愈循环从 `web/service.py` 迁至 `_query_core._run_guarded`，逐行比对语义等价（含 `record_self_heal_failure` 打点与 `raise exc from None` 透传语义），且 `web/service.py:264-265` 以 `executor=execute_sql` / `rewriter=rewrite_dsl` 模块级绑定传入——原有 monkeypatch 测试桩全部继续生效（3 个自愈测试绿色）✅
- `agent/heuristic.py` 的改动（默认窗口兜底 / "销售总额" 别名 / 本月 / 过去N月 / 半年 / 过去N周 / 明细清单 / 未履约过滤）全部产出受控 DSL 字段，`Granularity.WEEK` 本就在 `dsl_schema.py:33` 枚举内，`RelativeUnit.WEEK` 由 `sql_compiler.py:138-139` 支持——**无契约越界** ✅
- RAG 分支改为经 `explain_glossary` 工具执行（`service.py:228-244`），仍是纯词典检索不触 SQL ✅

---

## 3. 安全与架构红线实锤

**未发现"跳过 `apply_policy`"或"跳过 `exec/guards.py`"的代码路径**——受控查询链路（`_query_core.run_guarded_query`）对三个数据工具是无差别强制的，自愈重写后的 DSL 也重新过守卫（`_query_core.py:151`）。但以下三处属于**必须在合并前收敛的红线级风险**：

### 🔴 实锤 1（P0·阻断）：LLM 工厂构造崩溃
- **位置**：`agent/tool_agent.py:640`
  ```python
  planner = LLMPlanner(client, registry, max_retries=settings.LLM_MAX_RETRIES)
  ```
  `LLMPlanner.__init__`（`:233`）只有 `(client, max_retries)` 两个参数，`registry` 误占第二参 → `TypeError: got multiple values for argument 'max_retries'`。
- **影响面**：`settings.LLM_API_KEY` 非空 ⇒ RAG（`service.py:232`）与 TEXT2SQL（`service.py:249`）两分支全部崩溃，用户侧表现为统一的"系统繁忙，请稍后重试"。**测试全绿是假象**：259 个测试全部运行在离线确定性模式，且 LLMPlanner 单测直接用正确签名构造，工厂 LLM 分支零覆盖。

### 🟠 实锤 2（P1）：并发闸（P0-6）条件性旁路 + 双连接池
- **位置**：`exec/pool.py:88-103`（新立第二池单例）vs `web/service.py:96-107`（原池单例）；`web/service.py:94`（`_query_gate` 仅 `:259` TEXT2SQL 分支持有）；`tools/builtins/_query_core.py:57-61`（conn 为 None 时自动取第二池）。
- **触发路径**：RAG 分支（`service.py:232`）调用 `agent.run` 时不传 conn、不持闸；LLM 规划器若将口径类问题调度到 `query_metric`/`trend_analysis`/`export_report`（LLM 自由决策，与 router 判定可能不一致），查询将走第二连接池并**绕过 `MAX_CONCURRENT_QUERIES` 并发闸**。注：`exec/guards` 的只读/超时/扫描/行数四护栏依然生效，被旁路的仅是 web 层资源治理。

### 🟠 实锤 3（P1）：导出下载端点无属主绑定（跨租户 IDOR）
- **位置**：`tools/builtins/_export_store.py:62-80`（`save` 的 meta 仅 `{"filename","format","row_count","truncated"}`，**无 principal**）；`web/server.py:301-324`（`_get_export` 仅做 `_authenticate()`，任何已登录主体持 id 即可下载）。
- **定性**：导出**数据生成**侧 RLS 完整（`_query_core.py:121/123/151` 强制 `apply_policy`，不存在"导出工具不受租户行级权限约束"的数据越权）；但导出**产物获取**侧无属主校验，租户 A 生成的文件可被租户 B 下载（uuid4 十六进制 128-bit 不可枚举，现实可利用性低，但多租户模型在最后一公里断裂）。同时下载 URL 会进入审计 `steps`（`tool_agent.py:609` 摘要含 `download_url`），扩大 id 暴露面。

---

## 4. 测试与覆盖率核验

### 4.1 原有单测与静态检查（实测结果）
| 检查项 | 结果 |
|---|---|
| `python -m pytest -q`（futurebi 环境） | ✅ **259 passed**（含全部原有单测，无 skip/xfail） |
| `black --check .` | ✅ 92 files unchanged |
| `ruff check .` | ✅ All checks passed |

原有自愈/降级/澄清关键用例（`tests/test_web.py:73-150`：`test_run_query_self_heal_rewrites`、`test_run_query_scan_cap_self_heals`、`test_run_query_exec_error_surfaces_without_llm`、`test_run_query_degrades_to_heuristic_when_llm_fails`）**全部保持绿色且实际穿过新工具链路**（monkeypatch `svc.execute_sql` / `svc.rewrite_dsl` 经 `service.py:264-265` 模块级绑定注入生效）。

### 4.2 新增测试清单（35 个，全部通过）

**`tests/test_tools.py`（13 个）——工具协议与注册中心**：
| 用例 | 验证意图 |
|---|---|
| `test_register_and_get` / `test_unregister` | 注册/反查/注销基础生命周期 |
| `test_register_via_decorator` | `@register_tool(reg)` 装饰器形态 |
| `test_duplicate_registration_rejected` | 同名重复注册抛 `DuplicateToolError`（禁静默覆盖） |
| `test_unknown_tool_error` | 未注册工具名抛 `UnknownToolError`（白名单机制） |
| `test_default_registry_is_singleton` | 默认注册中心单例 + 4 个内置工具自注册 |
| `test_tool_definitions_openai_schema` / `test_to_definition_matches_registry` | Function Calling JSON Schema 适配正确性 |
| `test_illegal_extra_arg_rejected` | **越权参数（drop_table）在入口被拦**，`run()` 包装为失败结果 |
| `test_wrong_type_arg_rejected` / `test_missing_required_arg_rejected` | 类型/必填校验 |
| `test_run_valid_args_and_duration` | 正常执行 + 耗时统计 |
| `test_tool_exception_wrapped` | execute 抛错被结构化封装，不向外裸抛 |

**`tests/test_tool_agent.py`（18 个）——调度内核**：
| 用例 | 验证意图 |
|---|---|
| `test_accept_last_month_sales_total` | 验收场景1：单值指标 → query_metric + number 图表 |
| `test_accept_half_year_province_mom_trend` | 验收场景2：环比趋势 → trend_analysis（真实走库出数） |
| `test_accept_unfulfilled_orders_export` | 验收场景3：导出 → query_metric + export_report 组合、复用 prior 结果、产出下载链接 |
| `test_accept_gmv_glossary_no_sql` | 验收场景4：口径问题 → 仅 explain_glossary，**绝不触达 SQL 引擎** |
| `test_chitchat_no_tool_call` | 闲聊零工具调用 |
| `test_export_truncation_and_desensitization` | 导出截断 + 敏感列脱敏 + 落盘文件不含明文敏感值 |
| `test_llm_planner_picks_tool_and_executes` / `test_llm_planner_direct_answer` | LLM 规划：工具调用 / 直接回答（FakeLLM 零网络） |
| `test_llm_planner_retries_on_invalid_tool` / `test_llm_planner_exhausts_retries` / `test_llm_planner_illegal_args_rejected` | 非法工具名/参数被拦并反馈重试、重试耗尽报错 |
| `test_llm_synthesizer_uses_llm_answer` | LLM 总结器采纳洞察文本 |
| `test_max_steps_cap_enforced` / `test_max_steps_out_of_range_rejected` | Max Steps 3~5 上限（杜绝无限循环） |
| `test_self_correction_retries_failed_tool` | **工具失败 → Self-Correction 修复重试成功**（自愈闭环存活的直接证据） |
| `test_no_retry_on_permanent_error` | SecurityError 等永久错误不触发无意义自愈 |
| `test_steps_are_json_serializable` / `test_agent_result_to_dict` | 调度轨迹可序列化（审计链路输入） |

**`tests/test_web.py` 新增 4 个——service 层贯通**：`test_run_query_text2sql_returns_tool_steps`、`test_run_query_our_metrics_trend_uses_trend_tool`、`test_run_query_export_produces_download_url`、`test_run_query_glossary_returns_steps_and_documents`（覆盖 TEXT2SQL/趋势/导出/RAG 四路 steps 透传）。

### 4.3 覆盖缺口（必须补）
1. **`default_tool_agent()` 的 LLM 分支零覆盖** —— P0 逃逸的直接根因（现有 LLMPlanner 测试全部绕过工厂直接构造）。
2. **`/api/export/<id>` 无 HTTP 级测试**（401 未登录 / 404 非法 id / 属主校验 / Content-Disposition）。
3. **LLM clarify → 槽位回填断裂**（明细表 #5）无测试。
4. `eval/eval_runner.py:27` 仍锚定 `run_pipeline` 单路径 —— 多工具链路不进 golden 评测（可接受的阶段性事实，但应知情登记）。
5. 服务级导出测试写真实 `logs/exports`（未 monkeypatch 到 tmp_path），每次运行遗留 9 组文件（工具级测试已正确用 tmp_path）。

---

## 5. 整改清单（合并前必须完成）

> 按收敛优先级排列；#1 为阻断项，#2/#3 为红线级收敛项。P2 项可在本次或紧随其后的提交顺带处理。

### 修复指令 1【P0·阻断】修正 LLMPlanner 工厂构造 + 补工厂级测试
- `agent/tool_agent.py:640` 改为：
  ```python
  planner = LLMPlanner(client, max_retries=settings.LLM_MAX_RETRIES)
  ```
  （registry 由 `plan(query, principal, registry)` 入参传入，构造器本就不需要；顺带把 `LLMPlanner.correct`（`:318`）的 `default_registry()` 改为复用 `plan()` 收到的注册中心或在构造器持有）。
- 新增工厂级单测：monkeypatch `settings.LLM_API_KEY` + `set_default_tool_agent(None)` 后断言 `default_tool_agent()` 可构造、`planner` 为 `LLMPlanner` 实例；同时对 `MAX_AGENT_STEPS` 做加载期钳制（`settings.py:60-61` 取 `min(max(v,3),5)`），杜绝环境变量越界导致工厂 `ValueError`。

### 修复指令 2【P1】连接池与并发闸统一收口
- 删除 `web/service.py:96-107 _default_db_pool`，TEXT2SQL 分支改用 `exec.pool.default_pool()`，保证进程内**同一 DuckDB 文件只有一个池**；
- RAG 分支（`service.py:232`）与任何可能触达 SQL 的 agent 调用同样在 `_query_gate` 内执行并注入 `conn`（或把"取连接 + 并发闸"下沉为 `exec.pool` 的统一入口供 `_query_core._acquire_conn` 复用），确保**任何工具层查询都无法绕过 P0-6 资源治理**。

### 修复指令 3【P1】导出属主绑定 + principal 出清 LLM 入参
- `ExportStore.save`（`_export_store.py:62-80`）的 meta 记录 `principal`（由 `export_report_tool.execute` 从 `ctx.principal` 注入）；`web/server.py:301 _get_export` 校验 `item.meta.get("principal") == ctx.principal`（admin 角色可放行）；
- 把 `principal` 从 4 个工具的 `args_schema` 中删除（`query_metric_tool.py:25-27`、`trend_analysis_tool.py:49`、`export_report_tool.py:45`、`explain_glossary_tool.py:24`），执行处统一只取 `ctx.principal`——与 `tools/base.py:83-91` 的自述契约对齐，消除 LLM 声明主体的提权残余面。

### 顺带收敛（P2，同一提交内低风险处理）
- 删除或实现 `window_days`（`trend_analysis_tool.py:57-59`）；
- 默认窗口常量提取为共享函数（消除 `heuristic.py:77-88` 与 `trend_analysis_tool.py:266-288` 双实现）；
- `AgentResult.intent` 在 `ToolAgent.run` 内按规划结果赋值，并修正 `test_agent_result_to_dict` 的错误断言；
- `ToolAgent.run` 调度循环体包 try/except（兑现 `:471` "不抛异常"承诺）；删除 `_agent_lock` 死变量或实现加锁；
- 导出：CSV 公式前缀（`=`/`+`/`-`/`@`）转义、下载响应加 `X-Content-Type-Options: nosniff`、`ExportStore` 加保留期清理；
- `export_report_tool` 在 `prior` 存在但失败时不再以同一 query 重复查询；
- 服务级导出测试 monkeypatch 导出目录到 `tmp_path`；`tools/_p0_1_*.py` 迁出生产包（如 `scripts/` 或删除）；更新 `AGENTS.md` 的 tools/ 目录说明与本次新增约定。

---

## 附：评审结论一句话

> **架构方向完全正确（受控 DSL 零旁路、RLS/只读/熔断全链继承、自愈闭环迁移保留且增强、零依赖膨胀），但 `default_tool_agent()` 的 LLM 分支存在一行级 P0 构造错误，使所有真实 LLM 部署 100% 不可用——打回，按 §5 指令 1-3 修复后即可达到合并标准。**
