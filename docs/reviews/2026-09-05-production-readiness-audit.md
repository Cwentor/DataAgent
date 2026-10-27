# 评审报告｜就绪度评审：生产就绪度（Production Readiness）（2026-09）

- 评审对象：FutureBI（规格驱动 ChatBI：NL → DSL(JSON) → 确定性 SQL 编译器 → DuckDB）
- 评审方式：全量代码走读（agent/semantic/compiler/exec/security/auth/audit/present/web）+ 运行时实测取证
- 质量门实测：`pytest -q` **159 passed**；`black --check .` **64 files clean**；`ruff check .` **clean**；golden 评测 **19/19 通过**（oracle 与 agent 双模式）
- 冒烟实测：闲聊拒绝 / 口径 RAG / 澄清反问（"高活用户有多少"、"GMV是多少"）/ 正常查询 / RLS 注入核验 / 越权列拦截 / 降级拒绝，全部符合设计预期

---

## 1. 就绪度评分

**80 / 100 —— 需整改**（完成 P0 加固项后具备内网试点上线条件）

评分依据：五维链路（意图路由、受控生成、权限隔离、自愈闭环、可解释交付）均有真实实现且实测闭环，架构水准高于常见 demo；扣分集中在标识符注入面（结构性防御缺口）、生产部署形态（进程内会话/审计、弱默认密钥、无限流）、单方言绑定与澄清多轮交互缺失。

---

## 2. 核心链路闭环核验

### 维度1：语义与意图路由

| 检查点 | 结论 | 证据 |
| --- | --- | --- |
| 意图分流 | ✅ 已满足 | `agent/intent.py` 确定性三分类（TEXT2SQL/RAG/CHITCHAT），`agent/router.py` 编排出第四动作 CLARIFY；实测"你好"→礼貌拒绝、"GMV口径是什么"→RAG 命中 gmv 文档 |
| 语义澄清反问 | 🟡 部分满足 | `agent/clarify.py` 识别缺失时间窗口与未定义指标（含"高活/沉默/流失…用户"分群正则），实测精准反问且禁止静默回退默认值；启发式层对未定义指标"宁可拒绝"。**缺口：单轮反问，无会话级槽位回填——用户须重述完整问题，澄清答案不能结构化合并** |
| 元数据管理 | 🟡 部分满足 | `semantic/catalog.py` 逻辑字段→物理表/列/dtype 白名单 + 受控主外键 JOIN 规则；`agent/glossary.py` 口径词典（定义/别名/公式/依赖字段）；Prompt 按主体过滤注入字段白名单与口径约定（守卫前移）。**缺口：无向量化检索（bigram 打分，系确定性取舍）；列中文注释与枚举字典（mock/metadata.py、VALUE_LABELS）未注入 Prompt，仅用于展示层** |

### 维度2：Text2SQL 生成与执行安全

| 检查点 | 结论 | 证据 |
| --- | --- | --- |
| 安全防护 | 🟡 部分满足 | LLM 只产出 DSL JSON（Pydantic `extra="forbid"` + 受限枚举），编译器不接受自由 SQL 片段；字面量类型校验+转义；执行用 `duckdb.connect(read_only=True)`。**缺口A：alias/dimension.alias/order_by.field 为自由字符串未校验未转义，直接拼入 SQL（见 P0-1）；缺口B：无 DDL/DML 语句级硬拦截器，且实测 DuckDB `execute()` 允许多语句** |
| 语法引擎适配 | 🟡 部分满足 | DuckDB 单方言深度适配（date_trunc/generate_series/INTERVAL、calendar/trailing 双模式时间窗口、1:1 事实表 LEFT JOIN 防扇出、互斥组合显式 CompileError）。**缺口：无多引擎方言层（CH/Presto/Trino/MySQL/Doris）与 Few-Shot 方言约束，换引擎需重写编译器** |
| 查询保护 | ✅ 已满足（除分区校验） | 编译器强制追加 `LIMIT`（DSL 上限 10000）；`exec/guards.py` 三重护栏：statement_timeout 线程看门狗+`conn.interrupt()`（默认30s）、EXPLAIN ANALYZE 扫描行预检熔断（默认10M）、返回行数硬上限（默认20K），均有单测覆盖。**注：无"扫描分区必填"校验（依赖澄清层软约束 + 扫描熔断兜底）** |

### 维度3：数据权限与隔离

