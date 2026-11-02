"""风险巡检规则契约与 fail-closed loader（issue #26 最小闭环）。

规则是配置事实不是模型意见（与"LLM 不自由生成事实"同构）：

- 登记于 ``config/risk_rules.json``，调整走 json 契约变更流程（diff 可评审）；
- 指标口径零重复登记，``metric`` 必须命中 semantic.json ``metrics[]``
  （``semantic.catalog.METRICS``），规则文件只引用 key 与登记阈值；
- fail-closed：任何契约违规拒绝加载并给出聚合报错清单，严禁静默忽略；
- 判定形态两类。绝对阈值 ``thresholds``（当期值超红/黄线）与相对异动
  ``comparison``（当期对基线变化率超红/黄线），可单配可同配。

时间窗口为绝对窗口（锚定确定性，严禁"今天"类相对时间）：``current``
显式登记日期，``baseline: previous`` 按粒度推算上一期；窗口不越数据域
上界（``settings.DATA_DOMAIN_END``）。
"""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from semantic.catalog import COLUMNS, METRICS
from semantic.dsl_schema import IDENTIFIER_PATTERN, TIME_FIELDS

# 本期支持的查询域（流量域为后置扩展，见设计文档 §9）
SUPPORTED_QUERY_DOMAINS: frozenset[str] = frozenset({"trade"})

# 本期支持的基线形态（滚动上一期；同比等形态后置）
SUPPORTED_BASELINES: frozenset[str] = frozenset({"previous"})

# 相对异动方向白名单：drop 跌幅告警 / raise 涨幅告警
SUPPORTED_COMPARISON_DIRECTIONS: frozenset[str] = frozenset({"drop", "raise"})

# 规则契约允许的字段全集（其余一律拒绝，杜绝拼错字段静默失效）
_RULE_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "key",
        "title",
        "metric",
        "query_domain",
        "window",
        "dimension",
        "thresholds",
        "comparison",
        "time_field",
    }
)
_WINDOW_ALLOWED_KEYS: frozenset[str] = frozenset({"grain", "current", "baseline"})
_THRESHOLD_ALLOWED_KEYS: frozenset[str] = frozenset({"red", "yellow"})
_COMPARISON_ALLOWED_KEYS: frozenset[str] = frozenset({"direction", "red", "yellow"})

# 支持的时间粒度（与 DSL Granularity 枚举同名单）
SUPPORTED_GRAINS: frozenset[str] = frozenset({"day", "week", "month", "quarter"})


# --------------------------------------------------------------------------- #
# 契约对象（frozen dataclass，加载期一次构造）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ThresholdSpec:
    """绝对阈值判定：当期值 >= red 为红，>= yellow 为黄，否则绿。"""

    red: float
    yellow: float


@dataclass(frozen=True)
class ComparisonSpec:
    """相对异动判定：当期对基线变化幅度（比例，0.3 = 30%）超线判定。

    - ``drop``：跌幅告警，幅度 = (baseline - current) / baseline；
    - ``raise``：涨幅告警，幅度 = (current - baseline) / baseline。
    """

    direction: str
    red: float
    yellow: float


@dataclass(frozen=True)
class WindowSpec:
    """巡检时间窗口：显式登记当期代表日，基线按粒度推算上一期。"""

    grain: str
    current: date
    baseline: str


@dataclass(frozen=True)
class RiskRule:
    """单条风险巡检规则（契约对象，executor 与 report 的统一输入）。"""

    key: str
    title: str
    metric: str
    query_domain: str
    window: WindowSpec
    thresholds: ThresholdSpec | None
    comparison: ComparisonSpec | None
    dimension: str | None = None
    time_field: str = "order_time"


@dataclass(frozen=True)
class RiskRules:
    """规则文件契约对象（版本号 + 规则集合）。"""

    version: int
    rules: tuple[RiskRule, ...]


