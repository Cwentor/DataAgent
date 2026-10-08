# 语义层去耦与评测分层改造（P0→P1→P2 三期）

## 探查结论（方案依据）

1. **P0 前提修正**：`result_hash` 全仓无消费者（纯观测输出）；结果断言是"golden SQL vs 编译 SQL 同库差分"——**改数据值不会让 golden 失败，改 schema/语义目录才会 31 条雪崩**。真正的数据 pin 灾难点在测试夹具层（44.09 万 / 115.69 万 / "查询答案：34" / "TCL"/"数码" / `{"广东"}` / "2025-12-31" 水印）。
2. **挖出 3 个活缺陷**（顺手在 P0/P1 修复）：`eval/intent_golden.json` 省份计数仍钉死旧答案 `8`（实际 34）；`agent/tool_agent.py:59` 数据域提示写死 `2024-06-30`（与 `DATA_DOMAIN_END=2025-12-31` 已漂移）；`refresh_catalog` 不覆写 `DRILLDOWN_DIM_FIELDS`/`DIMENSION_MEMBER_FIELDS` 全局，且 `nodes.py:81`、`sql_lift.py:34` 的 import 时值绑定使 refresh 后 stale。
3. **P2 可行性实测**：`config/semantic.json` 与 `semantic/catalog.py` 内置目录在 fields/labels/aliases/joins/query_domains 各节**逐字相等**（机器比对零差异）——json 已具备唯一事实源能力，缺 5 个小节。
4. `present/labels.py` 与 json label 有 10 处文案漂移；`memory` 与 `tool_agent` 的 `_TREND_KEYWORDS` 同义词表已漂移（18 vs 19 词）。
5. L1 契约测试零数据依赖可行：`compile_sql` 只依赖静态 catalog；`test_agent.py` 已有"golden 全量 DSL 逐字断言"现成骨架；缺的只是 catalog-only 夹具与断言分层开关。

## M-P0 评测分层 + 数据 pin 清理（含活缺陷修复）

1. **eval_runner 断言分层**：`CaseReport` 拆 `contract_ok`（dsl_ok + sql_ok，零数据依赖）与 `snapshot_ok`（result_ok，依赖数仓快照）；汇总分节报告；CLI 加 `--skip-snapshot`（快速契约回归）；退出码语义不变（任一失败即非零）；`tests/test_eval.py` 同步。
2. **intent_golden 活缺陷**：省份计数 `8` → `34`（动态取 `len(DIMENSION_MEMBERS["province"])` 口径固化）；`list_answer` 的 `"数码"`（新类目域不存在）→ 换实际成员（如 `"手机"`）。
3. **测试桩值去 pin 化**（保断言强度、消灭字面值）：
   - `test_orchestrator.py` grounding 两处（44.09 万 / 115.69 万）→ 测试内先查库取该窗口真值再注入 stub 报告；
   - "查询答案：34"（2 处）→ `len(catalog.DIMENSION_MEMBERS["province"])` 动态拼接；
   - "2025-12-31" 超界水印（2 处）→ `settings.DATA_DOMAIN_END` 动态；
   - `test_security_views.py` 的 `{"广东"}` → `PRINCIPAL_ATTRS ∩ 库内省份` 动态计算；
   - `test_profiling.py` 存在性断言维持（已是弱耦合）。
4. `tool_agent.py` 数据域提示 → 改读 `settings.DATA_DOMAIN_END`（活缺陷）。
5. `boundary_eval` 加入 nightly CI 步骤（当前不在任何 CI）。

## M-P1 业务映射收编（约 40 处 → semantic.json）

**semantic.json v2 新增节**（catalog_loader 解析、refresh/reset 同步覆写全局）：
- `metrics[]`：`{key, title, aliases, definition, formula, fields, shape}`——shape 描述 DSL 产出形态（aggregate 的 field/agg/alias；ratio 的分子分母；expression）。作为 **glossary 公式**与 **heuristic._metrics 关键词分支**的公共事实源；
- `count_entities[]`（"多少订单/用户/商品"实体词表）、`paid_filter`（成功口径 field/value/文案/反义问法）、`default_window`（缺省分析窗口规则，替代 2024-05-01 字面量）、`reflector_concepts`（概念→字段组+产物别名）、`out_of_scope_concepts[]`、`undefined_metrics[]`（clarify）、`value_labels`、`table_labels`、`drilldown_dim_fields`、`region_province_mapping`、`dimension_members_seed`、`metric_aliases`（gmv/orders/buyers 产物别名约定）。

