"""埋点 JSON 日志落盘（复刻原 jar 的 file 输出通道）。

按天写 JSONL：logs/gmall_applog/<YYYY-MM-DD>.json，每行一条埋点日志
（common/start/page/actions/displays/err/ts，与原 jar fastjson 输出结构一致，
null 字段不输出）。会话事件按 ts 升序混排——原 jar 多线程乱序，本项目单线程
天然有序，无需排序。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def write_day_log(out_dir: Path, day: datetime, events: list[dict]) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    file_path = out_dir / f"{day.date().isoformat()}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(events)
