"""Hive 适配器（二十五期 spec D2-D5/D10/D11）：PyHive HTTP transport 只读执行面。

超时/取消/熔断（spec D3/D5）：HiveServer2 无会话级只读与预算设置面、PyHive
同步执行模型（runAsync=False 阻塞在 ExecuteStatement 上）无可靠取消通道——
- 超时 = 客户端读超时承载：THttpClient.setTimeout(应用预算 + 5s)，口径对齐
  MySQL/CH 先例（服务端先超时、连接不报废；Hive 无服务端 per-query 超时面，
  部署建议配置 hive.server2.query.timeout，见 spec §9）；
- cancel() 空操作（看门狗回调），与 MySQL/PG/CH 服务端自限型同口径；
- 扫描预检诚实不可计量：Hive EXPLAIN 无 actual rows，explain_scan_rows 常量
  返回 (0, False)，warning 留痕由护栏框架承接，真实预算 = LIMIT 硬上限 +
  客户端超时 + 部署侧配额兜底。

驱动实测（PyHive 0.7.0 + thrift 0.25.0，计划期 2026-10-27）：
- HTTP 模式经 ``hive.connect(scheme="http")`` 走 THttpClient（URI 固定
  ``/cliservice/``）；读超时须在连接后经 ``conn._transport.setTimeout(ms)``
  设置（PyHive 0.7 内部形态，单测锁定；换版本须重验）；
- 异常体系 pyhive.exc（Error/OperationalError/ProgrammingError/DataError），
  无结构化错误码（TStatus 文本），归一走文本路线（同 CH）；
- 客户端读超时冒泡为 socket.timeout（py3.12 下即 TimeoutError）。

建仓灌数（spec D10）：INSERT VALUES 每条语句一个 job（不可行），走 CSV
staging + LOAD DATA LOCAL INPATH——**LOCAL 语义是 hiveserver2 进程本地路径**，
CSV 先写 client staging 目录，再从 server staging 目录加载（CI/docker 部署中
两目录经卷映射为同一内容）。分隔符用 \\001（LazySimpleSerDe 原生默认）规避
文本值逗号/引号歧义（plan RF-1）；null 标记两字符 ``\\N`` 与 DDL 的
``NULL DEFINED AS '\\N'``（SQL 字面量双反斜杠）对应——单反斜杠会被 Hive
字面量解析吃掉落库成 'N'（spec §0 M4 实测坑）。

物理元数据（spec D11）：逐表 ``DESCRIBE`` 解析（col_name/data_type 两列），
遇分区段 fail-closed 报错（数仓单一来源无分区，出现即 schema 漂移）；Hive
DDL 语法无绑定参数，库名经反引号包裹 + 字符白名单校验（配置面非用户输入）。
"""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from config import settings
from exec.engine.base import physical_from_rows
from exec.engine.normalize import normalize_cell
from exec.errors import QueryTimeoutError, SqlExecutionError

# 客户端读超时 = 应用预算 + 5s（服务端先超时口径，spec D2）
HIVE_TIMEOUT_EXTRA_SECONDS = 5.0

# CSV staging 形态（spec D10）：分隔符 \001 为 LazySimpleSerDe 原生默认，
# 规避文本值逗号/引号歧义；null 标记为两字符 \N（Python 字面量 "\\N"）
CSV_FIELD_SEP = "\x01"
CSV_NULL_MARK = "\\N"

# 库名/表名字符白名单（DESCRIBE/SHOW 语句无绑定参数，值经反引号包裹后仍做
# fail-closed 校验——配置面注入错误尽早暴露）
_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_]+$")

# 驱动异常基类（模块级变量供测试注入假基类；惰性取真基类见 _hive_error_base）
_HIVE_ERROR_BASE: type | None = None


def _hive_error_base() -> type:
    """惰性取驱动异常基类（未装驱动环境 import 本模块零开销）。"""
    global _HIVE_ERROR_BASE
    if _HIVE_ERROR_BASE is None:
        from pyhive.exc import Error

        _HIVE_ERROR_BASE = Error
    return _HIVE_ERROR_BASE