**代码侧改造**：
- `heuristic.py`：`_metrics` 改**表驱动**（遍历 metrics[].aliases 匹配 → 按 shape 产 DSL；流量域/计数实体从 json 读）；`_DIM_KEYWORDS` 改读 catalog aliases；`_HAVING_FIELD_ALIAS` 从 metrics 派生；窗口/TopN/时间主轴等**问法层逻辑保留**；
- `nodes.py`：`_SCALAR_FIELD_AGG` → json `field_metrics`；`_SCALAR_ANCHOR_TABLE` 删除（读 `catalog.COLUMNS[].table`）；`_REFLECTOR_*` 三常量 → json；`_DIMENSION_LABELS` → catalog label；缺省窗口字面量 4 处 → `default_window` 推导（诊断两期测试期望一次性更新，形态级）；`'1002'` 五处 → `paid_filter`；
- `glossary.py` 变适配层（GLOSSARY/METRIC_TERMS 从 `catalog.METRICS` 派生，保留 `scoped_glossary`；删死常量 OPERATOR_TERMS）；
- `prompts.py`：`_CONVENTIONS` 中**业务条目**（指标映射/大区展开/支付口径/维度词）从 json 渲染；**结构性条目**（时间语法/窗口/TopN/排序）保留模板；白名单注入升级为"字段： 中文label"；
- `memory.py`：`_DIMENSION_SYNONYMS`/`_expand_dimension_fallback` 删除改读 aliases；
- `labels.py`：FIELD_LABELS 从 catalog label 派生（10 处文案漂移以 json 为准）、VALUE_LABELS 从 json；
- 问法层词表单源化：`memory`/`tool_agent` 重复的趋势/时间等词表合并到 `agent/lexicon.py`（独立于 semantic.json，遵"问法层独立"判断）；
- `intent.capability_catalog_lines` 从 json 派生（去 price 特判）；
- 修 import 值绑定 stale：`nodes._DIAGNOSTIC_DIM_POOL`、`sql_lift._MAIN_TABLE` 改函数内动态读。

**守护网**：golden 31/31（oracle + 离线 agent）+ `test_agent.py` 现成的 heuristic 全量 golden 逐字断言 + 全量 pytest；heuristic 表驱动改造分小步提交。

## M-P2 单一事实源（json 直读路线）

- semantic.json 补齐上述节后成为超集：`semantic/catalog.py` 内置常量改为 **import 时从 json 加载**（严格校验必要节，缺节报错），catalog.py 缩为加载器 + 访问层；`reset_defaults` 重读 json；删除 `_DEFAULT_*` 手写双源快照；
- `refresh_catalog` 语义不变（运行时仍从库 distinct 重建成员词表，json 的 `dimension_members_seed` 仅作离线回退）；`rebuild_golden` 行为不变；
- 备选：若你更倾向纯代码形态内置目录，可换 `catalog_gen.py` 代码生成 + pre-commit 钩子（达成同一目标，多一条生成链路维护）；
- 删除 `heuristic.REGIONS` 兼容别名，消费方直读 catalog。

## 文档与回归

- 规格文档 `docs/plans/2026-10-07-semantic-decoupling-spec.md`；AGENTS.md 关键约定更新（"业务语义映射严禁散落代码，一律登记 semantic.json"）；
- 每期完成跑：`pytest -q` 全绿 + `eval.eval_runner` oracle/离线 agent 双 31/31 + `black`/`ruff` 绿；P1 完成后额外手测一次 Web LLM 路径（prompt 渲染变化）。

## 风险与对策

1. heuristic 表驱动是最高风险点（golden 逐字断言）→ 问法层逻辑不动、只表驱动化"关键词→指标形态"，小步提交 + 全量 golden 断言守护；
2. prompt 渲染变化影响 LLM 路径 → golden/oracle 不经 prompt，离线评测不受影响；P1 后手测 Web；
3. json 直读后配置损坏面扩大 → loader 严格校验 + 缺节报错 + 测试覆盖坏配置路径。