# --------------------------------------------------------------------------- #
# 窗口推算（纯函数，executor 与测试共用）
# --------------------------------------------------------------------------- #
def window_bounds(grain: str, day: date) -> tuple[date, date]:
    """按粒度把代表日归一到窗口边界 ``[start, end)``。

    - day：当日；week：所在 ISO 周（周一对齐，与编译器 week 口径一致）；
    - month：自然月；quarter：自然季度。
    """
    if grain == "day":
        return day, day + timedelta(days=1)
    if grain == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    if grain == "month":
        start = day.replace(day=1)
        return start, _add_months(start, 1)
    if grain == "quarter":
        quarter_start_month = 3 * ((day.month - 1) // 3) + 1
        start = date(day.year, quarter_start_month, 1)
        return start, _add_months(start, 3)
    raise ValueError(f"不支持的时间粒度：{grain!r}")


def previous_bounds(grain: str, start: date, end: date) -> tuple[date, date]:
    """推算上一期窗口（baseline=previous）。"""
    if grain == "day":
        return start - timedelta(days=1), end - timedelta(days=1)
    if grain == "week":
        return start - timedelta(days=7), end - timedelta(days=7)
    if grain == "month":
        prev_start = _add_months(start, -1)
        return prev_start, start
    if grain == "quarter":
        prev_start = _add_months(start, -3)
        return prev_start, start
    raise ValueError(f"不支持的时间粒度：{grain!r}")


def _add_months(day: date, months: int) -> date:
    """自然月偏移（越界日钳制到月末，如 1-31 减一月得 12-31）。"""
    month_index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# --------------------------------------------------------------------------- #
# fail-closed loader
# --------------------------------------------------------------------------- #
def load_rules(
    path: Path | str,
    *,
    metric_keys: frozenset[str] | None = None,
    allowed_fields: frozenset[str] | None = None,
    data_domain_end: date | None = None,
) -> RiskRules:
    """加载并校验规则文件（fail-closed，报错聚合为一条 ValueError）。

    参数注入项供测试隔离；生产路径默认读语义目录与 settings 全局：
    - ``metric_keys``：指标 key 白名单（默认 catalog.METRICS 全部 key）；
    - ``allowed_fields``：维度字段白名单（默认 catalog.COLUMNS 全部字段）；
    - ``data_domain_end``：数据域上界（默认 settings.DATA_DOMAIN_END）。
    """
    if metric_keys is None:
        metric_keys = frozenset(m.get("key") for m in METRICS if m.get("key"))
    if allowed_fields is None:
        allowed_fields = frozenset(COLUMNS)
    if data_domain_end is None:
        from config import settings

        data_domain_end = settings.DATA_DOMAIN_END

    path = Path(path)
    if not path.exists():
        raise ValueError(f"规则文件不存在：{path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"规则文件读取失败：{path}（{exc}）") from exc

    errors: list[str] = []
    rules: list[RiskRule] = []
    seen_keys: set[str] = set()

    if not isinstance(raw, dict):
        raise ValueError("规则文件顶层必须是 JSON 对象")
    unknown_top = sorted(set(raw) - {"version", "rules"})
    if unknown_top:
        raise ValueError(f"规则文件存在契约外顶层字段：{unknown_top}")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("version 必须为正整数")
    raw_rules = raw.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        errors.append("rules 必须为非空数组")
        raw_rules = []

    for idx, item in enumerate(raw_rules):
        prefix = f"rules[{idx}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} 必须为 JSON 对象")
            continue
        unknown = sorted(set(item) - _RULE_ALLOWED_KEYS)
        if unknown:
            errors.append(f"{prefix} 存在契约外字段：{unknown}")
        rule = _parse_rule(item, prefix, errors, metric_keys, allowed_fields, data_domain_end)
        if rule is None:
            continue
        if rule.key in seen_keys:
            errors.append(f"{prefix} key 重复：{rule.key!r}")
            continue
        seen_keys.add(rule.key)
        rules.append(rule)

    if errors:
        raise ValueError("风险规则契约校验失败（fail-closed）\n" + "\n".join(errors))
    return RiskRules(version=version if isinstance(version, int) else 0, rules=tuple(rules))


def _parse_rule(
    item: dict[str, Any],
    prefix: str,
    errors: list[str],
    metric_keys: frozenset[str],
    allowed_fields: frozenset[str],
    data_domain_end: date,
) -> RiskRule | None:
    """解析单条规则；结构可恢复则返回对象，致命缺失返回 None（错误已聚合）。"""

    def _err(msg: str) -> None:
        errors.append(f"{prefix} {msg}")

    fatal = False
    key = item.get("key")
    if not isinstance(key, str) or not key:
        _err("key 必须为非空字符串")
        fatal = True
    elif not re.match(IDENTIFIER_PATTERN, key):
        _err(f"key {key!r} 必须匹配标识符白名单 {IDENTIFIER_PATTERN}")
        fatal = True
    elif len(key) > 59:
        # 数据集名 = key + 期次后缀（_cur/_base），须为导出名 64 上限留位
        _err(f"key {key!r} 过长（须 <= 59 字符）")
        fatal = True
    title = item.get("title")
    if not isinstance(title, str) or not title:
        _err("title 必须为非空字符串")
        fatal = True

    metric = item.get("metric")
    if not isinstance(metric, str) or not metric:
        _err("metric 必须为非空字符串")
        fatal = True
    elif metric not in metric_keys:
        _err(
            f"metric {metric!r} 未在语义目录 metrics[] 登记（口径单一事实源，"
            f"可选手：{sorted(metric_keys)}）"
        )
        fatal = True

    query_domain = item.get("query_domain")
    if not isinstance(query_domain, str) or query_domain not in SUPPORTED_QUERY_DOMAINS:
        _err(
            f"query_domain 必须为 {sorted(SUPPORTED_QUERY_DOMAINS)} 之一，" f"得到 {query_domain!r}"
        )
        fatal = True

    time_field = item.get("time_field", "order_time")
    if time_field not in TIME_FIELDS:
        _err(f"time_field {time_field!r} 不在时间字段白名单 TIME_FIELDS 内")
        fatal = True

    dimension = item.get("dimension")
    if dimension is not None:
        if not isinstance(dimension, str) or not dimension:
            _err("dimension 须为 null 或非空字符串")
            fatal = True
        elif dimension not in allowed_fields:
            _err(f"dimension {dimension!r} 未在语义目录 COLUMNS 登记")
            fatal = True

    thresholds = _parse_thresholds(item.get("thresholds"), prefix, errors)
    comparison = _parse_comparison(item.get("comparison"), prefix, errors)
    if thresholds is None and comparison is None:
        _err("thresholds 与 comparison 至少配置一项（否则规则无判定能力）")
        fatal = True

    window = _parse_window(item.get("window"), prefix, errors, data_domain_end)

    if fatal or window is None:
        return None
    return RiskRule(
        key=key,
        title=title,
        metric=metric,
        query_domain=query_domain,
        window=window,
        thresholds=thresholds,
        comparison=comparison,
        dimension=dimension,
        time_field=time_field,
    )


def _parse_thresholds(raw: Any, prefix: str, errors: list[str]) -> ThresholdSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.append(f"{prefix} thresholds 必须为对象")
        return None
    unknown = sorted(set(raw) - _THRESHOLD_ALLOWED_KEYS)
    if unknown:
        errors.append(f"{prefix} thresholds 存在契约外字段：{unknown}")
    red, yellow = raw.get("red"), raw.get("yellow")
    if not _is_number(red) or not _is_number(yellow):
        errors.append(f"{prefix} thresholds.red/yellow 必须为数值")
        return None
    if red < yellow:
        errors.append(f"{prefix} thresholds 红黄倒挂（red {red} 须 >= yellow {yellow}）")
    if yellow < 0:
        errors.append(f"{prefix} thresholds 阈值须 >= 0")
    return ThresholdSpec(red=red, yellow=yellow)


def _parse_comparison(raw: Any, prefix: str, errors: list[str]) -> ComparisonSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.append(f"{prefix} comparison 必须为对象")
        return None
    unknown = sorted(set(raw) - _COMPARISON_ALLOWED_KEYS)
    if unknown:
        errors.append(f"{prefix} comparison 存在契约外字段：{unknown}")
    direction = raw.get("direction")
    if direction not in SUPPORTED_COMPARISON_DIRECTIONS:
        errors.append(
            f"{prefix} comparison.direction 必须为 "
            f"{sorted(SUPPORTED_COMPARISON_DIRECTIONS)} 之一，得到 {direction!r}"
        )
    red, yellow = raw.get("red"), raw.get("yellow")
    if not _is_number(red) or not _is_number(yellow):
        errors.append(f"{prefix} comparison.red/yellow 必须为数值（比例，0.3 = 30%）")
        return None
    if red < yellow:
        errors.append(f"{prefix} comparison 红黄倒挂（red {red} 须 >= yellow {yellow}）")
    if yellow < 0:
        errors.append(f"{prefix} comparison 幅度阈值须 >= 0")
    return ComparisonSpec(direction=direction, red=red, yellow=yellow)


def _parse_window(
    raw: Any, prefix: str, errors: list[str], data_domain_end: date
) -> WindowSpec | None:
    if not isinstance(raw, dict):
        errors.append(f"{prefix} window 必须为对象")
        return None
    unknown = sorted(set(raw) - _WINDOW_ALLOWED_KEYS)
    if unknown:
        errors.append(f"{prefix} window 存在契约外字段：{unknown}")
    grain = raw.get("grain")
    if grain not in SUPPORTED_GRAINS:
        errors.append(
            f"{prefix} window.grain 必须为 {sorted(SUPPORTED_GRAINS)} 之一，" f"得到 {grain!r}"
        )
        return None
    current_raw = raw.get("current")
    current = _parse_date(current_raw)
    if current is None:
        errors.append(f"{prefix} window.current 必须为 YYYY-MM-DD 日期，" f"得到 {current_raw!r}")
        return None
    baseline = raw.get("baseline", "previous")
    if baseline not in SUPPORTED_BASELINES:
        errors.append(
            f"{prefix} window.baseline 必须为 {sorted(SUPPORTED_BASELINES)}，" f"得到 {baseline!r}"
        )
    # 数据域上界校验：窗口最晚一天不得晚于数据域末日（越界即查不到完整数据）
    _, cur_end = window_bounds(grain, current)
    if cur_end - timedelta(days=1) > data_domain_end:
        errors.append(
            f"{prefix} window.current {current} 的 {grain} 窗口越出数据域上界 "
            f"{data_domain_end}（严禁对不完整窗口出报告）"
        )
    return WindowSpec(grain=grain, current=current, baseline=baseline)


def _parse_date(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
