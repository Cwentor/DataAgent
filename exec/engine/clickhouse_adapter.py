"""ClickHouse 适配器（二十三期 spec D2-D5）：clickhouse-connect HTTP 只读执行面。

超时/扫描预算（spec D3-D5）：由账号 profile 承载（readonly=1 + max_execution_time
+ max_rows_to_read），客户端无法在 readonly=1 会话内改 settings——加固从"改"变
"验"（harden 查 system.settings 与应用预算比对，fail-closed）。

驱动实测（clickhouse-connect 0.7+，计划期 2026-10-26）：
- Client/HttpClient 均无 cancel/kill 方法 → cancel() 空操作，服务端
  max_execution_time 终止查询（与 MySQL/PG 先例同口径，spec D5 退化路径落定）；
- 异常无结构化 code 属性（HTTP 层 raise OperationalError/DatabaseError(err_str)，
  错误码嵌于文本 "code: N"）→ 文本正则提取归一（159 超时 / 131 扫描超限）；
- get_client 的 connect_timeout / send_receive_timeout 经 kwargs 转发 HTTP 客户端
  （客户端读超时 > 服务端超时 +5s：服务端先超时、连接不报废，对齐 MySQL 口径）。

扫描预检（spec D3）：CH 无 EXPLAIN ANALYZE，explain_scan_rows 诚实返回
(0, False)（warning 留痕由护栏框架承接）；真实预算由 profile max_rows_to_read
服务端硬熔断承担——口径为多表读取总和，比既有三后端的各算子最大值更严（fail-safe）。
"""

from __future__ import annotations

import re
from typing import Any

from config import settings
from exec.engine.normalize import normalize_cell
from exec.errors import QueryTimeoutError, SqlExecutionError

# ClickHouse 服务端错误码（异常文本 "code: N" 提取；版本演进由 CI parity 兜底）
CH_TIMEOUT_ERROR_CODE = 159  # TIMEOUT_EXCEEDED（max_execution_time 触发）
CH_SCAN_LIMIT_ERROR_CODE = 131  # LIMIT_EXCEEDED 族（max_rows_to_read 触发）

# 驱动异常基类（模块级变量供测试注入假基类；惰性取真基类见 _ch_error_base）
_CH_ERROR_BASE: type | None = None

_ERROR_CODE_RE = re.compile(r"\bcode:\s*(\d+)")


def _ch_error_base() -> type:
    """惰性取驱动异常基类（未装驱动环境 import 本模块零开销）。"""
    global _CH_ERROR_BASE
    if _CH_ERROR_BASE is None:
        from clickhouse_connect.driver.exceptions import Error

        _CH_ERROR_BASE = Error
    return _CH_ERROR_BASE


def parse_ch_error_code(message: str) -> int | None:
    """从驱动报错文本提取 CH 错误码（无结构化属性的实测约束）；缺失返回 None。"""
    m = _ERROR_CODE_RE.search(message)
    return int(m.group(1)) if m else None


def HARDEN_REQUIRED_SETTINGS() -> dict[str, int]:
    """验证式加固的期望设置（与应用预算同源；单测锁定同源性）。"""
    return {
        "readonly": 1,
        "max_execution_time": settings.QUERY_TIMEOUT_MS // 1000,
        "max_rows_to_read": settings.MAX_SCAN_ROWS,
    }


