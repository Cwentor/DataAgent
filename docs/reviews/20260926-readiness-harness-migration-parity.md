# 评审报告｜生产就绪度：十七期底座迁移双跑对比（2026-09）

> 归属：`docs/plans/2026-09-26-agent-harness-evolution-m0-m4.md` Task 16（M4 验收收敛前置）。
> 设计文档：`docs/superpowers/specs/2026-09-24-agent-harness-evolution-design.md`。

## 一、双跑对比结论（M0 验收门实测）

双引擎（`ORCHESTRATOR_ENGINE=native | langgraph`）对同一 golden 集（25 例）双模式评测：

- **逐例结果 diff 为空**（含失败模式一致）：23 PASS + 2 FAIL，两引擎逐例一致；
- 2 个 FAIL（M1/M2 多轮用例）为**环境性失败**：未跟踪的用户配置 `config/providers.json`
  （真实 provider）在离线环境调用失败；纯净基线（无该配置）25/25——与迁移零相关；
- 双跑原始输出留档：`.harness-plan/eval_native.txt` / `eval_lg.txt`（台账目录，不入库）。

## 二、分层等价语义（M2 修订）

设计文档 §8 的"SSE 九类事件逐类 diff 为空"验收门在 M2 后按实测修订为**分层等价**：

1. **传输层契约**（`tests/test_sse_contract.py`）：FastAPI 与 stdlib 双服务对同一 run 的
   帧序列——事件类序列一致、确定性帧（plan_created/step_start/tool_start/hitl_request）
   归一后逐帧相等、帧格式逐字节一致（`data: <json>\n\n`、`: ping\n\n`、X-Run-Id）。
2. **引擎层骨架等价**（`tests/test_langgraph_parity.py`）：step_start 节点序列 +
   事件类多重集一致（审批门对事件流的唯一增量是 hitl_request 事件本身）。
3. **源数据非确定性实测**（同源 stdlib 双跑同样漂移，与迁移无关）：
   - 取数预览行无 ORDER BY——行序与行子集（前 5/共 8）逐跑漂移；
   - DuckDB 聚合/沙箱浮点累加顺序的末位抖动；
   - 沙箱/工具耗时漂移。
   因此逐字节全量 payload 比对不可行；评测结果哈希不受影响（eval 不比 preview 行序）。

## 三、Golden 扩展裁决

计划 Task 16 原定新增 2-3 个并行归因类多步 golden 用例。实测发现：**eval 的 agent 模式
（`agent.pipeline.run_production_pipeline`）不经过编排器**（走单轮 NL→DSL→SQL 工具链），
golden 框架无法为 A/B 线（审批门 / fan-out）提供验收锚点。裁决：不新增"不可执行的
装饰性用例"；B 线验收锚点由确定性单测承担：

- `tests/test_fanout.py`：顺序无关确定性 / 单失败不炸主图 / 并发上限 / 重派 ≤1 轮 /
  缺口如实披露 / native 串行降级 / done 事件携带 run manifest；
- `tests/test_subagent.py`：白名单越集拒绝 / 预算硬顶 / 无 LLM 确定性降级；
- `tests/test_autonomy.py` + parity 三分支：L1-L4 行为矩阵 / approve-edit-reject。

## 四、迁移验收门核对（规格 §8）

| 验收项 | 状态 |
| --- | --- |
| 全量单测全绿 | 731/731（M3 收敛时点） |
| oracle 评测双跑 | 逐例一致（23+2 环境性；纯净基线 25/25） |
| SSE 事件 diff | 分层等价成立（本报告第二节） |
| 双跑对比输出 | 留档（本报告第一节） |
| 前置审计 | `docs/reviews/20260926-audit-orchestration-concurrency.md` |
| recursion_limit 校准 | `test_recursion_limit_anchors_termination`（limit=3 确定性触发护栏） |
| checkpointer 重启恢复 | `test_checkpointer_survives_process_restart` |

## 五、遗留风险与后续

- `config/providers.json` 为用户环境配置（未跟踪）：CI/离线环境多轮 golden 用例将失败，
  建议后续为 eval 增加 provider 隔离开关（本轮不做）。
- 前端审批卡 / 自主性下拉的浏览器端交互已由端到端 SSE 测试覆盖协议层，视觉验收需人工
  冒烟（M2 验收门记录在台账）。
