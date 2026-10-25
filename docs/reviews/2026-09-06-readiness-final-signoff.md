# 评审报告｜就绪度评审：终态生产就绪度综合审计与防退化验收（2026-09）

- 评审对象：FutureBI（规格驱动 ChatBI：NL → DSL(JSON) → 确定性 SQL 编译器 → DuckDB），审计基线 commit `f70f4e3`
- 评审方式：全量代码走读（exec / semantic / security / agent / tools / web / auth / present / persistence）+ 历史评审报告逐项闭环对照 + 全量测试与质量门实测
- 实测结果：`pytest -q` **350 passed（39.09s）**；`black --check .` **104 files clean**；`ruff check .` **clean**
- 对照基准：[`20260905-production-readiness-audit.md`](20260905-production-readiness-audit.md)（80/100，159 passed）、[`20260905-code-depth-audit.md`](20260905-code-depth-audit.md)（P0-1~P0-6）、[`20260905-pr-review-multi-tool-agent.md`](20260905-pr-review-multi-tool-agent.md)（P0×1 / P1×5 / P2×8）

---

## 1. 终验结论与就绪度打分

- **系统阶段判定**：**达到工业级生产上线标准（Production Ready）**（附 3 项上线前建议整改，均为小改动，不构成阻断）
- **综合健康度评分**：**92 / 100**（较前期审计基准 80 分 +12：历史 P0-1~P0-6 全部实质性闭环，新引入的路由/多工具/记忆三能力均收敛且未破坏既有防线，测试规模 159 → 350）
- **Sign-off 核心结论**：**【准予上线】**

评分依据：五个非协商审计维度全部有真实实现与测试闭环，无 P0/P1 硬伤残留；扣分集中在 4 处纵深防御不一致（任务结果属主校验缺失、澄清槽位单键隔离、双连接池并发闸旁路面、LLM 规划分支槽位断裂），均已定位到行号且利用门槛高（详见 §3）。

---

## 2. 核心模块与更新能力对照矩阵

