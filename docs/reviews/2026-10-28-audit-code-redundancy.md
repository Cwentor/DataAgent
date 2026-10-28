# 评审报告｜代码冗余深度审计：数据结构化 / 重复调用 / 死代码 / 过渡设计（2026-10）

> - **审计日期**：2026-10-28
> - **审计范围**：全仓 171 个生产 `.py` + 104 个测试 `.py`（53,478 行，其中测试 25,346 行）
> - **触发问题**：是否存在「该用数据结构却写冗余代码」「反复的方法调用」「永远用不到的代码与测试」「冗余的过渡设计与测试用例」
> - **结论**：四类问题全部存在，但**严重度差异极大**。真问题集中在 1 个已实测的线上缺陷 + 6 组结构性重复；测试侧大量「重复」按项目既定口径属有意冗余，不应清理。

---

## 0. 审计方法与判定口径

### 0.1 方法

| 通道 | 手段 | 产出 |
|---|---|---|
| 静态扫描 | 自写 AST 扫描器（`logs/_audit_scan.py`，一次性脚本，已删）：收集 3006 个函数/方法定义、全部调用点、长 `if/elif` 链、测试无断言用例 | 67 个死代码候选 / 219 个单次调用私有方法 / 11 条长分支链 / 29 个无断言用例 |
| 模块深读 | 4 个并行审计通道按包切分（`exec`+`compiler`+`semantic` / `core` / `agent`+`web`+`tools` / `tests`）逐文件通读 | 逐条带行号的候选清单 |
| 实测复核 | 对高严重度结论跑代码验证（本次复核 7 条） | 见 §2、§9 |

### 0.2 判定口径（沿用既有评审，避免重复报已裁定项）

本仓库在 [2026-10-05-test-suite-dedup-cleanup.md](2026-10-05-test-suite-dedup-cleanup.md) 已定过冗余口径：

> 冗余判定口径：删掉后故障检测力不降才算冗余；**跨层路径守卫、协议矩阵守卫、分支枚举锚点均为有意冗余，不清理**。

本报告严格遵守该口径。这直接决定了两处**看起来像冗余、实则不能动**的部分：

- 四份 `test_*_backend_parity.py` 近乎复制 → **跨层路径守卫**（每个后端要各自连真实数据库跑同一批 golden DSL），不清理；
- `test_type_map` 的 4 张后端类型表、各方言各一条「普通 SELECT 必须放行」→ **协议矩阵守卫**，不清理。

> ⚠️ 如果只按「代码相似度」做清理，会删掉这些用例并**真实丢失后端覆盖**。本报告把「真冗余」与「有意冗余」分列在 §7。

---

## 1. 结论摘要

| 类别 | 真问题 | 有意冗余（不动） | 最高严重度 |
|---|---|---|---|
| 该用数据结构却写冗余代码 | 约 20 处 | — | **P0**（1 处已实测崩溃） |
| 反复的方法调用 | 约 15 处 | — | P1（每查询 3 次 SQL 解析） |
| 永远用不到的代码 | 约 18 处 | — | P1（整个 `core/skills` 包零调用） |
| 冗余的过渡设计 | 约 14 处 | — | P1（纯转发层 / 恒真条件边） |
| 测试冗余 | 约 12 处 | 约 20 组 | P1（假测试 + 恒真断言） |

**TOP 6 风险**（按「会造成什么实际后果」排序，不是按代码量）：

| # | 位置 | 后果 | 状态 |
|---|---|---|---|
| 1 | `web/service.py:264-279` 重复实现 `security.guard.referenced_fields`，且漏了 expression 分支 | **客单价类查询抛 `AttributeError`** | ✅ 已实测复现 |
| 2 | `exec/guards.py:754` + `exec/audit.py:167,190` | 同一 SQL 每次查询解析 **3 次** | ✅ 已实测计数 |
| 3 | `core/skills/` 整个包（约 500 行） | 生产链路零调用，算法另在 `nodes.py` 里抄了一份 | ✅ 已验证 |
| 4 | `core/orchestrator/langgraph_engine.py:256-265` | `_get_worker_gate` 死代码，注释宣称的「进程级并发闸」从未生效 | ✅ 已验证 |
| 5 | `agent/tool_agent.py:1249-1270` vs `355-422` | 关键词分派两份且顺序已漂移 → 同一问题走两条路得到不同工具 | ✅ 已修复 |
| 6 | `exec/engine/type_map.py:113,163` | 正则兜底分支不可达（12 个位宽已全在 `_EXACT`） | ✅ 已验证 |

---

## 2. P0：不是风格问题，是已实测的缺陷

### 2.1 `web/service.py` 重复实现 `referenced_fields`，客单价查询崩溃

**位置**：`web/service.py:264-279`

`security/guard.py:46` 已有一份 `referenced_fields`，docstring 明写：

> 十九期评审收口：提取为共享函数——guard 与 agent.heuristic 的生成前作用域收窄共用同一实现，**权限回溯规则严禁双实现漂移**。

`web/service.py` 里又写了一份，且漏了 expression 指标的回溯：

```python
# web/service.py:264-279（复制品）
for m in dsl.metrics:
    if isinstance(m, RatioMetric): ...
    elif isinstance(m, WindowMetric): ...
    else:
        fields.add(m.field)      # ← ExpressionMetric 没有 .field，直接炸
```

```python
# security/guard.py:56-70（正版）
kind = getattr(m, "kind", "")
if kind == "expression":
    continue                      # ← 跳过，随后按 ref 回溯到被引用的聚合指标
```

**实测（conda `dataagent`）**：

