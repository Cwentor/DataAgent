# 设计文档｜枚举直答体验修复 + 编排审计闭环 + 握手重试落地（2026-10）

## 背景与动机

本轮立项源于对编排链路四条独立问题的排查与收口，四点相互无依赖，合并为一份设计文档分章固化。

**P1（正确性问题，最高优先级）**：`_analysis_material`（`core/orchestrator/nodes.py:2844`）的 `preview[:20]` + 3000 字符双重钳制是为"分析题"设计的成本护栏。但枚举题的答案就是行清单本身——"列举全部省份"只能答出 20/34，用户拿到系统性残缺的答案。关键发现：ENUMERATION 意图**已经**预路由到确定性规划（`nodes.py:247` 构造"纯维度投影直答"DAG），但 s2 synthesize 步骤仍走 LLM 叙述，素材被同一截断线误伤。

**P2（诚实透明收尾）**：`_degradation_banner`（`nodes.py:2988`）同一条"LLM 规划暂不可用"水印覆盖了两种语义完全不同的情形——设计内的确定性枚举预路由（LLM 根本没被调用）和真·LLM 故障降级（`planner_llm_error` 非空带原因）。前者让用户误以为系统坏了，后者才是故障告警。

**P3（可观测性/合规缺口）**：审计写入只挂在 `/api/query` 查询管道（`web/service.py:619`），编排器路由 `/api/agent/run`（`web/api.py:512`）完全不落 `audit.jsonl`——多轮对话（产品主链路）零审计留痕。`planner_llm_error` 只存于内存态、`_llm_json` 的降级 warning 只进 stderr，服务进程一关日志即失，出问题既查不到也复不了盘。

**P4（既有待审件）**：`handshake-retry-design`（spec `docs/superpowers/specs/2026-10-05-llm-handshake-retry-design.md` 已提交）一直待审。中转站瞬时抖动（60s 挂起与 0.8s 返回交替）仍靠"降级水印"兜着，该项落地后能自愈一大半真·LLM 故障场景。

## 范围与优先级

| 编号 | 主题 | 类型 | 改动面 | 优先级依据 |
|---|---|---|---|---|
| P1 | 枚举题全量直答（截断分流） | 正确性 | `nodes.py` 综合层出口 | 唯一影响答案正确性，用户可见度最高 |
| P2 | 降级水印文案分流 | 诚实透明 | `_degradation_banner` 一行文案级 | 收益/成本比最高，与 P1 同批落地 |
| P3 | 编排链路审计闭环 | 可观测性/合规 | `audit/store.py` + `web/api.py` + 日志 | 独立于前两者，涉及面稍宽 |
| P4 | 握手重试落地 | 既有 spec 实施 | providers 网关 | 走既有流程，本轮推进过审 + 实施 |

P1+P2 合成"枚举直答体验修复"小期（同属枚举体验，改动同在 `nodes.py` 综合层出口）；P3 独立立项；P4 走既有 spec 实施流程、不重复设计。

## P1：枚举题全量直答（截断分流）

### 问题

ENUMERATION 意图已预路由到确定性规划（`nodes.py:247-261` 构造 s1=query + s2=synthesize 两步 DAG），但 s2 synthesize 步骤仍走 LLM 叙述路径，LLM 拿到的素材由 `_analysis_material` 构造，其中数据集预览被 `preview[:20]` + `rows_desc[:3000]` 双重钳制。枚举题答案=行清单本身，被同一截断线误伤，"列举全部省份"只能答出 20/34。

### 方案与取舍

**候选①：按行数分流**（结果集 ≤ 某阈值如 50 行时预览全量，超限时维持截断）
- 优点：通用兜底，不限于枚举意图
- 缺点：阈值是魔法数字，需与 LIMIT 硬上限协同；不解决"枚举题必须全量"的语义需求；为非枚举意图引入不必要的全量预览成本
- **裁决：YAGNI 砍掉**——不为非枚举意图引入魔法数字阈值

**候选②：枚举类意图综合层直接确定性渲染行清单，不过 LLM 叙述**（推荐）
- 优点：语义精确，与现有 CARDINALITY 确定性直答同构（CARDINALITY s2 即"直接报告计数结果"）；纯名称清单无数值，绕过 grounding 口径风险低；零 LLM 调用，无截断无成本
- 缺点：绕过 LLM 叙述，需明确它不经 grounding 的口径
- **裁决：采用**

### 实现设计

ENUMERATION 路径的 s2 synthesize 不走 LLM 叙述，直接确定性渲染维度取值清单。实现要点：

