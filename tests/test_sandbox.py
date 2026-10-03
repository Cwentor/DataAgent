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
from core.sandbox.backends import (
    DEFAULT_TIMEOUT_SECONDS,
    BackendResult,
    SandboxBackend,
)

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


# --------------------------------------------------------------------------- #
# 后端解析（SANDBOX_BACKEND 配置）：显式传参 > 配置；降级如实留痕
# --------------------------------------------------------------------------- #


class _RecordingBackend(SandboxBackend):
    """记录型假后端：只验证分发正确性，不写产物（下游协议校验必失败）。"""

    name = "recording"

    def __init__(self):
        self.called = False

    def is_available(self) -> bool:
        return True

    def run(
        self,
        script_path: Path,
        workspace: Path,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> BackendResult:
        self.called = True
        return BackendResult(
            returncode=0,
            stdout="",
            stderr="",
            duration_ms=0.0,
            backend=self.name,
            limits_enforced=False,
        )


def test_run_code_explicit_backend_wins(tmp_path):
    """显式传参优先于 SANDBOX_BACKEND 配置（历史行为逐字不变）。"""
    ws = _make_workspace(tmp_path)
    recording = _RecordingBackend()
    result = run_code("x = 1", ws, backend=recording)
    assert recording.called
    assert result.backend == "recording"


def test_run_code_backend_config_auto_dispatch(monkeypatch, tmp_path):
    """SANDBOX_BACKEND=auto 分发到 default_backend() 的探测结果。"""
    from core.sandbox import api as sandbox_api

    ws = _make_workspace(tmp_path)
    recording = _RecordingBackend()
    monkeypatch.setattr("config.settings.SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(sandbox_api, "default_backend", lambda: recording)
    result = run_code("x = 1", ws)
    assert recording.called
    assert result.backend == "recording"


def test_run_code_backend_config_docker_degrades(monkeypatch, tmp_path):
    """SANDBOX_BACKEND=docker 但 Docker 不可用：如实降级子进程（不静默）。"""
    from core.sandbox.backends import DockerBackend

    ws = _make_workspace(tmp_path)
    monkeypatch.setattr("config.settings.SANDBOX_BACKEND", "docker")
    monkeypatch.setattr(DockerBackend, "is_available", lambda self: False)
    result = run_code("x = 1", ws)
    assert result.backend == "subprocess"


def test_run_code_backend_config_invalid_fails_fast(monkeypatch, tmp_path):
    """非法 SANDBOX_BACKEND 配置：显式抛错（拒绝猜测，不静默回退）。"""
    monkeypatch.setattr("config.settings.SANDBOX_BACKEND", "quantum")
    ws = _make_workspace(tmp_path)
    with pytest.raises(ValueError):
        run_code("x = 1", ws)


def test_default_backend_cached_and_clearable(monkeypatch):
    """Docker 探测结果进程级缓存：多次调用仅探测一次，clear 后重新探测。"""
    from core.sandbox import backends

    calls: list[int] = []

    def _fake_available(self) -> bool:
        calls.append(1)
        return False

    monkeypatch.setattr(backends.DockerBackend, "is_available", _fake_available)
    backends.clear_default_backend_cache()
    try:
        b1 = backends.default_backend()
        b2 = backends.default_backend()
        assert b1 is b2
        assert len(calls) == 1
        backends.clear_default_backend_cache()
        backends.default_backend()
        assert len(calls) == 2
    finally:
        backends.clear_default_backend_cache()


# --------------------------------------------------------------------------- #
# save_summary 参数校验（2026-09-29 线上案例：Coder 把统计 dict 误塞 title 形参）
# --------------------------------------------------------------------------- #
def test_run_code_rejects_dict_title_with_guidance(tmp_path):
    """title 非 str（Coder 位置参数误用）=> 沙箱精确报错并指引正确用法（反哺自愈）。"""
    ws = _make_workspace(tmp_path)
    code = "save_summary({'week1': {'gmv': 616872.81}, 'week2': {'gmv': 410248.48}})\n"
    result = run_code(code, ws, name="dict_title")
    assert not result.ok
    assert "title" in result.error
    assert "metrics" in result.error  # 指引把统计数据放 metrics/table


def test_run_code_rejects_repr_like_str_title(tmp_path):
    """title 为 repr/JSON 串形态（'{' 开头）同样拒绝——防 dump 上屏。"""
    ws = _make_workspace(tmp_path)
    code = "save_summary(\"{'total_delta_gmv': -206624.33}\")\n"
    result = run_code(code, ws, name="repr_title")
    assert not result.ok
    assert "title" in result.error


def test_run_code_str_title_still_ok(tmp_path):
    """合法中文短语 title 不受校验影响。"""
    ws = _make_workspace(tmp_path)
    code = "save_summary(title='驱动因子分解', metrics={'gmv': 350.0})\n"
    result = run_code(code, ws, name="str_title")
    assert result.ok, result.error
    assert result.summary["title"] == "驱动因子分解"
    assert result.summary["metrics"]["gmv"] == 350.0


# --------------------------------------------------------------------------- #
# 沙箱 duckdb.connect 旁路封堵（十九期 M5，spec §6.1）：取数权只在执行层
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "code",
    [
        "import duckdb\nduckdb.connect('analytics_sandbox.duckdb')",
        "import duckdb as d\nd.connect('x.duckdb')",
        "from duckdb import connect\nconnect('x.duckdb')",
        "import duckdb\nduckdb.database('x.duckdb')",
    ],
)
def test_static_check_blocks_duckdb_connect_bypass(code):
    """duckdb 直连数仓的旁路必须被静态校验拒绝（AST 守卫拦不住 C 扩展的
    connect 调用，必须在代码层封死）。"""
    report = static_check(code)
    assert not report.ok, report.summary()
    assert any("connect" in v["detail"] or "database" in v["detail"] for v in report.violations)


def test_static_check_allows_duckdb_read_parquet():
    """合法用途不受影响：read_parquet 只读消费 ParquetRef 导出。"""
    code = "import duckdb\n" "con = duckdb.connect()  # 内存连接仅用于 read_parquet 消费导出\n"
    report = static_check(code)
    assert not report.ok, "connect 调用（含内存库）一律封死，防止以内存连接为跳板"


def test_static_check_allows_pandas_read_parquet():
    report = static_check("import pandas as pd\ndf = pd.read_parquet('inputs/s1.parquet')")
    assert report.ok, report.summary()
