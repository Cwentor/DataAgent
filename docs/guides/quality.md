# 质量保障

## 本地检查

```bash
black --check .
ruff check .
python -m pytest -q
```

单元测试共 59 个测试文件（与源码包一一对应 14 个子目录：agent / audit / auth / compiler / core / eval / exec / persistence / present / providers / security / semantic / tools / web），覆盖语义契约、编译器、执行层（含执行前审计）、权限、
Agent 双路径、编排器（含重规划自愈上下文、意图路由收敛、诚实拒答短路、枚举预路由直答）、结果断言（DataQA）、
动态 profiling、沙箱、技能包、供应商网关、Grounding 数值溯源、日志可见性回归与 Web 层。

## Golden 评测

`eval/golden_dataset.json` 包含 31 条 Gmall 电商用例，覆盖聚合、维度、时间、过滤、Top-N、窗口、补零、多轮
对话序列等场景。评测同时检查 DSL 结构与 SQL 执行结果，断言分层为契约层（DSL/SQL 形态，--skip-snapshot）
与快照层（结果集）两级，并使用结果哈希保证可复现：

`eval/intent_golden.json`（十八期 + 二十期）为意图路由评测：12 个用例断言编排终态行为——基数问题
计数直答（问省份数严禁答金额）、指标锚定直答（含缺省口径说明）、目录外实体诚实拒答（附
能力清单）、枚举预路由直答（全量行清单截断分流），持续监控"答非所问率"与虚构风险：

```bash
python -m eval.eval_runner
python -m eval.eval_runner --pipeline agent
python -m eval.intent_eval
```

## DoD 端到端验收

```bash
python -m tests.dod_e2e_check
```

一次性验收脚本（不进 pytest 套件）：本地启动 mock 三协议供应商服务（OpenAI Chat /
Responses、Anthropic Messages），经 `web.service.run_query` 携带 `provider_id` / `model_id`
发起真实查询，验证意图路由与 DSL 生成的 LLM 调用命中自定义协议供应商、最终产出合法 DSL
与确定性 SQL 的请求级切换全链路。

## CI

`.github/workflows/ci.yml` 在 push（master）、pull request 与每日定时中运行：

- black 格式检查与 ruff 静态检查；
- pytest 全量测试；
- oracle 与 agent 双模式 Golden 评测；
- 意图路由评测（`eval.intent_eval`，答非所问率防回归）。

每日夜跑还会执行完整测试、Golden 双模式、意图路由评测及 RLS 对抗矩阵。

## 可复现锚点

- `AS_OF_DATE = 2025-12-31`；
- 随机种子 42；
- Mock DuckDB 可重复初始化。
