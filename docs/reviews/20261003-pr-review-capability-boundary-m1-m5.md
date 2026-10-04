# 评审报告｜PR 评审：十九期能力边界扩展 M1-M5（2026-10）

> 评审日期：2026-10-03 · 评审方式：双轴评审（Spec 轴 + Standards 轴并行子代理 + 主会话实跑验证）· 评审范围：`git diff d75b9ce..HEAD`（066e0fa），32 文件 +3049/-97

## 评审对象与需求来源

- **评审对象**：十九期「能力边界扩展」里程碑 M1-M5 全部代码变更（feature/wip 分支，M1 起点 `e2a197c` 至 HEAD `066e0fa`）。M6（探索层执行+审批门）/ M7（验收收口）未实施，属计划内分期，不计缺陷。
- **需求来源**：`docs/superpowers/specs/2026-10-02-capability-boundary-expansion-design.md`（总设计，§8 里程碑验收标准为硬验收线）+ `docs/plans/2026-10-02~03-capability-boundary-m1..m5.md` 五份实施计划。
- **工程三件套实跑验证**：`python -m pytest -q` **967 passed**（基线 875 → 零失败）；`black --check .` 184 files unchanged；`ruff check .` All checks passed。

## 做得好的

- **提升闸门执行等价真值断言**（`tests/test_sql_lift.py:14-20`）：每条可升 SQL 做「原始执行 == 提升后重编译执行」的行集判等，真正落实设计 §7「数据真值断言、防跑通但答错」的原则，而非仅结构断言。
- **LLM SQL 生命周期管控严密且是结构性的**（`core/orchestrator/nodes.py:478-533`）：提升成功即 `model_copy(update={"dsl": ..., "sql": None})` 置空表面语法；拒升清单经 `planner_prompt(error_context=...)` 注入自愈（严禁盲重试纪律）；SQL 永不出函数直接执行——与铁律改写方向逐字吻合。
- **DSL 契约扩展纪律严格**（`semantic/dsl_schema.py`）：`ExpressionMetric`/`ExprArg`/`having` 全部 `extra="forbid"`；表达式以结构化 AST 描述拒绝字符串拼接；HAVING 与 window/top_n/fill_gaps 互斥在契约层显式拒绝；guard 与 heuristic 两处禁列回溯同步补齐并有专项测试（`tests/test_security.py:176-222`）。
- **沙箱封堵比计划更保守且逐形态验证**（`core/sandbox/ast_guard.py`）：`connect`/`database` 双调用名、`ast.Name`/`ast.Attribute` 双匹配形态，连内存 `duckdb.connect()` 也封死（`tests/test_sandbox.py:315-329`）。

## Spec 轴（对照总设计 §3/§6/§8 + 五份实施计划）

### HIGH 1: sqlglot 解析无超时与输入长度上限——设计两处明示，实现落空

**File:** `core/retrieval/sql_lift.py:374`
**需求原句**：设计 §3.3「保守性优先：解析失败/**超时**/歧义一律拒升，严禁猜语义放行」（:85）；§6.2「解析失败/超时/歧义一律拒升」（:137）。
**Issue**：`sqlglot.parse_one(sql, dialect="duckdb")` 裸调，仅捕获异常（解析失败）与结构性拒升（歧义），无超时、无输入长度上限。该入口直接暴露给 LLM 产出的 SQL（可被 prompt 注入操纵），超长/恶意构造的 SQL 可令解析长时间占用 CPU——设计点名的「超时一律拒升」防线缺失。M4 计划亦未含此任务（计划低于设计，两处设计要求落空）。
**Fix**：入口处加 SQL 长度上限（超限即 `LiftRejection("parse", "oversized", ...)`）+ 子进程/看门狗式解析超时（可复用 `exec` 既有线程看门狗模式），超时产出拒升清单喂回自愈。

### MEDIUM 1: 设计 §8 M5 验收线部分项被 M5 计划改判 M6——需在 M6 计划显式立项

