# 评审报告｜方案评审：语义层去耦与评测分层三期规格（2026-10）

- 评审对象：`docs/plans/2026-10-07-semantic-decoupling-spec.md`（P0 评测分层 / P1 业务映射收编 / P2 单一事实源）
- 评审基线：branch `feature/wip` @ f144c5e
- 评审日期：2026-10-07
- 总结论：**通过（含 1 项 MEDIUM 偏差 + 3 项 LOW 偏差需收口）**

## 1. 时序说明

规格落盘于 f144c5e，而其大部分条目已在更早的提交中实现（e147927 json v2 全节登记、
ec1b72a agent 侧收编、62bc4d1 orchestrator 侧收编、9dca25c 评测分层、6b2a24e 测试适配）。
本评审因此按"规格是否忠实描述已落地代码 + 未兑现清单"双向核查，并复跑全部验收门禁。

## 2. 验收门禁复跑结果（2026-10-07，环境 futurebi）

| 门禁 | 结果 |
|---|---|
| `pytest -q` | **1000 passed, 2 skipped**（4 分 10 秒） |
| `eval.eval_runner`（oracle） | **31/31 PASS** |
| `eval.eval_runner --pipeline agent`（离线） | **31/31 PASS** |
| `black --check .` / `ruff check .` | 全绿（201 文件） |
| `python -m semantic.catalog_loader` 必要节校验 | metrics/paid_filter/default_window/reflector_concepts/out_of_scope_concepts/undefined_metrics 六节均在 |

## 3. 逐项兑现核查（12 项）

| # | 规格条目 | 状态 | 证据 |
|---|---|---|---|
| 1 | eval_runner 断言分层 + `--skip-snapshot` | ✅ | eval/eval_runner.py:107-109/161-166/391-395 |
| 2 | intent_golden 省份 8→34、"数码"换实际成员 | ✅ | intent_golden.json:5（34）；"数码"全仓零命中 |
| 3 | tool_agent 数据域读 settings.DATA_DOMAIN_END | ✅ | agent/tool_agent.py:62 |
| 4 | catalog.py import 时 json 直读 + 缺节快速失败 | ✅ | semantic/catalog.py:34-57 |
| 5 | heuristic 表驱动 / glossary 适配层 / memory 词表删 / labels 派生 | ✅ | heuristic.py:267-293、glossary.py:1-6、labels.py:13-43 |
| 6 | agent/lexicon.py 问法词表单源化 | ✅ | lexicon.py:8-31，memory/tool_agent 双方 import |
| 7 | stale 值绑定修复（_DIAGNOSTIC_DIM_POOL / sql_lift._MAIN_TABLE） | ✅ | nodes.py:110-123、sql_lift.py:35-37 |
| 8 | nodes 支付口径/反思概念/标量锚表收编 | ✅ | nodes.py:1027-1030/2003-2054/1021-1024 |
| 9 | boundary_eval 入 nightly CI | ✅ | .github/workflows/ci.yml（cron `17 4 * * *`） |
| 10 | 测试桩值去 pin（grounding/水印/RLS 成员） | ✅ | tests/ 中字面值零命中，均改动态构造 |
| 11 | REGIONS 兼容别名删除 + refresh 覆写下钻池 | ✅ | heuristic.py:49-53、catalog_loader.py:492-495 |
| 12 | 双模式 golden 31/31 | ✅ | 本评审复跑通过（见 §2） |

## 4. 偏差清单（需收口）

### 4.1 [MEDIUM] 两期诊断缺省窗口仍硬编码，json 事实无消费方

`config/semantic.json` 的 `default_window` 已含 `start: 2024-05-01` / `end: 2024-05-15` +
`two_period_midpoint: true`，但 `core/orchestrator/nodes.py:976-977` 的基期/现期切分
仍硬编码 `{"start": "2024-05-01", "end": "2024-05-08"}` / `{"start": "2024-05-08", "end": "2024-05-15"}`。
即切分结果可从 json 推导（中点 = 2024-05-08），消费方却不读——运行时改 json 缺省窗口
会使 scalar 兜底路径（nodes.py:321、:2261 已动态）与两期诊断路径口径漂移。这正是规格
M-P1 表格宣称消灭的"nodes 四处 2024-05-01 字面量"中未兑现的两处。
**建议**：改为从 `catalog.DEFAULT_WINDOW` 按中点切分推导（`two_period_midpoint` 语义已在
json 中声明），一行推导替换两处字面量。

### 4.2 [LOW] `_SCALAR_FIELD_AGG` 未迁 json（实现有意保留，规格未声明豁免）

`core/orchestrator/nodes.py:1009-1016` 保留手写映射，注释给出理由：order_id 在标量兜底
语义下用 `count` 而非 metrics 中的 `count_distinct`，两者语义不同、不可从 metrics shape
派生。理由成立，但规格 2.2 写的是"`_SCALAR_FIELD_AGG` → json"，二者矛盾。
**建议**：在规格 2.2 该行补注豁免理由（或在 json 增 `scalar_agg` 独立节），使规格与代码
互证一致。

