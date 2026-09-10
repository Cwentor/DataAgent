# 质量保障

## 本地检查

```bash
black --check .
ruff check .
python -m pytest -q
```

单元测试共 35 个测试文件、573 个用例，覆盖语义契约、编译器、执行层、权限、Agent 双路径、
编排器、沙箱、技能包、供应商网关与 Web 层。

## Golden 评测

`eval/golden_dataset.json` 包含 25 个覆盖聚合、维度、时间、过滤、Top-N、窗口、补零、多轮
对话序列等场景的问答用例。评测同时检查 DSL 结构与 SQL 执行结果，并使用结果哈希保证可复现：

```bash
python -m eval.eval_runner
python -m eval.eval_runner --pipeline agent
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

`.github/workflows/ci.yml` 在 push 与 pull request 中运行：

- black 格式检查与 ruff 静态检查；
- pytest 全量测试；
- oracle 与 agent 双模式 Golden 评测。

每日夜跑还会执行完整测试、Golden 双模式及 RLS 对抗矩阵。

## 可复现锚点

- `AS_OF_DATE = 2024-06-30`；
- 随机种子 42；
- Mock DuckDB 可重复初始化。
