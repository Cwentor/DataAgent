# 评审报告｜深度审计：ChatBI 领域专业性与问数能力穿透（2026-09）

> 评审日期：2026-09-05 · 评审方式：全量代码精读 + 运行验证（pytest 305 passed）+ 缺陷实证复现 · 评审范围：semantic / compiler / agent / exec / security / auth / audit / present / tools / web / eval / mock 全模块约 5,000 行 Python + 前端

---

## 1. 总体定性评估

- **系统类型判定**：**合格的专业级问数 ChatBI**（其中安全防护栈已达到工业级设计水准；因可枚举的时间代数缺口、单机态存储与个别死代码缺陷，尚不足以判定为"工业级完备"）。
- **ChatBI 契合度评分**：**80 / 100**。
- **判定核心依据**：
  1. 该系统的核心数据流完全围绕数据分析域实体展开——`Metric`（聚合/比率/窗口三态判别联合，`semantic/dsl_schema.py:123-177`）、`Dimension`、`TimeFilter`（trailing/calendar 双模态相对时间，`semantic/dsl_schema.py:57-108`）、`FilterOperator` 受限枚举（`semantic/dsl_schema.py:190-198`）、`TopN`/`fill_gaps`（`semantic/dsl_schema.py:239-271`），LLM 被锁死在 `extra="forbid"` 的 Pydantic 契约内，编译器为不接受任何自由 SQL 片段的确定性纯函数（`compiler/sql_compiler.py:1-16,627-702`）——**这不是"通用 Agent 挂数据库"，而是规格驱动的专用问数系统**。
  2. 生产级 ChatBI 的关键卡点大多被真实命中且实现有深度：编译前 RLS 注入（`security/guard.py:97-102`）+ 生成前字段作用域收敛（`security/scope.py:42-51`）双层权限；四层只读防线含 sqlglot AST 结构化校验与 DuckDB 恶意表函数家族黑名单（`exec/guards.py:80-258`）；超时看门狗/扫描行预检熔断/LIMIT 硬上限/并发信号量/只读连接池（`exec/guards.py:303-407`、`web/service.py:171-172`、`exec/pool.py:24-51`）。
  3. 扣分项集中在：时间代数缺季度/MTD 语义、comparison 与时间维度互斥（`compiler/sql_compiler.py:378-383`）、`LLMPlanner.correct` 自愈修复为死代码（`agent/tool_agent.py:318`，已实证）、多轮继承的维度替换覆盖面窄（`agent/memory.py:284-291,330-357`）、会话/限流/审计均为单机进程内实现、图表推荐未输出 ECharts/Vega-Lite 级配置且 pivot 无前端渲染。

---

## 2. 问数领域特性深度对照表

