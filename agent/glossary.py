"""业务指标口径文档（口径词典）——M-P1 起为语义目录的适配层。

指标口径的单一事实源 = semantic.json `metrics[]`（semantic.catalog.METRICS），
本模块把它适配为检索层（agent.rag）与澄清层（agent.clarify）消费的
GlossaryDoc 形态。严禁在本模块手写指标公式/别名（业务事实与代码双写是
Gmall 迁移 66 文件波及的主要根源之一）；新增指标一律登记 semantic.json。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GlossaryDoc:
    """口径文档：指标定义 / 计算公式 / 同义词 / 关联字段（Glossary entry）。"""

    key: str
    title: str
    aliases: tuple[str, ...]
    definition: str
    formula: str
    fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """序列化为检索结果字典（Serialize to a retrieval payload）。"""
        return {
            "key": self.key,
            "title": self.title,
            "aliases": list(self.aliases),
            "definition": self.definition,
            "formula": self.formula,
            "fields": list(self.fields),
        }


def _docs_from_catalog() -> tuple[GlossaryDoc, ...]:
    """从语义目录 metrics[] 派生口径文档。"""
    from semantic import catalog

    return tuple(
        GlossaryDoc(
            key=str(m["key"]),
            title=str(m.get("title", m["key"])),
            aliases=tuple(str(a) for a in m.get("aliases", ())),
            definition=str(m.get("definition", "")),
            formula=str(m.get("formula", "")),
            fields=tuple(str(f) for f in m.get("fields", ())),
        )
        for m in catalog.METRICS
    )


GLOSSARY: tuple[GlossaryDoc, ...] = _docs_from_catalog()

# 全部已定义指标别名（统一小写；中文小写等于原文），供澄清层判断"是否已定义业务指标"。
METRIC_TERMS: frozenset[str] = frozenset(alias.lower() for doc in GLOSSARY for alias in doc.aliases)


def scoped_glossary(principal: str | None = None) -> tuple[GlossaryDoc, ...]:
    """按主体过滤口径文档（守卫前移）：只保留引用字段全部可见的文档。

    - principal 为 None -> 全量（库级调用向后兼容）；
    - 如 refund_rate / refund_amount 依赖退款字段，restricted 主体不可见。
    """
    from security.scope import scoped_fields

    allowed = scoped_fields(principal)
    return tuple(doc for doc in GLOSSARY if set(doc.fields) <= allowed)