| 检查点 | 结论 | 证据 |
| --- | --- | --- |
| 行级/列级权限注入 | ✅ 已满足 | **双保险**：① 生成前最小权限注入——`security/scope.py` 按主体过滤 Prompt 字段白名单与口径约定，越权字段不进模型视野（启发式路径同理 `_enforce_scope`）；② 生成后纵深防御——`security/guard.py apply_policy` 表级/列级校验 + RLS row_filters 注入 DSL.filters（等价 AST 层强制注入 WHERE，编译器确定性保证不可绕过）。实测：restricted 主体的 SQL 中出现 `u.province IN ('广东')`、仅返回广东行；请求 `refund_amount` → `SecurityError: 无权访问字段`；admin 对照正常。principal 由服务端从 IdentityStore 强制映射，客户端声明一律忽略并告警（有审计日志）；PBKDF2 口令 + 恒定时间比较。**注：演示为省份 RLS（机制与 tenant_id 完全同构）；会话进程内存储、JWT 弱默认密钥、无登录限流（见 P0-2/P0-4）** |

### 维度4：容错与自愈闭环

| 检查点 | 结论 | 证据 |
| --- | --- | --- |
| 自反思重试 | ✅ 已满足 | `web/service._execute_with_self_heal`：CompileError / SqlExecutionError（含超时、扫描熔断、LIMIT 熔断的精确引擎报错）喂回 LLM 重写 DSL（`SQL_SELF_HEAL_MAX_RETRIES` 默认1，强制 ≥1 次）；**重写后重新 apply_policy 防权限逃逸**；校验失败另有 build_fix_messages 修正循环。单测覆盖 `test_run_query_self_heal_rewrites`、`test_run_query_scan_cap_self_heals`、LLM rewrite 重试成功/确定性拒绝 |
| 优雅降级 | 🟡 部分满足 | 确定性兜底模式透传原始报错（拒绝而非猜测，`PipelineError` 语义明确）；`run_query` 统一捕获异常 → error 字段；前端 showError。**缺口：异常直接透出技术消息（"PipelineError: 无法识别指标…"）而非用户友好话术映射；运行时 LLM 故障（已配 Key 但网络失败）不会降级到启发式，整链路报错** |

### 维度5：结果交付与可解释性

| 检查点 | 结论 | 证据 |
| --- | --- | --- |
| 图表自适应 | ✅ 已满足 | `present/viz.py` 确定性规则（无维度单指标→指标卡、时间维度→折线、单维单指标→柱/饼、其余→表格）+ x/y 轴配置，前端零依赖 SVG 渲染。**注：无透视表形态** |
| 可解释性与溯源 | ✅ 已满足 | `present/explain.py` DSL→中文计算逻辑说明（指标公式/维度/过滤/时间窗口/排序/TopN/补零/LIMIT）；前端三面板展开 **生成 SQL、DSL JSON、计算逻辑说明**；口径询问返回带公式的指标定义文档（RAG） |
| 审计埋点 | ✅ 已满足 | `audit/record.py` 完整快照：request_id/session_id/user/principal/prompt/retrieval_context/dsl/sql/latency_ms/row_count/scan_rows/rewrites/error/created_at；JSONL + DuckDB 双写（幂等迁移）、结构化 JSON 日志 request_id 贯穿、审计失败不影响主链路；实测落盘核验通过。**注：无 Prometheus 指标暴露、无审计管理查询 API** |

---

## 3. P0 致命生产隐患

1. **标识符注入面——alias 未校验直接拼接 SQL（数据越权类）**
   `AggregateMetric.alias`、`Dimension.alias`、`OrderBy.field`（回声）均为自由字符串（仅 `min_length=1`），编译器在 `AS {alias}`、`ORDER BY {field}` 等**标识符位置裸拼接**。实测构造 alias `gmv FROM dim_user u JOIN fact_orders f2 ON 1=1 WHERE 1=1 --` 后：编译产物中 FROM 被改写、**RLS 过滤（province IN ('广东')）落入注释区**——编译器"零注入"不变量在该表面被打破。当前完整利用被两个**偶然因素**阻断：编译器把 FROM/WHERE 排在独立换行（单行 `--` 注释不覆盖后续行）+ DuckDB 拒绝未闭合 `/*`。一旦 LLM 被提示注入操纵（ChatBI 现实威胁）、或编译器排版重构/换引擎，防线即失守。**这是"靠巧合安全"而非"靠设计安全"。**
2. **无 DDL/DML 语句级硬拦截 + DuckDB 多语句执行（引擎破坏类）**
   防线完全依赖"结构上只产 SELECT"+只读连接，无最终 SQL 白名单断言；实测 `conn.execute("SELECT 1; SELECT 2")` 可执行堆叠语句。若 P0-1 的注入面未来被打通，只读连接之外无第二道网。