def cell_to_csv_text(value: Any) -> str:
    """单元格 → CSV 文本（None 写 null 标记；字符串值含分隔符/换行 fail-closed）。"""
    if value is None:
        return CSV_NULL_MARK
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        if CSV_FIELD_SEP in value or "\r" in value or "\n" in value:
            raise ValueError(
                f"字符串值含 staging 分隔符或换行，拒绝写入（数据漂移面）: {value[:50]!r}"
            )
        return value
    return str(value)


def parse_describe_rows(rows: list[tuple]) -> list[tuple[str, str]]:
    """DESCRIBE 输出 → [(列名, 原始类型)]（spec D11 解析器，单测锁定 M5 形态）。

    跳过空行与 `#` 段头；`# Partition Information` 段即 fail-closed 报错
    （数仓单一来源无分区表）。
    """
    out: list[tuple[str, str]] = []
    in_partition = False
    for row in rows:
        name = str(row[0] or "")
        if name.startswith("# Partition Information"):
            in_partition = True
            continue
        if name.startswith("#"):
            continue
        if not name.strip():
            continue
        if in_partition:
            raise ValueError("DESCRIBE 输出出现分区列段（数仓单一来源无分区，schema 漂移）")
        out.append((name.strip(), str(row[1] or "").strip()))
    return out


def hive_cell(value: Any, thrift_type: str) -> Any:
    """按列 thrift 类型补驱动缺失的转换（PyHive 0.7 TYPES_CONVERTER 实测缺口）。

    PyHive 仅转换 DECIMAL_TYPE（Decimal）与 TIMESTAMP_TYPE（datetime）；
    **DATE_TYPE 原样返回字符串**——与全链路"日期列返回 date/datetime 对象"
    口径不符（parity norm_cell datetime→date 面），此处定向补 date 转换；
    解析失败保持原值由下游诚实报错。
    """
    if value is None or thrift_type != "DATE_TYPE" or not isinstance(value, str):
        return value
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return value