| 核心模块 / 新特性 | 当前代码具体实现文件 | 审查判定 | 现存隐患 / 待优化点 |
|---|---|---|---|
| **P0-1 只读 AST 校验** | `exec/guards.py:199-231`（`_assert_read_only_structure`：sqlglot 解析 → 恰好一条语句 → 根节点必须 SELECT/UNION → 全树 walk 拒绝被禁函数/引号字面量表/SELECT INTO）；四层防线入口 `assert_read_only_sql`（`:233-261`：语句形态 + 语句级黑名单 `:83-103`（含 COPY/EXPORT/IMPORT/INSTALL/LOAD）+ 函数家族黑名单 `:117-138` + `FROM '<path>'` 拒绝 `:141`），`execute_sql` 入口强制调用（`:410`） | **工业级完备** | 函数层为"AST 遍历 + 家族黑名单"而非正向函数白名单（结构性白名单已覆盖根节点）；未来新增自定义读文件扩展函数需手动追加家族规则 |
| **P0-2 元数据单轨化** | `semantic/catalog_loader.py`：物理面读 `information_schema.columns`（`:97-108`）+ `config/semantic.json` 覆写（`:161-212`）+ 维度成员词汇表从 dim 表 distinct 重建（`:111-130`）；启动时 `refresh_catalog`（`web/service.py:562-573`）；内置默认降级为回退且逐字段与物理元数据强校验（`:215-233`） | **工业级完备** | `_field_metadata` 中文注释仍未被程序消费（目录只消费 information_schema + 覆写），中文标签走 `present/labels.py` 硬编码（展示层，非安全面） |
| **P0-3 动态 RLS 策略** | `security/policy_loader.py:45-89`（config/policies.json 重建 POLICIES + PRINCIPAL_ATTRS）+ 参数化谓词模板 `{"field":"province","operator":"in","param":"principal.provinces"}`（`config/policies.json:12-14,19-21`）在施加时按主体解析（`security/guard.py:22-41`）；改权限/加主体只改 JSON | **工业级完备** | 暂无运行时热加载（启动时刷新；改策略需重启或重调 `refresh_policies`） |
| **并发治理与连接池** | `exec/pool.py:24-82`（固定容量只读连接池，池满阻塞排队）+ 全局信号量 `BoundedSemaphore(MAX_CONCURRENT_QUERIES)`（`web/service.py:172`）+ DATA_QUERY 路径池取还 + 持闸（`:407-427`）；另有 principal 感知结果缓存 LRU+TTL 默认关闭（`exec/query_cache.py:30-49,99-115`） | **工业级完备**（主链路） | 双池并存：`web/service.py:174-185` 自建池 ≠ `exec/pool.py:88-103` 工具层单例池；GLOSSARY_EXPLAIN 分支不持 `_query_gate`（见 §3-R3） |
| **意图识别与分流** | 五分类 `IntentType`（`agent/router/intent_router.py:55-65`）+ `RouteDecision` 契约（`:75-99`）+ 三级分派 Fast-Path→LLM→规则兜底（`:386-413,418-456,461-502,507-588`），异常一律安全降级为 CLARIFY（`:401-411`）；`web/service.py:308-470` 五分支物理隔离：CHITCHAT 零 DB 触达且不清 `last_dsl`（`:308-318`）、SYSTEM_ACTION 白名单动作（`:74-118`）、CLARIFY 反问（`:319-340`） | **工业级完备** | 关键词表仍为中文语料确定性枚举（有 LLM 分类 + 兜底 CLARIFY 双保险，误分流落入反问而非错误执行） |
| **多工具编排链路** | `agent/tool_agent.py`：LLM 规划器只能选注册工具 + 参数严格校验（`:320-324`）、max_steps 3~5（`:529-531`）、永久错误短路（`:714-722`）、轨迹落审计（`:700-712`）；4 个内置工具全部经 `tools/builtins/_query_core.py:66-185 run_guarded_query`：`apply_policy`（`:125`）→ `compile_sql` → `execute_sql`（AST 只读校验）→ 自愈后**重新 apply_policy**（`:166`）；全链路无一处裸拼 SQL；导出含脱敏 + CSV 公式注入防御（`export_report_tool.py:98-105,207-217`） | **工业级完备** | LLM 规划器 DATA_QUERY 分支的 clarify 不进槽位回填（PR 评审 P1-5 残余一半，见 §3-R4）；`export_report_tool.py:45` 仍暴露 `principal` 死字段（消费的是 `ctx.principal`，无提权，契约卫生欠佳） |
| **会话记忆与指代消解** | `agent/memory.py`：结构化继承上轮 `QueryDSL.last_dsl`（`SessionState:93-107`），`resolve_context` 判定 fresh/inherit/drilldown 并结构化合并增量（`:486-539`）、合并前 `strip_rls_filters` 剥离已注入 RLS 保证恰好注入一次（`:414-430`）；`SessionStore.get/clear` 按 `(session_id, user_id)` 强隔离、跨用户返回 None（`:167-195,215-224`）；SQLite 持久化 WAL（`persistence/kvstore.py:53-72`） | **工业级完备** | `SessionStore.update` 不校验原归属（同 session_id 被另一用户 update 会覆盖，`:197-213`）；澄清槽位 `ClarifySlotStore` 仅按 session_id 单键（`agent/slotfill.py:59-101`，见 §3-R2） |
| **呈现与可信溯源** | `present/viz.py:87-122` 六形态自适应（number/line/bar/pie/pivot/table）+ 数值占比/基数/正数信号（`:38-84`）+ ECharts option 渲染契约（`:159-218`）；`present/explain.py` DSL→中文计算说明；响应透出 `sql/dsl/explanation/context_summary/clarify_filled/steps`（`web/service.py:429-470,556-558`）+ 审计含路由字段（`:513-532`） | **工业级完备** | 透视表（pivot）为分组表格渲染，无交叉表宽长转换（前端形态限制，非缺陷） |
| **认证与部署形态** | principal 服务端强制绑定、客户端声明忽略并告警（`web/server.py:343-353`）；登录限流（`:191-211`）；启动强校验拒绝弱 JWT 密钥与 AUTH_ENABLED=0（`:462-483`）；会话绑定归属校验（`:446-459`）；PBKDF2 每用户随机盐（`auth/identity.py:27-46`）；会话/槽位/限流可外置 SQLite 共享存储（`auth/session.py:98-171`、`persistence/kvstore.py`） | **工业级完备** | 仍为标准库 `ThreadingHTTPServer`（生产建议反代 + TLS 终结，`docs/` 已有部署说明）；异步任务结果缺属主校验（§3-R1） |

