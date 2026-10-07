"""结构化日志文件 handler 测试（P3 可观测性）：log_file 非空时日志落盘。"""

from __future__ import annotations

import logging


def test_setup_logging_writes_file_when_enabled(tmp_path):
    """配置 log_file 时日志落盘（RotatingFileHandler + JsonFormatter）。"""
    from audit.logging import setup_logging

    log_file = tmp_path / "app.log"
    root = logging.getLogger()
    # 保存既有 handler，测试后恢复，避免污染其他测试的输出捕获
    saved = list(root.handlers)
    for h in saved:
        root.removeHandler(h)
    try:
        setup_logging(log_file=str(log_file))
        logging.getLogger("test").info("hello-audit")
        for h in list(root.handlers):
            h.flush()
        assert log_file.exists()
        assert "hello-audit" in log_file.read_text(encoding="utf-8")
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)


def test_setup_logging_without_log_file_no_file(tmp_path):
    """未配置 log_file 时不落盘（默认仅 stderr）。"""
    from audit.logging import setup_logging

    log_file = tmp_path / "app.log"
    root = logging.getLogger()
    saved = list(root.handlers)
    for h in saved:
        root.removeHandler(h)
    try:
        setup_logging()  # 无 log_file
        logging.getLogger("test2").info("stderr-only")
        for h in list(root.handlers):
            h.flush()
        assert not log_file.exists()
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)
