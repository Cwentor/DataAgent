"""沙箱引擎单测：AST 守卫 / 限权运行时 / 工作区隔离 / 产物协议 / 超时。

覆盖（对应企业级 Data Agent 需求 §3.2 代码沙箱安全）：
- AST：forbidden imports / calls / dunder 逃逸 / 相对导入 / 语法错误；
- 运行时：模块白名单 import、受限 open 越界拒绝、正常分析脚本跑通；
- 协议：summary.json 必需、协议外键拒绝、>100 行聚合矩阵拒绝；
- 后端：子进程超时强杀、limits 标注诚实性。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from core.sandbox.api import prepare_workspace, run_code
from core.sandbox.ast_guard import static_check

GOOD = """
df = read_input("sales")
gmv = float(df["amount"].sum())
save_summary(
    title="GMV 汇总",
    metrics={"gmv": gmv},
    table={"columns": ["region", "gmv"], "rows": [["all", gmv]]},
    findings=["GMV 合计 " + str(gmv)],
)
"""


def _make_workspace(tmp_path: Path) -> Path:
    ws = prepare_workspace(tmp_path / "ws")
    frame = pd.DataFrame({"region": ["华东", "华北", "华东"], "amount": [100.0, 200.0, 50.0]})
    frame.to_parquet(ws / "inputs" / "sales.parquet")
    return ws


# --------------------------------------------------------------------------- #
# AST 静态守卫
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import sys\nprint(sys.path)",
        "from subprocess import run",
        "import socket",
        "import pty",
        "import ctypes",
        "from pathlib import Path",
        "eval('1+1')",
        "exec('x=1')",
        "compile('1','<s>','eval')",
        "__import__('os')",
        "globals()",
        "locals()",
        "open('x.txt')",
        "obj.__class__",
        "cls.__subclasses__()",
        "from . import secret",
        "getattr(obj, '__globals__')",
    ],
)
def test_static_check_blocks(code):
    report = static_check(code)
    assert not report.ok
    assert report.violations


def test_static_check_allows_safe_code():
    report = static_check(GOOD)
    assert report.ok, report.summary()


def test_static_check_syntax_error_reported():
    report = static_check("def broken(:")
    assert not report.ok
    assert report.violations[0]["category"] == "syntax_error"


# --------------------------------------------------------------------------- #
# 限权运行时
# --------------------------------------------------------------------------- #
def test_run_code_happy_path(tmp_path):
    ws = _make_workspace(tmp_path)
    result = run_code(GOOD, ws, name="happy")
    assert result.ok, result.error
    assert result.summary["metrics"]["gmv"] == 350.0
    assert result.summary["table"]["rows"] == [["all", 350.0]]
    # Windows 子进程无法施加 rlimit/cgroups：limits_enforced 必须如实标注
    import sys

    if sys.platform == "win32":
        assert result.limits_enforced is False
    else:
        assert result.limits_enforced is True


def test_run_code_blocks_forbidden_import_at_runtime(tmp_path):
    ws = _make_workspace(tmp_path)
    # 静态守卫先拦（importlib 在模块黑名单）——双层防线第一层验证
    code = "import importlib\nimportlib.import_module('os')\n"
    result = run_code(code, ws, name="evil")
    assert not result.ok
    assert "静态校验" in result.error


def test_run_code_import_whitelist(tmp_path):
    ws = _make_workspace(tmp_path)
    code = "import math\nimport json\nimport collections\nsave_summary(metrics={'ok': 1})\n"
    result = run_code(code, ws, name="imports")
    assert result.ok, result.error
    assert result.summary["metrics"]["ok"] == 1


def test_run_code_open_escape_blocked(tmp_path):
    ws = _make_workspace(tmp_path)
    # open 直接形态被静态守卫拦截（第一层）
    code = "f = open('data.txt')\n"
    result = run_code(code, ws, name="open_escape")
    assert not result.ok
    assert "静态校验" in result.error


def test_run_code_missing_summary(tmp_path):
    ws = _make_workspace(tmp_path)
    result = run_code("x = 1\nprint(x)\n", ws, name="no_output")
    assert not result.ok
    assert "summary.json" in result.error


def test_run_code_rejects_oversized_table(tmp_path):
    ws = _make_workspace(tmp_path)
    rows = [[i] for i in range(101)]
    code = "save_summary(table={'columns': ['v'], 'rows': " + json.dumps(rows) + "})\n"
    result = run_code(code, ws, name="big")
    assert not result.ok
    assert "100 行上限" in result.error


def test_run_code_protocol_foreign_key(tmp_path):
    ws = _make_workspace(tmp_path)
    (ws / "outputs").mkdir(exist_ok=True)
    (ws / "outputs" / "summary.json").write_text(json.dumps({"rogue_key": 1}), encoding="utf-8")
    code = "pass\n"
    result = run_code(code, ws, name="rogue")
    assert not result.ok
    assert "协议外键" in result.error


def test_run_code_timeout_kills(tmp_path):
    ws = _make_workspace(tmp_path)
    result = run_code("while True:\n    pass\n", ws, name="loop", timeout_seconds=2)
    assert not result.ok
    assert "超时" in result.error


def test_run_code_rejects_bad_script_name(tmp_path):
    result = run_code("pass", tmp_path, name="../evil")
    assert not result.ok


def test_run_code_echarts_spec_roundtrip(tmp_path):
    ws = _make_workspace(tmp_path)
    code = (
        "save_summary(metrics={'gmv': 1})\n"
        "save_echarts_spec({'title': {'text': 'GMV'}, 'series': [{'type': 'bar'}]})\n"
    )
    result = run_code(code, ws, name="chart")
    assert result.ok, result.error
    assert result.echarts_spec["series"][0]["type"] == "bar"