```
$ python -c "from web.service import _referenced_fields; ..."
metrics: [('AggregateMetric','aggregate'), ('AggregateMetric','aggregate'), ('ExpressionMetric','expression')]
!! web/service._referenced_fields 崩溃: AttributeError 'ExpressionMetric' object has no attribute 'field'
security.guard.referenced_fields -> ['order_id', 'split_total_amount']   ← 正版正常
```

`config/semantic.json` 里「客单价」（`avg_order_amount`）的 shape 就含 `"kind": "expression"`，即这是一个**用户能直接问到的普通指标**，不是边缘 case。

**为什么没被测试发现**：全仓 `grep referenced_fields tests/` **零命中**；客单价只在 glossary/RAG 路径有测试（`test_router.py:93,140,366`，不执行查询）。

**影响面**：调用点 `web/service.py:610` 位于 `try` 块（355-589）**之外**，异常不会被转成友好文案。走 `/api/query` 时由 `TaskManager` 兜住 → 任务状态 `failed`（用户看到失败）；直接调 `run_query` 的路径（tools / eval）则原样抛出。

**修法**：删掉复制品，`_retrieval_context` 改 `from security.guard import referenced_fields`。

**✅ 已修复（2026-10-28）**：`web/service.py` 的 `_referenced_fields` 已删除，`_retrieval_context`
改用 `security.guard.referenced_fields`。补两组回归用例：
`tests/security/test_security.py::test_referenced_fields_traces_expression_refs`（expression 经
ref 回溯）、`::test_referenced_fields_covers_ratio_and_window`（ratio/window 三形态），
以及 `tests/web/test_web.py::test_retrieval_context_supports_expression_metric`（崩溃点本身）。
`referenced_fields` 此前零覆盖，这次一并补上。

---

## 3. 一类：该用数据结构却写了冗余代码

### 3.1 枚举值 → 输出的纯映射，写成 if/elif 链（应改 dict 分派）

| 位置 | 形态 | 建议 | 严重度 |
|---|---|---|---|
| `agent/heuristic.py:690-809` | **10 段完全同构的 dict 字面量**，仅 `granularity/amount/unit/mode` 三值不同，约 120 行可压到 15 行 | 常量表 `_REL_WINDOWS` + 构造器 `_relative(g, amount, unit, mode)` | 高 |
| `agent/heuristic.py:596-623` | 9 段 `if any(k in q for k in (...)): add(field)` | `_DIM_RULES: tuple[tuple[kw_tuple, field], ...]` + 3 行循环 | 中 |
| `core/orchestrator/nodes.py:229-270` | 对 `profile.intent` 连写 4 段 `if ==`（兜底主路径，无 LLM 时唯一规划器） | `_INTENT_PLANNERS: dict[intent, Callable]`，4 个分支体各抽函数 | 高 |
| `compiler/sql_compiler.py:310-324,339-357` | `AggFunc` 6 分支、`ExprOp` 7 分支，全是「枚举 → 模板字符串」 | `{AggFunc.SUM: "SUM({c})", ...}` + `.format(c=col)` | 中 |
| `compiler/sql_compiler.py:217-227,230-251` | 对同一 `dtype` 的 5 段 `if`，校验表与渲染表本应一一对应 | `_DTYPE_CHECK` / `_DTYPE_RENDER` 两张 dict，未知 key 统一 raise | 中 |
| `core/orchestrator/nodes.py:3022-3039` | 对 `state.answered_by` 连写 4 段 `if ==` 返回字面量文案 | `_BANNERS: dict[str, str]` | 中 |
| `compiler/dialect/mysql.py:38-52` | `granularity` 4 分支 `if` 链——而**同仓** `clickhouse.py:100-108` 对同一件事用的就是 dict | 与 CH/Hive 统一为 mapping | 低 |
| `semantic/catalog_loader.py:199-229` | 8 段一模一样的 `if not isinstance(x, dict): raise ValueError(...)` | `_as_dict(src, key)` 一行调用取代 30 行 | 低 |

### 3.2 同一份映射/逻辑在多个位置各写一遍（双事实源）

| 位置 | 重复内容 | 严重度 |
|---|---|---|
| `web/service.py:136-174,595-602` | 异常→文案/熔断映射**三份并存**：8 段 isinstance 链 + 15 项 dict + 4 项 dict，且条目数已不一致（15 vs 8 vs 4） | **高** ✅ 已修复 |
| `web/service.py:264-279` | 见 §2.1 | **高**（已实测） ✅ 已修复 |
| `agent/heuristic.py:550-563,581-594,882-895` | 同一份 9 词时间序列元组**同文件写 3 遍**，而 `agent/lexicon.py:12 TREND_KEYWORDS` 已是单一事实源（模块 docstring 明确「严禁再复制词表」） | **高** ✅ 已修复 |
| `exec/engine/mysql_adapter.py:32-44` / `postgres_adapter.py:47-59` | 「actual rows 解析」正则**逐字符相同**，函数体相同 | 高 |
| `tools/builtins/query_metric_tool.py:52-81` / `trend_analysis_tool.py:96-125` | **40 行 data/meta 装配逐字重复**（columns/rows/dsl/sql/scan_rows/rewrites/degraded/explanation/viz/qa_findings/chart_spec） | 高 |
| `agent/tool_agent.py:1249-1270` vs `355-422` | 关键词分派整段复制，且**顺序已漂移**（planner 先判导出，run 先判趋势） | 高 ✅ 已修复 |
| `compiler/sql_compiler.py:268-275` vs `939-946` | `FilterOperator → SQL 运算符` 映射表（7 项）写两遍 | 中 |
| `agent/clarify.py:50-53` vs `agent/router/intent_router.py:273-276` | 时间表达正则两份，**已漂移**（router 版多 `历史\|累计\|全部\|所有\|至今`） | 中 ✅ 已修复 |
| `web/runs.py:535-541` vs `intent_router.py:326-334` | 历史摘要转换（`history[-6:]` + role→中文 + join）两份，docstring 自称「口径对齐」却靠人肉同步 | 中 |
| `core/sandbox/api.py:81` vs `core/retrieval/export.py:92` | 同一数据集名正则两条实现 | 中 |
| `core/retrieval/parquet_ref.py:26` vs `:45` | `model_config` 在同一类里**声明两次**，后者覆盖前者 | 中（删 1 行） |
| `agent/tool_agent.py:55` vs `agent/router/legacy.py:16` | 同一句拒答文案定义两次 | 中 |
| `exec/engine/*_adapter.py` | 四后端 `connect_readonly` 同一段 try/harden/close/raise 样板；MySQL↔PG 的 `harden`/`insert_dataframe`/`physical_columns`/`install_secure_views` 四组几乎逐字相同 | 高（结构性） |
| `semantic/dsl_schema.py:26` vs `exec/guards.py:144` | 「安全标识符」白名单两份（一份带长度上限、一份不带） | 低 |