---

## 3. 残留风险实锤清单

> **处置状态（2026-09-06 预防修复闭环）**：R1~R5 与 P3 卫生项、测试盲区已全部修复并按项独立提交——
> R1 `8343762`、R2 `eba1bf0`、R3 `35a37bd`、R4 `e412af4`、R5 `0aef53a`、P3-4（导出属主/显式角色）`39911e8`、
> P3 卫生三项 `ec0d153`、黑名单补强 `3eda0fe`；修复后全量测试 364 passed，质量门全绿。

均无 P0/P1 定级；按建议处置优先级排列：

- **R1（已修复 `8343762`）异步任务结果无属主校验（IDOR 同类）**
  `web/tasks.py:91-95` `snapshot(task_id)` 不含属主；`web/server.py:308-316` `_get_task` 仅 `_authenticate()`，任意已登录用户持他人 task_id 可读取其查询结果（含 rows 数据）。task_id 为 uuid4 hex 不可枚举、需泄漏途径，可利用性低；但导出下载端点已有属主校验（`web/server.py:383-396`），任务端点未对齐。建议 `TaskManager.submit` 记录 owner 并在 `_get_task` 校验（同 P1-3 修复模式）。
  **修复**：`TaskManager.submit` 登记 owner，`snapshot(task_id, owner=...)` 归属不一致（含未登记属主的历史任务）fail-closed 返回 None（404，不泄露存在性）；admin 角色全局可见（owner=None 放行），与导出下载策略一致；新增单元级与 HTTP 级越权负向用例。
- **R2（已修复 `eba1bf0`）澄清槽位单键隔离，与会话记忆不一致**
  `agent/slotfill.py:59-101` `ClarifySlotStore` 仅按 `session_id` 读写，无 user 归属校验；`SessionStore` 已实现 `(session_id, user_id)` 双键（`agent/memory.py:167-195`）。HTTP 层 `_bound_session_id`（`web/server.py:446-459`）已拦截跨用户会话借用，故当前不可直接利用；属纵深防御不一致，建议槽位同样绑定 owner。
  **修复**：`ClarifySlotStore` 条目登记属主（内存 + SQLite 持久层同步），`get/clear` 带身份时校验归属，跨用户 / 未登记属主的条目视同不存在（fail-closed）；`web/service.py` 全部 6 处调用点传入 owner；新增跨用户隔离 / 不误删 / 持久层归属用例。
- **R3（已修复 `35a37bd`）双连接池并存 + GLOSSARY_EXPLAIN 分支不持并发闸**
  `web/service.py:174-185` 自建池与 `exec/pool.py:88-103` 工具层单例池是两个实例（PR 评审 P1-2 残余）。DATA_QUERY 主路径已注入池连接并持 `_query_gate`；但 GLOSSARY_EXPLAIN 分支（`web/service.py:357`）不传 conn、不持闸——LLM 规划器若把口径问题调度到数据工具，`run_guarded_query` 将从工具层池取连接执行，绕过 `MAX_CONCURRENT_QUERIES` 信号量（仍有池容量 `DB_POOL_SIZE` 闸 + AST/超时/扫描/上限四护栏，非裸奔）。建议 service 统一复用 `exec.pool.default_pool` 单例，并在该分支持闸或限制 glossary 意图仅允许 `explain_glossary` 工具。
  **修复**：service 删除自建池，DATA_QUERY 与工具层 `_query_core` 共享 `exec.pool.default_pool` 同一单例；GLOSSARY_EXPLAIN 分支纳入 `_query_gate`；新增持闸验证与统一池取用 e2e 用例。
