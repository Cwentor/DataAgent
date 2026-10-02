# 评审报告｜深度审计：clarify 挂起对新问题的误路由隐患（2026-10）

> 评审日期：2026-10-02 · 评审方式：M1 执行中缺陷定位 + 根因复现 · 评审范围：LangGraph 编排会话状态生命周期（`core/orchestrator/`）
> 状态：**已复核并修复**（2026-10-02，复核结论与修复记录见 §6/§7）

## 1. 问题描述

同一会话内，某轮对话以 `phase=clarify` 挂起（选项式澄清等待用户答复）后，**用户忽略澄清、直接提出的新问题会被当作该澄清的答复合并进旧查询**，导致误路由。

复现序列（`eval/intent_golden.json` 11 问连续同会话运行，MemorySaver 进程内 checkpointer）：

```text
done    | 有多少个省份
...
clarify | 海南省的GMV是多少        ← 挂起，等待澄清
done ref| 退款率是多少            ← 被合并为 "海南省的GMV是多少（用户补充：退款率是多少）"
done ref| 把全部品牌名列举给我    ← 同上，误路由为澄清答复
```

十九期 M1（提交 092d197）的意图评测新增枚举用例时暴露该问题；此前 8 条用例恰好全部期望"拒答"，污染路径的输出也恰好是拒答，因此**从未暴露**。M1 已在评测侧改为每用例独立会话（`eval/intent_eval.py`），评测绿但产品行为未变。

## 2. 根因

- `core/orchestrator/langgraph_engine.py:85-100`（`_clarify_gate`）：clarify 挂起经 LangGraph `interrupt()` 实现，恢复时把 resume 值合并为 `user_query（用户补充：…）`；
- `core/orchestrator/agent.py:280-292`（新问题入口）：`run_agent` 收到**不带 `resume_state` 的新问题**时，仍以 `thread_id=f"{user}:{session_id}"` 调用 `invoke_langgraph`——该线程在 checkpointer 中存在未消费的 clarify `interrupt`，新输入触发恢复语义，新问题文本被 `_clarify_gate` 当作 `resume_value` 合并；
- 影响：clarify 挂起 + 用户放弃澄清改问新问题 = 新问题被吞、旧查询被错误续跑；
- 放大条件：`ORCHESTRATOR_CHECKPOINT_DB` 配置为 SqliteSaver 时（settings.py:119），挂起跨进程重启持久存在，隐患窗口从"进程内"扩大到"跨重启"。

## 3. 影响评估

- 触发条件常见（真实对话中"系统反问后用户换话题"是高频行为）；后果为**答非所问**（诚实性原则的核心禁区），且报告口径基于被污染的合并查询；
- 无越权/数据泄露风险（权限管道不受影响），纯语义路由缺陷；
- 触及"宁可拒答，绝不答非所问"的用户核心原则，优先级建议：**高**。

## 4. 修复方向（候选，待审核拍板）

| 方案 | 内容 | 代价/风险 |
| --- | --- | --- |
| A. 线程按轮隔离（推荐评估） | `_checkpoint_thread_id` 引入 turn 维度（resume 经 `resume_state.turn_id` 找回同线程），新问题天然新线程 | 触及 HITL 恢复核心路径（web 层 plan_review/high_risk 恢复调用点），需全量回归 L1-L4 自主性与审批门测试 |
| B. 挂起探测 + 显式废弃 | fresh invoke 前经 `app.get_state` 探测线程存在未消费 clarify 中断 → 丢弃陈旧挂起、按新问题重开 | 依赖 LangGraph 内部状态 API；"丢弃"语义需审计留痕（诚实可见化） |
| C. 会话级澄清槽位 TTL/显式取消 | 复用 persistence 澄清槽位机制，新问题先检查并清除待答澄清 | 与 web 层澄清交互协议耦合，需前端确认行为 |

三个方案都需补齐评测：意图评测恢复"多轮混排 + 挂起后新问题"场景（不能再用每用例隔离会话掩盖），并加"澄清挂起后新问题必须按新查询路由"的回归锚点。

## 5. 处置建议

