# 设计规格｜语义层去耦与评测分层改造（P0→P1→P2 三期，2026-10）

> 背景：Gmall 数仓全量替换波及 66 个文件，暴露"元数据泄漏"（业务映射散落 agent/core 代码）
> 与"断言金字塔倒置"（数据快照断言混入契约测试）两大结构性问题。
> 本方案确立 semantic.json 为唯一静态业务事实源、评测断言分层，并把约 40 处散落映射收编回语义层。

## 0. 探查结论（方案依据）

1. **评测耦合面修正**：`eval/eval_runner.py` 的 `result_hash` 全仓无消费者（纯观测输出）；
   结果断言是"golden SQL vs 编译 SQL 同库差分"——**改数据值不会让 golden 失败，改 schema/语义目录
   才会 31 条雪崩**。真正的数据 pin 灾难点在测试夹具层：44.09 万 / 115.69 万（grounding 桩值）、
   "查询答案：34"（2 处）、"2025-12-31" 超界水印（2 处）、`{"广东"}`（RLS 视图）、"TCL"/"数码"
   （intent_golden 成员枚举）。
2. **活缺陷 3 个**：
   - `eval/intent_golden.json` 省份计数钉死 `8`（Gmall 重建前旧基数，实际 34）；
   - `agent/tool_agent.py` 数据域提示写死 `2024-06-30`（与 `settings.DATA_DOMAIN_END=2025-12-31` 漂移）；
   - `refresh_catalog` 不覆写 `DRILLDOWN_DIM_FIELDS` / `DIMENSION_MEMBER_FIELDS` 全局，
     且 `core/orchestrator/nodes.py:81`、`core/retrieval/sql_lift.py:34` 的 import 时值绑定
     使 refresh 后 stale。
3. **P2 可行性实测**：`config/semantic.json` 与 `semantic/catalog.py` 内置目录在
   fields/labels/aliases/joins/query_domains 各节**逐字相等**（机器比对零差异）——json 已具备
   唯一事实源能力，缺 table_labels / 维度成员种子 / 下钻池 / 大区映射等 5 个小节。
4. `present/labels.py` 与 json label 存在 10 处文案漂移；`memory.py` 与 `tool_agent.py` 的
   `_TREND_KEYWORDS` 同义词表已漂移（18 vs 19 词）。
5. **L1 契约测试零数据依赖可行**：`compile_sql` 只依赖静态 catalog（无需建库）；
   `tests/test_agent.py` 已有"golden 全量 DSL 逐字断言"现成骨架。

## 1. M-P0 评测分层 + 数据 pin 清理（含活缺陷修复）

1. **eval_runner 断言分层**：`CaseReport` 拆 `contract_ok`（dsl_ok + sql_ok，零数据依赖）与
   `snapshot_ok`（result_ok，依赖数仓快照）；汇总分节报告；CLI 加 `--skip-snapshot`
   （快速契约回归）；退出码语义不变（任一失败即非零）；`tests/test_eval.py` 同步。
2. **intent_golden 活缺陷**：省份计数 `8` → 实际 34；`list_answer` 的 `"数码"`
   （新类目域不存在）→ 换实际成员。
3. **测试桩值去 pin 化**（保断言强度、消灭字面值）：
   - grounding 桩值（44.09 万 / 115.69 万）→ 测试内先查库取该窗口真值再注入 stub 报告；
   - "查询答案：34" → `len(catalog.DIMENSION_MEMBERS["province"])` 动态拼接；
   - "2025-12-31" 超界水印 → `settings.DATA_DOMAIN_END` 动态；
   - `{"广东"}` → `PRINCIPAL_ATTRS ∩ 库内省份` 动态计算；
   - intent_golden 的成员枚举 pin → 实际维度成员。
4. `tool_agent.py` 数据域提示 → 改读 `settings.DATA_DOMAIN_END`。
5. `boundary_eval` 加入 nightly CI 步骤（当前不在任何 CI）。

## 2. M-P1 业务映射收编（约 40 处 → semantic.json）

### 2.1 semantic.json v2 新增节（catalog_loader 解析，refresh/reset 同步覆写全局）