#### 3.2.1 验证第 2 批时新发现：维度成员顺序有两条互不一致的来源

**位置**：`agent/heuristic.py:617`（原 `provinces = [p for p in dimension_members("province") if p in q]`）

不在初版报告里——是在跑第 2 批回归时，因用例顺序不同而偶然暴露的。它不是重复代码，
是**双事实源导致的行为不确定性**，所以单列：

| 来源 | 顺序规则 | 「广东」与「浙江」谁在前 |
|---|---|---|
| `config/semantic.json` 的 `dimension_members_seed` | 人工维护的行政区划顺序 | 浙江（第 8 位）在前 |
| `semantic/catalog_loader.py:137-141` 从数仓重建 | `SELECT DISTINCT … ORDER BY 1`，即 Unicode 码位序 | 广东（U+5E7F）在前 |

`heuristic._filters` 直接按词表顺序输出 `IN` 列表，于是**同一句话在不同启动路径下
生成不同 SQL**：

```
种子路径：WHERE … pr.province_name IN ('浙江', '广东')
重建路径：WHERE … pr.province_name IN ('广东', '浙江')
```

语义等价，但后果是：结果集字符串不稳定（审计 diff、结果缓存键、golden 断言全部受
影响）。实测复现方式：先跑 `tests/web/test_web_auth.py`（其 `warehouse` fixture 调
`ensure_db()` 触发 catalog 从数仓重建），再跑
`tests/agent/test_agent.py::test_heuristic_covers_all_golden_questions`，Q10 必红；
而 `tests/agent` 单独跑全绿——全量套件里 `tests/agent` 排在 `tests/web` 之前，所以
CI 一直没暴露，属于**顺序依赖的潜伏缺陷**。

**已修复**：`IN` 列表顺序改为按**提问中出现的先后**排序（`key=q.index`）。确定性
来自用户输入本身，与词表来源彻底解耦；顺带把「问句提到两个类目时取词表靠前者」
改成取「先提到的那个」（原逻辑与措辞无关，同属顺序依赖）。Q10 的 golden 期望与
SQL 同步更新为 `('广东', '浙江')`。修复后上述顺序依赖组合 25 passed。

### 3.3 该用 dataclass / NamedTuple 却用并列容器

| 位置 | 问题 | 严重度 |
|---|---|---|
| `compiler/sql_compiler.py:569-574,583,673-692` | 「表达式 + 别名」这个组合实体用 `zip(dim_exprs, dim_aliases)` + 两个同键 dict 承载 | 中 |
| `exec/audit.py:40-71` | 手写 `__slots__` + `__eq__` + `__repr__`（27 行）只为承载 4 个 `str` | 低（`@dataclass(frozen=True, slots=True)`） |
| `core/orchestrator/intent.py:16-23` | 用类属性平铺 5 个 str 常量冒充枚举，无成员校验 | 中（`StrEnum`） |

### 3.4 该用依赖注入 / 中间件却手写样板

| 位置 | 问题 | 严重度 |
|---|---|---|
| `web/api.py:315-392` | **6 个 provider 路由**逐个重复「鉴权 + `set_request_context` + 手工 json Response」12 行 | 高 |
| `web/api.py:222-231` vs `262-271` | `client_principal_ignored` 告警块逐字重复 10 行 | 中 |

---

## 4. 二类：反复的方法调用

### 4.1 同一次执行内对同一输入重复计算