不阻塞 M2/M3 主线；建议作为独立修复项插入 M3（分级透明作答与多轮语义同域）或更早，由用户拍板方案后实施。

## 6. 复核结论（2026-10-02，修复前审核）

### 6.1 原报告 §2 的根因机制**不成立**（钉版依赖上不可复现）

- 微实验（独立最小图 + `interrupt()`，langgraph 1.2.12）：对存在未消费 interrupt 的线程以**全新输入** invoke，引擎**不会**把输入当作 resume 值注入 `interrupt()`——而是开启新一轮执行，节点重跑到中断点后**再次挂起**（`__interrupt__` 重新出现、resume 返回值恒为 None）。若新问题不触发澄清，则本轮直接跑完，挂起被自然吸收；
- 端到端复现（本报告 §1 的 11 问共享会话序列）在 HEAD 与 092d197^（d51b963）上均无污染：clarify 挂起后的"有多少个省份"正确直答 8、无"（用户补充：…）"合并痕迹，全部用例 PASS。报告引用的合并串无法由"新输入触发恢复语义"产生（输入是整个 State dict，若真被合并应出现 dict 字符串而非纯问题文本）。

M1 当时观察到的"污染"另有成因（未能复现），原 §1 复现序列在当前代码上不成立。

### 6.2 但复核确认了**相邻的真实隐患**（thread_id 仅到会话粒度）

`_checkpoint_thread_id = f"{user}:{session}"` 使同会话所有轮共享一个 checkpointer 线程，由此：

1. **旧卡答复串扰（真实误路由）**：同会话两轮先后澄清挂起（轮 1 未答、轮 2 又挂起），线程上叠加两个 interrupt；此时答复**轮 1 的旧卡片**经 `Command(resume=...)` 恢复的是线程上最新的 interrupt——轮 1 的答复被合并进**轮 2 的查询**，输出格式恰为报告描述的"（用户补充：…）"合并形态。已用双澄清序列实测复现（修复前）；
2. **同会话并发 checkpoint 竞争**：web 层编排在后台线程执行，同会话并发提问会对同一线程并发 invoke，checkpoint 写入相互覆盖；
3. **子图线程跨轮碰撞**：subagent 线程 `f"{thread_id}:sub:{task_id}"` 派生自会话级 thread_id，同 task_id（如 s1）跨轮同碰撞。

SqliteSaver 下隐患窗口跨重启持久（原报告放大条件仍成立）。权限管道不受影响，纯语义路由/状态隔离缺陷，维持**高优先级**结论。

### 6.3 方案拍板

采纳**方案 A（线程按轮隔离）**：新问题天然新线程，恢复经 `resume_state.turn_id` 找回挂起轮线程，web 层零改动（`resume_state` 自带挂起轮 `turn_id`）；同时消除 §6.2 全部三类隐患，并把"挂起被新输入自然吸收"从对 LangGraph 隐式语义的依赖变为显式架构保证。

## 7. 修复记录（2026-10-02）

- `core/orchestrator/agent.py`：`_checkpoint_thread_id` 引入 turn 维度，规范改为 `f"{user_id}:{session_id}:{turn_id}"`；fresh 路径用本轮 `turn_id`，resume 路径用 `resume_state.turn_id`（`AgentState.apply` 为 model_copy，`turn_id` 天然保留）；
- 回归测试（`tests/test_orchestrator.py`，已验证修复前失败/修复后通过）：
  - `test_clarify_pending_then_new_question_routes_fresh`：报告 §1 复现序列——挂起后新问题独立作答 + 旧卡仍可在本轮恢复；
  - `test_sequential_pending_clarifies_resume_independently`：§6.2-1 双澄清串扰——两轮恢复各自归属、报告口径互不污染；
- 评测锚点（`eval/intent_eval.py`）：恢复共享会话多轮混排场景，新增 3 条锚点（澄清挂起 / 挂起后新问题独立作答 / 旧卡按本轮恢复），14/14 PASS；
- **兼容性注意**：thread id 格式变更——SqliteSaver 下跨升级存活的挂起态按新格式找不到旧线程（恢复报 EmptyInputError），评测阶段无此类在途状态；生产化前如需原地升级，须补旧格式回退或挂起态迁移。