| 领域核心维度 | 是否具备专用实现 | 对应实现代码位置 | 评价（玩具级/基础可用/工业级） |
|---|---|---|---|
| **时序计算与时间代数** | ✅ 具备 | 相对时间双模态：`semantic/dsl_schema.py:57-108`（trailing/calendar）；确定性窗口解析 `_resolve_window`/`_sub_months`/`_sub_years`：`compiler/sql_compiler.py:72-148`；同比/环比整体窗口平移 `_shift_window` + cur/prev 双 CTE 配对：`compiler/sql_compiler.py:88-107,360-459`；移动平均/累计 `_window_expr`：`compiler/sql_compiler.py:274-285`；日期补零 spine `generate_series` + `COALESCE(...,0)`：`compiler/sql_compiler.py:555-621`；时间解析器不依赖 LLM 猜日期：`agent/heuristic.py:371-455`（绝对年月/上个月 calendar/本月/过去N天周月，全部锚定 `reference_date`） | **基础可用偏工业级**：核心代数确定性且互斥校验显式（`compiler/sql_compiler.py:632-647`）；但缺 quarter 粒度（"上季度"不可表达，`RelativeUnit` 无 quarter）、无 MTD/QTD"至今"语义、comparison 与时间维度组合显式抛 `CompileError`（按位配对未实现，`compiler/sql_compiler.py:378-383`）、week 粒度不支持补零（`compiler/sql_compiler.py:577`）、时间过滤硬编码锚定 `f.order_time`（`compiler/sql_compiler.py:227-232`，第二事实表 `refund_time` 无法作为时间主轴） |
| **受控语义/指标字典** | ✅ 具备 | 口径词典 `GLOSSARY`（GMV/订单数/活跃用户/ARPU/退款率/退款金额/客单价，含公式+依赖字段+别名）：`agent/glossary.py:33-98`；Prompt 口径约定注入：`agent/prompts.py:38-91`；复合指标显式建模 `RatioMetric`/`WindowMetric`：`semantic/dsl_schema.py:132-177`；字段白名单 `COLUMNS`：`semantic/catalog.py:43-65`；数据驱动目录重建：`semantic/catalog_loader.py:200-255`；未定义口径澄清反问（复购率/留存率/转化率/DAU/MAU 显式登记 + `高活跃|沉默|流失...用户` 正则泛化）：`agent/clarify.py:28-44,99-137`；澄清槽位回填（短语作答合并回原问题）：`agent/slotfill.py:94-111` | **工业级**（在 mock 域内）："宁可拒绝不臆测"贯穿始终——启发式路径对未定义指标抛错（`agent/heuristic.py:60-65`）、LLM Prompt 尾部强制 `{"error": "无法可靠解析"}` 逃生口（`agent/prompts.py:94`）、词典按主体过滤（`agent/glossary.py:109-118`）。短板：词典 7 条为代码硬编码，未与 `catalog_loader` 的数据驱动机制打通 |
| **行级权限与安全穿透** | ✅ 具备 | 表级/列级/行级三段式守卫 `apply_policy`：`security/guard.py:72-104`；RLS 编译前注入 `dsl.filters`（四条编译路径全部消费 filters：`compiler/sql_compiler.py:403,518-520,588-589,677-681`）；参数化 RLS 模板 `principal.provinces`：`security/guard.py:22-41`、`security/policy.py:36-60`；守卫前移（生成前字段白名单注入 Prompt）：`security/scope.py:42-62`、`agent/prompts.py:97-118`；只读硬隔离四层防线（语句形态→关键字黑名单→函数家族黑名单→sqlglot AST 结构校验）：`exec/guards.py:230-258`，拦截 `read_csv/read_parquet/glob/query/*_scan` 等表函数（`exec/guards.py:102-135`）与 `FROM '<path>'`（`exec/guards.py:138`）；只读连接 `duckdb.connect(read_only=True)`：`exec/pool.py:47`；principal 服务端强制映射（客户端声明一律忽略）：`auth/gateway.py:70-96`、`web/server.py:277-284`；服务端会话/认证：PBKDF2+恒定时间比对 `auth/identity.py:33-70`，JWT+Session 双凭证 `auth/gateway.py:126-147` | **工业级**：权限纵深达四层（Prompt 白名单→DSL 生成前 scope 校验→编译前 apply_policy→AST/驱动只读），自愈重写后的 DSL 也强制重新过守卫（`tools/builtins/_query_core.py:151`）。短板：RLS 值列表形态单一（仅 `field IN (...)`，`security/policy_loader.py:8-10` 自认）；会话存储单机（`config/settings.py:122-124`） |
| **维度切片/追问继承** | ✅ 具备 | 结构化 DSL 状态记忆（非文本拼接）：`agent/memory.py:87-101`；跨用户会话强隔离：`agent/memory.py:134-147`；继承前剥离 RLS 防重复注入：`agent/memory.py:311-327`；语义增量提取（省份/大区/品类/时间/趋势/对比）：`agent/memory.py:254-275`；三模式消解（省略指代继承/下钻展开/话题切换重置）：`agent/memory.py:378-430`；路由层上下文追问判定：`agent/router/intent_router.py:515-522`；继承 DSL 仍走全护栏：`tools/builtins/trend_analysis_tool.py:78-92` + `web/service.py:396-422` | **基础可用**："那华南呢"（切片替换 province）、"按品类展开"（下钻加维）、趋势追问（trend 工具规范化）三类场景闭环且有 golden 级单测；但维度追加仅支持品类/品牌（`agent/memory.py:284-291`），"按省份展开"不被识别；指标切换、任意维度替换（如品类→品牌置换）无通用机制；继承合并全靠中文关键词规则 |
| **结果图表自适应推荐** | ✅ 具备 | 数据形状→图表类型确定性规则（无维度单指标→number、时间维度→line、单维单指标→pie/bar、多维多指标→pivot、其余 table）：`present/viz.py:21-51`；`ChartSpec` 复合输出契约：`present/viz.py:70-103`；前端开箱消费（number 卡片/bar/pie/line 手写 SVG 渲染）：`web/static/app.js:140-229` | **基础可用**：推荐规则正确且确定性可测，但输出契约仅 `{chart,x,y}` 三字段（`present/viz.py:63-67`），非 ECharts/Vega-Lite 完整 option；pie 阈值 `n_rows<=8` 过于武断（`present/viz.py:42-44`）；前端对 pivot 仅回退提示"以表格形式展示"（`web/static/app.js:228`），多维透视无真实渲染 |
| **SQL/口径可信溯源** | ✅ 具备 | DSL→中文业务话术确定性解释（指标公式/过滤/时间/TopN/补零全覆盖）：`present/explain.py:72-112`；工具返回同时携带 `sql/dsl/explanation/scan_rows/rewrites`：`tools/builtins/_query_core.py:159-169`、`tools/builtins/trend_analysis_tool.py:94-119`；前端直接展示 DSL JSON + 实际 SQL + 解释文本 + 工具调度轨迹（步骤/耗时/成败）：`web/static/app.js:272-297,340-344`；口径文档 RAG 检索（TF-IDF bigram 余弦，零外部依赖）：`agent/rag.py:63-89`；全链路审计落盘（JSONL+DuckDB 双 sink，request_id/session/principal/DSL/SQL/行数/耗时/路由）：`web/service.py:511-537`、`audit/store.py:21-55` | **工业级**：业务人员可展开看到真实编译 SQL、指标口径与每步工具轨迹，审计字段完整且跨进程文件锁保护（`audit/store.py:58-80`） |

