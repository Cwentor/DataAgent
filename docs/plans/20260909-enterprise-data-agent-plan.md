# 执行计划｜FutureBI 企业级 Data Agent 重型升级（2026-09）

> 状态：执行中（自审用计划，用户明确不审核；按阶段细粒度提交）
> 基线：435 passed 全绿；Mimosa 全项目审计 0 高危（88fd3c7 / c3e426b 已落）

## 0. 目标效果确认（达成后的可验证产物）

1. 既有 ChatBI 链路（semantic → compiler → exec → present）**零破坏**，435+ 测试全绿；
2. 新增 `core/` 四包：`retrieval`（既有链路的门面与 typed Tool）、`orchestrator`（图式编排）、
   `sandbox`（代码解释器）、`skills`（归因技能包）；
3. 编排器实现 StateGraph 范式：State Schema（session/turn/trace_id、plan_steps、
   tool_calls/results、scratchpad、artifacts、error_context）+ 六节点
   （Clarify / Planner / DSLQuery / CodeExec / Critic / Synthesizer）+ 条件边 + 重规划
   （≤3 次自愈）+ HITL 澄清中断/恢复；
4. 沙箱：AST 静态黑名单（import os/sys/subprocess/socket/pty、eval/exec/open/__import__/
   globals/locals 等）+ 限权 builtins + 工作区目录隔离 + 15s 超时强杀 + CPU/内存/输出
   限额（Docker 后端 cgroups；子进程后端尽力而为并如实标注）；
5. 数据交换：DSL 查询结果导出 Parquet 到 `workspace/{session}/inputs/`（PII 脱敏 +
   行数上限），脚本只读输入、输出 summary.json + echarts_spec.json（聚合矩阵 ≤100 行）；
6. 技能包：熵/信息增益下钻、乘法/加法指标分解树、DTW、Holt-Winters、Shapley 归因；
7. E2E：诊断类问题（GMV 波动归因）产出多步日志 + 自愈记录 + 归因摘要 + ECharts 规格；
8. 质量门：black / ruff / pytest 全绿；Mimosa 审计 0 高危；分阶段细粒度提交。

## 1. 关键技术决策（基于现状探测）

| 决策 | 结论 | 依据 |
|---|---|---|
| core/retrieval 形态 | 门面 + typed Tool，**不物理搬移** semantic/compiler/exec | "Do NOT destroy"；435 测试的 import 面保护 |
| StateGraph 依赖 | 自研轻量引擎（不引入 langgraph） | 项目零框架依赖哲学；langgraph 拉入 pydantic-graph 等重依赖 |
| 沙箱后端 | 可插拔：DockerBackend（探测可用时）+ SubprocessBackend（默认） | 本机无 Docker；接口对齐 gVisor/Firecracker 演进位 |
| 沙箱数据读取 | 优先 pandas/polars，兜底 **duckdb 读 Parquet** | 本机无 pandas/polars；duckdb 既有依赖 |
| 技能包数值栈 | numpy（新增依赖）+ 纯 Python 实现 | DTW/熵/Shapley/Holt-Winters 手写，免 statsmodels/sklearn 重依赖 |
| 原始 SQL 边界 | 网关层 `GuardrailViolation`：DSL payload 内出现 SQL 形态立即拒绝 | 需求 §3.1 非妥协项 |
| LLM 接入 | 复用 providers 网关（chat_text）；无 Key 时确定性启发式规划 | 既有哲学：离线可运行、可单测 |
| Arrow/Parquet | pyarrow + numpy + pandas 进 requirements-dev（开发依赖） | 沙箱与技能包运行栈；生产检索链路不引入 |

## 2. 阶段拆解与提交节点

### Phase 0：现状收尾（已完成）
- [x] 上会话遗留修复提交（c3e426b）
- [x] Mimosa 11 高危真实修复（SQL 拼接参数化/白名单、SSRF 改 http.client 显式建连）（88fd3c7）
- [x] 测试假密钥运行时生成（tests/fixture_keys.fake_key），源码零凭据字面量

### Phase 1：core 包骨架 + 计划落盘（commit 1）
- `core/__init__.py`、`core/retrieval/`、`core/orchestrator/`、`core/sandbox/`、`core/skills/`
- 本计划文档（本文）+ AGENTS.md 目录结构段更新
- requirements-dev 增加 numpy/pandas/pyarrow