- **R4（已修复 `e412af4`）LLM 规划器 DATA_QUERY 分支的 clarify 不进槽位回填**
  `agent/tool_agent.py:579-583` clarify 早退携带 `result.clarifications`，但 `web/service.py` DATA_QUERY 分支（`:387-470`）未写 `slot_store`（GLOSSARY_EXPLAIN 分支 `:362-370` 已修复）。用户能看到反问（answer 拼接，`:581`），但短语回答会被当全新问题。确定性模式不受影响（路由层 CLARIFY 分支已写槽位）。
  **修复**：DATA_QUERY 分支将 LLM clarify 写入槽位回填上下文并透出结构化 `clarifications`（与 CLARIFY 分支响应契约一致）；新增 e2e 用例验证反问 → 短语回答 → 回填执行闭环。
- **R5（已修复 `0aef53a`，防御性）`SessionStore.update` 不校验原归属**
  `agent/memory.py:197-213`：同 session_id 已被用户 A 占用时，用户 B 的 update 直接覆盖（clear 有归属校验、update 没有）。session_id 由服务端签发 uuid 时不可达；防御性建议与 get/clear 对齐。
  **修复**：`update` 在内存与持久层做归属校验，跨用户抢占拒绝覆盖并返回现有状态；新增内存与 SQLite 持久层两档用例。
- **P3 代码卫生（已修复 `39911e8` / `ec0d153`）**：`export_report_tool.py:45` args 暴露 `principal` 死字段（执行消费 `ctx.principal`，`:81`，无提权）；`agent/tool_agent.py:760` `_agent_lock = None` 死变量且 `default_tool_agent`（`:763-787`）无线程锁（并发首调可能重复构造，结果等价）；`tools/builtins/_export_store.py:106` 用 `__import__("json")`（顶部已 import json）；`web/server.py:390-394` 管理员判定依赖 `scoped_fields(None)` 不抛异常的探测式实现（脆弱，建议显式角色判定）。
  **修复**：死字段删除；工厂启用 `threading.Lock` 双检锁；改常规 `json.dumps`；管理员判定改为显式 `"admin" in ctx.roles`——**并由此发现探测式实现对 `principal=None` 恒不抛异常、导出属主校验实际失效为"恒放行"的实质缺陷，一并堵死**（非属主且非 admin 角色 → 403，新增 3 个 HTTP 级用例）。
- **测试盲区（已补齐 `8343762` / `35a37bd` / `3eda0fe`）**：异步任务结果越权读取（R1）无负向用例；GLOSSARY_EXPLAIN 分支被 LLM 调度到数据工具时的并发闸行为（R3）无测试；`st_read` 等未装载扩展的读文件函数不在黑名单家族内（依赖 INSTALL/LOAD 语句级拦截兜底，`exec/guards.py:83-103`），可加一条对抗用例固化。
  **修复**：三项盲区用例全部补齐；黑名单家族显式纳入 `sniff_csv` / `st_read`（预装扩展部署环境的兜底），新增对抗用例。

---

## 4. 测试集与覆盖率核验

- **全量单测**：`python -m pytest -q` → **350 passed in 39.09s，0 failed**（26 个测试文件）；`black --check .` 104 files clean；`ruff check .` clean。
- **恶意表函数拦截**（`tests/test_exec.py`）：5 个专项测试覆盖 40+ 对抗样本——`test_unsafe_sql_rejects_table_functions`（read_csv/read_json/read_parquet/read_duckdb/glob/parquet_scan/read_text/sqlite_scan/query/query_table，`:151-167`）、`..._rejects_copy_export_import`（COPY/EXPORT/IMPORT/INSTALL/LOAD，`:170-181`）、`..._rejects_string_table`（`FROM 'C:/windows/win.ini'`，`:184-192`）、`..._rejects_metadata_and_query_functions`（parquet 元数据函数 / postgres_query / 引号函数名 `"read_csv"` / dollar-quote / SELECT INTO / 嵌套子查询，`:195-215`）；正例不误伤（UNION/date_trunc/generate_series/`AS "read_csv"` 别名，`:218-249`）。
- **意图路由**（`tests/test_router.py`，32 用例）：五分类判决、契约字段、Fast-Path、LLM 失败/非法 JSON/低置信度三种降级、垃圾输入不抛异常（`:273`）、路由耗时预算（`:171`）、e2e 闲聊**无 DB 连接**守卫（`:285`）、**上下文隔离**（闲聊不破坏 GMV 继承，`:381`）、审计路由字段落盘（`:402`）。
- **多轮记忆**（`tests/test_memory.py`，29 用例）：跨用户隔离（内存 `:111` + SQLite 持久层 `:90`）、clear 外属不误删（`:120`）、TTL/LRU/滚动历史、inherit/drilldown/topic_switch 全分支、`strip_rls_filters`（`:262,286`）、e2e 跨用户隔离（`:360`）、**RLS 恰好注入一次**（`:436`）、自愈失败不污染 last_dsl（`:411`）；golden 评测含 M1（地区替换继承）/ M2（下钻加维）多轮序列用例。
- **多工具编排**（`tests/test_tool_agent.py` 20 用例 + `tests/test_tools.py` 13 用例）：三类 e2e 验收（点查/环比趋势/导出）、口径查询零 SQL（`:70`）、LLM 规划器重试/非法工具/非法参数/重试耗尽、max_steps 钳制、永久错误不重试（`:277`）、轨迹 JSON 可序列化；澄清槽位 12 用例（`tests/test_slotfill.py`）；导出截断与脱敏（`:88`）。
- **鉴权与会话**（`tests/test_web_auth.py` 19 用例）：会话绑定归属接受/拒绝/缺失（`tests/test_web.py:205-227`）、登录限流（`tests/test_ratelimit.py`）、连接池/缓存/策略加载/目录加载各有专项测试文件。