| 位置 | 重复次数 | 实测/估算代价 | 严重度 |
|---|---|---|---|
| `exec/guards.py:754` → `368`；`exec/audit.py:167`、`190` | **同一 SQL 解析 3 次** | ✅ 实测计数 3；单次 223 µs → 每查询约 0.45 ms 纯浪费 | **高** |
| `agent/heuristic.py:92,106,133,134` | `_metrics(q)` 单次 `run()` 内算 **3 次**、`_dimensions(q)` 2 次、`_dim_keyword_table()` 2-3 次（每次遍历全目录 + `field_label`） | 每请求都走的确定性路径 | **高** |
| `agent/semantic_check.py:81,115` | 同一 query 在**同一轮 LLM 重试循环内**被启发式全量解析 2 次，每轮重试都重算 | 与上一条叠加 | 高 |
| `core/orchestrator/nodes.py:1390,2309,2802,2854,2884` + `grounding.py:118` | 同一份 parquet 一轮被 `pd.read_parquet` 读 **3-4 次** | 直接放大磁盘 IO 与报告时延 | 高 |
| `compiler/sql_compiler.py:70` + 16 处 `get_renderer()` | 单次编译 **27+ 次**重新实例化 renderer（2.55 µs/次） | 约 70 µs/次编译，且多在循环内 | 中 |
| `compiler/sql_compiler.py:576-607,450/481,675-683/708-714,764/796` | comparison/top_n/fill_gaps 三条路径对同一数据重复算：`_collect_tables` 4 遍、`_filter_sql`/`_from_clause`/`_metric_expr` 各 2 遍 | 编译期纯浪费，且是「两个 CTE 产物不一致」类 bug 的温床 | 中 |
| `exec/guards.py:419,436` | 同一 SQL 被 `_strip_sql_comments` 完整扫描两遍（第二遍只为一个正则） | 中 | 中 |
| `agent/tool_agent.py:1576-1577` | 同一函数同一入参**连调两次**只为写两个字段 | 高（1 行修） | 高 |
| `core/retrieval/exploration.py:56,110` | 同一条 SQL 在探索链路被 sqlglot 解析两次，两次调用在 `nodes.py:517/585` 紧邻 | 中 | 中 |
| `core/orchestrator/nodes.py:3256` | `_default_scope_note(state)` 在 `for` 循环体内调用，参数完全不变 | 中 | 中 |
| `core/orchestrator/nodes.py:325-332` | `_wants_grouping` 在维度循环内每次重建 `dimension_terms()` 并全表遍历 | 中 | 中 |

### 4.2 目录派生的映射每次重建（目录进程内不变，应缓存）

| 位置 | 问题 | 严重度 |
|---|---|---|
| `core/orchestrator/intent.py:35-63,117-121` | 指标/维度词表每次调用重建（遍历全 `COLUMNS` + 逐 alias 展开 + `sorted(key=len)`），而 `classify_intent` 一轮被调 6 次 | 高 |
| `agent/memory.py:448-482` | `dim_keywords` 每次遍历全 `COLUMNS` + `field_label()`，目录不变时结果恒定 | 中 |
| `core/retrieval/sql_lift.py:462-468` | `expected_joins` 每次 `lift_sql` 从 `JOIN_RULES` 重建 | 中 |
| `core/retrieval/sql_lift.py:98,104-109` | 未限定列每次全表线性扫 `_COLUMN_INDEX`；表名反查每次线性扫 `ALIASES` | 中 |
| `agent/tool_agent.py:468,517,1137` | `registry.tool_definitions()` + `json.dumps` 在 3 处各算一次，工具集进程内不变 | 中 |
| `agent/heuristic.py:244-265` | `_having_field_alias_map()` 每次遍历 `catalog.METRICS` 重建 | 低 |

### 4.3 循环内现场编译正则 / 函数内重复 import

| 位置 | 问题 | 严重度 |
|---|---|---|
| `exec/guards.py:426-428` | 黑名单循环内 `re.search(rf"\b{kw}\b", ...)` **每个关键字现场编译一次**：19~42 次编译/执行 | **高**（实测 179 µs → 预编译 15.9 µs，11×） |
| `tools/builtins/_query_core.py:242-251` | `_json_safe` 对**每个单元格**调用，函数体内每次执行 3 个 `import` | 中 |
| `core/orchestrator/nodes.py:509,1863` + 8 处 `from config import settings` | 函数内重复导入模块级已导入的模块 | 低 |
| `core/retrieval/pii.py:62` | 每列名跑一次 `re.sub`，pattern 为字面量未预编译 | 低 |
| `core/retrieval/export.py:92-96` | 函数体内 `import re` + 字面量 pattern | 低 |

---

## 5. 三类：永远用不到的代码

### 5.1 整包/整函数零调用（已交叉验证）

| 位置 | 证据 | 严重度 |
|---|---|---|
| `core/skills/`（`drilldown.py:27`、`decomposition.py:22,82`、`shapley.py:36`、`timeseries.py:19,67`） | ✅ 已验证：全仓仅在 `nodes.py:64` import + `3295` 的 `__all__` 出现，**无实际调用**；4 个算法模块约 500 行 | **高** |
| `core/orchestrator/langgraph_engine.py:256-265` | ✅ 已验证：`_worker_gate` / `_get_worker_gate` 全仓仅定义处出现；而 `_execute_wave:291` 另建 semaphore → 注释宣称的「进程级共享闸」从未生效 | **高** |
| `core/orchestrator/langgraph_engine.py:368-380` | `run_fanout_serial` 是已删除的 native 引擎降级路径，生产无调用（仅测试引用） | 中 |
| `core/orchestrator/state.py:279-287` | `estimate_tokens()` 无调用点 | 中 |
| `core/retrieval/tools.py:44-52` | `compile_dsl_to_duckdb` 无调用点（docstring 称「供计划期成本预估」，从未接入） | 中 |
| `tools/base.py:70-79` | `ToolResult.to_dict()` 无调用点 | 中 |
| `tools/registry.py:118-145` | `_register_tool` 三形态装饰器生产零调用（仅测试用） | 中 |
| `web/service.py:192` | `_PROVIDER_FRIENDLY_BY_CODE` 定义后从未读取 | 中 |
| `agent/memory.py:251-261` | `SessionStore.prune()` 全仓无调用 | 低 |
| `core/sandbox/api.py:225-227` | `elapsed_budget_guard` 无调用点，`budget_ms` 形参也从未使用 | 低 |
| `tools/mock_llm_server.py`（整文件 98 行） | 开发用 mock 服务混在 `tools/` 包内，不被任何模块导入 | 低 |

### 5.2 不可达分支 / 恒真条件（已逐条验证 2 条）