---

## 3. "通用脱靶 / 领域偏差"发现清单

### 3.1 属于"通用 Agent 概念但偏离问数场景"的冗余/低效设计

本项目**没有**网页爬取、任意文件读写、无约束 ReAct 自由联想这类典型通用脱靶——工具注册中心仅四个问数域工具（`tools/builtins/__init__.py:3-7`：query_metric/trend_analysis/export_report/explain_glossary），工具入参全部走 Pydantic `extra="forbid"` 校验（`tools/base.py:9-11`），调度上限 3~5 步（`agent/tool_agent.py:456-457`），CHITCHAT 分支显式拒绝并绝不触碰数仓引擎（`agent/router/intent_router.py:21-26`）。但仍有三处轻度冗余：

1. **双路由体系并存**：旧三分类 `agent/intent.py:16-78` + `agent/router/legacy.py` 与新五分类 `agent/router/intent_router.py` 同时存活，`web/service.py` 走新路由而 `agent/tool_agent.py:197-201`（DeterministicPlanner）仍消费旧 `classify_intent`，两套关键词表（`_CHITCHAT_PATTERNS` 等）语义重叠，长期将产生判定漂移。
2. **`LLMPlanner.correct` 是死代码（已实证）**：`agent/tool_agent.py:318` 引用不存在的 `self.registry`（`LLMPlanner.__init__` 未定义该属性），`AttributeError` 被 `except Exception: return None`（`agent/tool_agent.py:321-322`）静默吞掉，导致"LLM 自愈修复"路径恒返回 `None`——工具级 Self-Correction（`agent/tool_agent.py:523-544`）在 LLM 规划器模式下实际永不生效。已用最小脚本复现：`correct()` 返回 `None`，`hasattr(planner, 'registry') == False`。
3. **`agent/llm.py`/`tools/mock_llm_server.py` 的 OpenAI 兼容层**：为通用协议付出的抽象在当前仅消费 chat 一种能力，属可接受的轻量冗余。

### 3.2 属于"严肃问数场景不可或缺，但当前被简陋绕过"的核心缺口

