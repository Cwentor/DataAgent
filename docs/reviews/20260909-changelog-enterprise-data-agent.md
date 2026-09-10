# 评审报告｜升级变更记录：企业级 Data Agent 重型升级（2026-09）

> 分支：feat/relaxed-chat-constraints；计划与决策详见
> [docs/plans/20260909-enterprise-data-agent-plan.md](../plans/20260909-enterprise-data-agent-plan.md)

## 升级总览

FutureBI 从"契约绑定的 ChatBI 引擎"升级为"全周期企业级 Data Agent"：
在**完整保留**既有确定性安全底座（DSL 契约 -> 确定性编译 -> 受控执行）的前提下，
新增多步分析推理、根因归因、沙箱代码执行与自主报告合成能力。

## 架构分层（新增 core/ 四包）

| 包 | 职责 | 关键提交 |
|---|---|---|
| `core/retrieval` | 既有 DSL 链路的 typed Tool 门面（`execute_dsl_query -> ParquetRef`）、PII 脱敏导出、裸 SQL 网关守卫 | 1b87b87 |
| `core/orchestrator` | StateGraph 图式编排（六节点 + 条件边 + HITL + 反思重规划 ≤3 次自愈） | 507c4cd |
| `core/sandbox` | 沙箱代码解释器（AST 守卫 + 限权 runner + Docker/子进程双后端） | c583b1d |
| `core/skills` | 归因技能包（熵下钻 / 分解树 / DTW / Holt-Winters / Shapley） | 361cd66 |

## 安全与合规（非妥协项落实）

1. **DSL 边界强制**：`core/retrieval/guardrails.py` 在网关层拒绝一切 SQL 形态
   （字符串载荷 / `sql` 键 / 自由文本 SQL），命中抛 `GuardrailViolation`；
   LLM 规划产出的分析代码进入沙箱前先过 AST 静态校验。
2. **沙箱纵深**：静态层（模块/调用/dunder 三层黑名单 + getattr 常量逃逸拦截）
   -> 运行时层（模块白名单 import、workspace 受限 open、动态求值类内建不注入）
   -> 进程层（Docker `--net=none --cap-drop=ALL --read-only --user 10001` cgroups
   硬限 / 子进程超时强杀 + POSIX rlimit；Windows 诚实标注 `limits_enforced=False`）。
3. **数据外泄防护**：导出前 PII 脱敏（列名启发式 + 邮箱/手机/身份证形态正则，
   sha256 确定性掩码保留分组性）；行数上限截断（默认 5 万行）；沙箱 summary
   聚合矩阵 ≤100 行硬限；stdout/stderr 捕获上限 256KB。
4. **测试凭据治理**：源码/测试零凭据字面量，`tests/fixture_keys.fake_key` 运行时生成；
   Mimosa 全项目审计 11 高危清零（SQL 拼接参数化/白名单、SSRF 改 http.client
   显式建连 + 协议白名单）。

## 端到端验收（tests/test_agent_e2e.py）

- 诊断问题（"GMV 下滑原因，按地区品类定位"）→ 多步执行日志 + Parquet 数据集 +
  归因摘要 + 可渲染 ECharts 规格产物；
- 自愈回路：注入一次性失败 → error_context 记录 → 重规划 → 二次成功，
  `self_heal_count` 可观测；重试耗尽如实放弃（不编造）；
- HTTP 全流程：`/api/agent/run` → clarify 中断（resume_token）→ 答复恢复 → done。

## 质量门终态

- `python -m pytest -q`：**505 passed**（升级前基线 435）
- `black --check .` / `ruff check .`：全绿
- Mimosa 全项目审计：0 高危 / 0 中危 / 2 低危（seeded Random，确定性复现设计意图）