| 节 | 内容 | 替代的代码硬编码 |
|---|---|---|
| `metrics[]` | key/title/aliases/definition/formula/fields/**shape**（DSL 产出形态：aggregate 的 field/agg/alias；ratio 分子分母；expression） | glossary 公式 + heuristic._metrics 关键词分支 |
| `count_entities[]` | "多少订单/用户/商品"实体词→{field, agg, alias} | heuristic._count_entity_metric |
| `paid_filter` | 成功口径 field/value/中文文案/反义问法 | nodes 五处 '1002'、heuristic"成功/成交"、prompts 支付口径 |
| `default_window` | 缺省分析窗口规则（trailing 天数），两期诊断按中点切分 | nodes 四处 2024-05-01 字面量 |
| `reflector_concepts` | 概念→{fields, produced_aliases} | nodes._REFLECTOR_CONCEPT_FIELDS/_ALIASES |
| `out_of_scope_concepts[]` | 数仓未采集概念 | nodes._REFLECTOR_OUT_OF_SCOPE_TERMS |
| `undefined_metrics[]` | 明确未定义指标清单 | clarify._UNDEFINED_METRICS |
| `value_labels` | 枚举值→中文（order_status 1001-1006 等） | present.VALUE_LABELS |
| `table_labels` | 表→中文标签 | catalog.TABLE_LABELS |
| `drilldown_dim_fields` | 下钻候选池 | catalog.DRILLDOWN_DIM_FIELDS |
| `region_province_mapping` | 大区→省份 | catalog.REGION_PROVINCE_MAPPING |
| `dimension_members_seed` | 成员词表离线回退 | catalog.DIMENSION_MEMBERS 内置值 |
| `metric_aliases` | 聚合产物别名约定（gmv/orders/buyers） | nodes._METRIC_LABELS 部分 |

### 2.2 代码侧改造

- `heuristic.py`：`_metrics` 改**表驱动**（遍历 metrics[].aliases 匹配 → 按 shape 产 DSL；
  流量域/计数实体从 json 读）；`_DIM_KEYWORDS` 改读 catalog aliases；`_HAVING_FIELD_ALIAS`
  从 metrics 派生；窗口/TopN/时间主轴等**问法层逻辑保留**；
- `nodes.py`：`_SCALAR_FIELD_AGG` → json；`_SCALAR_ANCHOR_TABLE` 删除（读 catalog）；
  `_REFLECTOR_*` 三常量 → json；`_DIMENSION_LABELS` → catalog label；缺省窗口字面量 →
  `default_window` 推导；'1002' 五处 → `paid_filter`；
- `glossary.py` 变适配层（GLOSSARY/METRIC_TERMS 从 catalog.METRICS 派生，保留
  `scoped_glossary`；删死常量 OPERATOR_TERMS）；
- `prompts.py`：`_CONVENTIONS` 业务条目（指标映射/大区展开/支付口径/维度词）从 json 渲染；
  结构性条目（时间语法/窗口/TopN/排序）保留模板；白名单注入升级为"字段： 中文 label"；
- `memory.py`：`_DIMENSION_SYNONYMS`/`_expand_dimension_fallback` 删除改读 aliases；
- `labels.py`：FIELD_LABELS 从 catalog label 派生（10 处文案漂移以 json 为准）、
  VALUE_LABELS 从 json；
- 问法层词表单源化：memory/tool_agent 重复的趋势/时间等词表合并到 `agent/lexicon.py`
  （独立于 semantic.json——问法层非业务事实）；
- `intent.capability_catalog_lines` 从 json 派生（去 price 特判）；
- 修 import 值绑定 stale：`nodes._DIAGNOSTIC_DIM_POOL`、`sql_lift._MAIN_TABLE` 改函数内动态读。

### 2.3 守护网

golden 31/31（oracle + 离线 agent）+ `test_agent.py` 的 heuristic 全量 golden 逐字断言 +
全量 pytest；heuristic 表驱动改造分小步提交。

## 3. M-P2 单一事实源（json 直读路线）

- semantic.json 补齐后成为超集：`semantic/catalog.py` 内置常量改为 **import 时从 json 加载**
  （严格校验必要节，缺节报错），catalog.py 缩为加载器 + 访问层；`reset_defaults` 重读 json；
  删除 `_DEFAULT_*` 手写双源快照；
- `refresh_catalog` 语义不变（运行时仍从库 distinct 重建成员词表，`dimension_members_seed`
  仅作离线回退）；`rebuild_golden` 行为不变（内置词表 = json 词表）；
- 备选路线（未采纳）：`catalog_gen.py` 代码生成 + pre-commit 钩子——达成同一 SSOT 目标，
  多一条生成链路维护；
- 删除 `heuristic.REGIONS` 兼容别名，消费方直读 catalog。

## 4. 文档与回归

- AGENTS.md 关键约定更新："业务语义映射严禁散落代码，一律登记 semantic.json"；
- 每期完成跑：`pytest -q` 全绿 + `eval.eval_runner` oracle/离线 agent 双 31/31 +
  `black --check` / `ruff check` 绿；P1 完成后额外手测一次 Web LLM 路径（prompt 渲染变化）。

## 5. 风险与对策

1. heuristic 表驱动是最高风险点（golden 逐字断言）→ 问法层逻辑不动、只表驱动化
   "关键词→指标形态"，小步提交 + 全量 golden 断言守护；
2. prompt 渲染变化影响 LLM 路径 → golden/oracle 不经 prompt，离线评测不受影响；P1 后手测 Web；
3. json 直读后配置损坏面扩大 → loader 严格校验 + 缺节报错 + 测试覆盖坏配置路径。