1. **时间代数缺口（最痛）**：
   - 无 quarter 粒度——"上季度/QTD"在 DSL 层不可表达（`semantic/dsl_schema.py:50-54` 的 `RelativeUnit` 仅 day/week/month/year；calendar 模式仅支持 month/year，`compiler/sql_compiler.py:121-133`）；
   - 无 MTD/"本月至今"语义——`agent/heuristic.py:402-415` 把"这个月/本月"解析为整月 `[当月1日, 次月1日)`，"至今"被静默扩展为全月；
   - comparison 与时间维度互斥——"6月每日 GMV 同比"这类最常见问法直接 `CompileError`（`compiler/sql_compiler.py:378-383`），按位配对（当前日 vs 基准周期对应日）未实现；
   - 时间主轴硬编码 `f.order_time`（`compiler/sql_compiler.py:227-232`），退款时间序列分析（"近30天每日退款金额"）无法通过 time_filter 表达。
2. **`RatioMetric` 无除零防护**：编译为 `(num)/(den)` 裸除法（`compiler/sql_compiler.py:267-270`），分母为 0 时输出 inf/NaN 而非 NULL——对比指标都做了 `NULLIF(prev, 0)`（`compiler/sql_compiler.py:429`），口径防御不一致。
3. **多轮继承覆盖面窄**：`_expand_dimension` 仅识别品类/品牌（`agent/memory.py:284-291`）；`_apply_deltas` 仅处理省份/大区/品类/时间四类增量（`agent/memory.py:330-357`），无通用"维度替换/指标切换"算子；且全部依赖中文关键词硬编码，未从语义目录派生。
4. **启发式解析器词汇表与数仓硬绑定**：`PROVINCES/CATEGORIES` 为代码常量（`agent/heuristic.py:30-41`），`catalog_loader` 已实现数据驱动目录（`semantic/catalog_loader.py:200-255`）但启发式层不消费——新增字段后 LLM 路径可用、离线路径失明。**（✅ 2026-09-06 已实施数据驱动化：`semantic/catalog.py` 新增 `DIMENSION_MEMBERS` 词汇表，`catalog_loader` 从数仓 dim 表 distinct 值随 `refresh_catalog` 重建；`heuristic`/`memory`/`intent_router` 三处消费点全部改为动态读取，大区展开与数仓省份取交集——commit `f5512c4`、`46f5700`）**
5. **单机态存储三件套**：会话记忆（`agent/memory.py:107-131` 进程内 OrderedDict）、登录限流（`auth/ratelimit.py` 进程内）、澄清槽位（`agent/slotfill.py:46-77`）均无跨进程共享，水平扩展即失效；审计虽有跨进程文件锁（`audit/store.py:58-80`）但多 worker 下 DuckDB 单写文件仍是瓶颈。
6. **EXPLAIN ANALYZE 预检成本翻倍**：扫描行熔断以"先真实执行一遍"为代价（`exec/guards.py:12-17` 自述），大查询延迟×2，且预检与正式执行之间无计划缓存。
7. **评测维度缺口**：golden 仅 19 例（`eval/golden_dataset.json`），无多轮追问用例、无 RLS 越权用例、无澄清反问用例——这些恰是本项目最具特色的防线，却不在回归保护网内（渗透测试仅散落在 `scripts/_p0_1_pen.py`）。

---

## 4. 结论与整改路线图（Top 3）

**总体结论**：FutureBI 是一个**领域纯度极高、骨架正确、防线真实**的专业级问数 ChatBI——"LLM 只产 DSL、编译器确定性出 SQL、权限与资源护栏纵向贯穿、结果可解释可审计"四大原则不是口号而是代码事实（305 项测试全绿佐证）。它的问题不是方向性脱靶，而是若干"最后一公里"缺口。

### 整改指令 1：补全时间代数（最高优先级）