---

## 5. 历史评审闭环对照

| 历史项 | 处置状态 |
|---|---|
| code-depth-audit P0-1 只读校验表函数绕过 | ✅ sqlglot AST 结构校验 + 家族黑名单 + 40+ 对抗测试 |
| code-depth-audit P0-2 语义目录双轨制 | ✅ catalog_loader 数据驱动（information_schema + config 覆写 + 词汇表 distinct 重建） |
| code-depth-audit P0-3 权限内容写死 | ✅ policy_loader + 参数化 RLS 模板 + 主体属性外置 JSON |
| code-depth-audit P0-4 RAG bigram | ✅ 已升级 TF-IDF 稀疏向量余弦（`agent/rag.py:5-14`），守卫前移按主体过滤 |
| code-depth-audit P0-5 固定口令盐 | ✅ 每用户 16 字节随机盐 + 旧格式兼容校验（`auth/identity.py:27-53`） |
| code-depth-audit P0-6 无连接池/并发闸 | ✅ ReadOnlyConnectionPool + BoundedSemaphore（主链路；残余见 R3） |
| production-readiness-audit §3 P0-1/P0-2 标识符注入 + 语句拦截 | ✅ alias/order_by.field 全部 `IDENTIFIER_PATTERN` 校验（`semantic/dsl_schema.py:23,158,172,197,213,261`）+ AST 只读断言 |
| production-readiness-audit §3 P0-3 弱 JWT 默认密钥 | ✅ 启动强校验拒绝弱密钥（`web/server.py:462-483`） |
| production-readiness-audit §3 P0-4 部署形态 | ✅ 会话/槽位/限流 SQLite 外置 + 登录限流 + 审计多写者文件锁 |
| production-readiness-audit §3 P0-5 澄清单轮 | ✅ 槽位回填 + golden 多轮序列（残余见 R4） |
| pr-review-multi-tool-agent P0（LLMPlanner TypeError） | ✅ 构造签名已修正（`agent/tool_agent.py:269-277,776`） |
| pr-review-multi-tool-agent P1-3 导出 IDOR | ✅ 下载端点属主校验（`web/server.py:383-396`；同类问题在任务端点的残余见 R1） |
| pr-review-multi-tool-agent P1-4 principal 入参暴露 | ✅ query_metric/trend/explain 已清理；export 残留死字段（P3） |
| pr-review-multi-tool-agent P1-2 双池、P1-5 槽位断裂 | 🟡 部分闭环（R3 / R4） |
| pr-review-multi-tool-agent P2 系列 | ✅ window_days/默认窗口双实现/correct 注册表/UnknownToolError 捕获/CSV 公式注入/nosniff/导出 TTL 均已修复；`_p0_1_*` 脚本已移至 `scripts/` |
| audit-authentication-error-null-root | ✅ 核实为误报，立项关闭（该报告 §3） |
