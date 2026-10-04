# 十九期 M7 实施计划：验收收口（铁律改写 / 文档同步 / 红线入 CI / 安全复审）

> 沿用 M1-M6 执行模式；前置：HEAD @ f020020，985 测试绿。

**Goal:** 十九期收官——铁律与文档与新架构对齐、红线矩阵核对入 CI、安全复审落盘、设计承诺偏差显式修订。

**Tasks:**

### Task M7-T1: AGENTS.md 铁律改写与目录同步
- 铁律：旧「严禁绕过 DSL 试图直接拼接生成裸 SQL」→ 新「LLM 产出的 SQL 永不直接执行——必须经提升闸门（core/retrieval/sql_lift）转译为 DSL 契约后由确定性编译器重新生成，或在安全视图上经受治理探索执行（core/retrieval/exploration）；一切执行只发生在治理管道内」
- 目录结构段补 `security/views.py`、`core/retrieval/exploration.py`、`core/retrieval/sql_lift.py`
- 关键约定补一条：探索层三件套（视图重写/审批门/TEMP 视图）与分级透明作答（assumptions）
- [ ] Commit `docs: AGENTS.md 铁律改写与目录同步（十九期 M7）`

### Task M7-T2: roadmap / architecture / 设计文档状态同步
- roadmap.md 增十九期行；architecture.md 增三层同心圆与探索审批门描述
- 设计文档状态改「已实施（M1-M6 完成，M7 收口）」；§3.4「编译器产 SQL 改查安全视图」承诺显式修订（理由：编译器注入 RLS 已等价 + 双保险一致性测试锁定，视图层服务探索层；评审 MEDIUM-1 建议采纳）
- [ ] Commit `docs: roadmap/architecture/设计文档同步（十九期 M7）`

### Task M7-T3: 红线矩阵核对与题库补全
- 核对五类红线（越权列/行、写操作、外部访问、资源炸弹、PII）在 CI 的落位：越权列（test_security 表达式回溯）、越权行（test_security_views 语义一致性 + RLS 对抗矩阵）、写操作/外部访问（test_security_views harden + exec guards）、资源炸弹（test_exploration 行数熔断 + test_exec）、PII（export 管道既有测试）——补漏
- [ ] Commit `test: 红线矩阵核对与补全（十九期 M7）`

### Task M7-T4: Mimosa 式安全复审落盘
- 对 M1-M6 diff 做安全复审（提升闸门绕路面 / 视图层逃逸面 / 审批门绕过面 / 沙箱旁路）→ `docs/reviews/20261003-security-review-capability-boundary-m1-m6.md`
- [ ] Commit `docs(reviews): 十九期安全复审（M7）`

### Task M7-T5: 全量收口
- [ ] 全量 pytest / black / ruff / intent_eval / 双模式 eval + 记忆更新