**File:** `security/views.py:225-231`、`docs/plans/2026-10-03-capability-boundary-m5.md:23`
**需求原句**：设计 §8 M5 验收标准「视图逃逸矩阵（**基表不可达**/禁列不存在/行过滤生效）」（:169）；§3.4「DSL 核……**编译器产 SQL 同样改查安全视图**」（:91）。
**Issue**：当前 `install_secure_views` 不删基表、无命名空间限制，同一连接可直接查 `fact_orders`；「编译器改查安全视图」未实施（M5 计划明示「不改编译器」）。这是计划对设计的有意改判（CREATE VIEW 需可写连接等工程边界，`m5.md:27` 如实记录），但意味着 M5 的设计验收线未完全达成。
**Fix**：在 M6 实施计划中显式立项：探索层连接只暴露 `sec_*` 命名空间（连接级 schema 限制/基表收回）、`read_only` 连接补位（当前 `views.py:234-237` 仅 `enable_external_access` + `lock_configuration` 两项）、编译器-安全视图切换或显式放弃该承诺（修订设计文档）。

### MEDIUM 2: M6 三个审计事件零预留 + 拒升耗尽后待审批 SQL 无暂存通路

**File:** `core/orchestrator/nodes.py:512,528,532`；`audit/`（无变更）
**需求原句**：设计 §6.5「新增审计事件：`lift_reject` / `exploration_approval` / `exploration_execute`」（:139）；§3.3「仍不可表达 → 转探索层审批」（:84）。
**Issue**：M4 拒升只写 scratchpad 文本，无结构化事件发射点；`_lift_plan_sql` 自愈耗尽返回 `(None, notes)`，LLM 原始 SQL 被丢弃不入 state——M6 审批门所需的「待审批 SQL」实体当前无处存放，接入时必须改造返回路径。M1-M5 交付物未为设计明示的 M6 依赖预留挂点。
**Fix**：M6 计划立项时补：`LiftRejection.to_dict()`（已就绪，`sql_lift.py:46`）接审计事件；自愈耗尽分支将原始 SQL 与拒升清单存入 state（如 `pending_exploration` 字段）而非直接丢弃。

### MEDIUM 3: 「最近似可答问法建议」未实现

**File:** `core/orchestrator/nodes.py:2764-2767`
**需求原句**：设计 §3.8「能力边界类拒答……改为告知问法超出当前能力 + **给出最近似可答问法建议**」（:113）。
**Issue**：现有实现仅输出静态文案「请调整问法（明确指标或维度）……（能力边界），与 LLM 服务状态无关」+ 静态能力清单，无按拒答成因推导最近似可答问法的逻辑。M1 计划 Task 5 自身未含此项（计划低于设计，三层层层收窄）。
**Fix**：按拒答成因分流补建议生成（如维度锚定成功时建议「列出{该维度}的全部取值」这类最近似可答形态）；或修订设计文档降级该承诺。

### LOW 1: 多维枚举 goal 联合标签未做（M2 计划明示任务）

**File:** `core/orchestrator/nodes.py:236`
**需求原句**：M2 计划「`enumeration_dsl` 多维……`_heuristic_plan` goal 联合标签」（`m2.md:76`）。
**Issue**：goal 固定「列出{第一个维度}的全部取值」——多维枚举返回两列但计划 goal 只提一维。DSL 投影本身正确（`core/orchestrator/intent.py:207` 多维已放开），纯文案层半成品。

### LOW 2: eval_runner SKIP 机制残留死代码

**File:** `eval/eval_runner.py:104,114,272-296`
**Issue**：`skipped` 字段与 SKIP 打印分支在全仓库无赋值点（`_is_contract_pending_case` 移除后分支不可达）；`eval/eval_runner.py:282` 的 skip_note「M2 新契约形态待 M3 接入」已过期。无害清理半成品。

### 说明性发现（不计缺陷）