- 判定锚点：`state.answered_by` 为枚举直答标识（或 state 携带 ENUMERATION 预路由标记），且 `planner_llm_error` 为 None（非 LLM 故障路径）
- 渲染内容：维度取值行清单（纯名称，无数值），格式与现有确定性报告的行清单一致
- 与 LIMIT 硬上限协同：全量预览的字符上限仍需独立钳制（防止极端宽行撑爆报告），但行数不截断
- 不改动 `_analysis_material` 的截断逻辑本身（该逻辑服务于分析题成本护栏，保留）

### 风险与注意点

- **grounding 口径**：确定性渲染绕过 LLM grounding，但纯名称清单无数值溯源需求，风险低；在报告头部如实标注"确定性路径直答"（与 P2 水印联动）
- **golden 快照**：枚举类意图评测用例（34 行全量断言）受影响，需重固化 golden（`eval/rebuild_golden.py`）
- **互斥组合**：ENUMERATION 直答与 comparison/top_n/fill_gaps/window 的互斥由编译器显式抛 `CompileError`，枚举直答只走纯维度投影，不触及互斥组合

## P2：降级水印文案分流

### 问题

`_degradation_banner`（`nodes.py:2988`）已区分 `degraded_confirmed`/`degraded_auto`/`heuristic` 三种降级形态，且 `planner_llm_error`（`nodes.py:3010`）会透出失败原因。但缺"枚举类预路由（LLM 根本没被调用）"这第四条中性路径，它与真·LLM 故障共用一条"LLM 规划暂不可用"水印，让用户误以为系统坏了。

### 方案与取舍

**方案：新增第四条分支**——ENUMERATION 预路由直答路径下，`planner_llm_error` 为 None 且未走 LLM 综合时，改用中性文案。

- 判定条件：`answered_by` 为枚举直答标识 + `planner_llm_error` 为 None（LLM 未被调用，非故障路径）
- 文案（中性，区别于故障告警）："枚举类问题按确定性路径直答，未调用 LLM 规划"
- 文案与 P1 联动：P1 走确定性渲染行清单，P2 据此给出中性水印，二者口径一致

**取舍**：不引入复杂分类体系，只区分"LLM 被调用但失败"vs"LLM 根本没被调用（预路由）"两个本质语义。这是对"假设透明"原则的直接补齐，符合项目已确立的"诚实 = 分级透明作答"口径。

### 实现设计

在 `_degradation_banner` 的分支判定中新增 ENUMERATION 预路由直答分支（位于 `degraded_auto` 之后、`heuristic` 之前或之后，视 `answered_by` 取值顺序而定）。该分支返回中性引用块文案，不带"暂不可用"故障语义。

### 风险

- 文案必须与真·LLM 故障降级（`planner_llm_error` 非空）视觉可区分，避免用户误判
- 测试断言：新增枚举预路由路径的水印文案双路径断言（枚举直答中性文案 vs LLM 故障告警文案）

## P3：编排链路审计闭环

### 问题

审计写入只挂在 `/api/query` 查询管道（`web/service.py:619` 的 `store.write(record)`），编排器路由 `/api/agent/run`（`web/api.py:512`）全程无 `AuditStore` 写入。多轮对话（产品主链路）零审计留痕。`planner_llm_error` 只存于内存态（`state.py:220`）、`_llm_json` 的降级 warning 只进 stderr（`audit/logging.py:81` 仅 `StreamHandler(sys.stderr)`），服务进程一关日志即失。

### 方案与取舍

**候选①：编排路由复用 AuditStore + 扩展 `audit_log` 字段**（推荐）
- 优点：与查询管道审计同构，可统一追溯；`_migrate_schema` 幂等迁移机制现成（`store.py:164` 逐列对比 `information_schema` 后 `ALTER` 补齐）；`_CrossProcessLock` 跨进程串行化现成
- 缺点：需扩展表结构（新字段 + 迁移）
- **裁决：采用**

**候选②：编排路由走独立审计通道（不扩展 `audit_log`）**
- 优点：不动现有表
- 缺点：审计分散，无法与查询管道统一查询聚合
- **裁决：YAGNI 砍掉**

### 实现设计

**审计表扩展**（`audit/store.py`）：

`audit_log` 新增编排特有字段，统一在 `_AUDIT_COLUMN_TYPES` / `_AUDIT_DDL` / `_INSERT_SQL` 三处同步，迁移由 `_migrate_schema` 自动补齐：

