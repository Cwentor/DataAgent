四项实现缺口按风险从低到高分四步串行处理，每步完成即跑相关测试，最后全量质量门。关键设计决策（用户未答时按推荐执行）：沙箱后端走 settings 开关（默认子进程不变）；L3 高危确认端到端实现（前端已展示该选项，兑现承诺）。

## 第 1 步：修复 config/semantic.json 与 policies.json 的 dim_shop 遗漏（缺口 4）

探明结论：git 历史无有意排除的证据，判定为遗漏；catalog_loader 的 overlay 是整体替换而非合并，导致服务启动后 dim_shop 整表对语义层不可见（「店铺/门店」意图词失效、反思器判店铺概念不可执行），且 web 与内置目录两套口径不一致无测试覆盖。

- 对照 `semantic/catalog.py` 内置定义，在 `config/semantic.json` 补：`shop_id`（fact_orders.shop_id）、`shop_name`（dim_shop.shop_name，中文 label+aliases 店铺/门店）、`product_name`（dim_product，顺带补齐同源遗漏）；`aliases` 补 dim_shop 别名 s；`join_rules` 补 dim_shop inner join。
- `config/policies.json` 三角色 allowed_tables 补 `dim_shop`。
- 补回归测试：overlay 生效后 shop_name 可编译可查（tests/test_catalog_loader.py 或 web 链路测试），堵住两套口径缺口。
- 验证 test_agent / test_intent（内置目录口径）不回归。

## 第 2 步：Subagent allowed_tools 执行路径裁剪（缺口 2）

探明结论：allowed_tools 仅做构造校验（⊆ registry），子图节点直接复用主图 execute_dsl_query/run_code，白名单是死配置。

- 语义定义：allowed_tools = 子任务允许的能力名集合；合法名扩为 registry 工具名 + 内置能力名 `run_code`（`state.py` SubagentTask 校验同步扩展）。能力归并：query 能力 = {query_metric, trend_analysis, export_report} 任一；analyze 能力 = run_code。
- `subagent.py`：SubagentState 加 allowed_tools 字段，run_subagent 从 task.allowed_tools 填入；`_build_subagent_app` 用轻量 wrapper 节点（贴合既有 gate/wrapper 纪律，不污染主图 nodes.py）按 step.kind 检查所需能力 ∈ 白名单，不满足则确定性跳过该步骤并在子任务报告中如实标注原因（严禁静默吞掉）。
- 兼容：空白名单 = 不限制（现有 test_fanout 全传空列表，保持向后兼容）。
- 补测试：白名单不含查询能力 → query 步骤跳过且报告如实披露；含 → 正常执行；空白名单行为不变。

## 第 3 步：沙箱后端 settings 开关（缺口 3）

- `config/settings.py` 新增 `SANDBOX_BACKEND`（默认 "subprocess"；可选 "auto"/"docker"）。
- `core/sandbox/backends.py`：default_backend() 加进程级缓存（模块级单例 + 锁，仿 langgraph_engine._app 模式），避免每次 run_code 都 spawn docker image inspect。
- `core/sandbox/api.py`：run_code 默认后端解析改为显式传参 > settings——subprocess 恒子进程（默认，行为不变）；auto 走 default_backend() 缓存探测；docker 显式用 DockerBackend，不可用时如实降级子进程并在结果/日志标注降级（不静默）。
- `.env.example`、`docs/configuration.md` 补 SANDBOX_BACKEND；`docs/architecture.md`/README 沙箱表述改为「默认子进程，SANDBOX_BACKEND 可切 auto（Docker 优先探测）/docker 强隔离」。
- 测试：默认路径回归不变；auto/docker 路径 monkeypatch 探测函数验证分发与降级标注。

## 第 4 步：L3 高危确认端到端（缺口 1）

高危操作定义：编排链路中沙箱执行 LLM 生成代码（analyze 步骤 step.code 非空）。

- `core/orchestrator/langgraph_engine.py`：新增 _risk_gate 轻量节点插在 analyze 前（plan→query→analyze 与 plan→analyze 两条路径），仿 _plan_gate 模式：L3 且即将执行 LLM 产码且未确认 → interrupt(kind=high_risk, question+resume_token)；L4 经 maybe_interrupt 直通并发 auto_resolved 事件；L1/L2 不打断（保持现状）。critic 重规划后确认态复位。
- `core/orchestrator/state.py`：新增 high_risk_approved 字段（extra=forbid 契约内）。
- `core/orchestrator/autonomy.py`：high_risk 触发点接入 maybe_interrupt 的 L4 直通查表（复用现有机制，L3 走真 interrupt）。
- `web/runs.py`、`web/api.py`：kind=high_risk 透传与 resume（action=approve|reject）解码。
- `web/static` 前端：hitl_request kind=high_risk 渲染确认卡（approve/reject，复用审批卡样式与上送通道）。
- 测试：L3 挂起-恢复用例（approve 放行 / reject 转 blocked 如实拒答或终止）、L2/L4 回归不变、SSE 契约确定性帧集合更新、web 层 kind 透传用例。
- 文档：README/architecture 的 L1-L4 描述同步（L3 高危确认已落地；L1 step_confirm 逐节点确认仍未实现，如实标注）。

## 收尾

- `black --check .` + `ruff check .` + `python -m pytest -q`（766+ 全绿）。
- 四步各自独立成 commit（遵循仓库 commit 规范：feat/fix 前缀 + 简体中文描述），用户确认后再提交。
- 顺序依据：1/2 是纯正确性修复（风险低、无行为分叉），3 引入新配置面（中等），4 是最大功能开发（涉及前后端契约），故按 1→2→3→4 排序。
