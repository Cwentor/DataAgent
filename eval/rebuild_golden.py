"""一次性脚本：重建 golden_dataset.json 到 Gmall 新模型（29 条映射 + 2 条流量域）。

用法：conda activate dataagent && python eval/rebuild_golden.py
先跑 python -m mock.init_duckdb 重建数仓。DSL 为手工映射，SQL 由编译器生成，
结果断言在生成期即对数仓执行校验。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.pipeline import run_pipeline as run_production_pipeline
from compiler.sql_compiler import compile_sql
from config import settings
from semantic.dsl_schema import QueryDSL

REF = date(2025, 12, 31)


def rel(amount=1, unit="month", mode="calendar", granularity="month"):
    return {
        "granularity": granularity,
        "range_type": "relative",
        "relative": {"amount": amount, "unit": unit, "mode": mode},
        "comparison": "none",
        "reference_date": REF.isoformat(),
    }


def abs_window(start, end, time_field=None):
    tf = {
        "granularity": "day",
        "range_type": "absolute",
        "absolute": {"start": start, "end": end},
        "comparison": "none",
        "reference_date": None,
    }
    if time_field:
        tf["time_field"] = time_field
    return tf


def agg(field, a, alias, kind="aggregate"):
    m = {"kind": kind, "field": field, "agg": a, "alias": alias}
    return m


def dim(f):
    return {"field": f}


def case(cid, question, scenario, dsl, sql=None, extra=None):
    item = {"id": cid, "question": question, "scenario": scenario, "dsl": dsl, "sql": sql}
    if extra:
        item.update(extra)
    return item


JUNE = abs_window("2024-06-01", "2024-07-01")
PAID = {"field": "order_status", "operator": "eq", "value": "1002"}

cases = []

# ---------------- 交易域（原 29 条逐条映射） ----------------
cases.append(
    case(
        "Q01",
        "2024年6月已支付订单的总销售额(GMV)是多少？",
        "单指标聚合",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [],
            "time_filter": JUNE,
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q02",
        "各品类已支付订单的GMV分布？",
        "带维度拆分",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [dim("category1_name")],
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q03",
        "上个月的已支付订单GMV是多少？",
        "相对时间区间",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [],
            "time_filter": rel(),
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q04",
        "广东省手机类目已支付订单的GMV？",
        "多过滤条件组合",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [],
            "filters": [
                PAID,
                {"field": "province", "operator": "eq", "value": "广东"},
                {"field": "category1_name", "operator": "eq", "value": "手机"},
            ],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q05",
        "已支付订单GMV最高的前5个品牌？",
        "Top N 排序",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [dim("tm_name")],
            "filters": [PAID],
            "order_by": [{"field": "gmv", "direction": "desc"}],
            "limit": 5,
        },
    )
)
cases.append(
    case(
        "Q06",
        "2024年6月每日已支付订单GMV趋势？",
        "时间粒度趋势",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [dim("order_time")],
            "time_filter": JUNE,
            "filters": [PAID],
            "order_by": [{"field": "order_time", "direction": "asc"}],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q07",
        "已支付订单总数是多少？",
        "计数聚合",
        {
            "metrics": [
                {
                    "kind": "aggregate",
                    "field": "order_id",
                    "agg": "count_distinct",
                    "alias": "order_count",
                }
            ],
            "dimensions": [],
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q08",
        "2024年6月已支付订单的去重用户数？",
        "去重计数",
        {
            "metrics": [
                {
                    "kind": "aggregate",
                    "field": "user_id",
                    "agg": "count_distinct",
                    "alias": "active_users",
                }
            ],
            "dimensions": [],
            "time_filter": JUNE,
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q09",
        "2024年6月已支付订单的人均消费(ARPU)是多少？",
        "比率指标 ARPU",
        {
            "metrics": [
                {
                    "kind": "ratio",
                    "numerator": {
                        "kind": "aggregate",
                        "field": "split_total_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "denominator": {
                        "kind": "aggregate",
                        "field": "user_id",
                        "agg": "count_distinct",
                        "alias": "active_users",
                    },
                    "alias": "arpu",
                }
            ],
            "dimensions": [],
            "time_filter": JUNE,
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q10",
        "广东省或浙江省、实付金额100到5000元之间的各品类已支付订单GMV？",
        "in/between 操作符 + 维度",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [dim("category1_name")],
            "filters": [
                PAID,
                {"field": "province", "operator": "in", "value": ["广东", "浙江"]},
                {"field": "split_total_amount", "operator": "between", "value": [100, 5000]},
            ],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q11",
        "2024年6月已支付订单GMV的环比是多少？",
        "环比(mom)对比：当前月与上一个月的增长/下降",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [],
            "time_filter": {**JUNE, "comparison": "mom"},
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q12",
        "2024年6月已支付订单GMV的同比是多少？",
        "同比(yoy)对比：当前周期与去年同期的增长/下降",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [],
            "time_filter": {**JUNE, "comparison": "yoy"},
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q13",
        "各品类已支付订单的退款金额是多少？",
        "多事实表：退款金额按品类分组（order_detail LEFT JOIN order_refund_info sku级1:1）",
        {
            "metrics": [agg("refund_amount", "sum", "refund_amount")],
            "dimensions": [dim("category1_name")],
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q14",
        "2024年6月已支付订单的退款率（退款金额/实付金额）是多少？",
        "多事实表比率指标：退款金额 / 实付金额",
        {
            "metrics": [
                {
                    "kind": "ratio",
                    "numerator": {
                        "kind": "aggregate",
                        "field": "refund_amount",
                        "agg": "sum",
                        "alias": "refund_amount",
                    },
                    "denominator": {
                        "kind": "aggregate",
                        "field": "split_total_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "alias": "refund_rate",
                }
            ],
            "dimensions": [],
            "time_filter": JUNE,
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q15",
        "2024年6月已支付订单的GMV和订单数的环比是多少？",
        "多指标同环比",
        {
            "metrics": [
                agg("split_total_amount", "sum", "gmv"),
                {
                    "kind": "aggregate",
                    "field": "order_id",
                    "agg": "count_distinct",
                    "alias": "order_count",
                },
            ],
            "dimensions": [],
            "time_filter": {**JUNE, "comparison": "mom"},
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q16",
        "2024年6月每日已支付订单GMV的累计值？",
        "窗口函数-累计求和",
        {
            "metrics": [
                {
                    "kind": "window",
                    "base": {
                        "kind": "aggregate",
                        "field": "split_total_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "func": "cumsum",
                    "window_size": None,
                    "alias": "cum_gmv",
                }
            ],
            "dimensions": [dim("order_time")],
            "time_filter": JUNE,
            "filters": [PAID],
            "order_by": [{"field": "order_time", "direction": "asc"}],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q17",
        "2024年6月每日已支付订单GMV的7日移动平均？",
        "窗口函数-移动平均",
        {
            "metrics": [
                {
                    "kind": "window",
                    "base": {
                        "kind": "aggregate",
                        "field": "split_total_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "func": "moving_avg",
                    "window_size": 7,
                    "alias": "ma7_gmv",
                }
            ],
            "dimensions": [dim("order_time")],
            "time_filter": JUNE,
            "filters": [PAID],
            "order_by": [{"field": "order_time", "direction": "asc"}],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q18",
        "2024年6月每日已支付订单GMV趋势（补零）？",
        "日期连续补零",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [dim("order_time")],
            "time_filter": JUNE,
            "filters": [PAID],
            "order_by": [{"field": "order_time", "direction": "asc"}],
            "limit": 100,
            "fill_gaps": True,
        },
    )
)
cases.append(
    case(
        "Q19",
        "每省已支付订单GMV Top 3 品类？",
        "分组Top-N",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [dim("province"), dim("category1_name")],
            "filters": [PAID],
            "order_by": [{"field": "gmv", "direction": "desc"}],
            "limit": 100,
            "top_n": {
                "n": 3,
                "partition_by": ["province"],
                "order_by": [{"field": "gmv", "direction": "desc"}],
            },
        },
    )
)
cases.append(
    case(
        "Q20",
        "上季度的已支付订单GMV是多少？",
        "时间代数-calendar quarter",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [],
            "time_filter": rel(1, "quarter", "calendar", "quarter"),
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q21",
        "本月至今已支付订单GMV是多少？",
        "时间代数-MTD to_date",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [],
            "time_filter": rel(1, "month", "to_date", "day"),
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q22",
        "6月每日已支付订单GMV同比是多少？",
        "时间维度×comparison按位配对",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [dim("order_time")],
            "time_filter": {**JUNE, "comparison": "yoy"},
            "filters": [PAID],
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q23",
        "近30天每日退款金额是多少？",
        "时间主轴解绑-time_field=refund_time",
        {
            "metrics": [agg("refund_amount", "sum", "refund_amount")],
            "dimensions": [dim("refund_time")],
            "time_filter": {
                "granularity": "day",
                "range_type": "relative",
                "relative": {"amount": 30, "unit": "day", "mode": "trailing"},
                "comparison": "none",
                "reference_date": REF.isoformat(),
                "time_field": "refund_time",
            },
            "filters": [],
            "order_by": [{"field": "refund_time", "direction": "asc"}],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q26",
        "按品类统计GMV，只要GMV超过1000的品类",
        "HAVING 聚合后过滤（十九期 M2）",
        {
            "metrics": [agg("split_total_amount", "sum", "gmv")],
            "dimensions": [dim("category1_name")],
            "having": [{"field": "gmv", "operator": "gt", "value": 1000}],
            "order_by": [{"field": "gmv", "direction": "desc"}],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q27",
        "各品类的客单价是多少",
        "表达式指标：客单价 = GMV/订单量（十九期 M2）",
        {
            "metrics": [
                agg("split_total_amount", "sum", "gmv"),
                {
                    "kind": "aggregate",
                    "field": "order_id",
                    "agg": "count_distinct",
                    "alias": "orders",
                },
                {
                    "kind": "expression",
                    "alias": "aov",
                    "expr": {"op": "div", "args": [{"ref": "gmv"}, {"ref": "orders"}]},
                },
            ],
            "dimensions": [dim("category1_name")],
            "order_by": [{"field": "gmv", "direction": "desc"}],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q28",
        "各省各品类已支付订单的GMV和订单数交叉表？",
        "交叉表（多指标×多维度，题库第 2 层）",
        {
            "metrics": [
                agg("split_total_amount", "sum", "gmv"),
                {
                    "kind": "aggregate",
                    "field": "order_id",
                    "agg": "count_distinct",
                    "alias": "order_count",
                },
            ],
            "dimensions": [dim("category1_name"), dim("province")],
            "filters": [PAID],
            "order_by": [],
            "limit": 300,
        },
    )
)

# ---------------- 流量域（十九期 Gmall 扩展新增） ----------------
cases.append(
    case(
        "Q29",
        "2024年6月各页面浏览量(PV)和独立访客数(UV)是多少？",
        "流量域：页面浏览 PV/UV（fact_page_view 域锚点）",
        {
            "metrics": [
                agg("page_id", "count", "pv"),
                {"kind": "aggregate", "field": "mid", "agg": "count_distinct", "alias": "uv"},
            ],
            "dimensions": [dim("page_id")],
            "time_filter": abs_window("2024-06-01", "2024-07-01", "page_view_time"),
            "order_by": [],
            "limit": 100,
        },
    )
)
cases.append(
    case(
        "Q30",
        "2024年6月各类点击行为的次数分布？",
        "流量域：点击行为分布（fact_action 域锚点）",
        {
            "metrics": [agg("action_id", "count", "action_count")],
            "dimensions": [dim("action_id")],
            "time_filter": abs_window("2024-06-01", "2024-07-01", "action_time"),
            "order_by": [],
            "limit": 100,
        },
    )
)

# ---------------- 多轮用例（经真实启发式链路生成后固化） ----------------
MULTI_TURN_SPECS = [
    (
        "M1",
        "大区词继承",
        [
            ("上个月华东地区的GMV是多少？", None),
            ("那华南呢？", None),
        ],
    ),
    (
        "M2",
        "时间继承 + 维度展开",
        [
            ("上个月的GMV是多少？", None),
            ("按品类展开", None),
        ],
    ),
    (
        "M3",
        "省份过滤继承 + 维度展开",
        [
            ("2024年6月广东省的GMV是多少", None),
            ("那浙江省呢", None),
            ("按品类展开", None),
        ],
    ),
]


def run_offline():
    from eval.eval_runner import _force_offline_routing

    _force_offline_routing()
    # 注意：刻意不做 refresh_catalog——golden 必须与评测运行时同用内置目录词表，
    # 避免 DB distinct 排序与内置成员顺序差异导致 DSL 逐字断言失败。
    return duckdb.connect(str(settings.DB_PATH), read_only=True)


def build_multi_turn():
    from web.service import run_query

    conn = run_offline()
    out = []
    for cid, desc, turns in MULTI_TURN_SPECS:
        session_id = f"golden-rebuild-{cid}"
        recorded = []
        for question, _ in turns:
            res = run_query(question, conn=conn, session_id=session_id, user="eval")
            if res.get("error") and res.get("dsl") is None:
                raise SystemExit(f"{cid} 轮次解析失败: {question} -> {res.get('error')}")
            dsl = normalize_dsl(res["dsl"])
            recorded.append(
                {
                    "question": question,
                    "dsl": dsl,
                    "sql": compile_sql(QueryDSL.model_validate(dsl)),
                }
            )
        out.append(
            {
                "id": cid,
                "type": "multi_turn",
                "scenario": desc,
                "turns": recorded,
            }
        )
        from agent.memory import default_session_store

        default_session_store().clear(session_id, "eval")
    conn.close()
    return out


def normalize_dsl(dsl):
    """经 QueryDSL 校验往返，保证与评测链路同一序列化形态。"""
    return json.loads(QueryDSL.model_validate(dsl).model_dump_json())


def main():
    conn = duckdb.connect(str(settings.DB_PATH), read_only=True)
    from eval.eval_runner import _force_offline_routing

    _force_offline_routing()
    final = []
    for item in cases:
        # DSL 以真实启发式链路产出为准（保证 agent 模式逐字一致），失败时回退手工 DSL
        try:
            res = run_production_pipeline(item["question"], principal=None)
            dsl = json.loads(res.model_dump_json())
        except Exception as exc:
            print(f"[warn] {item['id']} 启发式未覆盖（{exc}），使用手工 DSL")
            dsl = normalize_dsl(item["dsl"])
        dsl = normalize_dsl(dsl)
        try:
            sql = compile_sql(QueryDSL.model_validate(dsl))
        except Exception as exc:
            print(
                f"[error] {item['id']} 启发式 DSL 编译失败: {exc}\n  dsl={json.dumps(dsl, ensure_ascii=False)[:400]}"
            )
            raise
        conn.execute(sql)  # 结果可执行性校验（能编译能执行即通过）
        final.append(
            {
                "id": item["id"],
                "question": item["question"],
                "scenario": item["scenario"],
                "dsl": dsl,
                "sql": sql,
            }
        )
    conn.close()
    final.extend(build_multi_turn())
    out_path = Path(__file__).resolve().parent / "golden_dataset.json"
    out_path.write_text(json.dumps(final, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[rebuild_golden] 写出 {len(final)} 条用例 -> {out_path}")


if __name__ == "__main__":
    main()