- **自愈次数设计-实现不一致**：设计 §3.3「复用既有 ≤3 次闭环」（:84），实现 `_LIFT_MAX_RETRIES = 2`（`nodes.py:475`）跟 M4 计划「≤2 次」（`m4.md:8`）。取收紧值方向保守，建议设计文档同步口径。
- **口径模糊题库落位**：设计 §7 第 3 层「澄清或 assumptions 标注，两种都算通过」（:152）在 CI 中实际以「兜底 refusal（`eval/intent_golden.json:48-51`）+ mock 路由状态机单测」形态达成——LLM 在场的端到端分级作答无 CI 覆盖（CI 无 Key 的既有约束），属设计第 3 层与第 6 层适用域张力的实施选择，非缺陷。
- **可接受范围蔓延（如实报告）**：① checkpointer 线程按轮隔离（`ce62a05`）——十九期未要求，但有独立审计-拍板-修复记录（`docs/reviews/20261002-audit-clarify-pending-hijack.md`）；② 沙箱 `FORBIDDEN_CALLS` 额外加入 `"database"`（`ast_guard.py:95-96`）——计划只要求 `connect`，方向更保守；③ 启发式 HAVING 触发词覆盖计数类指标（`agent/heuristic.py:227-233`）——计划写「金额类」，有 `alias` 前置约束兜住，无害外扩。**§9「明确不做」清单零命中**（无沙箱任意 SQL、无写操作、无外部数据源、无策略可视化面板）。

## Standards 轴（对照 AGENTS.md + 通用底线速查表）

### HIGH 1: 多时间字段窗口对循环覆盖——静默错译，结果集变宽

**File:** `core/retrieval/sql_lift.py:532-561`
**Issue**：`time_filter` 在 `starts.items()` 循环中被逐次覆盖，多个时间字段各有完整窗口对时（如 `order_date` 与 `ship_date` 各一窗口对）只剩最后一个字段，前序窗口静默丢失；同字段双 `>=` 时 `starts[field_name] = value` 覆盖也非取交集语义。DSL 契约 `time_filter` 为单时间字段，多字段窗口本属「不可表达构造」，正确行为是拒升——现实现直接错译，重编译结果比原 SQL 宽。与模块 docstring 自declared 的「宁拒升不错译」（`sql_lift.py` 模块注释）直接冲突，违反 AGENTS.md「兜底模式严禁隐式吞错」同族纪律。
**Fix**：`len({f for f, _, _ in time_pairs}) > 1` 或同字段多窗口对时产出 `LiftRejection("where", "多时间字段窗口", "DSL 单时间字段契约无法表达，拒升")`。

### HIGH 2: JOIN ON 子句非 EQ 谓词静默丢弃——结果集扩大

**File:** `core/retrieval/sql_lift.py:434-449`
**Issue**：JOIN 核对仅收集 `on.find_all(exp.EQ)` 的列集合与语义目录 `JOIN_RULES` 判等：`ON f.id = d.id AND f.dt > '2024-01-01'` 中的 GT 谓词不进集合、核对通过后被静默丢弃（DSL 的受控连接只描述等值），提升产物行集比原 SQL 大。同为「错译」。
**Fix**：校验 ON 子句整体仅由 EQ 经 AND 构成（复用 `_flatten_and`），含非 EQ 谓词即拒升。

### MEDIUM 1: `_render_view_body` 死代码 + 安全边界双实现

**File:** `security/views.py:99-130`
**Issue**：该函数全仓库零调用点（grep 仅定义处），与 `build_secure_views` 内联逻辑（:189-214）逐字级重复——两份等价的「跨表 RLS join + 谓词渲染」实现将随时间漂移，安全边界改一处漏一处即权限洞。出处：坏味道表「重复代码」。
**Fix**：删除 `_render_view_body`，或让 `build_secure_views` 复用它。

### MEDIUM 2: dsl/sql 同给时合法 DSL 被连坐丢弃 + 「dsl 优先」测试名不副实