class ClickHouseAdapter:
    """ClickHouse 查询执行适配器（EngineAdapter 协议实现）。"""

    name = "clickhouse"
    dialect = "clickhouse"

    def __init__(self, dsn: dict[str, Any] | None = None) -> None:
        # dsn 可注入（parity 测试指向独立实例）；缺省读 settings.CLICKHOUSE_*
        self._dsn = dsn or {
            "host": settings.CLICKHOUSE_HOST,
            "port": settings.CLICKHOUSE_PORT,
            "user": settings.CLICKHOUSE_USER,
            "password": settings.CLICKHOUSE_PASSWORD,
            "database": settings.CLICKHOUSE_DATABASE,
        }

    def _new_client(self):
        import clickhouse_connect

        # connect_timeout / send_receive_timeout 经 get_client kwargs 转发 HTTP
        # 客户端（客户端读超时 = 服务端预算 + 5s：服务端先超时、连接不报废）
        return clickhouse_connect.get_client(
            host=self._dsn["host"],
            port=self._dsn["port"],
            username=self._dsn["user"],
            password=self._dsn["password"],
            database=self._dsn["database"],
            connect_timeout=5,
            send_receive_timeout=settings.QUERY_TIMEOUT_MS / 1000.0 + 5.0,
        )

    def connect_readonly(self):
        client = self._new_client()
        try:
            self.harden(client)
        except Exception:
            client.close()
            raise
        return client

    def run_query(self, conn, sql: str) -> tuple[list[str], list[list[Any]]]:
        result = conn.query(sql)
        columns = list(result.column_names)
        rows = [[normalize_cell(v) for v in row] for row in result.result_rows]
        return columns, rows

    def cancel(self, conn) -> None:
        # 驱动实测无 cancel 方法（spec D5 退化路径落定）：看门狗回调空操作，
        # 服务端 max_execution_time 终止查询
        return None

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        if not isinstance(exc, _ch_error_base()):
            return None
        code = parse_ch_error_code(str(exc))
        if code == CH_TIMEOUT_ERROR_CODE:
            return QueryTimeoutError(
                f"查询超时（ClickHouse max_execution_time），已强制取消: {exc}"
            )
        if code == CH_SCAN_LIMIT_ERROR_CODE:
            return SqlExecutionError(f"扫描行数超限（ClickHouse max_rows_to_read 熔断）: {exc}")
        # 文本格式漂移/其他错误：普通执行错误（喂回自愈，严禁误归一）
        return SqlExecutionError(f"{type(exc).__name__}: {exc}")

    def harden(self, conn) -> None:
        """验证式加固（spec D4，fail-closed）：readonly=1 会话内无法 SET，只能验。

        三类失配全拒绝并指名：设置缺失 / readonly<1 / 预算与应用配置不一致
        （后者同时捕获 profile 与 settings 的部署漂移）。
        """
        from security.errors import SecurityError

        try:
            result = conn.query(
                "SELECT name, value FROM system.settings "
                "WHERE name IN ('readonly', 'max_execution_time', 'max_rows_to_read')"
            )
            actual = dict(result.result_rows)
            expected = HARDEN_REQUIRED_SETTINGS()
            missing = sorted(set(expected) - set(actual))
            if missing:
                raise SecurityError(f"ClickHouse 加固校验失败，缺设置项: {missing}")
            for name, want in expected.items():
                got = int(float(actual[name]))
                if name == "readonly":
                    if got < want:
                        raise SecurityError(
                            f"ClickHouse readonly={got} < 1（账号 profile 未启用只读）"
                        )
                elif got != want:
                    raise SecurityError(
                        f"ClickHouse {name}={got} 与应用预算不一致（期望 {want}）——"
                        "部署 profile 漂移，须与 users.d 同步"
                    )
        except SecurityError:
            raise
        except Exception as exc:
            raise SecurityError(f"ClickHouse 连接加固失败: {exc}") from exc

    def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
        """CH 无 EXPLAIN ANALYZE：诚实不可计量（spec D3）；真实预算 profile 承载。"""
        return 0, False

    def insert_dataframe(self, conn, table: str, df) -> int:
        """DataFrame 批量入库（仅 mock 建仓链路），insert_df 走 Arrow 批量通道。

        无列名 DataFrame（_write_rows 元组行构造，columns 为 0..n 整数）经
        DESCRIBE TABLE 取物理列名按位置补齐（与 MySQL/PG 位置模式同语义）；
        空值统一清洗为 None（Nullable 列语义，NaN/NaT/None 全覆盖——MySQL/PG
        先例实锤 pandas 空值直插会报类型错）。
        """
        import pandas as pd

        data = df.copy()
        if all(str(c).isdigit() for c in data.columns):
            desc = conn.query(f"DESCRIBE TABLE {self.quote_ident(table)}")
            data.columns = [row[0] for row in desc.result_rows]
        data = data.astype(object).where(pd.notna(data), None)
        conn.insert_df(table, data)
        return len(data)

    def quote_ident(self, name: str) -> str:
        # adapter 内部 DESCRIBE 等元数据语句的标识符包裹（与方言渲染器同口径）
        return "`" + str(name).replace("`", "``") + "`"


__all__ = [
    "CH_SCAN_LIMIT_ERROR_CODE",
    "CH_TIMEOUT_ERROR_CODE",
    "HARDEN_REQUIRED_SETTINGS",
    "ClickHouseAdapter",
    "parse_ch_error_code",
]