| 位置 | 为什么不可达 | 严重度 |
|---|---|---|
| `exec/engine/type_map.py:113,163-164` | ✅ 已验证：`_INT_WIDTH_RE` 可匹配的 12 个位宽**全部**已登记在 `_EXACT`，差集为空 → 正则兜底永不生效（两份实现并存，其中一份是死的） | 中 |
| `compiler/sql_compiler.py:842-843,849-850,856-857` | 三条 `if not dsl.metrics: raise` 是死分支：契约层 `dsl_schema.py:491-500` 已拒绝该形态 | 中 |
| `compiler/sql_compiler.py:551-552` | 上方循环已保证「只要有时间维度就赋 `time_dim`」，`time_dim is None` 恒假 | 中 |
| `exec/engine/*_adapter.py`（duckdb:104 / mysql:131 / pg:164 / hive:310） | 四处 `except SecurityError: raise` 的 try 体内**没有任何 SecurityError 来源** | 中 |
| `core/orchestrator/nodes.py:210-213` | `clarify_node` 的 `if state.history_digest` 两分支返回**完全相同**的值 | 中 |
| `core/orchestrator/nodes.py:2650-2656` | `_render_table` 内容器守卫重复判断，`else` 分支里的 `isinstance(v, (dict,list,tuple))` 永假 | 中 |
| `core/retrieval/sql_lift.py:141-142,607-608` | 两处已被上游分支完全覆盖 | 中 |
| `compiler/sql_compiler.py:781-784,136` | `Granularity` 四值已被上方四支全覆盖；`_shift_window` 的兜底 raise 不可达 | 低 |
| `semantic/catalog_loader.py:39-60,361-394` | 22 个 `_DEFAULT_*` 快照 + `_build_defaults` 不可达（`semantic.json` 缺失时 import 已失败），模块注释自认「不再回放本快照」 | 中 |

### 5.3 只写不读的字段

| 位置 | 问题 | 严重度 |
|---|---|---|
| `semantic/catalog.py:117,163` | `FACT_TABLES` 写入 4 处、**全仓零读取**（docstring 称「用于校验/文档」，两件事都没做） | 低 |
| `core/orchestrator/state.py:228` | `intent_anchors` 只写不读 | 中 |
| `core/orchestrator/state.py:133` | `ToolRecord.args` 在 core 内从未赋值（恒空 dict） | 低 |
| `core/orchestrator/state.py:146` | `Artifact.kind` 的 `"parquet"` 成员从未被构造 | 低 |
| `tools/builtins/_query_core.py:61` | `GuardedQueryResult.cached` 只写不读 | 中 |
| `core/orchestrator/state.py:40-50` | `Phase` Literal 缺 `"exploration"`，而 `nodes.py:537` 写入该值；`model_copy` 不做校验 → 契约形同虚设 | 中 |
| `agent/slotfill.py:19-25,187` | 为让两个**实际未使用**的私有常量「看起来被使用」而写的 `assert` | 中 |

---

## 6. 四类：冗余的过渡设计

### 6.1 纯转发层（函数体就一个 return）

| 位置 | 形态 | 严重度 |
|---|---|---|
| `core/orchestrator/agent.py:224-251` | ✅ 已验证：`_run_agent_inner` 函数体只有一个 `return _run_agent_langgraph_path(...)`，把 9 个参数逐字抄一遍；docstring 自认「M4 单引擎收敛…签名保持」 | **高**（删 28 行） |
| `core/orchestrator/agent.py:59-61` + `langgraph_engine.py:395` | `route_from_clarify` **恒返回 `"plan"`**，却仍以 `add_conditional_edges` + 单目标映射注册 | **高**（改 `add_edge` 即可） |
| `core/orchestrator/subagent.py:135-151` | 三个一行转发函数，各只被调一次，仅为规避循环导入 | 中 |
| `agent/router/legacy.py`（整文件 29 行） | 纯转发层：自建常量 + 别名 + 4 个 import 转发；真实消费方只有 `web/service.py:33` 取一个常量 | 中 |
| `agent/tool_agent.py:804,811,944` | `Synthesizer.synthesize(..., query)` 的 `query` 在确定性实现里未使用 | 低 |
| `core/orchestrator/langgraph_engine.py:383,453-455` | `build_langgraph_app` 只是 `_compile` 的一层壳，唯一职责是补默认 checkpointer | 低 |
| `compiler/sql_compiler.py:58-60` | `_time_field_qualify(tf)` 就是 `return _qualify(tf.time_field)` | 低 |
| `exec/guards.py:616-618` | `_two_stage_enabled()` 就是 `return bool(settings.X)`，单调用者 | 低 |
| `tools/builtins/trend_analysis_tool.py:270-279` | `_default_window` 一行转发 + 函数体内 import | 低 |
| `agent/pipeline.py:69-72` | `run_pipeline` 只是丢掉 bool 的一行包装 | 低 |
| `core/sandbox/_bootstrap.py:236-243` | `_dynamic_compile` 只调一次的一行 wrapper，且紧邻的 `exec` 取用是内联写法（风格不一致） | 低 |

### 6.2 形参从未被使用（「预留」但没人用）

| 位置 | 未使用形参 | 严重度 |
|---|---|---|
| `exec/guards.py:515-533,536-557` | `cached_scan_rows`/`cache_scan_rows` 的 `cache: dict \| None = None` **全仓零调用**，却撑起两条并行的「加锁/不加锁」分支 | 中 |
| `agent/router/intent_router.py:425,525,580` | `principal` 贯穿 3 层方法却从不被读取 | 中 |
| `agent/tool_agent.py:1513-1525` | `_execute_once` 的 `query` 形参体内从未使用（3 个调用点白传） | 中 |
| `web/service.py:299` | `run_query(retrieval_context=...)` 无任何调用方传值 | 中 |
| `core/retrieval/quality.py:57` | `_check_empty_result(columns, rows)` 的 `columns` 从未使用（为统一签名强行带上） | 低 |
| `tools/builtins/export_report_tool.py:169-173` | `_mask_value(column, value)` 的 `column` 从未使用 | 低 |
| `core/orchestrator/subagent.py:113` | `_build_subagent_app(checkpointer=None)` 无人传 | 低 |
| `exec/audit.py:74-78` | 两个 `set \| None = None` 形参的唯一调用者总是全传，默认值永不生效 | 低 |