**File:** `core/orchestrator/nodes.py:497-507`、`:649`、`tests/test_orchestrator.py`（`test_planner_dsl_takes_precedence_over_sql`）
**Issue**：`_plan_from_llm` 允许 dsl 与 sql 同给（`:550-552` 独立取值）；`_lift_plan_sql` 只看 `step.sql`——可升 sql 会用 lift 产物**覆盖**合法 dsl，不可升 sql 自愈耗尽返回 None 使整个计划（含合法 dsl 步骤）连坐落兜底。测试宣称「dsl 优先」，实际靠兜底路径产出同形 DSL 凑巧通过，`answered_by` 仍为 heuristic。出处：HIGH 级「错误路径缺失」降级——影响为计划质量降级而非数据错误。
**Fix**：`_lift_plan_sql` 内对 `step.dsl is not None` 的步骤跳过提升并置 `sql=None`（真 dsl 优先），测试改为断言 `answered_by` 非 heuristic。

### MEDIUM 3: 沙箱测试名与断言完全相反

**File:** `tests/test_sandbox.py:332-336`
**Issue**：`test_static_check_allows_duckdb_read_parquet` 名为 allows、docstring 称「合法用途不受影响」，断言却是 `assert not report.ok, "connect 调用（含内存库）一律封死"`，且 code 里根本没有 `read_parquet`。下一个维护者会误读封堵语义；M5 计划验收点「合法 read_parquet 不受影响」（`m5.md:57`）实际由 pandas 形态代偿，duckdb 形态无正面覆盖。
**Fix**：改名 `test_static_check_blocks_even_in_memory_connect` 并对齐 docstring；补 `duckdb.read_parquet(...)` 模块级调用的正面放行用例（该形态 attr 名不在 `FORBIDDEN_CALLS`，静态检查应放行——需实测确认后锚定）。

### MEDIUM 4: 禁列回溯权限语义双实现

**File:** `agent/heuristic.py:190-219` 与 `security/guard.py:46-84`
**Issue**：表达式 ref 回溯收集引用字段的核心逻辑两处逐字级重复（heuristic 注释自认「与 security.guard._referenced_fields 同口径」）。禁列回溯规则变更时两处需人肉同步，漏一处即禁列借表达式绕过。出处：坏味道表「重复代码」。
**Fix**：提取单一共享函数（建议 `security/guard.py` 导出 `referenced_fields(dsl)`），heuristic 复用。

### MEDIUM 5: AST 层别名导入封堵缺口（有运行时兜底，不可利用）

**File:** `core/sandbox/ast_guard.py:92-97`
**Issue**：`FORBIDDEN_CALLS` 按调用名匹配，`from duckdb import connect as c; c('x.duckdb')` 别名绑定形态在 AST 层静默通过（`visit_ImportFrom` 只查 module 顶层名）。因运行时 `_ALLOWED_IMPORT_ROOTS` 白名单无 duckdb（`core/sandbox/_bootstrap.py:33-55`），实际不可利用——非安全洞，但「必须在代码层封死调用入口」的注释宣称不完整，且 LLM 自愈拿到的只有运行时 `ImportError`，无精确违规信息。
**Fix**：`visit_ImportFrom` 对导入名 ∈ {"connect", "database"}（无论是否别名）产出违规。

### LOW 级（5 条，简要）