### 4.3 [LOW] `intent.py` price 特判未去

规格 2.2："intent.capability_catalog_lines 从 json 派生（去 price 特判）"。实际
`core/orchestrator/intent.py:226/229` 仍以字面量 `"price"` 做独立"其他"分组，docstring
:216 自述保留（对齐原 unit_price 设计）。派生本身已做，仅该特判与规格矛盾。
**建议**：与 4.2 同策略——要么真去掉（如按 dtype + label 缺失判定归组），要么修订规格。

### 4.4 [LOW] `reset_defaults` 回放快照而非重读 json

规格 M-P3（P2）："reset_defaults 重读 json"。实际 `semantic/catalog_loader.py:514-549`
回放 import 时快照（`_DEFAULT_COLUMNS = dict(catalog.COLUMNS)` 等）。快照源是 json 直读，
故符合"删除手写双源"的精神，但运行中编辑 json 后 reset 不会感知新值，与字面承诺不符。
**建议**：让 `reset_defaults` 复用 `_load_builtin()` 重读 json（改动约一行），或在规格中
改述为"回放 import 时快照"。

## 5. 规格外发现

1. [INFO] `core/retrieval/sql_lift.py:78` `_COLUMN_INDEX = _build_column_index()` 仍为
   import 时值绑定——与本规格修复的 stale 隐患同类。因索引内容是纯 schema 映射（非业务
   事实），refresh 场景影响低，暂可不动，建议登记观察。
2. [INFO] intent_golden.json 的 34/TCL 为静态 pin，但均为数仓实际成员（本评审实测：
   base_province 34 省、base_trademark 含 TCL），且 intent_eval 混排用例已做动态断言
   （intent_eval.py:103-104）。JSON golden 天然静态，属可接受形态。
3. [PENDING] 规格 §4 "P1 完成后额外手测一次 Web LLM 路径（prompt 渲染变化）"——本次
   静态评审无法验证，prompts.py `_CONVENTIONS` 渲染已改，该手测仍欠一次，建议执行后在此
   登记。

## 6. 规格文本质量点评

- §0 探查结论逐条经代码核实为准确（result_hash 无消费者、intent_golden 旧基数、
  tool_agent 日期漂移、labels 10 处漂移、_TREND_KEYWORDS 18/19 词漂移均已按描述修复）；
- 三期划分与守护网设计（golden 逐字断言 + 小步提交 + 分层断言）合理，风险对策 §5 与
  实际风险匹配；
- 备选路线（catalog_gen 代码生成）的否决理由成立；
- 唯一结构性弱点：规格将"已实现内容"与"计划"混写而未标注各条目兑现状态，后续读者
  难以区分"待办"与"已完成"，建议按 §4 偏差收口时同步补一节"兑现状态表"（可直接复用
  本报告 §3）。

## 7. 处置建议汇总

| 优先级 | 事项 |
|---|---|
| MEDIUM | nodes.py:976-977 两期诊断窗口改从 catalog.DEFAULT_WINDOW 中点切分推导 |
| LOW | 规格 2.2 补 `_SCALAR_FIELD_AGG` 与 price 特判的豁免声明或真移除 |
| LOW | reset_defaults 改重读 json（或修订规格表述） |
| PENDING | 执行 Web LLM 路径手测并登记；sql_lift._COLUMN_INDEX 登记观察 |

## 8. 收口记录（2026-10-07 同日）

| 事项 | 处置结果 |
|---|---|
| §4.1 两期诊断窗口硬编码（MEDIUM） | 已修：`_diagnostic_dsl_pair` 从 `catalog.DEFAULT_WINDOW` 按 `two_period_midpoint` 中点推导（未声明标记时两期共用整窗）；新增 `test_diagnostic_dsl_pair_default_window_from_catalog` |
| §4.2 `_SCALAR_FIELD_AGG` 豁免 | 规格已补豁免声明（语义不同不可从 metrics shape 派生，代码内注释为准） |
| §4.3 price 特判 | 已修：semantic.json `fields[].non_aggregatable` 登记（price=true），`FieldMeta` 增 `non_aggregatable` 位，intent.py 按 meta 派生分组；`test_capability_catalog_groups_price_separately` 增加单一事实源断言 |
| §4.4 reset_defaults | 已修：`semantic/catalog.py` 新增 `apply_builtin()` 单一派生路径（import 初始化与 reset 共用；容器原地 clear/update 保对象身份，import 绑定消费方不 stale），`reset_defaults` 重读 json；新增 `test_reset_defaults_rereads_json` |
| §5.3 Web LLM 路径手测 | 未执行（需真实供应商），仍欠 |
| §5.1 _COLUMN_INDEX | 已在 sql_lift.py 注明约束（纯 schema 索引可 import 绑定；引入业务语义须改动态读） |

同步更新：规格 §2.2 豁免声明 + 新增 §6 兑现状态节。
