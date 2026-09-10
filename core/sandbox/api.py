"""沙箱执行编排：静态校验 -> 后端执行 -> 结构化产物校验（API 门面）。

完整执行管线（run_code）：
1. ``ast_guard.static_check``：AST 静态校验，违规立即拒绝（附行号供自愈）；
2. 工作区准备：``{workspace}/inputs/``（只读数据集）、``{workspace}/outputs/``
   （脚本唯一可写位置）、脚本落盘 ``{workspace}/scripts/``；
3. 注入 bootstrap 头（限权 builtins + workspace 约束 + 输出协议），与用户
   代码拼接后经后端执行；
4. 产物校验：``outputs/summary.json`` 必须存在且为合法 JSON、聚合矩阵
   ≤100 行；``echarts_spec.json`` 可选（须为 dict）。

run_code 永不抛沙箱内异常——失败统一结构化为 SandboxResult（ok=False +
violations / stderr 摘要），供编排层 error_context 与 LLM 自愈消费。
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.sandbox.ast_guard import static_check
from core.sandbox.backends import DEFAULT_TIMEOUT_SECONDS, SandboxBackend, SubprocessBackend

# 聚合矩阵行数上限（需求 §3.3：强制聚合，杜绝海量原始行回流）
MAX_SUMMARY_ROWS = 100

# bootstrap 模板：runner 注入到用户代码前的限权运行时（详见 runner.py）
_BOOTSTRAP_PATH = Path(__file__).with_name("_bootstrap.py")

# summary.json 允许的顶层键（结构化输出协议）
_SUMMARY_ALLOWED_KEYS = frozenset({"title", "metrics", "table", "findings", "extra"})


@dataclass
class SandboxResult:
    """一次沙箱执行的结构化结果（编排层可直接消费）。"""

    ok: bool
    backend: str
    duration_ms: float
    summary: dict | None = None
    echarts_spec: dict | None = None
    stdout_tail: str = ""
    error: str | None = None
    violations: list[dict[str, str]] = field(default_factory=list)
    limits_enforced: bool = False

    def to_dict(self) -> dict:
        """序列化（审计与编排轨迹落盘）。"""
        return {
            "ok": self.ok,
            "backend": self.backend,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "echarts_spec": self.echarts_spec,
            "stdout_tail": self.stdout_tail[-2000:],
            "error": self.error,
            "violations": self.violations,
            "limits_enforced": self.limits_enforced,
        }


def prepare_workspace(workspace: Path | str) -> Path:
    """建立会话工作区目录结构（inputs/scripts/outputs），返回根路径。"""
    ws = Path(workspace)
    for sub in ("inputs", "scripts", "outputs"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    return ws


def _script_ok_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name))


def run_code(
    code: str,
    workspace: Path | str,
    *,
    name: str = "analysis",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    backend: SandboxBackend | None = None,
) -> SandboxResult:
    """执行一段分析代码（先静态校验，后隔离执行，产物协议校验）。

    - ``name``：脚本逻辑名（决定落盘文件名，须为安全标识符）；
    - 脚本预期从 ``inputs/`` 读 Parquet、向 ``outputs/`` 写 summary.json。
    """
    if not _script_ok_name(name):
        return SandboxResult(
            ok=False, backend="none", duration_ms=0.0, error=f"非法脚本名: {name!r}"
        )

    # 1) 静态守卫：违规即拒绝，代码不落盘不执行
    report = static_check(code)
    if not report.ok:
        return SandboxResult(
            ok=False,
            backend="static",
            duration_ms=0.0,
            error=f"静态校验未通过: {report.summary()}",
            violations=report.violations,
        )

    ws = prepare_workspace(workspace)
    script_path = ws / "scripts" / f"{name}.py"
    bootstrap = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    # 用户代码 base64 内嵌（避免引号/转义破坏 runner 脚本结构）
    user_code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    script = bootstrap.replace("__USER_CODE_B64__", user_code_b64)
    if "__USER_CODE_B64__" in script:
        raise RuntimeError("runner 占位符替换失败")
    script_path.write_text(script, encoding="utf-8")

    # 2) 后端执行
    backend = backend or SubprocessBackend()
    result = backend.run(script_path, ws, timeout_seconds=timeout_seconds)

    # 3) 产物协议校验
    outputs_dir = ws / "outputs"
    summary_path = outputs_dir / "summary.json"
    if not summary_path.exists():
        error = (
            result.stderr.strip().splitlines()[-1]
            if result.stderr.strip()
            else "脚本未产出 summary.json"
        )
        return SandboxResult(
            ok=False,
            backend=result.backend,
            duration_ms=result.duration_ms,
            stdout_tail=result.stdout,
            error=(f"沙箱超时（{timeout_seconds}s 硬上限）" if result.timed_out else error),
            limits_enforced=result.limits_enforced,
        )

    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return SandboxResult(
            ok=False,
            backend=result.backend,
            duration_ms=result.duration_ms,
            error=f"summary.json 非法: {exc}",
            limits_enforced=result.limits_enforced,
        )

    unexpected = set(summary) - _SUMMARY_ALLOWED_KEYS
    if unexpected:
        return SandboxResult(
            ok=False,
            backend=result.backend,
            duration_ms=result.duration_ms,
            error=f"summary.json 含协议外键: {sorted(unexpected)}",
            limits_enforced=result.limits_enforced,
        )
    table = summary.get("table")
    if isinstance(table, dict) and len(table.get("rows", [])) > MAX_SUMMARY_ROWS:
        return SandboxResult(
            ok=False,
            backend=result.backend,
            duration_ms=result.duration_ms,
            error=f"聚合矩阵 {len(table['rows'])} 行超出 {MAX_SUMMARY_ROWS} 行上限（请先聚合）",
            limits_enforced=result.limits_enforced,
        )

    echarts_spec = None
    spec_path = outputs_dir / "echarts_spec.json"
    if spec_path.exists():
        try:
            echarts_spec = json.loads(spec_path.read_text(encoding="utf-8"))
            if not isinstance(echarts_spec, dict):
                echarts_spec = None
        except (json.JSONDecodeError, OSError):
            echarts_spec = None

    return SandboxResult(
        ok=True,
        backend=result.backend,
        duration_ms=result.duration_ms,
        summary=summary,
        echarts_spec=echarts_spec,
        stdout_tail=result.stdout,
        limits_enforced=result.limits_enforced,
    )


def elapsed_budget_guard(started: float, budget_ms: float) -> float:
    """编排层 token/时间预算工具：返回已消耗毫秒（超预算由编排层裁剪）。"""
    return round((time.perf_counter() - started) * 1000.0, 3)


__all__ = [
    "MAX_SUMMARY_ROWS",
    "SandboxResult",
    "elapsed_budget_guard",
    "prepare_workspace",
    "run_code",
]
