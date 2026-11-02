# 设计方案｜图表类型候选集与用户切换控件（2026-11）

对应 issue #24。推荐契约从单结果扩展为候选集，用户可在适配性候选集内切换图表类型，主推荐默认渲染不变，不适配类型不可达。

## 背景与目标

`present/viz.py` 的 `recommend_viz(dsl, columns, rows)` 用确定性规则输出唯一图表类型，前端 `web/static/js/canvas.js` 渲染即定死，用户只能接受系统推荐。规则信号（数值占比 / 时间维度 / 基数 / 正负 / 维度指标数）已足够精细，缺的是把单选二分支改造成逐类型评估，并把选择权在适配范围内交给用户。

架构红线是本设计不能做成自由选型。裸开全类型切换会让不适配数据渲染成误导性视图（如二维透视数据切饼图）。在诚实等于不虚构的原则下，误导性图表是一种虚构，因此切换必须限制在适配性候选集内。

## 链路事实（现状核实补充）

设计前全链路核实，两个 issue 正文未覆盖的事实决定实施范围。

1. 前端图表 Tab 的 ECharts 产物唯一来源是编排链路沙箱 Coder 的 `save_echarts_spec`（`core/orchestrator/nodes.py` 分析节点，LLM 自绘、数据自包含）。`query_metric_tool` 与 `trend_analysis_tool` 虽输出 `viz` 与 `chart_spec`，但前端不消费这两个字段。
2. `viz_config` 输出的 ECharts option 数据外置（series 仅声明 encode 映射，数据由 columns/rows 独立传递），而 `canvas.js` 渲染 `artifact.content` 时不注入 dataset。所以推荐图即使发射也画不出来，发射点必须保证数据自包含。

推论：仅改 `present/viz.py` 与 `canvas.js` 不产生用户可见变化（死代码），必须在编排链路取数后发射一份带候选集的推荐图表 artifact 闭环才成立。

## 与 issue 的范围偏差及理由

issue 自述改动面集中在 `present/viz.py` 与 `web/static/js/canvas.js`，不碰编排与执行管道。本设计在编排取数节点 `dsl_query_node` 取数成功后新增一处 artifact 发射。这是对字面范围的必要扩充。

理由：不发射则候选集契约没有任何前端可见的消费点，验收标准中"前端可在候选集内切换图表类型"无法成立。发射动作是既有 `artifact_emit` 事件机制的一次调用，不触碰编排状态机、节点边、执行管道与治理面。沙箱 Coder 图不受影响（无推荐上下文，不加候选集，控件不出现）。

## 方案

### 1 候选集契约（present/viz.py）

新增逐类型适配评估，对 number / line / bar / pie / pivot / table 六类型逐一判定，复用既有信号，不需要 LLM，推荐保持确定性。

- `number` 适配条件为无维度、单指标、行数不超过 1（单值卡片）；
- `line` 适配条件为维度含时间字段；无时间维度排除，排除原因为缺少时间维度；
- `bar` 适配条件为有维度有指标且 y 列数值占比不低于 0.5；
- `pie` 适配条件为单维度单指标、基数不超过 8、y 列存在正数；基数超限、全负或零值、数值占比不足均排除并给出原因（占比语义在全负或零值上误导）；
- `pivot` 适配条件为多维或多指标；
- `table` 永远可用（兜底），y 列数值占比低于 0.5 时升级为主推荐（拒绝在非数值列上画图）。

契约结构：`viz_config` 新增 `candidates` 字段，既有 `chart / x / y / echarts` 不动（本文件已有新增 echarts 字段为增量的兼容先例）。

```json
{
  "chart": "line", "x": "order_time", "y": "gmv",
  "echarts": { },
  "candidates": [
    {"chart": "line", "status": "recommended", "reason": "时间维度优先折线", "echarts": { }},
    {"chart": "bar", "status": "available", "reason": "适配", "echarts": { }},
    {"chart": "pie", "status": "excluded", "reason": "时间维度上饼图丢失时序", "echarts": null}
  ]
}
```

要点：

- 主推荐 status 为 recommended 且居首，与 `chart` 字段一致（单一事实源，`recommend_viz` 重构为取评估首位，规则不漂移，返回值仍为 str）；
- 可用候选 status 为 available，按适配度紧跟主推荐，前端控件渲染 recommended 与 available 全部条目；
- 不适配类型 status 为 excluded，带排除原因（供评测断言与透明度），前端控件不渲染（候选外类型不可达，不以灰置或报错方式呈现）；
- 每个非 excluded 候选携带预生成的 `echarts` option（`_echarts_option` 已按 chart 参数化，直接复用）。option 仅结构无数据，六份约 3KB，前端切换零请求。

### 2 透传（ChartSpec 与 tool 层）

`ChartSpec` 新增 `candidates` 字段，`to_dict` 序列化（`include_data=False` 时也带，切换控件需要）。`query_metric_tool` 与 `trend_analysis_tool` 零改动，返回值自动携带。旧链路 `AgentResult.viz / chart_spec` 经 `tool_agent` 回填机制自动透出。