### 6.3 单实现抽象 / 空节点

| 位置 | 问题 | 严重度 |
|---|---|---|
| `core/orchestrator/langgraph_engine.py:397,414` | 为收敛到 END 专门注册一个空节点 `plan_gate_end`（`lambda s: s`） | 中 |
| `core/orchestrator/autonomy.py:18,21-26,33` | `AUTONOMY_LEVELS`、`AutonomyPolicy` 无任何引用；`"step_confirm"` 默认动作永不触发（引擎自认 L1 逐节点确认未实现） | 中 |
| `exec/__init__.py:7-31` | 整包级 re-export（10 个符号）**零消费者**，副作用是 `import exec.engine.xxx` 也会拉起 guards+sqlglot | 中 |
| `exec/engine/__init__.py:20-28` vs `compiler/dialect/__init__.py:26-34` | `_lazy_factory` 逐字相同两份 | 低 |
| `exec/engine/base.py:30-44` | `ScanPrecheck.usable` 的四个条件里有两个对唯一生产者恒为常量（`estimated=True` 硬编码、`measurable=rows>=1`），实际等价于 `rows>=1 and operator` | 低 |
| `tools/builtins/__init__.py:29,45-47` | `_registered` 开关 + `try/except` 幂等**双重**保护 | 低 |

---

## 7. 测试侧：真冗余 vs 有意冗余

### 7.1 真冗余（建议清理）

| 位置 | 问题 | 证据 | 严重度 |
|---|---|---|---|
| `tests/audit/test_audit.py:105` | ✅ 已验证：`conn` 在 103 行 `finally: conn.close()` 之后，105 行 `assert conn is not None` — **恒真断言** | 断言的是刚被用过的局部变量 | **高**（已删） |
| `tests/engine/test_mysql_adapter.py:293` | ✅ 已验证：`test_question_mark_placeholder_breaks_percent_formatting` 只做 `sql % ("gmall",)` + `pytest.raises(TypeError)`，**全程不碰 `exec.*`/`providers.*`** — 测的是 CPython 的 `str.__mod__` | docstring 自称「根因锚」，但作为回归锚点它保护不了任何项目代码 | **高**（已删） |
| `tests/core/test_orchestrator.py:315` | 用例名/docstring 承诺「语义不变」，函数体**只调 `validate_dsl_payload`，没有任何相等断言** — 实现把载荷改坏本用例仍绿 | 名实不符 | **高**（已补 `assert d == payload`） |
| `tests/exec/test_exec.py:280-287` | **4 分支 `if/elif`** 用「回看 SQL 文本」决定期望列名，且无 `else` 兜底 → 新增 SQL 漏改分支会静默通过 | 教科书级的该参数化样本 | **高** |
| `tests/core/test_degraded_fallback.py:141` | `_NoLLM` 类定义后零引用（用例实际用 `monkeypatch.setattr`） | 死辅助 | 中 |
| `tests/security/test_security_views.py:21` | `_make_conn()` 定义后零调用（已被 `conftest.conn` 取代） | 死辅助 | 中 |
| `tests/mock/test_schema_hive.py:18` | `_table_of()` 本文件零调用（从 `test_schema_clickhouse.py:24` 复制而来，那份在用） | 死辅助 | 低 |
| `tests/dod_e2e_check.py`（262 行） | 文件名不以 `test_` 开头，**CI 永不执行**；内部 mock 与 `test_providers.py:526`、`test_agent_e2e.py:95` 的桩重复 | 「看起来有覆盖其实没有」 | 中 |
| `tests/auth/test_ratelimit.py:10,26,33,57` | 4 个用例只调 `limiter.check(...)` 结尾，**零断言**（契约是「不抛即放行」，但至少 1 条正向 + 3 条合并参数化即可） | 低 | 低 |
| `tests/core/test_orchestrator_events.py:305` | docstring 承诺「吞并**并告警**」，实际只验证「不抛」，未断言告警 | 承诺与断言不符 | 低 |
| `tests/providers/test_providers.py:1460,1468` | 为已删除的 Gemini 实现写的用例，且用 `pytest.raises(Exception)`（过宽） | 迁移期已过 | 低 |
| `tests/compiler/test_dialect.py:390/573,393/576,409/594,421/617,457/652,467/662,477/672,480/675` | **8 对用例函数体 AST 逐字节相等**（CH vs Hive 渲染器）——注意：这**不是**跨层路径守卫，是同一份代码同一断言写两遍 | 可参数化，删约 70 行 | 中 |

### 7.2 有意冗余（按 2026-10-05 口径，**不要动**）

| 位置 | 为什么保留 |
|---|---|
| `tests/test_{dual,pg,clickhouse,hive}_backend_parity.py`（157/157/163/180 行，相似度 0.98–1.00） | **跨层路径守卫**：每个后端各自连真实数据库跑同一批 golden DSL，这是「四后端结果一致」的唯一证据。相似度高恰恰是设计意图 |
| `tests/engine/test_type_map.py:66,71,76,81` | **协议矩阵守卫**：四张后端类型表 |
| `tests/exec/test_guards_postgres.py:65` / `test_guards_clickhouse.py:56` / `test_exec.py:435` | **分支枚举锚点**：每个方言各一条「普通 SELECT 必须放行」 |
| `tests/engine/test_{mysql,postgres,clickhouse,duckdb}_adapter.py` 的「自建连接必须关、注入连接不许关」 | **协议矩阵守卫**（同一契约 4 份） |
| 28 个 skipped | 全部是真实环境变量门控（`*_TEST_HOST`、`LIVE_LLM_TESTS`），**无常量/恒真 skip**（已核） |