3. **AUTH_JWT_SECRET 弱默认值且无启动强校验（认证旁路类）**
   默认 `dev-insecure-jwt-secret-change-me` 仅有注释提醒。携带该默认值上线 = 任何人可伪造任意用户 JWT（含 admin），直接击穿 principal 服务端映射与全部 RLS。
4. **生产部署形态缺失（可用性/合规类）**：会话与审计均进程内存储（重启丢失、不支持多 worker）；登录无限流（可爆破 PBKDF2 之外的用户名字典）；服务绑定 127.0.0.1 单进程 `http.server`，无 TLS/反代/健康探活/水平扩展方案。
5. **口径误导残余（用户口径类）**：澄清反问无多轮槽位回填，用户重述问题时仍可能带入歧义；失败兜底直接透出技术异常串，业务人员无法据此自救。

---

## 4. 针对性补齐建议（按优先级）

1. **编译器标识符防御 + 语句白名单断言**（堵死 P0-1/P0-2，约1天）：
   `semantic/dsl_schema.py` 为所有 alias/`order_by.field` 增加 `pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"` 约束（Pydantic 校验失败自动进入既有自愈/拒绝路径）；`compiler/sql_compiler.py` 输出前统一以 `"` 引号包裹标识符并转义内部引号；`exec/guards.execute_sql` 入口增加硬断言：SQL 去注释后首 token 必须 `SELECT/WITH`、语句内不得出现 `;` 分隔的第二语句、不得命中 `INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|PRAGMA` 关键字。三道防御相互独立，任一失效仍有兜底。
2. **生产模式启动强校验与账号加固**（堵死 P0-3/P0-4 认证部分，约1天）：
   新增 `AUTH_STRICT=1`（或检测非 localhost 绑定）时启动即拒绝默认 JWT 密钥、拒绝 `AUTH_ENABLED=0`；登录端点按 用户名+IP 维度做指数退避限流；会话迁移到共享存储（SQLite/Redis），审计 DuckDB 独立文件加文件锁或多写者策略。
3. **澄清多轮槽位回填**（补齐口径误导残余，约2天）：
   `/api/query` 支持 `session_id` 级澄清上下文：系统反问后缓存原始 query + 待填槽位（时间窗口/指标定义），用户回答"最近30天"等短语时结构化合并进 DSL 再执行，而非要求整句重述；澄清对话同样入审计。
4. **失败兜底标准化 + 运行时降级**（补齐优雅降级，约1天）：
   异常→用户话术映射表（超时→"查询超时，请缩小时间范围"；扫描熔断→"查询范围过大"；越权→"您无权查看该数据，可联系的口径见…"；解析失败→提示可问的指标列表），技术细节折叠进"查看详情"；LLM 调用连续失败时自动切换确定性启发式并标注"降级模式"。
5. **可观测性与图表补齐**（上线后运营必需，约2天）：
   暴露 `/api/metrics`（QPS、P50/P95 耗时、自愈成功率、熔断次数、澄清触发率、意图分布）；图表推荐增加透视表（多维+多指标）形态；golden 评测纳入 CI 夜跑并增加 RLS 对抗用例（受限主体 × 敏感指标矩阵）。

---

## 附：实测取证摘要

| 用例 | 主体 | 实测结果 |
| --- | --- | --- |
| "你好，今天天气怎么样" | admin | chitchat → 礼貌拒绝 ✅ |
| "GMV的口径是什么意思" | admin | RAG → gmv/active_users 文档 ✅ |
| "高活用户有多少" | admin | clarify → 未定义指标反问 ✅ |
| "GMV是多少" | admin | clarify → 缺时间窗口反问 ✅ |
| "2024年6月广东的GMV是多少" | admin | SQL+结果284260.22+解释+指标卡 ✅ |
| "2024年6月各省的GMV是多少" | restricted | SQL 注入 `province IN ('广东')`，仅返回广东 ✅ |
| "2024年6月的退款金额是多少" | restricted | SecurityError 拒绝越权列 ✅ |
| 同上 | admin | 正常执行 131889.58 ✅ |
| "给我讲个哲学故事关于订单的" | admin | PipelineError 拒绝而非猜测 ✅ |
| alias 注入探针 | restricted | SQL 结构被改写、RLS 入注释区；执行被排版+解析器偶然阻断 ⚠️ P0-1 |
| `execute("SELECT 1; SELECT 2")` | — | DuckDB 允许多语句 ⚠️ P0-2 |
