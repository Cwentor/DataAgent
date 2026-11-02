# 设计方案｜真 xlsx 导出（openpyxl 序列化终点）与 PDF 打印样式（#28）

> - **状态**：v1 实施中（2026-11-02）。
> - **来源**：[Issue #28](https://github.com/Cwentor/DataAgent/issues/28)（真 xlsx 导出与
>   PDF 打印样式替代，范围收窄版）；能力盘点缺口清单（2026-11-01）#28 项。
> - **分支**：`feat/issue28-xlsx-export`。
> - **前置事实**：#22 / #23 / #24 已合入 master（PR #31 / #32 / #33），本项无阻塞依赖。

## 1. 背景与范围

能力盘点确认"导出 Excel"为部分具备，现状是 CSV 伪装 excel（UTF-8 BOM + .csv 扩展名）。
痛点真实且高频，长数字被 Excel 转科学计数且精度丢失（11 位以上显示损毁、15 位以上值损毁）、
前导零丢失、日期变文本、单文件无工作簿语义。导出物真实去向是企业 Excel 工作流
（IM 预览 / 邮件附件 / 二次加工），xlsx 是其母语。

范围（对齐 issue 收窄结论）：

1. 后端真 xlsx 导出（openpyxl），类型保真；
2. `excel` 保留现语义（Excel 兼容 CSV），新增 `xlsx` 枚举，既有调用方零破坏；
3. 前端报告页 PDF 打印样式（浏览器 Ctrl+P 替代后端 PDF）。

明确不做（issue 原文）：后端 PDF 生成（weasyprint / reportlab 均否）、整报告转图片、
超格式上限静默截断。

## 2. 现状核实（代码事实）

| 事实 | 位置 | 对方案的影响 |
|---|---|---|
| 导出格式枚举 `Literal["csv","excel","markdown","json"]`，`excel` 实为 CSV+BOM | `tools/builtins/export_report_tool.py:29`、`_EXT_BY_FORMAT:36` | 新增 `xlsx` 枚举值，`excel` 原语义保留 |
| 脱敏（`_mask_value`）与截断（`limit`，默认 1000 上限 100000）发生在序列化之前 | 同文件 `execute` 第 96~116 行 | xlsx 只是替换最后一段序列化器，治理面零触碰 |
| 序列化统一出口 `_serialize(fmt, columns, rows) -> (bytes, ext)` | 同文件 176 行 | xlsx 接入点唯一，新增一个分支 |
| CSV 公式注入防护为前导空格（`_escape_formula_cell`，连表头都过） | 同文件 209 行 | xlsx 有更优策略（见 D4），CSV 路径不动 |
| 值类型现状：`_json_safe` 把 date/datetime 转 ISO 字符串、Decimal 转 float，int/float 保持原生 | `tools/builtins/_query_core.py:246` | 日期保真必须在序列化器内做保守识别（见 D3），不能指望上游原生类型 |
| 前置复用路径 `ctx.prior` 的 rows 可能经 JSON 往返（全部变 str/float） | `export_report_tool.py:77` | 类型识别只能面向"字符串值"，与上一条同解 |
| 下载端点 media_type 按 MIME 表查扩展名，现无 `.xlsx` | `web/api.py:434`（FastAPI 版）、`web/server.py:53`（stdlib 版） | 两处 MIME 表都要补，漏一处则该端点下载 xlsx 变 `application/octet-stream` |
| openpyxl / xlsxwriter 均不在 requirements-dev.txt，现有 xlsx 能力仅 pandas（默认引擎 openpyxl 未显式声明） | `requirements-dev.txt:7` | 依赖需显式登记（见 D5） |
| 前端报告导出按钮为 Markdown / HTML，无打印样式 | `web/static/index.html:126`、`web/static/js/canvas.js` | 打印样式为纯增量 CSS（见 §4.5） |
| 导出工具功能测试现挂在 `tests/agent/test_tool_agent.py`（CSV 形态） | `tests/agent/test_tool_agent.py:95` | xlsx 测试就近新增，同源同测 |

## 3. 选型对比矩阵

### D1 xlsx 序列化库

| 候选 | 依赖重量 | 写入性能（10 万行量级） | 类型与单元格控制 | 生态与现状 | 结论 |
|---|---|---|---|---|---|
| **openpyxl**（选定） | 纯 Python，零系统依赖，比 pandas 还轻 | 默认模式慢，write_only 模式可接受 | `Cell.data_type` 可逐格强制类型，日期原生支持 | pandas `to_excel` 默认引擎，issue 点名，社区事实标准 | **采用** |
| xlsxwriter | 纯 Python | write_only 性能最优 | 默认字符串即字符串（无公式推断），`write_formula` 才写公式 | 只写不读，项目内无既有使用 | 否决，性能优势在本场景（limit ≤ 10 万行）不构成决策级差距，且引入第二套 API 面增加维护面 |
| pandas `to_excel` 直接产出 | 零新增（pandas 已有） | 一般 | 高层封装，逐格控制需回调 openpyxl，隐式行为多（索引列、类型推断） | 已有 | 否决，类型保真恰恰是本项核心诉求，需要逐格显式控制，绕不开 openpyxl 还多一层封装 |

### D2 openpyxl 写入模式

| 候选 | 内存 | API 复杂度 | 单元格控制 | 结论 |
|---|---|---|---|---|
| 常规模式（`Workbook` + 逐格赋值） | 全量驻留，10 万行 × 30 列约 300 万 Cell 对象，峰值内存 GB 级 | 简单直观 | 完整 | 否决，内存峰值不可控 |
| **write_only 流式模式**（选定） | 恒定低内存，append 行即序列化 | 略异（`append` 行、需 `WriteOnlyCell`） | `WriteOnlyCell` 同样支持 `data_type` 与 number_format | **采用**，上限 10 万行场景下内存确定性优先 |
| 分批多 sheet（每 sheet 上限内切分） | 低 | 高（列对齐与 sheet 命名逻辑） | 完整 | 否决，单表切多 sheet 改变数据语义，超出"诚实导出"范围；超限场景正确动作是报错建议 CSV（见 D6） |

### D3 类型保真策略（本项核心决策）

上游 `_json_safe` 已把日期字符串化、Decimal 浮点化，且 `ctx.prior` 复用路径可能整体 JSON 往返。
因此类型保真只能在序列化器内基于"值形态"做，策略如下：

| 值形态 | 写入策略 | 理由 |
|---|---|---|
| `int` 且绝对值 ≤ 2^53 | 数字单元格 | Excel 数字为 IEEE 754 double，2^53 内整数无损 |
| `int` 且绝对值 > 2^53 | **文本单元格** | 超出 double 精度边界，写数字必丢精度，文本是唯一保真方式（雪花 ID 场景） |
| `float` | 数字单元格 | 原生类型直写 |
| `str` 且严格匹配 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM:SS`（含 T 分隔） | 日期单元格 | 无歧义 ISO 形态才转换；Gmall 时间维度字段均为此形态 |
| 其余 `str`（含前导零、`=` 开头、普通文本） | 文本单元格 | 字符串天然文本，前导零无损 |
| `None` | 空单元格 | 与 CSV 路径空串语义一致 |
| `bool` | 布尔单元格 | openpyxl 原生支持 |

| 候选 | 描述 | 结论 |
|---|---|---|
| **值形态保守识别**（选定） | 上表策略，逐值判定，无歧义 ISO 才转日期 | **采用**，零上游破坏、确定性可测 |
| 上游改造 `_json_safe` 保留原生类型 | date/datetime/Decimal 直达导出层 | 否决，`_json_safe` 服务于 JSON/SSE/缓存全链路，为导出单项改全链路影响面失控 |
| 列级 uniform cast（按列名/抽样推断整列类型） | 一列统一 cast | 否决，混合形态列（如脏数据）会被强行统一，静默改值违反诚实原则 |

### D4 公式注入防护（xlsx 路径）

CSV 路径用前导空格（OWASP CSV Injection 建议）。xlsx 有更优解，openpyxl 对 `=` 开头
字符串会推断 `data_type='f'`（公式），Excel 打开即执行：

| 候选 | 描述 | 结论 |
|---|---|---|
| **`data_type='s'` 强制文本**（选定） | 逐格对字符串值显式置 `data_type='s'`，`=SUM(A1)` 落盘即文本 | **采用**，防护彻底且不污染显示值（无前导空格副作用） |
| 同 CSV 前导空格 | 值前加空格 | 备选兜底，若 openpyxl 版本行为差异导致 `data_type` 置位不生效再启用（测试验证） |
| 黑名单正则替换 | 检测公式特征后删除/转义 | 否决，改值破坏保真，违反诚实原则 |

### D5 依赖策略

| 候选 | 描述 | 结论 |
|---|---|---|
| **requirements-dev.txt 硬登记 + 延迟 import**（选定） | 依赖显式可复现；`import openpyxl` 放在序列化函数内，未安装时仅在真正使用 xlsx 时报错且错误信息给出安装指引 | **采用**（issue 方案 2 默认前者） |
| 可选依赖 try-import 降级 | 未安装则 xlsx 枚举不可用并诚实标注 | 否决为主方案，描述维护成本高于收益；错误信息诚实性已由延迟 import 覆盖 |
| 锁定 pandas 间接依赖（不显式登记） | 靠 pandas 装出的 openpyxl | 否决，隐式依赖不可复现，`requirements-dev.txt` 意义尽失 |

### D6 行数上限处置

双层语义，必须区分：

| 层 | 机制 | 行为 |
|---|---|---|
| 用户预算层 | 工具既有 `limit`（默认 1000，上限 100000） | 维持现状，超限自动截断 + notes 显著提示（既有契约，诚实） |
| 格式硬限层 | openpyxl 单 sheet 上限 1048576 行 | 序列化前防御检查（含表头），超限抛显式错误建议改用 csv，**严禁截断**（issue 验收） |

当前 `limit` 上限 100000 远低于 1048576，硬限检查属防御性（防未来 `limit` 上限调整后
静默超限），成本一行判断，必须保留。

## 4. 方案设计

### 4.1 序列化器（`export_report_tool._serialize` 新增 xlsx 分支）

```python
ExportFormat = Literal["csv", "excel", "xlsx", "markdown", "json"]
_EXT_BY_FORMAT = {"csv": "csv", "excel": "csv", "xlsx": "xlsx", "markdown": "md", "json": "json"}
```

xlsx 分支要点：

1. 延迟 `import openpyxl`（ImportError 时返回诚实错误信息与安装指引）；
2. 行数防御检查（含表头）> 1048576 抛显式错误建议 csv；
3. `Workbook(write_only=True)`，逐行 `append`；
4. 表头走与数据格相同的文本强制（表头是列名，恒为文本）；
5. 逐格类型映射按 D3 表执行；字符串一律显式 `data_type='s'`（D4）；
6. 日期单元格不做时区假设，仅接受无时区 ISO 形态（带时区字符串保持文本，不猜时区）。

### 4.2 契约变更（零破坏）

| 项 | 变更 |
|---|---|
| `ExportFormat` | 增加 `"xlsx"` 枚举值，既有四值语义不变 |
| `excel` 描述 | 更名澄清为"Excel 兼容 CSV（UTF-8 BOM，非真 xlsx）"，引导 LLM 优先选 `xlsx` |
| 工具 description | 补充 xlsx 能力与适用场景（长数字 / 前导零 / 日期保真） |
| 下载端点 | 两处 MIME 表补 `.xlsx -> application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| Content-Disposition | 无需改动（沿用 `filename` 后缀机制） |

### 4.3 脱敏与审计（同源不变）

脱敏（`_mask_value` 掩码为 `***`）与截断（`limit` + notes）逻辑位于序列化之前，
xlsx 路径自动继承，零改动零分叉。测试断言同源（同输入下 csv 与 xlsx 的
`desensitized` / `truncated` / `notes` 完全一致）。

### 4.4 前端 PDF 打印样式

报告页 `@media print` 样式：隐藏侧栏 / 输入区 / 导出按钮 / 交互控件，报告容器全宽，
保留表格边框与标题层级。浏览器 Ctrl+P 即得 PDF（浏览器自带"另存为 PDF"）。
纯 CSS 增量，不新增 JS 逻辑。

### 4.5 测试计划

| 用例组 | 覆盖 |
|---|---|
| 类型保真 | 整数数字格、超 2^53 整数文本格、float 数字格、ISO 日期转日期格、前导零文本格、`None` 空格、`bool` 布尔格 |
| 公式防护 | `=cmd()` / `+1` / `@x` 等 `=` 开头字符串落盘为文本，读回非公式 |
| 脱敏同源 | 同输入 csv 与 xlsx 的脱敏列掩码、`desensitized` 标记、notes 一致 |
| 超限报错 | 伪造行数超 1048576（mock 序列化入参）显式报错且信息含 csv 建议；`limit` 截断语义回归 |
| 契约兼容 | `excel` 输出仍为 BOM CSV（字节级不变）；`xlsx` 枚举可用、扩展名正确 |
| MIME | 下载端点对 `.xlsx` 返回正确 media_type |
| 读回验证 | openpyxl 读回产物校验单元格类型与值（write_only 落盘后读回，不信内存对象） |

## 5. 验收标准对照（issue 原文逐条）

- [ ] 导出 xlsx 为真 Excel 工作簿，长数字 / 前导零 / 日期类型保真 → §4.1 + D3；
- [ ] 脱敏与审计行为与 CSV 路径完全一致（同源同测）→ §4.3；
- [ ] 超 openpyxl 行数上限显式报错，不截断 → D6 格式硬限层；
- [ ] `excel` 既有语义不变（兼容回归），`xlsx` 新枚举可用 → §4.2；
- [ ] （可选）报告页打印样式生效，交互控件不出现在打印输出 → §4.4；
- [ ] 测试覆盖（类型保真、脱敏同源、超限报错、契约兼容）→ §4.5；
- [ ] `black` / `ruff` / `pytest` 全绿 → 收尾门禁。