class HiveAdapter:
    """Hive 查询执行适配器（EngineAdapter 协议实现）。"""

    name = "hive"
    dialect = "hive"

    def __init__(
        self,
        dsn: dict[str, Any] | None = None,
        staging_client: str | None = None,
        staging_server: str | None = None,
    ) -> None:
        # dsn 可注入（parity 测试指向独立实例）；缺省读 settings.HIVE_*
        self._dsn = dsn or {
            "host": settings.HIVE_HOST,
            "port": settings.HIVE_PORT,
            "user": settings.HIVE_USER,
            "password": settings.HIVE_PASSWORD,
            "database": settings.HIVE_DATABASE,
        }
        # staging 双路径（spec D10）：client=本机写 CSV；server=hiveserver2 进程
        # 可见路径（LOAD DATA LOCAL INPATH 的 LOCAL 语义）
        self._staging_client = staging_client or settings.HIVE_STAGING_CLIENT_DIR
        self._staging_server = staging_server or settings.HIVE_STAGING_SERVER_DIR

    def _new_connection(self):
        from pyhive import hive

        password = self._dsn["password"] or None
        conn = hive.connect(
            host=self._dsn["host"],
            port=self._dsn["port"],
            username=self._dsn["user"],
            password=password,
            database=self._dsn["database"],
            scheme="http",  # spec D2：HTTP transport（THttpClient，/cliservice/）
        )
        # 读超时经 THttpClient.setTimeout 设置（PyHive 0.7 内部 _transport 形态；
        # 换驱动版本须重验，spec §0 M10）
        timeout_ms = (settings.QUERY_TIMEOUT_MS / 1000.0 + HIVE_TIMEOUT_EXTRA_SECONDS) * 1000
        conn._transport.setTimeout(timeout_ms)
        return conn

    def connect_readonly(self):
        conn = self._new_connection()
        try:
            self.harden(conn)
        except Exception:
            conn.close()
            raise
        return conn

    def run_query(self, conn, sql: str) -> tuple[list[str], list[list[Any]]]:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            columns = [str(d[0]) for d in cur.description]
            types = [str(d[1]) for d in cur.description]
            rows = [
                [normalize_cell(hive_cell(v, t)) for v, t in zip(row, types, strict=False)]
                for row in cur.fetchall()
            ]
        finally:
            cur.close()
        return columns, rows

    def physical_columns(self, conn=None) -> dict[str, dict[str, str]]:
        """Hive 物理列（spec D11）：SHOW TABLES + 逐表 DESCRIBE 解析。

        Hive 无 information_schema/system.columns（INFORMATION_SCHEMA 依赖
        metastore SQL 授权配置且 3.x 无，spec §4 S9）；元数据源 = 逐表
        DESCRIBE（Hive 3/4 通用、零额外权限）。dtype 经 physical_from_rows →
        map_dtype 归一（未知类型 fail-closed）。
        """
        own = None
        if conn is None:
            own = self.connect_readonly()
            conn = own
        try:
            cur = conn.cursor()
            try:
                cur.execute(f"SHOW TABLES IN {self._qual_name(self._dsn['database'])}")
                tables = [str(r[0]) for r in cur.fetchall()]
                pairs: list[tuple[str, str, str]] = []
                for table in tables:
                    cur.execute(
                        f"DESCRIBE {self._qual_name(self._dsn['database'])}."
                        f"{self._qual_name(table)}"
                    )
                    for col, raw_type in parse_describe_rows(cur.fetchall()):
                        pairs.append((table, col, raw_type))
            finally:
                cur.close()
        finally:
            if own is not None:
                own.close()
        return physical_from_rows(pairs)

    def cancel(self, conn) -> None:
        # 同步执行模型（runAsync=False 阻塞在 ExecuteStatement）无可靠取消
        # 通道（spec D5）：看门狗回调空操作，客户端读超时 + 部署侧查询超时兜底
        return None

    def engine_error(self, exc: BaseException) -> SqlExecutionError | None:
        # 客户端读超时（THttpClient socket 冒泡，py3.12 即 TimeoutError）
        if isinstance(exc, TimeoutError):
            return QueryTimeoutError(f"查询超时（Hive 客户端读超时）: {exc}")
        if isinstance(exc, _hive_error_base()):
            text = str(exc)
            # TStatus 无结构化错误码，文本路线归一（同 CH；服务端查询超时
            # 文本随部署配置存在，识别宽口径 "timeout"/"timed out"）
            if "timeout" in text.lower() or "timed out" in text.lower():
                return QueryTimeoutError(f"查询超时（Hive 服务端）: {text}")
            return SqlExecutionError(f"{type(exc).__name__}: {text}")
        return None

    def harden(self, conn) -> None:
        """验证式加固（spec D4，fail-closed）：Hive 无会话级加固设置面。

        可验证面 = 身份回读：``current_user()`` 必须与 dsn 用户一致——拦截
        "用户名未生效" 的部署漂移。账号防线（只读）完全由部署侧授权承担
        （SQL 标准授权 / Ranger，spec §9），此处为纵深防御最后一道回读校验。
        """
        from security.errors import SecurityError

        try:
            cur = conn.cursor()
            try:
                cur.execute("SELECT current_user()")
                rows = cur.fetchall()
            finally:
                cur.close()
        except SecurityError:
            raise
        except Exception as exc:
            raise SecurityError(f"Hive 连接加固失败: {exc}") from exc
        got = str(rows[0][0]) if rows else ""
        want = str(self._dsn["user"])
        if got != want:
            raise SecurityError(
                f"Hive 身份回读不一致: current_user={got!r} 与配置用户 {want!r} 不符"
                "（认证/会话配置漂移，fail-closed）"
            )

    def explain_scan_rows(self, conn, sql: str, timeout_ms: int | None) -> tuple[int, bool]:
        """Hive EXPLAIN 无 actual rows：诚实不可计量（spec D3）；预算部署侧承载。"""
        return 0, False

    def insert_dataframe(self, conn, table: str, df) -> int:
        """DataFrame 批量入库（仅 mock 建仓链路）：CSV staging + LOAD DATA。

        流程（spec D10）：空值清洗统一 None → 逐列转 CSV 文本（cell_to_csv_text）
        → 写 client staging 目录（每表一文件，覆盖写）→ LOAD DATA LOCAL INPATH
        （server staging 路径，hiveserver2 进程本地语义）。每表一条 LOAD 语句；
        幂等性由 init 脚本 DROP 重建保证（INTO TABLE 追加语义不依赖）。

        无列名 DataFrame（_write_rows 元组行构造）按 DESCRIBE 物理列顺序补名
        （与 MySQL/PG/CH 位置模式同语义）。
        """
        import pandas as pd

        data = df.copy()
        if all(str(c).isdigit() for c in data.columns):
            cur = conn.cursor()
            try:
                cur.execute(f"DESCRIBE {self._qual_name(table)}")
                data.columns = [name for name, _ in parse_describe_rows(cur.fetchall())]
            finally:
                cur.close()
        data = data.astype(object).where(pd.notna(data), None)
        lines = [
            CSV_FIELD_SEP.join(cell_to_csv_text(v) for v in row)
            for row in data.itertuples(index=False, name=None)
        ]
        client_dir = Path(self._staging_client)
        client_dir.mkdir(parents=True, exist_ok=True)
        # 文件名统一加 data_ 前缀：Hadoop 把 `_` 开头文件视为隐藏文件，Hive
        # LOAD 直接跳过（_field_metadata 实锤 No files matching path）
        csv_path = client_dir / f"data_{self._safe_name(table)}.csv"
        # 空表也写空文件（LOAD 空文件 = 0 行，行数对照由 init 打印承担）
        csv_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        load = (
            f"LOAD DATA LOCAL INPATH '{self._load_path()}/data_{self._safe_name(table)}.csv' "
            f"INTO TABLE {self._qual_name(table)}"
        )
        cur = conn.cursor()
        try:
            # staging 写传播重试：Windows bind mount（Docker Desktop 文件共享）
            # 的写传播有秒级延迟，写后立即 LOAD 偶发 "No files matching path"
            # （本机建仓实测；Linux CI 原生挂载无此问题）。仅对路径不可见重试，
            # 其他错误原样抛出
            for attempt in range(3):
                try:
                    cur.execute(load)
                    break
                except Exception as exc:
                    if attempt == 2 or "No files matching path" not in str(exc):
                        raise
                    time.sleep(1.0)
        finally:
            cur.close()
        return len(data)

    def quote_ident(self, name: str) -> str:
        # adapter 内部 DESCRIBE/SHOW 等元数据语句的标识符包裹（与方言渲染器同口径）
        return "`" + str(name).replace("`", "``") + "`"

    def _qual_name(self, name: str) -> str:
        """反引号包裹 + 字符白名单双重校验（SHOW/DESCRIBE 无绑定参数的替代面）。"""
        self._safe_name(name)
        return self.quote_ident(name)

    def _safe_name(self, name: str) -> str:
        if not _IDENTIFIER_RE.match(str(name)):
            raise ValueError(f"Hive 标识符含非法字符（fail-closed）: {name!r}")
        return str(name)

    def _load_path(self) -> str:
        path = str(self._staging_server).rstrip("/")
        if "'" in path:
            raise ValueError(f"staging server 路径含单引号（fail-closed）: {path!r}")
        return path


__all__ = [
    "CSV_FIELD_SEP",
    "CSV_NULL_MARK",
    "HIVE_TIMEOUT_EXTRA_SECONDS",
    "HiveAdapter",
    "cell_to_csv_text",
    "hive_cell",
    "parse_describe_rows",
]