1. `compiler/sql_compiler.py:322` — `_expr_sql` 对 `lit` 渲染 `repr(float(...))` 绕过 `_literal` 的 NaN/Inf 防线；`json.loads` 默认接受 `Infinity`/`NaN`，LLM 产出可致 `lit=inf` → CompileError（无注入面，仅错误路径不精确）。修法：契约层 `ExprArg.lit` 加 is_finite 校验。
2. `eval/eval_runner.py:104,114,272-296` — 同 Spec 轴 LOW 2（SKIP 残留死代码）。
3. `tests/test_security_views.py:71` — `__import__("semantic.dsl_schema", fromlist=[...])` 动态导入毫无必要，改直接 `from semantic.dsl_schema import QueryDSL`。
4. `agent/heuristic.py:227-249` — HAVING 触发词含中文指标字面词表，与 `FieldMeta.aliases` 存在双源漂移风险（AGENTS.md 词表铁律字面仅约束 intent.py，heuristic 本为字面词表驱动，故列 LOW）。建议 alias 反查改从 `semantic.catalog` 派生。
5. `security/views.py:29` — 从 compiler 导入私有符号 `_literal`，私有实现变更会静默破坏视图安全渲染。建议提升为 compiler 公开 API。

## 结论：能不能合

- Spec 轴 6 条（HIGH 1 / MEDIUM 3 / LOW 2）+ 3 条说明性发现；Standards 轴：🔴 0 / 🟠 2 / 🟡 5 / ⚪ 5。
- **判定：修完再合。** 无 CRITICAL；3 条 HIGH 均可本地修复且互不依赖（sqlglot 超时与长度上限、多时间字段窗口拒升、JOIN ON 非 EQ 拒升），建议合并前收口；10 条 MEDIUM/LOW 可随 M6 计划立项一并处理（其中 M6 挂点缺口两条必须进 M6 计划显式立项，否则设计 §3.4 核心隔离承诺落空）。
- 工程约定实跑全绿：pytest 967 passed（基线 875 零破坏）、black、ruff。

## 修复记录（2026-10-03 同日收口）

上表问题已在本分支修复（pytest 974 passed / black / ruff 全绿）：

- **HIGH 1/2/3**：`sql_lift.py` 增加输入长度上限（10000 字符）+ 守护线程解析超时（5s，超时拒升）；多时间字段窗口对、同字段重复窗口、JOIN ON 非等值谓词均显式拒升并注入自愈清单。对应新增拒升矩阵测试 5 条。
- **Standards MEDIUM 1**：`_render_view_body` 死代码已删除（`build_secure_views` 为唯一实现）。
- **Standards MEDIUM 2**：`_lift_plan_sql` 对 `dsl is not None` 的步骤跳过提升并置空 `sql`（真 dsl 优先）；测试断言升级为 `answered_by == "llm"`。
- **Standards MEDIUM 3**：测试改名 `test_static_check_blocks_even_in_memory_connect`；补 `duckdb.read_parquet` 正面放行用例与 `from duckdb import connect as c` 别名导入拦截用例（`visit_ImportFrom` 按被导入名拦截）。
- **Standards MEDIUM 4**：`security.guard.referenced_fields` 公开化为共享函数，`agent/heuristic.py` 删除逐字重复实现。
- **Spec MEDIUM 3**：`_cannot_answer_report` 能力边界分支按词表命中附「最近似可答的问法参考」（维度词→枚举/分组问法、指标词→标量问法；UNKNOWN 意图锚点为空的既有契约保持不变，故对问句直接做词表命中）。
- **LOW**：`ExprArg.lit` 契约层拒绝 NaN/Inf（`math.isfinite`）；eval_runner SKIP 残留死代码删除；`test_security_views` 动态导入改直接导入；`compiler._literal` 公开化为 `render_literal`（`security/views.py` 弃用私有导入）。
- **不改并说明**：Standards LOW 4（heuristic HAVING 中文触发词）——`FieldMeta.aliases` 仅字段级别名（"订单量"挂 `order_id` 物理列），HAVING 需映射聚合别名 `order_count`，catalog 无聚合口径注册表可派生，硬派生会错误映射；属目录 schema 扩展，留待后续立项。
- **M6 立项项**（Spec MEDIUM 1/2，非代码修复）：基表命名空间隔离、`read_only` 连接补位、编译器改查安全视图、三个审计事件（`lift_reject`/`exploration_approval`/`exploration_execute`）、拒升耗尽后待审批 SQL 暂存通路——须在 M6 实施计划中显式立项。
