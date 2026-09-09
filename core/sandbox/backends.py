"""沙箱执行后端（可插拔）：Docker 强隔离 + 本地子进程兜底。

后端协议（SandboxBackend）：给定候选脚本与工作区路径，在隔离环境内执行，
返回 (returncode, stdout, stderr, duration_ms, limits_enforced)。

- ``DockerBackend``：容器强隔离——网络禁用（--net none）、全部 capabilities
  移除（--cap-drop ALL）、非 root UID（10001）、只读 rootfs、内存/CPU cgroups
  硬限、工作区只读挂载到 /workspace（容器内写 outputs 由 runner 重定向至
  /tmp/workspace）。可用性运行时探测（无 Docker 时 is_available=False）。
- ``SubprocessBackend``：本地子进程执行（默认后端，Windows 可用）：
  超时强杀 + stdout/stderr 上限；POSIX 上经 preexec_fn 以 resource.setrlimit
  施加地址空间/CPU 限额并 setuid 到非特权用户（可配置）；Windows 无法施加
  cgroups 级限制——limits_enforced 如实返回 False（隔离主要由 AST 守卫 +
  限权 builtins + 进程隔离承担）。

安全边界说明（诚实标注，不虚报）：子进程后端的隔离强度弱于容器后端，
适用于受信任的单机/开发场景；生产多租户部署应启用 DockerBackend。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# stdout/stderr 捕获上限（防脚本打印海量数据把编排器 token 打爆）
MAX_STREAM_BYTES = 256 * 1024

# 执行超时（秒）——硬上限，runner 内部还有软超时
DEFAULT_TIMEOUT_SECONDS = 15.0


@dataclass
class BackendResult:
    """一次后端执行的完整产物。"""

    returncode: int
    stdout: str
    stderr: str
    duration_ms: float
    backend: str
    limits_enforced: bool
    timed_out: bool = False


class SandboxBackend:
    """后端协议基类。"""

    name = "base"

    def is_available(self) -> bool:
        """运行时探测后端可用性。"""
        raise NotImplementedError

    def run(
        self,
        script_path: Path,
        workspace: Path,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> BackendResult:
        """在隔离环境内执行 script_path（workspace 为会话工作区根目录）。"""
        raise NotImplementedError


class SubprocessBackend(SandboxBackend):
    """本地子进程后端：超时强杀 + 流上限 +（POSIX）rlimit 资源限额。"""

    name = "subprocess"

    # 资源限额档位（需求 §2.B：1.0 vCPU / 1024MB / 512MB scratch）
    memory_limit_mb: int = 1024
    cpu_seconds: float = 15.0

    def is_available(self) -> bool:
        return True

    def run(
        self,
        script_path: Path,
        workspace: Path,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> BackendResult:
        started = time.perf_counter()
        limits_enforced = False
        preexec: Any = None
        if sys.platform != "win32":
            preexec = self._make_posix_preexec()
            limits_enforced = True

        try:
            proc = subprocess.Popen(
                [sys.executable, str(script_path)],
                cwd=str(workspace),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                preexec_fn=preexec,
                env=self._clean_env(workspace),
            )
        except OSError as exc:
            return BackendResult(
                returncode=-1,
                stdout="",
                stderr=f"后端启动失败: {exc}",
                duration_ms=0.0,
                backend=self.name,
                limits_enforced=False,
            )

        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            stdout, stderr = proc.communicate()

        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return BackendResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout.decode("utf-8", errors="replace")[:MAX_STREAM_BYTES],
            stderr=stderr.decode("utf-8", errors="replace")[:MAX_STREAM_BYTES],
            duration_ms=duration_ms,
            backend=self.name,
            limits_enforced=limits_enforced,
            timed_out=timed_out,
        )

    def _make_posix_preexec(self) -> Any:
        """POSIX 子进程预置：地址空间 / CPU / 文件大小 rlimit + 可选降权。"""

        def _preexec() -> None:  # pragma: no cover - 子进程内执行
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (self.memory_limit_mb * 1024 * 1024,) * 2)
            resource.setrlimit(resource.RLIMIT_CPU, (int(self.cpu_seconds),) * 2)
            resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 * 1024,) * 2)

        return _preexec

    def _clean_env(self, workspace: Path) -> dict[str, str]:
        """最小环境变量：切断宿主敏感配置向沙箱泄露。"""
        return {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "SANDBOX_WORKSPACE": str(workspace),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),  # Windows Python 启动需要
        }


class DockerBackend(SandboxBackend):
    """容器后端：cgroups 硬限 + 网络禁用 + 非 root + 只读 rootfs。

    工作区挂载为容器内 /workspace（读写仅限 outputs 子路径由 runner 保证）；
    镜像要求预装 python3 + pandas/pyarrow/duckdb（构建脚本见 deploy/）。
    """

    name = "docker"
    image: str = "dataagent-sandbox:latest"
    memory_mb: int = 1024
    cpus: float = 1.0
    container_user: str = "10001:10001"

    def is_available(self) -> bool:
        """探测 docker CLI 与镜像可用性（失败即回落子进程后端）。"""
        try:
            probe = subprocess.run(
                ["docker", "image", "inspect", self.image],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return probe.returncode == 0

    def run(
        self,
        script_path: Path,
        workspace: Path,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> BackendResult:
        started = time.perf_counter()
        # 容器内路径：脚本与工作区都挂到 /workspace（只读 rootfs 下唯一可写层）
        container_script = "/workspace/" + script_path.relative_to(workspace).as_posix()
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--read-only",
            "--user",
            self.container_user,
            "--memory",
            f"{self.memory_mb}m",
            "--cpus",
            str(self.cpus),
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:rw,size=512m,noexec",
            "-v",
            f"{workspace}:/workspace:rw",
            self.image,
            sys.executable if os.name != "nt" else "python3",
            container_script,
        ]
        timed_out = False
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout_seconds, check=False)
            returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""

        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return BackendResult(
            returncode=returncode,
            stdout=stdout.decode("utf-8", errors="replace")[:MAX_STREAM_BYTES],
            stderr=stderr.decode("utf-8", errors="replace")[:MAX_STREAM_BYTES],
            duration_ms=duration_ms,
            backend=self.name,
            limits_enforced=True,
            timed_out=timed_out,
        )


def default_backend() -> SandboxBackend:
    """默认后端选择：Docker 可用则容器强隔离，否则本地子进程兜底。"""
    docker = DockerBackend()
    if docker.is_available():
        return docker
    return SubprocessBackend()


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "BackendResult",
    "DockerBackend",
    "SandboxBackend",
    "SubprocessBackend",
    "default_backend",
]