### 3 编排链路发射点（dsl_query_node）

取数成功后对数据集构造推荐图表 artifact：

- 用数据集溯源 DSL（`ParquetRef.dsl`）与列结构调 `viz_config`，信号评估基于 Parquet 预览切片（复用 `_preview_rows`，图表行上限 200，缓存机制支持更大 limit 复用）；
- 发射条件为 DSL 含聚合指标且执行返回行数不超过 200。聚合组数天然有界；明细查询本就推荐 table 不发射；超限不发射不截断（截断图表失真即误导，违背诚实原则）；
- artifact 结构与沙箱图同构自包含：`{type: "echarts", title, content: 主推荐option(含dataset数据), candidates: [...每候选option同样自包含...]}`，后端注入 `option.dataset = {source: rows}` 后前端渲染零逻辑；
- 候选集无非表格图形类（仅 pivot/table 可用）时不发射，保持现状；
- 复用 T14 同规格去重（`_has_same_echarts`），反射重规划多轮执行不重复发射。

### 4 前端受控切换（canvas.js）

- 图表块渲染时若 `artifact.candidates` 中 recommended 与 available 条目不少于 2，在导出行渲染类型按钮组（柱状图 / 折线图 / 饼图 / 数值卡 / 透视表 / 明细表中实际可用的子集）；
- 点击切换即 `setOption(对应候选echarts, notMerge=true)` 重绘，实例复用不重建，数据与口径不变（候选 option 均由同一份 columns/rows 生成）；
- 默认渲染仍是主推荐（`artifact.content`）；无 candidates 或可用项不足 2 时控件不渲染，现状不变；
- 候选集之外的类型不出现在控件中；PNG 导出保留，导出当前显示的类型。

## 选型对比矩阵

### 决策点 1 候选 option 生成时机

| 候选 | 说明 | 结论 |
|---|---|---|
| A 契约内预生成全部候选 option | 前端零请求零构造逻辑，后端单点确定性；体积约 +3KB | 采用 |
| B 前端按类型本地重写 option | 契约最小，但前端复刻六类型构造逻辑，双实现漂移风险 | 否决 |
| C 新增切换 API 按需重算 | 无冗余体积，但多一次往返，新端点需传递 dsl 与 rows，会话状态复杂化 | 否决 |

### 决策点 2 候选集到前端的通路

| 候选 | 说明 | 结论 |
|---|---|---|
| A 编排取数后发射推荐图表 artifact | 完整闭环用户立即可见可切换；新增一处发射调用 | 采用 |
| B 仅改契约与前端能力 | 不碰编排，但无消费点，功能不可演示 | 否决（死代码） |
| C 沙箱图 payload 附加候选集 | 不新增发射，但沙箱图是 LLM 自定义归因图，无推荐上下文，语义错位 | 否决 |

### 决策点 3 图表数据有界性

| 候选 | 说明 | 结论 |
|---|---|---|
| A 仅聚合类 DSL 发射，行数取执行返回行数 | 聚合组数有界，明细不画图，不截断不失真 | 采用 |
| B 全类型发射并按上限截断 | 截断图表失真即误导，违背诚实原则 | 否决 |
| C 图表数据独立懒加载 | 体积最小，但新增端点与前端异步逻辑，改动面膨胀 | 否决 |

### 决策点 4 前端控件形态

| 候选 | 说明 | 结论 |
|---|---|---|
| A 文字按钮组（沿用 mini-btn 样式） | 可用候选一般 2 到 4 个，平铺最直白，样式零新增 | 采用 |
| B 下拉 select | 占位小但多一次点击，可用项少时收益为负 | 否决 |
| C 图标 tab | 需引入图标资源，与现有工作台风格不一致 | 否决 |

## 明确不做

- 自由选型（用户越过适配性检查）；
- LLM 参与推荐（保持确定性规则）；
- 改变主推荐口径（candidates 仅为增量，默认渲染仍是主推荐）；
- 沙箱 Coder 图加候选集（无推荐上下文）；
- 明细查询发射图表、超限截断画图。

## 验收标准

- 每个 `viz_config` 输出包含按适配度排序的候选类型列表，主推荐居首且与 `chart` 一致；
- 前端可在候选集内切换图表类型，切换后数据与口径不变；
- 不适配类型不出现在控件中；
- 既有契约（chart / x / y / echarts）不破坏，评测与快照层无回归；
- 测试覆盖候选集生成、适配信号、排除原因、契约兼容、发射条件与去重；
- black / ruff / pytest 全绿。

## 实施顺序

1. `present/viz.py` 候选集契约与 `ChartSpec` 透传；
2. `tests/present` 候选集测试；
3. `core/orchestrator/nodes.py` 发射点与编排测试；
4. `web/static/js/canvas.js` 切换控件；
5. 全量检查、设计文档索引登记、提交。