1. `semantic/dsl_schema.py`：`Granularity` 增加 `QUARTER`；`RelativeTime` 增加 `mode="to_date"`（MTD/QTD/YTD 语义，窗口 `[周期起点, reference_date)`）；`RelativeUnit` 增加 `QUARTER`。
2. `compiler/sql_compiler.py`：`_resolve_window` 支持 quarter 的 calendar/to_date 解析（复用 `_sub_months(n*3)`）；实现 comparison 与时间维度的按位配对（cur/prev CTE 以 `date_add(时间列, -1 year/-1 month)` 对齐后 JOIN，替代当前的 `CompileError` 逃生）；`_time_window_sql` 的时间列改为从 `TimeFilter` 显式声明的锚定时间字段解析（新增 `time_field` 属性，默认 `order_time`，登记于 `TIME_FIELDS` 白名单），解除退款时序分析的硬编码封锁。
3. `RatioMetric` 编译统一包裹 `NULLIF`：`(num) / NULLIF(den, 0)`，与 comparison 列（`compiler/sql_compiler.py:429`）对齐。
4. `agent/heuristic.py:371-455` 与 `agent/prompts.py:38-91` 同步登记"上季度/本季度/季度环比/本月至今"的解析规则与 Prompt 约定，golden 追加对应用例。

### 整改指令 2：修复多轮继承与自愈的确定性断点

1. **修复 `LLMPlanner.correct` 死代码**：`agent/tool_agent.py:318` 的 `self.registry` 改为构造注入（`__init__(self, client, registry, max_retries=2)`），并删除 `agent/tool_agent.py:321-322` 的裸 `except Exception`——至少收窄为具体异常类型并打日志，杜绝"静默吞 AttributeError"再次发生；为 `correct()` 补一条回归单测（当前 `tests/test_tool_agent.py` 无该路径覆盖）。
2. **维度替换通用化**：`agent/memory.py:284-291` 的 `_expand_dimension` 改为从 `semantic.catalog.COLUMNS` 派生的"维度字段×中文标签"映射（与 `present/labels.py` 的 `field_label` 同源），使"按省份/按品牌/按支付状态展开"全部可继承；`_apply_deltas` 增加通用"维度置换"增量类型（新维度出现且旧维度未提 → 替换而非追加）。
3. **合并双路由**：`agent/tool_agent.py:197-201` 的 DeterministicPlanner 改为消费 `agent/router/intent_router.py` 的五分类判决，废弃 `agent/intent.py` 旧三分类，消除两套关键词表的漂移风险。
4. **golden 增补多轮用例**：为 eval 增加"多轮对话序列"用例类型（断言第二轮继承后的 DSL 与 SQL），把会话继承纳入回归保护网。

### 整改指令 3：交付与横向扩展的最后一公里

1. **图表推荐升级为完整渲染契约**：`present/viz.py:54-103` 的 `viz_config`/`ChartSpec` 扩展为输出 ECharts option 级结构（含 series/axis/legend/tooltip），`recommend_viz` 引入数值类型/基数/时序间距信号（当前 `pie` 仅凭 `n_rows<=8` 判定，`present/viz.py:42-44`）；前端 `web/static/app.js:228` 补 pivot 真实渲染（或明确降级为分组表格 + 提示）。
2. **会话与限流存储外置化**：落实 `agent/memory.py:109-112` 预留的 `_persist/_load` 扩展点，接 SQLite/Redis（`config/settings.py:122-124` 已有 `AUTH_SESSION_DB` 先例），使 `SessionStore`/`ClarifySlotStore`/登录限流三者在多 worker 下一致。
3. **扫描预检降本**：对同一 DSL 编译产物缓存 EXPLAIN ANALYZE 结果（DSL 哈希 → scan_rows），自愈重试同 SQL 不再重复预检；或改用 DuckDB `query_arrow` 的 profiling 信息在正式执行后回填校验（对超限结果熔断丢弃），将预检限定在首次执行。

---

### 附：审查验证记录

- 测试套件：`python -m pytest -q` → **305 passed**（2026-09-05，Windows/conda futurebi 环境）。
- 缺陷实证：`LLMPlanner.correct` 最小复现脚本（已清理）确认 `hasattr(planner, 'registry') == False`、`correct()` 恒返回 `None`。
- 全部结论均基于逐文件精读（非抽样），关键模块（semantic/compiler/exec/security/web 主链路）为全文阅读，行号以当前工作区为准。
