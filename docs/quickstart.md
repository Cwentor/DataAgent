# 快速开始

## 准备环境

项目使用 conda 环境 `dataagent`，Python 3.12：

```bash
conda create -n dataagent python=3.12 -y
conda activate dataagent
pip install -r requirements-dev.txt
```

## 初始化与验证

```bash
python -m mock.init_duckdb
python -m eval.eval_runner
python -m eval.eval_runner --pipeline agent
python -m pytest -q
```

`eval_runner` 默认运行 oracle 流程；`--pipeline agent` 运行真实 Agent（未配置 API Key 时使用启发式兜底）。如需打印编译 SQL：

```bash
python -m eval.eval_runner --print-sql
```

## 启动 Web UI

```bash
python -m web.server 8000
```

浏览器访问 `http://127.0.0.1:8000`。API 与登录示例见 [API 参考](api.md)。

## 配置 LLM

从模板创建配置文件：

```bash
copy .env.example .env
```

编辑 `.env`，至少配置 `LLM_API_KEY`。兼容 OpenAI、DeepSeek、Kimi、vLLM、Ollama 等 Chat Completions 端点，详见 [环境配置](configuration.md)。

## 离线验证

项目提供本地 OpenAI 兼容模拟服务，无需真实 Key：

```bash
python tools/mock_llm_server.py 8765
```

另开终端后指向模拟端点：

```bash
set LLM_API_KEY=sk-mock
set LLM_BASE_URL=http://127.0.0.1:8765/v1
set LLM_MODEL=mock
python -c "from agent.pipeline import run_pipeline; print(run_pipeline('2024年6月GMV多少').model_dump_json())"
```

检查当前默认 Agent：

```bash
python -c "from agent.pipeline import _default_agent; print(type(_default_agent()).__name__)"
```

`LLMNL2DSL` 表示 LLM 路径，`DeterministicNL2DSL` 表示启发式兜底。
