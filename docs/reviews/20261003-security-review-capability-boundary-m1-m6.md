# 评审报告｜安全评审：十九期能力边界扩展 M1-M6（2026-10）

> 评审日期：2026-10-03 · 评审方式：攻击面走查（四条安全边界逐一推演）+ 红线测试矩阵核对 + 前序 PR 评审修复闭环核验 · 评审范围：`git diff d75b9ce..HEAD`（十九期 M1-M6 全量）
> 结论：**未发现可利用的高危漏洞**；2 项延后 Minor 与 1 项设计承诺修订如实记录（均不构成可利用路径）。

## 1. 评审对象与安全边界

十九期引入的四条新安全边界（M1-M6）：

| 边界 | 实现 | 防线形态 |
| --- | --- | --- |
| SQL 提升闸门 | `core/retrieval/sql_lift.py` | 白名单提升，拒升精确清单；解析超时 3s + 长度上限 20K |
| 探索层执行 | `core/retrieval/exploration.py` | 表名全量重写到 sec_* 视图 + 三护栏 + PII 脱敏 |
| 安全视图层 | `security/views.py` | 禁列物理投影、RLS 固化、TEMP 视图、连接加固 |
| 沙箱取数旁路 | `core/sandbox/ast_guard.py` | `connect`/`database` 调用封死（含别名导入形态） |

## 2. 攻击面走查（逐条推演）

### 2.1 提升闸门（L2）

- **注入面**：LLM 产出的 SQL 直入 `sqlglot.parse_one`——已有 3s 线程看门狗 + 20K 长度上限（PR 评审 HIGH-S1 修复）；解析失败/超时一律拒升（保守性优先）。
- **错译面**：字面量经 `compiler._literal` 按 dtype 强转义；多时间字段窗口、JOIN ON 非 EQ 谓词两处"静默错译"已按 PR 评审 HIGH 修复为显式拒升（`git log` 3950475 前的评审修复批次）；执行等价真值断言（`tests/test_sql_lift.py`）锁定提升语义。
- **资源面**：提升产物由 `compile_sql` 确定性重编译，走 `execute_sql` 三护栏（超时/扫描熔断/行数硬上限）。
- **残留**：sqlglot 解析器自身漏洞为供应链风险（版本锁定 30.18，随依赖审计走）。

### 2.2 探索层（L3）

- **越权表**：`rewrite_sql_to_views` 对全部 `exp.Table` 节点核对——基表必须重写到 sec_* 视图（principal 无视图即拒绝），未登记表拒绝，CTE 别名跳过（其体照常重写）；大小写混淆（如 `FACT_ORDERS`）因目录精确匹配而 fail-closed 拒绝。
- **禁列**：双重防线——重写后 SQL 只见 sec_* 视图（禁列物理不存在）；`exploration_risk` 文本级敏感判定是 L4 自动放行的硬边界（含敏感列强制人工审批）。`SELECT *` 形态的文本级绕过由视图层兜底（sec 视图本就无禁列，无法泄露）。
- **审批绕过**：L4 自动放行仅当"无敏感列"；principal 取 `state.principal`（web 链路服务端绑定，M6 修复了硬编码 admin 使边界失效的缺陷）。deny → 诚实拒答，无重试路径。
- **写操作**：探索 SQL 经 `execute_sql` 只读白名单硬拦截（`tests/test_exploration.py::test_execute_exploration_rejects_write_statement` 端到端锚点）。
- **残留（Minor）**：`allow_session` 仅本轮状态内生效，跨轮持久化未接 session store——影响为 UX（重复询问），非安全面（宁多问不漏问）。

### 2.3 安全视图层

- **视图逃逸**：视图安装于连接的 TEMP catalog（read_only 连接可用、不污染库文件）；**基表在受管连接上仍物理可达**——隔离依赖三重补偿：①探索 SQL 表名全量重写（本节 2.2）；②`execute_sql` 只读结构断言；③`harden_connection`（enable_external_access=false + lock_configuration，ATTACH/COPY/read_csv 实测拒绝）。设计 §3.4 原"编译器产 SQL 改查安全视图"承诺已显式修订（编译器注入 RLS 等价 + 双保险语义一致性测试锁定，见设计文档状态段）。
- **视图定义注入**：`_render_predicate` 仅支持 eq/in 两形态，列名取自语义目录、字面量经 `_literal` 强转义——策略文件（policies.json）为管理员受控输入，非用户输入面。
- **RLS join 面跨表过滤**：BFS 受控连接路径仅沿语义目录 JOIN_RULES/FACT_JOIN_RULES 展开，无法构造任意 join。

### 2.4 沙箱旁路

- `connect`/`database` 双调用名封死，`duckdb.connect(...)`（含内存库）、`import duckdb as d; d.connect(...)`、`from duckdb import connect`（别名导入，PR 评审 MEDIUM-S5 已修）四形态全部静态拒绝；`duckdb.read_parquet` 合法消费放行（实证）。
- 运行时兜底：`_ALLOWED_IMPORT_ROOTS` 白名单本就不含 duckdb 直连所需形态——纵深防御成立。

## 3. 红线矩阵核对（六类，全部入 CI）

| 红线 | CI 落位 |
| --- | --- |
| 越权列（含表达式回溯） | `tests/test_security.py`（restricted 对抗矩阵 + 表达式回溯专项） |
| 越权行（RLS） | `tests/test_security_views.py`（视图固化 + guard 语义一致性交叉验证）+ `tests/test_security.py` |
| 写操作/DDL/多语句 | `tests/test_exec.py`（UnsafeSqlError）+ `tests/test_exploration.py`（探索端到端锚点） |
| 外部访问 | `tests/test_security_views.py`（ATTACH/COPY/read_csv 实测拒绝） |
| 资源炸弹 | `tests/test_exec.py`（超时/扫描熔断）+ `tests/test_exploration.py`（行数硬上限） |
| PII 出域 | `tests/test_retrieval.py`（mask 管道；探索导出复用同一 `export_to_parquet`） |

## 4. 前序评审修复闭环核验

M1-M5 PR 评审（20261003-pr-review）全部 HIGH×3 / MEDIUM×7 / LOW×2 已闭环：并发修复批次 + 本会话核对（LOW1 多维枚举 goal 于 3950475 收口）；MEDIUM-S5 别名导入封堵已实证。

## 5. 遗留事项（如实登记，均非可利用路径）

1. **allow_session 跨轮持久化**（Minor，UX）：本轮状态内生效；跨轮需接 session store，M7 后按需立项。
2. **sqlglot 供应链**（信息项）：解析器直接暴露给 LLM 输出，依赖版本锁定与定期审计。
3. **探索连接命名空间**（信息项）：受管连接上基表物理可达，隔离靠重写 + 只读断言 + 连接加固三重补偿；若未来引入交互式 SQL 编辑面，须先补连接级 schema 隔离。