> ⚠️ **这一节是本报告最重要的边界**。如果按代码相似度做「清理」，上面这些会被删掉，真实后端覆盖随之消失。历史评审已明确裁定过，本次复核确认裁定仍然成立。

### 7.3 测试辅助代码重复（conftest 化收益明确）

| 位置 | 重复内容 | 严重度 |
|---|---|---|
| `tests/agent/test_agent_e2e.py:95-122` ↔ `tests/providers/test_providers.py:526-552` | 整套 HTTP 桥接桩（`_FakeSrv` + `_start_test_client` + `_client_req` + `_REQ_CLIENT`）**逐字复制**，docstring 一字不差 | 高 |
| `tests/agent/test_agent.py:125` ↔ `tests/agent/test_tool_agent.py:141` | 同一个 `FakeLLM` 写两遍（后者多一层 `RecordingLLM` 依赖它） | 高 |
| `tests/test_{dual,pg,clickhouse,hive}_backend_parity.py` 的 `_test_dsn()` / `_run_dsl()` | `parity_utils.py` 提取只做了一半：DSN 构造 + 双后端执行仍 4 份复制 | 高（但注意：抽的是**代码**，不是删用例） |
| `tests/web/test_web_api.py:13` / `test_sse_contract.py:17` / `test_agent_audit.py:11` / `test_web.py:51` | `client` fixture 4 份，函数体逐字相同 | 中 |
| `tests/web/test_web_auth.py:23` ↔ `test_agent_stream.py:31` | `_FakeServer` 两份 | 中 |
| `tests/engine/test_mysql_adapter.py:209,230` ↔ `test_postgres_adapter.py:199,218` | `_Cursor`/`_Conn` 跨文件同构 | 中 |
| `tests/engine/test_mysql_adapter.py:68,79,157,168,187,197` | 同一文件内 3 份局部 `_Cur`/`_Conn` | 中 |
| `tests/semantic/test_catalog_loader.py:472,494` ↔ `tests/test_catalog_backend_parity.py:63,81` | `_FakeAdapter` + `_NoopConn` 两份 | 中 |
| `tests/exec/test_pool.py` | 14 处 `try/finally: pool.close()` + 13 处重复构造 | 中（可用 fixture） |
| `tests/web/test_web_auth.py` | 23 处 `_start_server()` + 22 段 `try/finally` 围绕一个空桩（`port` 恒 0 且未被使用） | 中（约 90 行样板） |

### 7.4 真冗余 vs 有意冗余的判定表（供后续评审复用）

| 判据 | 结论 |
|---|---|
| 函数体 AST 逐字节相同、断言相同、**只差类名/方言常量** | 真冗余 → 参数化 |
| 同一契约在不同后端/方言上各跑一次，**实际连不同后端** | 有意冗余 → 保留 |
| 断言恒真 / 只测语言内建行为 / 名字承诺的断言不存在 | 真冗余 → 修或删 |
| 只测「不抛异常」且契约就是「不抛」 | 可接受，但同类应合并参数化 |
| skip 条件依赖真实环境变量 | 有意 → 保留 |

---

## 8. 修复优先级

### 第 1 批：止血（1 天内，改动小、后果明确）—— ✅ 已完成 2026-10-28

| 动作 | 位置 | 状态 |
|---|---|---|
| 删 `web/service.py` 重复的 `_referenced_fields`，改用 `security.guard` 正版 + 补 expression 指标回归用例 | `web/service.py:264-279` | ✅ 修掉客单价查询失败，补 3 条用例 |
| 删恒真断言 | `tests/audit/test_audit.py:105` | ✅ 已删 |
| 删假测试（只测 CPython `%` 格式化） | `tests/engine/test_mysql_adapter.py:293` | ✅ 已删，根因说明并入 `test_physical_columns_uses_percent_placeholder` docstring |
| 补缺失的相等断言 | `tests/core/test_orchestrator.py:315` | ✅ 已加 `assert d == payload` |
| 合并重复调用 | `agent/tool_agent.py:1576-1577` | ✅ `_summarize` 只调一次 |

### 第 2 批：正确性风险（1 周内）—— ✅ 已完成 2026-10-28

| 动作 | 位置 | 状态 |
|---|---|---|
| 统一关键词分派（两份已漂移，顺序相反） | `agent/tool_agent.py:1249-1270` vs `355-422` | ✅ 提取 `_route_by_keywords` + `_KeywordRoute`，统一取「导出优先于趋势」 |
| 统一异常→文案/熔断映射（三份已不一致） | `web/service.py:136-174,595-602` | ✅ 改单一 `_ERROR_RULES` 三元组，三个消费方全部派生 |
| 统一时间表达正则（两份已漂移） | `agent/clarify.py:50-53` vs `intent_router.py:273-276` | ✅ 拆成 `TIME_WINDOW_RE`（澄清用）与 `TIME_SIGNAL_RE`（路由用），语义分离 |
| 统一时间序列词表（同文件 3 遍，违反 lexicon 单一事实源约定） | `agent/heuristic.py:550-563,581-594,882-895` | ✅ 提取 `TIME_SERIES_KEYWORDS`（`TREND_KEYWORDS` 的严格子集） |
| **额外发现**：维度成员顺序双来源导致 SQL 不确定 | `agent/heuristic.py:617` | ✅ 见 §3.2.1，`IN` 列表改按提问先后排序 |

