"""意图路由评测（十八期）：离线确定性运行，监控"答非所问率"。

对每条 golden 用例断言编排终态行为：
- count_answer  => 报告含"查询答案："且不含金额化（计数直答，杜绝 GMV 万能答案）；
- metric_wan_answer => 报告含"查询答案："且金额万元化（金额类锚点直答）；
- multi_metric  => 单数据集多列呈现：计数列名（order_id_count）进报告表格
  列头，无"无法作答"（多锚不丢锚）；
- refusal => 报告含"无法作答"（诚实拒答 + 能力清单）；2026-09 分层降级起，
  LLM 失联时枚举未命中的筛选取值走选项式澄清挂起（零查询/零 LLM/无报告），
  与 blocked 拒答同属"不答非所问"的诚实不产出终态，同样判 PASS。
- list_answer => 维度取值枚举直答（十九期 M1）：报告含结果小节与指定取值。

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

from core.orchestrator.agent import run_agent  # noqa: E402  (需在 patch 后导入链路)

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
            # 2026-09 分层降级：枚举未命中的筛选取值（如数仓无"海南"）=>
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
            # 十九期 M1：维度取值枚举直答——报告含结果小节与指定取值，
            # 不得出现拒答文案（多行清单经表格渲染，非"查询答案："单值形态）
            expected_contains = case.get("expected_contains")
            ok = (
                trace.phase == "done"
                and "查询结果" in report
                and "无法作答" not in report
                and (expected_contains is None or expected_contains in report)
            )
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {question} (expect={expect})")
        if not ok:
            failures.append(question)
            print(f"       report head: {report[:200]!r}")
    print(f"\n{len(cases) - len(failures)}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