| 新字段 | 类型 | 语义 |
|---|---|---|
| `answered_by` | VARCHAR | 编排裁决方（枚举直答/CARDINALITY 直答/heuristic/degraded_auto/degraded_confirmed 等） |
| `planner_llm_error` | VARCHAR | LLM 规划失败原因（None = 非 LLM 故障路径） |

同步更新点：`_AUDIT_COLUMN_TYPES` 加两列 → `_AUDIT_DDL` 加两列 → `_INSERT_SQL` 加两个 `?` 占位符 → `_insert_db` 参数列表加两个值。`_migrate_schema` 自动迁移旧表（幂等，不丢历史数据）。`tests/test_audit.py` 的一致性断言（`_AUDIT_COLUMN_TYPES` 与 DDL 列清单一致）需同步。

**编排路由接入**（`web/api.py:512` `/api/agent/run`）：

参照查询管道 `web/service.py:619-624` 的模式，在 `post_agent_run` 出口构造 `AuditRecord` 并 `store.write(record)`，审计失败绝不影响主链路（`try/except` + `logger.exception`）。记录编排特有字段：`answered_by`、`planner_llm_error`、`detected_intent`（复用既有字段）。

**结构化日志文件 handler**（`audit/logging.py:81`）：

当前仅 `StreamHandler(sys.stderr)`。增加可选的 `RotatingFileHandler`（按配置开关启用，默认可关闭），使服务进程退出后日志仍可落盘追查。文件路径由配置驱动。

### 风险

- 审计契约扩展涉及 `audit_log` 表结构迁移，需保证 `_migrate_schema` 幂等且不丢历史数据（既有机制守护）
- `answered_by` 取值需与 `state.answered_by` 枚举保持一致（P1/P2 新增的枚举直答标识须同步登记）
- 日志文件 handler 的文件权限与磁盘占用需按配置开关约束，避免默认开启撑爆磁盘

## P4：握手期超时安全重试落地

### 说明

本项为既有待审 spec 的落地实施，不重复设计。

- **既有规格**：`docs/superpowers/specs/2026-10-05-llm-handshake-retry-design.md`
- **核心内容**：握手期（收到任何响应字节之前）超时抛专用异常 `StreamHandshakeTimeout`，允许安全重试一次；握手阶段使用独立短超时预算 `PROVIDER_HANDSHAKE_TIMEOUT`（默认 30s）；三类事件（握手超时 / 重试成功 / 重试仍失败）计入 `/api/metrics`；不触碰 mid-stream 防重复计费铁律
- **本轮动作**：推进 spec 过审 + 实施落地，验收锚点沿用既有 spec 的测试覆盖（握手超时专用异常 / 重试配置 / 握手指标计数器）

## 测试与验收

| 立项点 | 验收锚点 |
|---|---|
| P1 | 枚举类意图评测用例（34 行全量断言，不截断）；golden 快照重固化（`eval/rebuild_golden.py`）；ENUMERATION 直答不经 LLM 叙述（answered_by 为枚举直答标识） |
| P2 | 横幅文案双路径断言：枚举预路由中性文案 vs LLM 故障告警文案（`planner_llm_error` 非空）视觉可区分 |
| P3 | 编排审计记录断言（`/api/agent/run` 落 `audit.jsonl` + `answered_by`/`planner_llm_error` 字段可查）；日志文件 handler 落盘断言（配置开关启用时）；`tests/test_audit.py` 一致性断言通过 |
| P4 | 握手超时专用异常 / 重试配置 / 握手指标计数器（沿用既有 spec 测试覆盖） |

通用门禁：`black --check .`、`ruff check .`、`python -m pytest -q` 全绿；新增意图词表枚举时同步 `intent_golden.json` 锚点。

## 风险与假设

- **P1 grounding 口径**：确定性渲染绕过 LLM grounding，纯名称清单无数值溯源需求，风险低；报告头部如实标注"确定性路径直答"（与 P2 水印联动）
- **P1 golden 重固化**：ENUMERATION 全量断言改变结果集，需重固化 golden，确保种子 42 / 锚点 AS_OF_DATE 确定性可复现
- **P3 迁移兼容**：`audit_log` 表扩展字段由 `_migrate_schema` 幂等迁移，旧表不丢历史数据
- **P4 计费边界**：握手期重试基于"未返回任何响应字节即未计费"假设，既有 spec 已声明免责口径与 `PROVIDER_HANDSHAKE_RETRY_MAX=0` 关闭开关；切换供应商时须重新确认该假设