验证：`black --check` / `ruff check` 全绿，全量 pytest **1421 passed / 28 skipped**。

### 第 3 批：性能（1 周内，一次改完）

| 动作 | 预期收益 |
|---|---|
| SQL 只解析一次（把 AST 传下去） | 每查询省 ~0.45 ms |
| 黑名单关键字预编译成单条 alternation | 每次执行省 ~0.16–0.36 ms（11×） |
| `_metrics`/`_dimensions`/词表/`dim_keywords` 加 `lru_cache` + 目录刷新时 `cache_clear()` | 确定性路径每请求省 2-3 次全目录遍历 |
| parquet 预览按 `(path, mtime)` 缓存 | 省 2-3 次磁盘读 |
| `get_renderer()` 提到编译函数开头 | 单次编译省 ~70 µs |

### 第 4 批：结构收敛（2 周，需评审）

| 动作 | 收益 |
|---|---|
| 四后端 adapter 上提 `connect_readonly` / `harden` / `insert_dataframe` / `physical_columns` / `install_secure_views` 到 base | adapter 总量约 1000 → 500 行，新增后端成本减半 |
| 决定 `core/skills` 生死：要么接进 `_analysis_template`，要么删除（当前 500 行死代码 + 算法被抄进 `nodes.py` 模板字符串） | 消除双事实源 |
| 修 `_get_worker_gate` 或删除（当前注释与实现相反） | 消除「读代码会被误导」 |
| 枚举分派改 dict（`nodes.py:229-270`、`heuristic.py:690-809`、`sql_compiler.py:310-357`） | 新增意图/聚合/时间词从「改代码」变「加表项」 |
| 删除已证不可达的分支（§5.2） | 约 40 行，安全语义反而更清晰 |
| 测试 conftest 化（§7.3） | 约 200 行重复消除，且不损失检测力 |

---

## 9. 附录

### 9.1 本次实测复核的 8 条（可复现）

| # | 结论 | 验证方式 | 结果 |
|---|---|---|---|
| 1 | `web/service._referenced_fields` 对 ExpressionMetric 崩溃 | 用 `DeterministicNL2DSL` 生成「2025年客单价是多少」的 DSL 后调用 | ✅ `AttributeError: 'ExpressionMetric' object has no attribute 'field'`；`security.guard` 正版正常返回 `['order_id','split_total_amount']` |
| 2 | 同一 SQL 解析 3 次 | `patch('sqlglot.parse')` 计数，依次调 `assert_read_only_sql` + `audit_compiled_sql` | ✅ 计数 = 3 |
| 3 | `core/skills` 生产零调用 | 全仓 grep 4 个公开函数名，排除 `tests/` 与 `core/skills/` 自身 | ✅ 仅 `nodes.py:64` import + `3295` 的 `__all__` |
| 4 | `_get_worker_gate` 死代码 | 全仓 grep `_worker_gate` | ✅ 仅定义处 4 行 |
| 5 | `type_map` 正则兜底不可达 | 枚举 12 个位宽，比对 `_EXACT` 键集与 `_INT_WIDTH_RE` 命中集 | ✅ 差集为空 |
| 6 | `_run_agent_inner` 是纯转发层 | 读 `agent.py:224-251` 函数体 | ✅ 仅一个 `return _run_agent_langgraph_path(...)` |
| 7 | `test_audit.py:105` 恒真断言 | 读 98-106 行 | ✅ `conn.close()` 后 `assert conn is not None` |
| 8 | 维度成员顺序双来源导致 `IN` 列表不确定（§3.2.1） | `pytest tests/web/test_web_auth.py tests/agent/test_agent.py::test_heuristic_covers_all_golden_questions`（web 在前） | ✅ Q10 必红；调换顺序或 `tests/agent` 单独跑全绿 → 确认是顺序依赖的潜伏缺陷 |

### 9.2 明确排除（避免误伤）

| 项 | 为什么不是问题 |
|---|---|
| `EngineAdapter` 协议 + 5 实现 | 有意多引擎抽象；`explain_scan_estimate` 是**可选**能力（`getattr` 探测），仅 DuckDB 实现且被 `guards.py:697` 真实调用 |
| `DialectRenderer` 5 实现 | 同上，多方言抽象 |
| `core/skills/` 四技能「功能并列」 | 并列本身不冗余（冗余的是它们没被调用） |
| `nodes.py` 3300 行模板字符串体量 | 设计选择，非本报告四类问题 |
| 28 个 skipped 用例 | 全部真实环境变量门控（已核，无常量条件） |
| `test_unknown_principal_raises`（`security/test_scope.py:68` / `test_security.py:169`） | 分别测 `scoped_fields` 与 `apply_policy`，语义不同 |
| `test_ttl_cleanup`（`web/test_tasks.py:64` / `test_web_runs.py:163`） | 分别测 TaskManager 与 RunRegistry |
| `_fake_error_base`（`test_hive_adapter.py:79`） | `autouse=True` fixture，生效中 |
| `ProbeAdapter.test_connection` | `BaseAdapter.test_connection` 是 `@abstractmethod`，覆盖为实例化所必需 |

### 9.3 本报告的已知局限

- 行号为审计时快照，若期间有改动需重新定位；
- 死代码判定基于**静态**调用点，动态调用（`getattr` / 字符串分派 / 框架注册）可能造成误报——§5.1 的每条都已用全仓 grep 交叉验证，但 `core/skills` 这类「被 import 后 re-export」的形态仍建议改前再确认一次；
- 性能收益为单点实测/估算，未做端到端压测；实际收益取决于调用频次。
