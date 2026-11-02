# 设计方案｜语义目录 Web API：json 唯一事实源不变，API 为受控写入界面（2026-11）

对应 issue：[#27 语义目录 Web API 编辑端点](https://github.com/Cwentor/DataAgent/issues/27)
分支：`feat/issue27-semantic-catalog-api`（worktree 隔离）
范围确认：**第一步 + 第二步一并实施**（用户拍板，2026-11-03）

---

## 1. 背景与目标

业务术语（别名、口径、枚举标签、映射等）当前唯一维护途径是手改 `config/semantic.json`
后重启/重载，Web API 无术语端点。本设计把入口从文件换成 HTTP，同时守住单一事实源
高压线：**`config/semantic.json` 仍是唯一事实源，API 是它的受控写入界面，不是平行存储。**

目标：

1. GET 完整语义目录（术语 / 字段 / 别名 / 口径清单）；
2. POST 试校验（不落盘，返回校验结果与影响面）；
3. PUT/PATCH 受控写入（管理员权限 + 审计 diff + 乐观锁 + 原子写 + 热重载）；
4. 分节白名单：业务运营节可写，结构性契约节只读；
5. 前端语义目录管理面板（只读浏览 + 可写节编辑 + 试校验 + 保存）。

## 2. 高压线（唯一不可破）

- 写入流程固定为：**读 → 锁内版本校验 → 合并 → `catalog_loader` 全量校验 → 原子写 →
  `refresh_catalog` → 审计**。任何一步失败即中止，半成品不落盘；
- 严禁旁路存储：无数据库副本、无内存覆盖层，overlay 只来自 json 文件；
- 严禁绕过 `catalog_loader` 校验链的任何写入；
- 校验 fail-closed：校验失败拒绝写入并返回结构化错误；
- LLM 不参与术语维护（术语变更是人的决策）。

## 3. 选型对比矩阵

### D1 端点实现归属

| 候选 | 说明 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. `web/api.py` 直接实现 | 路由函数内联端点逻辑 | 少一个文件 | api.py 已 830 行且定位"薄路由层"；providers_api 已确立独立模块模式 | 否决 |
| **B. `web/catalog_api.py` 独立模块**（采纳） | 端点逻辑独立成模块，返回 `(status, body)`，api.py 只挂路由 | 与 `web/providers_api.py` 模式同构；逻辑可脱离 HTTP 单测 | 多一个文件 | **采纳** |
| C. 下沉 `core/retrieval` | 语义面归 core | — | 依赖方向违规风险：core 不应感知 HTTP 契约与 auth | 否决 |

### D2 乐观锁机制

| 候选 | 说明 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. json 内嵌 `_meta.version` | 文件内自增版本号 | 实现简单 | **手改文件不 bump 版本号**：双入口并发（API 客户端持旧版本覆盖手改内容）防线失效；且向"唯一事实源"注入工程元数据节 | 否决 |
| **B. 内容哈希 ETag**（采纳） | `version = sha256(文件字节)[:12]`；GET 下发，写入必须携带 | 无状态零存储；与内容天然绑定，**手改文件后旧 version 自动失效**（双入口并发正确收敛）；单调性无关紧要——乐观锁只判"变没变" | 客户端无法区分版本先后（只需重读，可接受） | **采纳** |
| C. 独立版本文件 | `config/semantic.version` | 可读可审计 | 两文件原子同步困难，崩溃窗口出现不一致；又是第二存储 | 否决 |
| D. 进程内计数器 | 模块级自增 | 最简单 | 重启归零；多 worker 部署失效；对手改文件无感知 | 否决 |

写入侧再叠加**进程内 `threading.Lock`** 串行化"读-改-写"临界区：哈希校验 + 合并 + 写入
全在锁内，杜绝两个写请求交错覆盖。B + 锁是双保险而非二选一。

### D3 校验执行方式

| 候选 | 说明 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. API 层自写一套校验规则 | 在 web 层重新实现节结构检查 | 表面灵活 | 与 `catalog_loader` 校验链形成**第二份校验事实**，必然漂移（项目已有"文案映射写三份"前车之鉴） | 否决 |
| **B. 公开 `catalog_loader.validate_overlay()`**（采纳） | 新增公开校验入口，内部复用 `_build_from_overlay(overlay, physical)` 试构建，捕异常收集为错误信息清单 | **零新校验逻辑**：fields 引用 / 必要业务节 / 节类型 / 连接规则检查全部复用既有解析器；API 与文件双入口同源同校验（issue 验收项） | 需把私有函数用法固化为公开契约（改动小） | **采纳** |
| C. 只做 JSON Schema 级校验 | pydantic 定义节 schema | 报错信息好定位 | 覆盖不了"字段指向的物理列不存在"这类跨系统校验，仍需 B 兜底，变双重维护 | 否决 |

### D4 审计落盘形态

| 候选 | 说明 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A. 扩展 `AuditRecord` + `catalog_change` 列**（采纳） | 审计记录新增可选 `catalog_change`（dict：操作/节 diff/操作者/版本），JSONL + DuckDB 双写同源 | 复用现成 `AuditStore` 双写通道与 `_migrate_schema` 幂等加列机制；`_record_params` 单一映射点已防"两处漏一处"；审计面统一（合规回溯一个入口） | 需动 `_AUDIT_COLUMN_TYPES` / `_INSERT_SQL` / `_record_params` 三处常量（有迁移机制护栏） | **采纳** |
| B. 独立 `catalog_changes.jsonl` | 单独文件记变更 | 不动既有表 | **旁路存储**：审计面分裂，脱离 audit 双写与失败计数监控 | 否决 |
| C. 仅结构化日志 | `get_logger` 落 web 日志 | 零改动 | 日志滚动即丢，不构成合规审计 | 否决 |

### D5 写端点语义

| 候选 | 说明 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. 仅 PUT 全量提交 | 提交完整 json | 语义最简 | 运营者只需改一个枚举标签也要回传全量；白名单外节被提交的风险大 | 否决 |
| **B. PUT + PATCH 均按节提交**（采纳） | `PUT`：body.sections 中每节**整体替换**（可完成删除）；`PATCH`：节内 deep merge（值 `null` = 删键） | 两者都是"节级"契约，白名单校验同一套；PATCH 适合单项标签微调，PUT 适合整节重写 | 两种语义要各自测试（成本可控） | **采纳** |
| C. RFC 6902 JSON Patch | 标准补丁操作集 | 表达力强 | 运营者手写 op 序列反人类；前端还要维护补丁生成器 | 否决 |

### D6 前端编辑形态

| 候选 | 说明 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. 全结构化表单 | 每节渲染专属表单控件 | 使用门槛最低 | 每节一套 UI 逻辑，节多工作量大；json 节结构演进要跟前端 | 否决 |
| **B. 分节卡片 + JSON 文本编辑**（采纳） | 可写节渲染折叠卡片：只读摘要 + 展开后 JSON `textarea` 编辑 + 「试校验」「保存」；结构性节纯只读展示 | 前端零依赖原生实现，与 workspace 现有风格一致；契约完整（所见即所提交）；试校验前置把错误拦截在落盘前 | 要求运营者懂 JSON（运营期可接受） | **采纳** |
| C. 第三方 JSON 编辑器库 | 引入 CodeMirror 等 | 编辑体验好 | 前端刻意零构建链（vendor 手工管理），引库破坏现状 | 否决 |

### D7 fields / metrics 受限编辑边界（本次默认从紧，用户可推翻）

issue 明确：结构性契约节（fields 定义 / metrics 的 DSL shape / join_rules /
query_domains / paid_filter）开放编辑需单独立项评审。但 fields 的 `label` / `aliases`
与 metrics 的 `title` / `aliases` / `definition` 本质是纯术语子键（不触碰物理映射与
DSL 契约），恰是意图路由词源、运营价值最高的一档。两种走向：

| 候选 | 说明 | 结论 |
|---|---|---|
| **A. 两节整节只读**（本次默认） | 严格遵循 issue 字面边界，受限编辑另立项评审 | **默认采纳**（从紧） |
| B. 子键受限编辑 | fields 仅开放 `label` / `aliases` / `non_aggregatable`，metrics 仅开放 `title` / `aliases` / `definition` / `formula`；条目不可增删，`table` / `column` / `dtype` / `shape` / `fields` 等契约键提交值必须与现文件逐字一致否则拒绝；校验自然复用 validate_overlay（改坏即拒） | 价值高但**越 issue 红线**，需用户明示才实施；机制上预留（白名单结构支持 SUBKEY 模式，扩展零重构） |

**本次按 A 实施**；若用户拍板 B，仅扩白名单常量即可，不影响架构。

## 4. 分节白名单

```python
# 可整节写入（业务运营节）
EDITABLE_SECTIONS = (
    "aliases",                    # 表别名（键须为已知物理表，校验见 §5）
    "metric_aliases",             # 聚合产物中文映射
    "value_labels",               # 枚举值中文标签
    "table_labels",               # 表中文标签
    "region_province_mapping",    # 大区-省份映射
    "dimension_members_seed",     # 维度成员离线回退种子
)
# 只读（结构性契约节，开放需单独立项评审）
READONLY_SECTIONS = (
    "fact_table", "fact_tables", "fields", "join_rules", "fact_join_rules",
    "query_domains", "dimension_member_fields", "drilldown_dim_fields",
    "metrics", "count_entities", "paid_filter", "default_window",
    "reflector_concepts", "out_of_scope_concepts", "undefined_metrics",
)
```

未知节名一律拒绝（`extra="forbid"` 精神：不在契约内的提交不接受）。只读节出现在写请求
body 中 = 400 拒绝（不静默剥离——静默丢弃会让运营者误以为改成功）。

## 5. 端点契约

统一前缀 `/api/catalog`，鉴权与既有受保护路由同语义（`authenticate`，Bearer JWT 或会话）。
响应体风格沿用 `{"error": ...}`；错误码 400 invalid json / 400 校验失败 / 401 / 403 / 409。

### GET /api/catalog

登录即可（与 `/api/schema/summary` 同级，浏览不设限）。

```json
{
  "version": "a1b2c3d4e5f6",
  "editable_sections": ["aliases", "..."],
  "readonly_sections": ["fact_table", "..."],
  "data": { "<semantic.json 全部节原样>" }
}
```

`version` 即内容哈希（§3 D2）。`data` 直读文件内容（非 `catalog.COLUMNS` 投影）——
编辑界面的对象是文件本身，从运行态目录反推会引入第二事实源。

### POST /api/catalog/validate（试校验，不落盘）

要求 `admin`。请求体与 PUT/PATCH 相同形态（可省略 `version`）：

```json
{ "sections": { "value_labels": { "order_status": { "1": "已付款" } } }, "patch": true }
```

响应：

```json
{
  "valid": true,
  "errors": [],
  "diff": [{ "section": "value_labels", "added": [], "updated": ["order_status.1"], "removed": [] }],
  "impact": ["value_labels 变更影响澄清选项与结果标签渲染"]
}
```

`valid=false` 时 `errors` 为错误信息字符串清单（校验链 fail-fast，每次报告首个构建
错误，运营者按提示逐项修复），HTTP 400。

### PUT /api/catalog 与 PATCH /api/catalog

要求 `admin`。请求体：

```json
{ "version": "a1b2c3d4e5f6", "sections": { "...": "..." } }
```

- `PUT`：`sections` 内每节整体替换；未提及的节不动；
- `PATCH`：`sections` 内 dict 节 deep merge，值 `null` = 删该键；
- `version` 缺失或不匹配 → 409，响应携带当前 `version`（客户端重读后再提交）。

处理管道（锁内执行）：

```
读文件字节 -> version 校验（哈希比对）
  -> 白名单校验（未知节/只读节出现在 body 即 400）
  -> 合并（PUT 整节替换 / PATCH deep merge）
  -> validate_overlay（复用 catalog_loader 全量校验链，含物理列存在性）
  -> 原子写（同目录 .tmp 写入 + os.replace）
  -> refresh_catalog()（热重载，bump_cache_epoch 即时生效）
  -> 审计（AuditRecord.catalog_change = {action, user, version_before/after, diff}）
  -> 200 {version: 新哈希, diff, impact}
```

校验失败 / 写入失败绝不落盘、绝不 refresh——目录保持旧状态。

`aliases` 节附加自检：键必须是 `fact_tables ∪ join_rules ∪ query_domains 内已声明的表`
（防新增别名指向未知表导致编译器渲染失配；该检查在 validate_overlay 内做，不另立规则）。

### GET /api/catalog/changes（可选只读增强，本次不实施）

变更历史回溯走 `audit.jsonl` 的 `catalog_change` 记录即可，暂不开端点。

## 6. 影响面提示

静态映射（纯提示，不阻断）：变更节 → 受影响链路说明。

| 节 | 影响面文案要点 |
|---|---|
| aliases | 影响编译器表别名渲染与查询 SQL 形态 |
| metric_aliases | 影响聚合结果列中文展示 |
| value_labels | 影响澄清选项文案、结果枚举标签、Planner 词表 |
| table_labels | 影响侧栏目录展示与 Planner 提示词 |
| region_province_mapping | 影响区域词展开与诊断下钻 |
| dimension_members_seed | 影响库不可用时的离线词表回退 |
| metrics（如未来开放） | 影响意图路由指标词表，建议跑 eval 回归 |

diff 汇总（added/updated/removed 键清单）随试校验与写入响应返回，变更不静默生效。
golden 影响计数（issue 可选增强）本次以"建议跑 eval_runner"文案替代，不做静态扫描。

## 7. 前端（workspace 内新增「语义目录」管理面板）

- 入口：顶栏设置区新增按钮（与供应商面板同模式）；
- 只读视图：全节折叠卡片按 `editable_sections` / `readonly_sections` 分组，可写节带标记；
- 编辑流：可写节展开 JSON 编辑框 → 「试校验」（调 validate 端点，错误内联渲染）→
  「保存」（带当前 version，409 时提示"目录已被他人修改，已为你刷新最新内容"并重拉）；
- 成功后提示影响面并自动刷新面板数据；
- 侧栏知识目录面板（`/api/schema/summary`）保持现状不动。

## 8. 测试计划

- `tests/web/test_catalog_api.py`：GET 全量与鉴权（401/403/200）；validate 合法/非法/
  未知节/只读节拒绝；PUT/PATCH 成功路径（tmp 文件 + os.replace 可注入）、409 版本冲突、
  400 校验失败不落盘（文件字节不变 + 目录未 refresh）；PATCH null 删键；aliases 未知表
  拒绝；审计记录含 catalog_change diff；
- `tests/semantic/test_catalog_loader_validate.py`（或在既有 loader 测试内补）：
  validate_overlay 对 fields 坏引用 / 缺必要节 / 节类型错误的收集行为；
- `tests/audit/test_store.py` 补 catalog_change 列迁移与写入；
- 手改文件 + CLI 链路回归：`python -m semantic.catalog_loader --overlay` 依旧可用（双入口同源）；
- 全量 `python -m pytest -q` + `black --check .` + `ruff check .`。

## 9. 明确不做

- 旁路存储 / 内存覆盖层；
- 绕过 catalog_loader 校验链的写入；
- 结构性契约节写端点（fields / join_rules / query_domains / paid_filter / metrics 等）；
- LLM 自动维护术语；
- 变更历史 HTTP 端点（走 audit.jsonl）；
- golden 静态影响扫描（文案提示替代）。

## 10. 实施顺序（摘要，详见 plans/2026-11-03-semantic-catalog-web-api-plan.md）

M1 catalog_loader.validate_overlay 公开化 → M2 web/catalog_api.py 三端点 +
api.py 路由 → M3 审计扩展（AuditRecord.catalog_change + store 三处常量）→
M4 前端管理面板 → M5 测试补全与全量回归 → M6 文档索引登记。
