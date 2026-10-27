"""五后端本地实测矩阵：连通性 / 实用性 / 调度链路稳定性（docker 编排驱动）。

背景
----
四个外部后端（mysql / postgres / clickhouse / hive）的正确性此前只在 GitHub
Actions 的服务级 job 里被真实执行过，本地开发者无法一键复现；调度链路
（web.service.run_query -> 多工具 Agent -> exec.pool -> execute_sql）在后端
切换下的稳定性也没有本地实测面。本脚本把这条链路搬到本地 Docker 上。

三档检查（口径固定，勿各自解释）
--------------------------------
L1 连通性：``get_adapter(b).connect_readonly()`` 成功（该调用内含 harden
    只读校验）+ ``SELECT 1`` 可执行 + 连接可关闭。只读校验失败即视为不连通
    ——能连上但没加固，等于把写权限暴露给 LLM 链路。
L2 实用性：跑 ``tests/test_<backend>_backend_parity.py``（golden 全量 DSL 双后端
    一致性，比对口径在 tests/parity_utils.py）+ ``semantic.catalog_loader`` 冒烟
    （物理元数据面能否从真实后端读到）。「能连上」与「能算对」是两件事。
L3 调度链路稳定性：经 ``web.service.run_query`` 走完整链路（意图路由 → 多工具
    Agent 规划 → 编译 → 池化连接执行 → 结果组装），串行 N 轮 + 并发 M 线程，
    断言成功率、连接池不泄漏（size <= capacity）、失败不污染后续轮次。

强制启发式：L3 显式清空 LLM_API_KEY。有 key 时链路会走真实 LLM，延迟与结果
都不确定，稳定性测量失去意义；无 key 时 Agent 回退确定性启发式 NL2DSL，这是
项目既有的离线可评测路径。

用法
----
    # 起容器 -> 建仓 -> 建只读账号 -> 三档体检 -> 出报告
    python scripts/backend_matrix.py --up

    # 容器已在跑，只重跑体检（跳过建仓）
    python scripts/backend_matrix.py --skip-provision

    # 单后端 / 调轮次
    python scripts/backend_matrix.py --only hive --chain-rounds 5

    # 收容器（含卷）
    python scripts/backend_matrix.py --down

前置：Docker Desktop 可用；端口 3307/5433/8124/10001 空闲（本机 10000 常被
百度网盘占用，故 Hive 偏移到 10001）。DuckDB 是进程内文件库，不参与容器编排，
作为一致性基线一起体检。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 以脚本方式执行时 sys.path[0] 是 scripts/，项目根不在导入路径——显式补上，
# 否则 `from exec.engine import get_adapter` 之类的包内导入会 ModuleNotFoundError。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

COMPOSE_FILE = PROJECT_ROOT / "docker" / "docker-compose.backends.yml"
REPORT_JSON = PROJECT_ROOT / "logs" / "backend_matrix.json"

# 与 docker/docker-compose.backends.yml 的端口默认值必须一致（两侧同步改）
HOST_PORTS = {
    "mysql": 3307,
    "postgres": 5433,
    "clickhouse": 8124,
    "hive": 10001,
}

# 建仓账号（compose 的 root/postgres/default/hive）与运行时只读账号分离
WRITE_ACCOUNTS = {
    "mysql": ("root", "root"),
    "postgres": ("postgres", "root"),
    "clickhouse": ("default", "ch_local_pwd"),
    "hive": ("hive", ""),
}
RO_ACCOUNTS = {
    "mysql": ("dataagent_ro", "ro_pwd"),
    "postgres": ("dataagent_ro", "ro_pwd"),
    "clickhouse": ("dataagent_ro", "ch_ro_pwd"),
    # Hive 单容器默认 authentication=NONE，无法做账号分离——部署缺口，见报告
    "hive": ("hive", ""),
}

PARITY_TESTS = {
    "mysql": "tests/test_dual_backend_parity.py",
    "postgres": "tests/test_pg_backend_parity.py",
    "clickhouse": "tests/test_clickhouse_backend_parity.py",
    "hive": "tests/test_hive_backend_parity.py",
}

ALL_BACKENDS = ("duckdb", "mysql", "postgres", "clickhouse", "hive")

# L3 链路问题：取 golden 数据集里的真实问句（保证启发式解析有解）
CHAIN_QUESTIONS = (
    "最近7天的GMV是多少",
    "各渠道的订单量分别是多少",
    "2025年每个月的GMV趋势",
)


@dataclass
class CheckResult:
    """单后端单档检查结果。"""

    backend: str
    stage: str
    ok: bool
    elapsed_s: float
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _log(msg: str) -> None:
    print(msg, flush=True)


# 后端相关环境变量前缀：子进程必须从干净基线开始，否则父进程的 DB_BACKEND
# 会漏进 pytest 的 DuckDB 建库 fixture（实测：占位符风格错配 → Parser Error）
_BACKEND_ENV_PREFIXES = ("MYSQL_", "POSTGRES_", "CLICKHOUSE_", "HIVE_")
_BACKEND_ENV_KEYS = ("DB_BACKEND", "SQL_DIALECT")


def _clean_env() -> dict[str, str]:
    """剥离全部后端配置后的环境基线（子进程只吃显式传入的后端变量）。"""
    return {
        k: v
        for k, v in os.environ.items()
        if k not in _BACKEND_ENV_KEYS and not k.startswith(_BACKEND_ENV_PREFIXES)
    }


def _run(
    cmd: list[str], env: dict[str, str] | None = None, timeout: int = 1800
) -> subprocess.CompletedProcess:
    """子进程执行（干净基线 + 显式后端变量，统一捕获输出）。"""
    full_env = {**_clean_env(), **(env or {})}
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=full_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc


def backend_env(backend: str, *, readonly: bool = True) -> dict[str, str]:
    """构造某后端的完整环境变量（运行时口径，只读账号）。

    ``readonly=False`` 时给建仓用的可写账号。duckdb 无需连接变量。
    """
    env: dict[str, str] = {"DB_BACKEND": backend}
    if backend == "duckdb":
        return env
    user, pwd = RO_ACCOUNTS[backend] if readonly else WRITE_ACCOUNTS[backend]
    port = HOST_PORTS[backend]
    host = "127.0.0.1"
    if backend == "mysql":
        env.update(
            MYSQL_HOST=host,
            MYSQL_PORT=str(port),
            MYSQL_USER=user,
            MYSQL_PASSWORD=pwd,
            MYSQL_DATABASE="gmall",
        )
    elif backend == "postgres":
        env.update(
            POSTGRES_HOST=host,
            POSTGRES_PORT=str(port),
            POSTGRES_USER=user,
            POSTGRES_PASSWORD=pwd,
            POSTGRES_DATABASE="gmall",
            POSTGRES_SCHEMA="public",
        )
    elif backend == "clickhouse":
        env.update(
            CLICKHOUSE_HOST=host,
            CLICKHOUSE_PORT=str(port),
            CLICKHOUSE_USER=user,
            CLICKHOUSE_PASSWORD=pwd,
            CLICKHOUSE_DATABASE="gmall",
        )
    elif backend == "hive":
        env.update(
            HIVE_HOST=host,
            HIVE_PORT=str(port),
            HIVE_USER=user,
            HIVE_PASSWORD=pwd,
            HIVE_DATABASE="gmall",
            HIVE_STAGING_CLIENT_DIR=str(PROJECT_ROOT / "build" / "hive_staging"),
            HIVE_STAGING_SERVER_DIR="/opt/dataagent_staging",
        )
    return env


def test_env(backend: str) -> dict[str, str]:
    """parity 测试专用变量（``<BACKEND>_TEST_*``，与运行时配置解耦）。"""
    if backend == "duckdb":
        return {}
    user, pwd = RO_ACCOUNTS[backend]
    port = str(HOST_PORTS[backend])
    prefix = {"mysql": "MYSQL", "postgres": "POSTGRES", "clickhouse": "CLICKHOUSE", "hive": "HIVE"}[
        backend
    ]
    return {
        f"{prefix}_TEST_HOST": "127.0.0.1",
        f"{prefix}_TEST_PORT": port,
        f"{prefix}_TEST_USER": user,
        f"{prefix}_TEST_PASSWORD": pwd,
        f"{prefix}_TEST_DATABASE": "gmall",
    }


# --------------------------------------------------------------------------- #
# 容器编排
# --------------------------------------------------------------------------- #
def compose_up() -> None:
    """起四个后端容器（幂等：已存在的容器复用）。"""
    (PROJECT_ROOT / "build" / "hive_staging").mkdir(parents=True, exist_ok=True)
    _log("[compose] up -d ...")
    proc = _run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"], timeout=1800)
    if proc.returncode != 0:
        raise SystemExit(f"compose up 失败：\n{proc.stdout}\n{proc.stderr}")
    _log(proc.stdout.strip() or "[compose] ok")


def compose_down() -> None:
    """停容器并删卷（幂等）。"""
    _log("[compose] down -v ...")
    proc = _run(["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"], timeout=600)
    _log(proc.stdout.strip() or proc.stderr.strip())


def _probe_service(backend: str) -> None:
    """驱动级探活（用**建仓账号**直连，不经 adapter/harden）。

    为什么不用 ``connect_readonly``：只读账号由 provision 阶段创建，探活时还
    不存在——用它探活会把「账号未建」误报成「服务未起」，且 ClickHouse 的
    harden 会正确拒绝非 readonly profile 的 default 账号。探活只回答一个问题：
    服务端能否接受 SQL。
    """
    user, pwd = WRITE_ACCOUNTS[backend]
    host, port = "127.0.0.1", HOST_PORTS[backend]
    if backend == "mysql":
        import pymysql

        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=pwd,
            connect_timeout=5,
            charset="utf8mb4",
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
    elif backend == "postgres":
        import psycopg

        conn = psycopg.connect(
            host=host, port=port, user=user, password=pwd, dbname="gmall", connect_timeout=5
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
    elif backend == "clickhouse":
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=host, port=port, username=user, password=pwd, connect_timeout=5
        )
        try:
            client.query("SELECT 1")
        finally:
            client.close()
    elif backend == "hive":
        from pyhive import hive

        # 建库前 gmall 还不存在，探活用 default
        conn = hive.Connection(
            host=host, port=port, username=user, database="default", scheme="http"
        )
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
        finally:
            conn.close()


# 确定性失败（账号/配置错），重试无意义——立即返回，别白等满超时
_FATAL_PROBE_MARKERS = (
    "Access denied",
    "authentication failed",
    "Authentication failed",
    "password authentication failed",
    "does not exist",
    "Unknown database",
)


def wait_ready(backend: str, timeout_s: int = 240) -> CheckResult:
    """等待后端可接受 SQL（驱动级探活，不是端口探活）。

    端口通了不代表服务可用：Hive 的 HiveServer2 要初始化内嵌 Derby 与 Tez
    （实测 40-90s），ClickHouse 的 initdb 脚本要先跑完。账号类错误是确定性
    失败，直接返回而不是重试满超时。
    """
    started = time.perf_counter()
    if backend == "duckdb":
        return CheckResult(backend, "wait_ready", True, 0.0, {"note": "进程内文件库，无需等待"})

    deadline = time.time() + timeout_s
    last_err: str | None = None
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        try:
            _probe_service(backend)
            return CheckResult(
                backend,
                "wait_ready",
                True,
                time.perf_counter() - started,
                {"attempts": attempts},
            )
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if any(m in str(exc) for m in _FATAL_PROBE_MARKERS):
                break
            time.sleep(3)
    return CheckResult(
        backend,
        "wait_ready",
        False,
        time.perf_counter() - started,
        {"attempts": attempts},
        last_err,
    )


# --------------------------------------------------------------------------- #
# 建仓 + 只读账号
# --------------------------------------------------------------------------- #
def _create_ro_account(backend: str) -> str:
    """建仓后创建运行时只读账号（两账号分离的部署动作）。返回说明文本。"""
    user, pwd = RO_ACCOUNTS[backend]
    if backend == "mysql":
        import pymysql

        wuser, wpwd = WRITE_ACCOUNTS[backend]
        conn = pymysql.connect(
            host="127.0.0.1",
            port=HOST_PORTS[backend],
            user=wuser,
            password=wpwd,
            database="gmall",
            autocommit=True,
            charset="utf8mb4",
        )
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{pwd}'")
                cur.execute(f"GRANT SELECT ON gmall.* TO '{user}'@'%'")
                cur.execute("FLUSH PRIVILEGES")
        finally:
            conn.close()
        return f"CREATE USER {user}@% + GRANT SELECT ON gmall.*"

    if backend == "postgres":
        import psycopg

        _, wpwd = WRITE_ACCOUNTS[backend]
        conn = psycopg.connect(
            host="127.0.0.1",
            port=HOST_PORTS[backend],
            user="postgres",
            password=wpwd,
            dbname="gmall",
            autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s",
                    (user,),
                )
                if cur.fetchone() is None:
                    cur.execute(f"CREATE ROLE {user} LOGIN PASSWORD '{pwd}'")
                cur.execute(f"GRANT USAGE ON SCHEMA public TO {user}")
                cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {user}")
        finally:
            conn.close()
        return f"CREATE ROLE {user} + GRANT SELECT ON ALL TABLES"

    if backend == "clickhouse":
        return "由 docker/clickhouse/initdb/00-gmall-and-ro-user.sql 在容器启动时创建"

    if backend == "hive":
        return "跳过：单容器 authentication=NONE，无账号体系（部署缺口）"

    return "duckdb 无需账号"


def _ensure_hive_database() -> str:
    """Hive 建仓前必须先有库（CREATE DATABASE 属部署动作）。"""
    from pyhive import hive

    conn = hive.Connection(
        host="127.0.0.1",
        port=HOST_PORTS["hive"],
        username="hive",
        database="default",
        scheme="http",
    )
    try:
        cur = conn.cursor()
        cur.execute("CREATE DATABASE IF NOT EXISTS gmall")
        cur.close()
    finally:
        conn.close()
    return "CREATE DATABASE gmall"


def provision(backend: str) -> CheckResult:
    """建仓 + 建只读账号（幂等重建）。"""
    started = time.perf_counter()
    detail: dict[str, Any] = {}
    try:
        if backend == "duckdb":
            from mock.init_duckdb import main as init_duckdb

            init_duckdb()
            return CheckResult(
                backend, "provision", True, time.perf_counter() - started, {"step": "init_duckdb"}
            )

        if backend == "hive":
            detail["database"] = _ensure_hive_database()

        module = f"mock.init_{backend}"
        proc = _run([sys.executable, "-m", module], env=backend_env(backend, readonly=False))
        detail["init_returncode"] = proc.returncode
        if proc.returncode != 0:
            raise RuntimeError(f"{module} 失败：{(proc.stderr or proc.stdout)[-1500:]}")
        detail["init_tail"] = (proc.stdout or "").strip().splitlines()[-3:]

        detail["ro_account"] = _create_ro_account(backend)
        return CheckResult(backend, "provision", True, time.perf_counter() - started, detail)
    except Exception as exc:
        return CheckResult(
            backend,
            "provision",
            False,
            time.perf_counter() - started,
            detail,
            f"{type(exc).__name__}: {exc}",
        )


# --------------------------------------------------------------------------- #
# L1 连通性
# --------------------------------------------------------------------------- #
def check_connectivity(backend: str) -> CheckResult:
    """连通性：只读建连（内含 harden）+ SELECT 1 + 连接关闭。"""
    started = time.perf_counter()
    detail: dict[str, Any] = {}
    try:
        from exec.engine import get_adapter

        adapter = get_adapter(backend)
        t0 = time.perf_counter()
        conn = adapter.connect_readonly()
        detail["connect_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        try:
            t1 = time.perf_counter()
            cols, rows = adapter.run_query(conn, "SELECT 1 AS ok")
            detail["query_ms"] = round((time.perf_counter() - t1) * 1000, 1)
            detail["columns"] = cols
            detail["rows"] = rows
            if not rows or str(rows[0][0]) not in ("1", "True"):
                raise RuntimeError(f"SELECT 1 返回异常: {rows!r}")
        finally:
            conn.close()
        detail["harden"] = "通过（connect_readonly 内含验证式加固）"
        return CheckResult(backend, "L1_connectivity", True, time.perf_counter() - started, detail)
    except Exception as exc:
        return CheckResult(
            backend,
            "L1_connectivity",
            False,
            time.perf_counter() - started,
            detail,
            f"{type(exc).__name__}: {exc}",
        )


# --------------------------------------------------------------------------- #
# L2 实用性
# --------------------------------------------------------------------------- #
def check_usefulness(backend: str) -> CheckResult:
    """实用性：golden 全量 DSL 双后端一致性 + 物理元数据面冒烟。"""
    started = time.perf_counter()
    detail: dict[str, Any] = {}
    try:
        if backend == "duckdb":
            # DuckDB 是基线本身，无需与自己比对；用 golden oracle 评测替代
            proc = _run([sys.executable, "-m", "eval.eval_runner"], env=backend_env(backend))
            detail["eval_returncode"] = proc.returncode
            detail["eval_tail"] = (proc.stdout or "").strip().splitlines()[-4:]
            if proc.returncode != 0:
                raise RuntimeError(f"golden oracle 评测失败：{(proc.stdout or '')[-1200:]}")
            return CheckResult(
                backend, "L2_usefulness", True, time.perf_counter() - started, detail
            )

        test_file = PARITY_TESTS[backend]
        # 只传 *_TEST_*：parity 用例内部显式 get_adapter("duckdb") + 目标后端
        # adapter，**不能**带 DB_BACKEND——否则测试的 DuckDB 建库 fixture 会
        # 按目标后端的占位符风格灌数（%s 打到 DuckDB → Parser Error）。CI 的
        # 同名 job 也是这个口径。
        #
        # --basetemp 必须显式给：默认落在系统临时目录，pytest 每轮清理上一次的
        # 残留时，文件数超过批量删除保护的阈值（50）就会被拦下，退出码变 1——
        # 而用例全绿，表现为"L2 假失败"。2026-10-27 实测 ClickHouse 一档即如此
        # （62 个文件被拦）。落到 logs/ 下既绕开该阈值，也便于失败时留现场。
        proc = _run(
            [
                sys.executable,
                "-m",
                "pytest",
                test_file,
                "-q",
                f"--basetemp={PROJECT_ROOT / 'logs' / f'.pytest_parity_{backend}'}",
            ],
            env=test_env(backend),
        )
        detail["parity_returncode"] = proc.returncode
        detail["parity_tail"] = (proc.stdout or "").strip().splitlines()[-6:]
        if proc.returncode != 0:
            raise RuntimeError(
                f"parity 失败 rc={proc.returncode}："
                f"{(proc.stdout or '')[-1200:]}\n[stderr] {(proc.stderr or '')[-1200:]}"
            )

        # 物理元数据面：catalog_loader 必须能从真实后端读出列定义
        cat = _run([sys.executable, "-m", "semantic.catalog_loader"], env=backend_env(backend))
        detail["catalog_returncode"] = cat.returncode
        cat_out = cat.stdout or ""
        detail["catalog_has_fact"] = "fact_table=order_detail" in cat_out
        if cat.returncode != 0 or not detail["catalog_has_fact"]:
            raise RuntimeError(f"目录刷新失败：{(cat_out or cat.stderr)[-1500:]}")

        return CheckResult(backend, "L2_usefulness", True, time.perf_counter() - started, detail)
    except Exception as exc:
        return CheckResult(
            backend,
            "L2_usefulness",
            False,
            time.perf_counter() - started,
            detail,
            f"{type(exc).__name__}: {exc}",
        )


# --------------------------------------------------------------------------- #
# L3 调度链路稳定性
# --------------------------------------------------------------------------- #
def _apply_runtime_env(backend: str) -> None:
    """把后端配置落到当前进程（settings 属性 + 环境变量 + 池重建）。

    settings 是模块级常量（import 时读 env），必须显式赋值才能生效；
    池按 DB_BACKEND 惰性绑定 adapter，切换后端必须重建。
    """
    from config import settings
    from exec.pool import reset_default_pool

    # 只改 settings 属性，**不**写 os.environ：父进程一旦把 DB_BACKEND 写进
    # 环境，后续 subprocess 会继承它，pytest 的 DuckDB fixture 就会按目标后端
    # 的占位符风格灌数（实测踩过）。子进程的后端变量由 _run() 显式注入。
    settings.DB_BACKEND = backend
    settings.SQL_DIALECT = ""
    if backend != "duckdb":
        user, pwd = RO_ACCOUNTS[backend]
        port = HOST_PORTS[backend]
        if backend == "mysql":
            settings.MYSQL_HOST, settings.MYSQL_PORT = "127.0.0.1", port
            settings.MYSQL_USER, settings.MYSQL_PASSWORD = user, pwd
            settings.MYSQL_DATABASE = "gmall"
        elif backend == "postgres":
            settings.POSTGRES_HOST, settings.POSTGRES_PORT = "127.0.0.1", port
            settings.POSTGRES_USER, settings.POSTGRES_PASSWORD = user, pwd
            settings.POSTGRES_DATABASE, settings.POSTGRES_SCHEMA = "gmall", "public"
        elif backend == "clickhouse":
            settings.CLICKHOUSE_HOST, settings.CLICKHOUSE_PORT = "127.0.0.1", port
            settings.CLICKHOUSE_USER, settings.CLICKHOUSE_PASSWORD = user, pwd
            settings.CLICKHOUSE_DATABASE = "gmall"
        elif backend == "hive":
            settings.HIVE_HOST, settings.HIVE_PORT = "127.0.0.1", port
            settings.HIVE_USER, settings.HIVE_PASSWORD = user, pwd
            settings.HIVE_DATABASE = "gmall"
            settings.HIVE_STAGING_CLIENT_DIR = str(PROJECT_ROOT / "build" / "hive_staging")
            settings.HIVE_STAGING_SERVER_DIR = "/opt/dataagent_staging"
    reset_default_pool()
    # 扫描行预检缓存按「后端 + 数据源」隔离，但同一后端重灌数据后旧值仍会过时；
    # 切后端/重建库是明确的数据变更点，顺手清掉（2026-10-27 审计 §9.1）。
    from exec.guards import clear_scan_cache

    clear_scan_cache()


def _one_chain_round(question: str) -> tuple[bool, float, str | None]:
    """跑一轮完整调度链路，返回 (是否成功, 耗时秒, 错误摘要)。"""
    from web.service import run_query

    t0 = time.perf_counter()
    try:
        result = run_query(question, principal="admin", request_id=None, session_id=None)
    except Exception as exc:
        return False, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}"

    elapsed = time.perf_counter() - t0
    # 成功口径：拿到 answer 或 steps，且没有 error 字段（degraded 也算成功，
    # 但要在报告里区分——降级说明链路活着、能力受限）
    if result.get("error"):
        return False, elapsed, str(result["error"])[:300]
    if not result.get("steps") and not result.get("answer"):
        return False, elapsed, "空结果：既无 steps 也无 answer"
    return True, elapsed, None


def _force_heuristic() -> tuple[Any, Any]:
    """强制链路走确定性启发式，返回 (恢复函数所需的状态元组)。

    只清 ``settings.LLM_API_KEY`` 不够——``providers/`` 网关的默认分派优先取
    「启用且有 Key」的持久化供应商（``config/providers.json``），与 settings
    里的环境变量回退是两条独立路径。故临时把进程级 provider factory 换成空
    store，并清掉 pipeline / tool_agent / intent_router 的装配缓存。
    """
    from config import settings
    from providers.factory import (
        ProviderFactory,
        invalidate_provider_caches,
        reset_provider_factory,
    )
    from providers.store import ProviderStore

    saved_key = settings.LLM_API_KEY
    settings.LLM_API_KEY = ""
    empty_path = PROJECT_ROOT / "logs" / ".backend_matrix_no_providers.json"
    if empty_path.exists():
        empty_path.unlink()
    reset_provider_factory(ProviderFactory(store=ProviderStore(path=empty_path)))
    invalidate_provider_caches()
    return saved_key, empty_path


def _restore_llm(saved: tuple[Any, Any]) -> None:
    """恢复真实 LLM 装配（L3 结束后调用，避免污染同进程的后续后端）。"""
    from config import settings
    from providers.factory import invalidate_provider_caches, reset_provider_factory

    saved_key, empty_path = saved
    settings.LLM_API_KEY = saved_key
    reset_provider_factory(None)
    invalidate_provider_caches()
    try:
        empty_path.unlink(missing_ok=True)
    except OSError:
        pass


def check_chain_stability(backend: str, rounds: int = 10, concurrency: int = 4) -> CheckResult:
    """调度链路稳定性：串行 N 轮 + 并发 M 线程，检查成功率与池泄漏。"""
    started = time.perf_counter()
    detail: dict[str, Any] = {"rounds": rounds, "concurrency": concurrency}
    try:
        # 强制启发式：有可用供应商时链路会真实调 LLM，延迟与结果都不确定，
        # 稳定性测量失去意义
        saved = _force_heuristic()
        try:
            _apply_runtime_env(backend)

            # 目录/策略刷新是链路前置（等价服务启动时的 ensure_db）
            from web.service import ensure_db

            t_db = time.perf_counter()
            ensure_db()
            detail["ensure_db_s"] = round(time.perf_counter() - t_db, 2)

            from exec.pool import default_pool

            pool = default_pool()
            detail["pool_capacity"] = pool.capacity

            # 串行：覆盖 3 个不同问句轮转（不同 DSL 形态，避免单路径偶然成功）
            serial: list[dict[str, Any]] = []
            for i in range(rounds):
                q = CHAIN_QUESTIONS[i % len(CHAIN_QUESTIONS)]
                ok, elapsed, err = _one_chain_round(q)
                serial.append({"ok": ok, "s": round(elapsed, 3), "err": err, "q": q})
                if err:
                    _log(f"  [{backend}] 串行第 {i + 1} 轮失败: {err[:160]}")

            # 并发：验证池排队与线程安全（链路内部有 _query_gate 信号量 + 池）
            def _task(idx: int) -> tuple[bool, float, str | None]:
                return _one_chain_round(CHAIN_QUESTIONS[idx % len(CHAIN_QUESTIONS)])

            t_conc = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool_exec:
                conc = list(pool_exec.map(_task, range(concurrency * 2)))
            detail["concurrency_wall_s"] = round(time.perf_counter() - t_conc, 2)

            lat = [r["s"] for r in serial if r["ok"]]
            ok_serial = sum(1 for r in serial if r["ok"])
            ok_conc = sum(1 for r in conc if r[0])
            detail.update(
                serial_ok=ok_serial,
                serial_total=rounds,
                conc_ok=ok_conc,
                conc_total=concurrency * 2,
                p50_ms=round(statistics.median(lat) * 1000, 1) if lat else None,
                p95_ms=(
                    round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)] * 1000, 1) if lat else None
                ),
                pool_size_after=pool.size,
                pool_leak=pool.size > pool.capacity,
                failures=[r["err"] for r in serial if r["err"]][:5]
                + [c[2] for c in conc if c[2]][:5],
            )
            if ok_serial < rounds:
                raise RuntimeError(
                    f"串行成功率 {ok_serial}/{rounds}，首个失败: {detail['failures'][0] if detail['failures'] else 'N/A'}"
                )
            if ok_conc < concurrency * 2:
                raise RuntimeError(f"并发成功率 {ok_conc}/{concurrency * 2}")
            if detail["pool_leak"]:
                raise RuntimeError(f"连接池泄漏: size={pool.size} > capacity={pool.capacity}")
            return CheckResult(
                backend, "L3_chain_stability", True, time.perf_counter() - started, detail
            )
        finally:
            _restore_llm(saved)
    except Exception as exc:
        return CheckResult(
            backend,
            "L3_chain_stability",
            False,
            time.perf_counter() - started,
            detail,
            f"{type(exc).__name__}: {exc}",
        )


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def render_markdown(results: list[CheckResult]) -> str:
    """把结果渲染成 Markdown 矩阵（给人看）。"""
    backends = sorted({r.backend for r in results}, key=ALL_BACKENDS.index)
    stages = ["wait_ready", "provision", "L1_connectivity", "L2_usefulness", "L3_chain_stability"]
    by_key = {(r.backend, r.stage): r for r in results}

    lines = ["| 后端 | " + " | ".join(stages) + " |", "|" + "---|" * (len(stages) + 1)]
    for b in backends:
        cells = []
        for s in stages:
            r = by_key.get((b, s))
            if r is None:
                cells.append("—")
            else:
                cells.append(("PASS" if r.ok else "FAIL") + f" ({r.elapsed_s:.1f}s)")
        lines.append(f"| {b} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_report(results: list[CheckResult], out_md: Path) -> None:
    """落 JSON（机读）+ Markdown（人读）双份报告。"""
    payload = [
        {
            "backend": r.backend,
            "stage": r.stage,
            "ok": r.ok,
            "elapsed_s": round(r.elapsed_s, 2),
            "detail": r.detail,
            "error": r.error,
        }
        for r in results
    ]
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# 五后端实测矩阵（自动生成）", "", render_markdown(results), ""]
    failures = [r for r in results if not r.ok]
    if failures:
        md.append("## 失败项")
        md.append("")
        for r in failures:
            md.append(f"- **{r.backend} / {r.stage}**：`{r.error}`")
        md.append("")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md), encoding="utf-8")
    _log(f"\n报告：{out_md}\nJSON：{REPORT_JSON}")


def main() -> int:
    """入口：编排 -> 三档体检 -> 报告；有失败项返回 1。"""
    parser = argparse.ArgumentParser(description="五后端本地实测矩阵")
    parser.add_argument("--up", action="store_true", help="先起 docker 容器")
    parser.add_argument("--down", action="store_true", help="停容器并删卷后退出")
    parser.add_argument(
        "--only",
        action="append",
        choices=ALL_BACKENDS,
        help="只测指定后端（可重复）",
    )
    parser.add_argument("--skip-provision", action="store_true", help="跳过建仓（容器数据已就绪）")
    parser.add_argument("--skip-wait", action="store_true", help="跳过就绪等待")
    parser.add_argument("--chain-rounds", type=int, default=10, help="L3 串行轮次（默认 10）")
    parser.add_argument("--concurrency", type=int, default=4, help="L3 并发线程数（默认 4）")
    parser.add_argument(
        "--report",
        type=Path,
        # 默认落 logs/（gitignore 覆盖）：这是运行产物，人工审计报告单独写，
        # 两者不要互相覆盖
        default=PROJECT_ROOT / "logs" / "backend_matrix.md",
        help="Markdown 矩阵输出路径（默认 logs/backend_matrix.md）",
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.down:
        compose_down()
        return 0

    backends = tuple(args.only) if args.only else ALL_BACKENDS
    if args.up:
        compose_up()

    results: list[CheckResult] = []
    for backend in backends:
        _log(f"\n===== {backend} =====")
        # 先落地本后端配置：adapter 读的是 settings.*（import 时从 .env 固化），
        # 不显式覆盖就会拿 .env 里的默认端口去连，得到"连接被拒"的假阴性。
        _apply_runtime_env(backend)
        if not args.skip_wait:
            r = wait_ready(backend)
            results.append(r)
            _log(
                f"[{backend}] wait_ready: {'PASS' if r.ok else 'FAIL'} {r.elapsed_s:.1f}s {r.error or ''}"
            )
            if not r.ok:
                continue

        if not args.skip_provision:
            r = provision(backend)
            results.append(r)
            _log(
                f"[{backend}] provision: {'PASS' if r.ok else 'FAIL'} {r.elapsed_s:.1f}s {r.error or ''}"
            )
            if not r.ok:
                continue

        r = check_connectivity(backend)
        results.append(r)
        _log(
            f"[{backend}] L1 连通性: {'PASS' if r.ok else 'FAIL'} {r.elapsed_s:.1f}s {r.error or ''}"
        )

        r = check_usefulness(backend)
        results.append(r)
        _log(
            f"[{backend}] L2 实用性: {'PASS' if r.ok else 'FAIL'} {r.elapsed_s:.1f}s {r.error or ''}"
        )

        r = check_chain_stability(backend, rounds=args.chain_rounds, concurrency=args.concurrency)
        results.append(r)
        _log(
            f"[{backend}] L3 链路稳定性: {'PASS' if r.ok else 'FAIL'} {r.elapsed_s:.1f}s {r.error or ''}"
        )

    _log("\n" + render_markdown(results))
    write_report(results, args.report)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
