"""语义目录：逻辑字段 -> 物理表/列 的受控映射。

这是"杜绝随意 Join / SQL 注入"的关键防线：编译器只允许引用本目录登记的字段，
表连接关系也只由本目录声明，禁止任意 Join。

多事实表模型（Gmall 二十期）：
- FACT_TABLE 是主事实表（order_detail，查询锚点，FROM 主表）；
- 第二事实表（order_refund_info）通过 FACT_JOIN_RULES 受控连接，
  业务上保证 1:1（每订单至多一条退款），避免一对多扇出放大聚合结果；
- 流量域（fact_page_view/fact_action/fact_display/fact_start）为独立查询域
  （QUERY_DOMAINS 域锚点），跨域查询由编译器拒绝。

单一事实源（M-P2，2026-10）：**config/semantic.json 是唯一静态业务事实源**。
本模块在 import 时从 json 直读构建全部内置目录常量（严格校验必要节，
缺节/坏节直接报错），严禁在本文件手写任何业务事实——字段/标签/别名/连接/
指标口径/大区映射/枚举值标签等一律改 json 登记即可，无需改 Python。
生产启动时 semantic.catalog_loader.refresh_catalog() 再从 DuckDB
information_schema + json 覆写重建目录（维度成员词表从库内 distinct 重建，
dimension_members_seed 仅作库不可用时的离线回退）。
compiler / guard / agent 一律通过 `catalog.XXX` 动态读取本模块当前状态。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# json 直读（M-P2 单一事实源）
# --------------------------------------------------------------------------- #

# import 时必须存在的节：缺失即视为配置损坏，快速失败（严禁静默半目录）
_REQUIRED_SECTIONS: tuple[str, ...] = (
    "fact_table",
    "fact_tables",
    "aliases",
    "fields",
    "join_rules",
    "fact_join_rules",
    "query_domains",
    "dimension_member_fields",
)


def _load_builtin(path: Path | None = None) -> dict:
    """读取并校验 semantic.json（单一事实源；损坏配置快速失败）。"""
    p = path or (Path(__file__).resolve().parents[1] / "config" / "semantic.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"semantic.json 顶层必须是对象: {path}")
    missing = [k for k in _REQUIRED_SECTIONS if k not in data]
    if missing:
        raise RuntimeError(f"semantic.json 缺少必要节 {missing}，请补齐配置: {p}")
    return data


@dataclass(frozen=True)
class FieldMeta:
    """字段元数据：物理表 + 列名 + 类型（dtype 用于字面量安全转义）。

    label：中文语义标签（如「订单金额」），供 Web 侧栏展示与 Planner
    提示词注入；None 时消费方回退物理列名。
    aliases：业务别名（意图分类/问法匹配词源），单一事实源登记在 json。
    """

    table: str
    column: str
    dtype: str  # 用于字面量安全转义：str / int / float / bool / timestamp
    label: str | None = None
    aliases: tuple[str, ...] = ()
    # 标价类参考字段（如商品标价）：非可聚合交易金额，消费方据此归入独立分组
    non_aggregatable: bool = False


@dataclass(frozen=True)
class JoinRule:
    """受控连接声明（P0-2：不再裸拼 SQL）。

    - join_type：inner / left；
    - on：一或多个 (joined_table_col, fact_table_col) 字段对，
      渲染为 `{joined_alias}.{col1} = {fact_alias}.{col2}`。
    """

    join_type: str
    on: tuple[tuple[str, str], ...] = ()


def _rules(raw: dict) -> dict[str, JoinRule]:
    """json 连接声明节 -> JoinRule 映射（type: inner/left，on: 字段对列表）。"""
    return {
        table: JoinRule(str(spec.get("type", "inner")), tuple(tuple(p) for p in spec.get("on", [])))
        for table, spec in raw.items()
    }


_BUILTIN = _load_builtin()


# 以下目录容器统一定义为空壳、由 apply_builtin 填充：import 初始化与 reset_defaults
# 共用单一派生路径，reset 前后对象身份不变（`from catalog import COLUMNS` 式 import
# 绑定消费方在 reset 后依然指向同一容器）。
COLUMNS: dict[str, FieldMeta] = {}
TABLE_LABELS: dict[str, str] = {}
ALIASES: dict[str, str] = {}
JOIN_RULES: dict[str, JoinRule] = {}
FACT_JOIN_RULES: dict[str, JoinRule] = {}
QUERY_DOMAINS: dict[str, dict[str, JoinRule]] = {}
DIMENSION_MEMBERS_SEED: dict[str, list[str]] = {}
DIMENSION_MEMBERS: dict[str, tuple[str, ...]] = {}
REGION_PROVINCE_MAPPING: dict[str, tuple[str, ...]] = {}
VALUE_LABELS: dict[str, dict[str, str]] = {}
METRIC_ALIASES: dict[str, str] = {}
REFLECTOR_CONCEPTS: dict[str, dict] = {}

FACT_TABLE: str = ""
FACT_TABLES: tuple[str, ...] = ()
DIMENSION_MEMBER_FIELDS: tuple[str, ...] = ()
DRILLDOWN_DIM_FIELDS: tuple[str, ...] = ()
METRICS: list[dict] = []
COUNT_ENTITIES: list[dict] = []
PAID_FILTER: dict = {}
DEFAULT_WINDOW: dict = {}
OUT_OF_SCOPE_CONCEPTS: list[str] = []
UNDEFINED_METRICS: list[dict] = []


def apply_builtin(data: dict) -> None:
    """把 semantic.json 派生目录写入本模块全局。

    - 容器一律原地 clear/update（对象身份不变，import 绑定消费方 reset 后不 stale）；
    - 标量（str/tuple/list）允许重绑定，消费方须动态读 ``catalog.X``
      （M-P1 stale 修复约定，见 nodes/sql_lift）；
    - import 初始化与 catalog_loader.reset_defaults 共用本路径，严禁另写第二份派生。
    """
    g = globals()

    # 逻辑字段 -> 物理字段
    columns = {
        name: FieldMeta(
            str(spec["table"]),
            str(spec["column"]),
            str(spec.get("dtype") or ""),
            label=spec.get("label"),
            aliases=tuple(str(a) for a in spec.get("aliases", ())),
            non_aggregatable=bool(spec.get("non_aggregatable", False)),
        )
        for name, spec in data["fields"].items()
    }
    COLUMNS.clear()
    COLUMNS.update(columns)

    # 物理表 -> 中文表标签：Web 侧栏分组标题与 Planner 摘要展示用
    TABLE_LABELS.clear()
    TABLE_LABELS.update(data.get("table_labels", {}))

    # 表别名（编译器内部使用；主锚点别名固定为 f，编译器 FROM 子句引用）
    ALIASES.clear()
    ALIASES.update(data["aliases"])

    # 主事实表（交易域查询锚点，FROM 主表）与全部事实表（用于校验/文档）
    g["FACT_TABLE"] = str(data["fact_table"])
    g["FACT_TABLES"] = tuple(str(t) for t in data["fact_tables"])

    # 受控连接规则：只允许从主事实表星型连接维度表（全部 N:1，无扇出）
    JOIN_RULES.clear()
    JOIN_RULES.update(_rules(data["join_rules"]))

    # 第二事实表 -> 主事实表 的受控连接（LEFT JOIN）
    FACT_JOIN_RULES.clear()
    FACT_JOIN_RULES.update(_rules(data["fact_join_rules"]))

    # 独立查询域：锚点表 -> 该域允许的维度连接（编译器域锚点解析，跨域报错）
    QUERY_DOMAINS.clear()
    QUERY_DOMAINS.update({anchor: _rules(rules) for anchor, rules in data["query_domains"].items()})

    # 维度成员词汇表离线回退种子（生产由 catalog_loader 从库内 distinct 重建）
    DIMENSION_MEMBERS_SEED.clear()
    DIMENSION_MEMBERS_SEED.update(data.get("dimension_members_seed", {}))

    # 维度成员词汇表（逻辑字段 -> 成员值）：启发式解析与多轮继承从问题文本抽取维度值用。
    # 内置默认 = seed 快照；服务启动时由 catalog_loader 按白名单从数仓 distinct 重建。
    DIMENSION_MEMBERS.clear()
    DIMENSION_MEMBERS.update({k: tuple(v) for k, v in DIMENSION_MEMBERS_SEED.items()})

    # 维度成员词汇表重建的字段白名单（逻辑字段名）
    g["DIMENSION_MEMBER_FIELDS"] = tuple(str(f) for f in data["dimension_member_fields"])

    # 可下钻字符串维度白名单（诊断归因候选维度池）
    g["DRILLDOWN_DIM_FIELDS"] = tuple(str(f) for f in data.get("drilldown_dim_fields", ()))

    # 大区 -> 省份成员映射（区域词展开；消费方一律经 region_provinces() 与成员词表求交）
    REGION_PROVINCE_MAPPING.clear()
    REGION_PROVINCE_MAPPING.update(
        {
            region: tuple(provinces)
            for region, provinces in data.get("region_province_mapping", {}).items()
        }
    )

    # 指标口径（glossary/heuristic/提示词的公共事实源）：key/title/aliases/
    # definition/formula/fields/shape（DSL 产出形态）
    g["METRICS"] = list(data.get("metrics", []))

    # 计数实体词表（"多少订单/用户/商品" -> count/count_distinct 形态）
    g["COUNT_ENTITIES"] = list(data.get("count_entities", []))

    # 支付口径（成功过滤的字段/值/问法词/反义问法）
    PAID_FILTER.clear()
    PAID_FILTER.update(data.get("paid_filter", {}))

    # 缺省分析窗口（无显式时间解析时使用；两期诊断按中点切分）
    DEFAULT_WINDOW.clear()
    DEFAULT_WINDOW.update(data.get("default_window", {}))

    # 反思器概念覆盖表：概念 -> {fields, produced_aliases}
    REFLECTOR_CONCEPTS.clear()
    REFLECTOR_CONCEPTS.update(data.get("reflector_concepts", {}))

    # 数仓未覆盖的业务概念（出现在反思理由中即为不可执行缺口）
    g["OUT_OF_SCOPE_CONCEPTS"] = list(data.get("out_of_scope_concepts", []))

    # 明确未定义业务指标清单（澄清层"宁拒答不近似"词源）
    g["UNDEFINED_METRICS"] = list(data.get("undefined_metrics", []))

    # 枚举值 -> 中文标签（order_status 码表/退款类型/评价/性别）
    VALUE_LABELS.clear()
    VALUE_LABELS.update(data.get("value_labels", {}))

    # 聚合产物别名 -> 中文（gmv/orders/buyers 等产物列的人读化）
    METRIC_ALIASES.clear()
    METRIC_ALIASES.update(data.get("metric_aliases", {}))


apply_builtin(_BUILTIN)
