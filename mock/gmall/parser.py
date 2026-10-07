"""埋点 JSON 日志解析器：JSONL -> 行为事实表行（DuckDB 入仓前的"解析"链路）。

真实读取 logs/gmall_applog/*.json 并逐行解析（不依赖生成期的内存对象），
复刻数仓 ODS->DWD 的日志解析语义：
- 启动日志（含 start 字段）→ fact_start；
- 页面日志（含 page 字段）→ fact_page_view，其 displays[] / actions[]
  平铺为 fact_display / fact_action（page_id / uid 冗余到每条明细，便于漏斗分析）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _ms_to_ts(ms: int) -> datetime:
    """毫秒时间戳 -> naive datetime（与生成期的 UTC 口径互逆）。"""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None)


def parse_log_file(file_path: Path) -> tuple[list[list], list[list], list[list], list[list]]:
    """解析单日 JSONL，返回 (starts, page_views, actions, displays) 四组行。"""
    starts: list[list] = []
    page_views: list[list] = []
    actions: list[list] = []
    displays: list[list] = []
    with open(file_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            log = json.loads(line)
            common = log.get("common", {})
            sid = common.get("sid")
            mid = common.get("mid")
            uid = common.get("uid")
            province_id = int(common["ar"]) if common.get("ar") is not None else None
            ts = _ms_to_ts(log["ts"])
            if "start" in log:
                start = log["start"]
                starts.append(
                    [
                        sid,
                        mid,
                        uid,
                        province_id,
                        common.get("ch"),
                        common.get("os"),
                        start.get("entry"),
                        start.get("open_ad_ms"),
                        start.get("open_ad_skip_ms"),
                        start.get("loading_time"),
                        ts,
                    ]
                )
                continue
            page = log.get("page", {})
            err = log.get("err") or {}
            page_views.append(
                [
                    sid,
                    mid,
                    uid,
                    province_id,
                    page.get("page_id"),
                    page.get("last_page_id"),
                    page.get("during_time"),
                    page.get("item"),
                    page.get("item_type"),
                    page.get("from_pos_id"),
                    page.get("from_pos_seq"),
                    err.get("error_code"),
                    ts,
                ]
            )
            page_id = page.get("page_id")
            for action in log.get("actions") or []:
                actions.append(
                    [
                        sid,
                        action.get("action_id"),
                        action.get("item"),
                        action.get("item_type"),
                        page_id,
                        uid,
                        _ms_to_ts(action["ts"]) if action.get("ts") else ts,
                    ]
                )
            for display in log.get("displays") or []:
                displays.append(
                    [
                        sid,
                        display.get("item"),
                        display.get("item_type"),
                        display.get("pos_id"),
                        display.get("pos_seq"),
                        page_id,
                        uid,
                        ts,
                    ]
                )
    return starts, page_views, actions, displays


def parse_applog_dir(applog_dir: Path) -> tuple[list[list], list[list], list[list], list[list]]:
    """解析目录下全部日志文件（按文件名 = 日期升序）。"""
    all_starts: list[list] = []
    all_views: list[list] = []
    all_actions: list[list] = []
    all_displays: list[list] = []
    for file_path in sorted(applog_dir.glob("*.json")):
        starts, views, actions, displays = parse_log_file(file_path)
        all_starts.extend(starts)
        all_views.extend(views)
        all_actions.extend(actions)
        all_displays.extend(displays)
    return all_starts, all_views, all_actions, all_displays