### Phase 2：retrieval 门面 + typed Tool（commit 2）
- `core/retrieval/parquet_ref.py`：ParquetRef（路径/行列数/schema/hash，惰性校验）
- `core/retrieval/export.py`：结果集 → Parquet 导出（PII 脱敏器 + 行数上限 + 输入目录协议）
- `core/retrieval/tools.py`：`execute_dsl_query(dsl) -> ParquetRef`、
  `compile_dsl_to_duckdb(dsl) -> SQL`；复用 security.guard + compiler + exec.guards
- `core/retrieval/guardrails.py`：原始 SQL 网关（SQL 形态检测 → GuardrailViolation）
- 单测：ParquetRef、脱敏、行数上限、guardrail 拒绝

### Phase 3：沙箱执行引擎（commit 3/4）
- `core/sandbox/ast_guard.py`：AST 白名单校验（模块黑名单 + 危险调用黑名单 + 属性访问检查）
- `core/sandbox/runner.py`：受限执行入口（限权 builtins、workspace-scoped open、
  超时、输出上限、summary.json 协议）
- `core/sandbox/backends.py`：`SandboxBackend` 协议 + SubprocessBackend +
  DockerBackend（`--net none --cap-drop ALL --memory --cpus --user 10001`）
- `core/sandbox/api.py`：`run_code(code, inputs, limits) -> SandboxResult`
- 单测：AST 拦截矩阵、超时、越界文件读写拒绝、PII 输入脱敏、限额

### Phase 4：图式编排器（commit 5/6）
- `core/orchestrator/state.py`：AgentState 契约（Pydantic，extra=forbid）
- `core/orchestrator/graph.py`：StateGraph 引擎（节点/条件边/中断恢复/迭代上限/token 预算修剪）
- `core/orchestrator/nodes.py`：六节点（Clarify 触发 HITL 中断；Critic 触发重规划）
- `core/orchestrator/prompts.py`：Planner/Coder/Reflector 系统提示词 + Few-Shot（指标分解树示例）
- `core/orchestrator/agent.py`：编排入口 `run_agent(question, ...) -> AgentTrace`
- LLM 规划（providers.chat_text）+ 确定性启发式兜底
- 单测：图引擎（条件边/中断恢复/预算修剪）、状态契约、重规划上限

### Phase 5：归因技能包（commit 7）
- `core/skills/drilldown.py`：熵 / 信息增益维度的异常定位
- `core/skills/decomposition.py`：乘法（GMV=UV×CR×AOV）/加法分解树
- `core/skills/timeseries.py`：DTW + Holt-Winters 三次指数平滑异常检测
- `core/skills/shapley.py`：Shapley 值偏差归因（因子 ≤8 的精确排列法）
- 技能以"沙箱可执行脚本模板 + 可导入库"双形态提供
- 单测：合成数据上的数值正确性（与已知解对拍）

### Phase 6：端到端验证 + Web 暴露（commit 8）
- `tests/test_agent_e2e.py`：GMV 诊断问题 → 多步日志 + 自愈日志 + 归因摘要 + ECharts 产物断言
- `web/server.py`：`POST /api/agent/run`（同步简化版）+ 鉴权复用既有网关
- mock 数仓补一个确定性"下滑窗口"情景（如需）

### Phase 7：文档与收尾（commit 9）
- AGENTS.md 架构段 / README 能力段 / CHANGELOG
- 全量质量门 + 推送 + 记忆更新

## 3. 风险与缓解

- **Windows 无 cgroups**：子进程后端超时强杀 + 输出上限强制；内存/CPU 限额仅在
  Docker 后端强制，结果元数据如实标注 `limits_enforced` 字段，不虚报。
- **不装重依赖**：statsmodels/sklearn 不引入；HW/DTW/Shapley 手写并对拍已知解。
- **现有测试零回归**：每阶段跑全量 pytest；core/ 仅新增不改旧行为。
- **Hook 迭代**：Mimosa PreToolUse 对 execute(变量)/f-string SQL 敏感，新代码遵循
  "execute(内联字面量, params)"纪律，标识符走白名单函数。
