"""风险巡检与风险报表（issue #26 最小闭环）。

分层（自配置到产物）：
- ``rules``：判定规则契约与 fail-closed loader（config/risk_rules.json）；
- ``executor``：确定性巡检执行器（规则直产 DSL，经治理管道全护栏取数）；
- ``report``：风险报告确定性渲染（红黄绿状态 / 异常清单 / 证据链附录）。

定位约束：
- 规则是配置事实不是模型意见，调整走 json 契约变更流程（类比 semantic.json）；
- 指标口径零重复登记，``metric`` 必须命中 semantic.json metrics[]（catalog.METRICS）；
- 全部取数经 ``core.retrieval.execute_dsl_query`` 六层防御栈，零旁路；
- 纯确定性零 LLM 依赖，同输入必出同报告（渲染可快照断言）；
- 定时化形态为 CLI + 外部 cron 调度（``python -m risk``），不进 LangGraph。
"""

from risk.executor import InspectionResult, RuleFinding, run_inspection
from risk.report import render_report
from risk.rules import RiskRule, RiskRules, load_rules

__all__ = [
    "InspectionResult",
    "RiskRule",
    "RiskRules",
    "RuleFinding",
    "load_rules",
    "render_report",
    "run_inspection",
]
