"""意图路由评测（十八期）：离线确定性运行，监控"答非所问率"。

对每条 golden 用例断言编排终态行为：
- count_answer  => 报告含"查询答案："且不含金额化（计数直答，杜绝 GMV 万能答案）；
- metric_wan_answer => 报告含"查询答案："且金额万元化（金额类锚点直答）；
- multi_metric  => 单数据集多列呈现：计数列名（order_id_count）进报告表格
  列头，无"无法作答"（多锚不丢锚）；
- refusal => 报告含"无法作答"（诚实拒答 + 能力清单）；2026-09 分层降级起，
  LLM 失联时枚举未命中的筛选取值走选项式澄清挂起（零查询/零 LLM/无报告），
  与 blocked 拒答同属"不答非所问"的诚实不产出终态，同样判 PASS。
- list_answer => 维度取值枚举直答（十九期 M1 + 二十期 P1 确定性渲染）：
  报告含"维度取值清单"小节与指定取值。

用法：
    python -m mock.init_duckdb   # 若数仓文件不存在
    python -m eval.intent_eval
"""

from __future__ import annotations

import json
from pathlib import Path

# 评测钉死离线兜底链路（设计 §3.6：离线确定性运行）——屏蔽环境中的 LLM
# 配置，确保断言针对确定性规则引擎的输出（可复现、不烧 token）
import core.orchestrator.nodes as _orch_nodes
from config import settings

_orch_nodes._resolve_llm = lambda: None

from core.orchestrator.agent import AgentTrace, run_agent  # noqa: E402  (需在 patch 后导入链路)
from core.orchestrator.state import AgentState  # noqa: E402
from semantic import catalog  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "intent_golden.json"


def main() -> int:
    if not settings.DB_PATH.exists():
        raise SystemExit(f"未找到数仓文件 {settings.DB_PATH}，请先执行: python -m mock.init_duckdb")
    cases = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []
    for i, case in enumerate(cases):
        question = case["question"]
        expect = case["expect"]
        # 每用例独立会话（十九期 M1 发现）：共享会话时前序用例的 clarify
        # 挂起会把后续新问题当作澄清答复合并（LangGraph 同线程 interrupt
        # 恢复语义），导致误路由——评测用例相互独立，必须会话隔离
        trace = run_agent(question, session_id=f"intent-eval-{i}", autonomy_level="L4")
        report = trace.report or ""
        ok = False
        if expect == "count_answer":
            expected_value = case.get("expected_value")
            ok = (
                trace.phase == "done"
                and "查询答案：" in report
                and "万元" not in report
                and (expected_value is None or str(expected_value) in report)
            )
        elif expect == "metric_wan_answer":
            ok = trace.phase == "done" and "查询答案：" in report and "万元" in report
        elif expect == "multi_metric":
            ok = trace.phase == "done" and "order_id_count" in report and "无法作答" not in report
        elif expect == "refusal":
            ok = trace.phase == "done" and "无法作答" in report and "万元" not in report
            # 2026-09 分层降级：枚举未命中的筛选取值（如数仓无"琼崖省"）=>
            # 选项式澄清挂起（设计 Review Focus #2：严禁自动猜测条件）。
            # 该终态零查询、零 LLM、无报告，与 blocked 拒答同属诚实不产出，
            # 达成 golden refusal 的根本意图（不答非所问、不编造口径）。
            ok = ok or (
                trace.phase == "clarify"
                and bool(trace.clarification)
                and not trace.plan_steps
                and not report
            )
        elif expect == "list_answer":
            # 十九期 M1 + 二十期枚举直答（P1）：维度取值枚举直答——报告含
            # "维度取值清单"小节（确定性渲染，非"查询答案："单值形态）与指定取值，
            # 不得出现拒答文案
            expected_contains = case.get("expected_contains")
            ok = (
                trace.phase == "done"
                and "维度取值清单" in report
                and "无法作答" not in report
                and (expected_contains is None or expected_contains in report)
            )
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {question} (expect={expect})")
        if not ok:
            failures.append(question)
            print(f"       report head: {report[:200]!r}")

    # 多轮混排回归锚点（docs/reviews/20261002-audit-clarify-pending-hijack.md）：
    # 恢复"共享会话连续提问"场景——澄清挂起后新问题必须按新查询独立作答、
    # 旧澄清卡仍可在本轮线程恢复（checkpointer 线程按轮隔离）。严禁用每用例
    # 隔离会话掩盖多轮误路由。
    mixed_session = "intent-eval-mixed"
    paused = run_agent("琼崖省的GMV是多少", session_id=mixed_session, autonomy_level="L4")
    ok = isinstance(paused, AgentState) and paused.phase == "clarify"
    print(f"[{'PASS' if ok else 'FAIL'}] 混排#1 澄清挂起（琼崖GMV）")
    if not ok:
        failures.append("多轮混排#1 澄清挂起")

    trace = run_agent("有多少个省份", session_id=mixed_session, autonomy_level="L4")
    # 省份计数动态断言（去 pin）：与数仓实际省份基数同源，防夹具漂移
    province_answer = f"查询答案：{len(catalog.DIMENSION_MEMBERS['province'])}"
    ok = (
        isinstance(trace, AgentTrace)
        and trace.phase == "done"
        and province_answer in trace.report
        and "用户补充" not in trace.report
    )
    print(f"[{'PASS' if ok else 'FAIL'}] 混排#2 挂起后新问题独立作答（省份计数）")
    if not ok:
        failures.append("多轮混排#2 挂起后新问题独立作答")

    final = run_agent(
        "琼崖省的GMV是多少",
        session_id=mixed_session,
        autonomy_level="L4",
        resume_state=paused.apply(human_reply="广东省"),
    )
    ok = (
        isinstance(final, AgentTrace)
        and final.phase == "done"
        and "广东" in final.report
        and "有多少个省份" not in final.report
    )
    print(f"[{'PASS' if ok else 'FAIL'}] 混排#3 旧澄清卡按本轮恢复（广东GMV）")
    if not ok:
        failures.append("多轮混排#3 旧澄清卡按本轮恢复")

    total_checks = len(cases) + 3  # 3 = 多轮混排回归锚点
    print(f"\n{total_checks - len(failures)}/{total_checks} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
