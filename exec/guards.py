"""SQL 执行层资源治理与自愈支持（P0/P1）。

本模块把"执行 SQL"封装为带资源护栏的受控操作，供 web.service 的完整链路使用：

1. statement_timeout / 查询超时取消
   DuckDB 1.5.x 不提供 max_execution_time 设置，因此在 Python 侧实现看门狗：
   查询在独立工作线程内执行，主线程等待超时阈值；一旦超时调用
   conn.interrupt() 强制取消（DuckDB 抛 InterruptException），翻译为
   QueryTimeoutError。

2. 扫描行数上限（扫描行熔断）
   DuckDB 没有原生的"扫描行数上限"，故采用 EXPLAIN ANALYZE 预检：
   先以分析执行的方式得到每个物理算子实际处理的行数（TABLE_SCAN 的 rows），
   若任一基表扫描超过 max_scan_rows 则直接熔断（MaxRowsScannedExceeded），
   不再执行真实查询。预检在超时看门狗内运行，病态查询同样会被超时拦截。
   注意：EXPLAIN ANALYZE 会实际执行查询，这是"先计量后放行"的成本，
   也是 DuckDB 上实现预防式扫描上限的唯一可靠手段。

   版本敏感：本机制的计量口径完全依赖 DuckDB 计划文本的 "N rows" 渲染格式。
   DuckDB 2.0 起 count/sum 等聚合走元数据快路径，计划被折叠为单算子且
   EXPLAIN ANALYZE 只输出 Summary 块（无任何算子行数），届时本护栏会失效——
   因此 requirements.txt 对 duckdb 锁定主版本上界，且计划不可测量时记 warning
   留痕（plan_rows_measurable），避免"测不到"被静默当成"扫了 0 行"。

3. LIMIT 硬上限熔断（返回行数熔断）
   无论 DSL 的 limit 是多少，执行层对返回行数做独立硬上限 max_result_rows
   （防御性校验，不依赖 schema 约束），超限抛 ResultLimitExceeded。

可恢复的执行错误统一抛 SqlExecutionError（含精确的引擎报错信息），
上层自愈循环可将其喂回 LLM 重写 DSL。所有执行错误都可安全序列化为字符串。
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from audit.logging import get_logger
from config import settings
from exec.engine.base import EngineAdapter
from exec.engine.plan_text import parse_scan_rows, plan_rows_measurable
from exec.errors import (
    MaxRowsScannedExceeded,
    QueryTimeoutError,
    ResultLimitExceeded,
    SqlExecutionError,
    UnsafeSqlError,
)

logger = get_logger("exec.guards")

# --------------------------------------------------------------------------- #
# 异常体系（二十一期迁至 exec.errors；本模块 re-export 保旧 import 路径）
# --------------------------------------------------------------------------- #


# 语句级黑名单：DDL/DML/外部资源语句。
# 除既有 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/PRAGMA 外，补充：
# - COPY/EXPORT/IMPORT：DuckDB 可把任意文件导入导出（COPY ... TO/IMPORT DATABASE），
#   会读写本地磁盘（数据外泄/污染），必须拒绝；
# - INSTALL/LOAD：加载外部扩展（可执行任意 DuckDB 扩展代码）；
# - CALL/DETACH/SET/RESET/VACUUM/CHECKPOINT：语句形态非只读 SELECT，编译器永不生成。
_FORBIDDEN_SQL_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "ATTACH",
    "DETACH",
    "PRAGMA",
    "COPY",
    "EXPORT",
    "IMPORT",
    "INSTALL",
    "LOAD",
    "CALL",
    "SET",
    "RESET",
    "VACUUM",
    "CHECKPOINT",
}

# 函数调用级黑名单：DuckDB 表函数可读取任意本地文件/外部数据源（P0-1 实锤）。
# 匹配"标识符（"的函数调用形态，避免误伤 AS "read_csv" 这类被引号包裹的列别名。
# 家族覆盖（渗透测试实锤 + 已装 DuckDB 函数全量枚举）：
# - read_*/write_*    ：read_csv/read_json/read_parquet/read_ndjson/read_duckdb/read_text/
#                        read_blob/write_csv 等任意外部文件读写；
# - parquet_/sqlite_/postgres_/mysql_/mongo_/delta_/iceberg_/excel_/arrow_/http_/
#   azure_/s3_/gcs_/hdfs_：文件/外部数据源扫描（parquet_scan/parquet_kv_metadata/
#                        parquet_schema/parquet_file_metadata/sqlite_scan/sqlite_query/
#                        postgres_query/pandas_scan/arrow_scan 等）；
# - *_scan/*_query/*_execute/*_glob/*_http：任意外部源扫描与路径枚举；
# - glob/query/query_table/json_execute_serialized_sql/write_log：路径枚举、把字符串
#   当 SQL 执行（可嵌套 read_* 形成二次绕过）、外部写；
# - sniff_csv/st_read：文件内容探测与空间扩展读文件（就绪度评审测试盲区处置：
#   扩展被预装的部署环境下绕过 INSTALL/LOAD 语句级拦截的兜底）。
_FORBIDDEN_FUNCTION_CALL_RE = re.compile(
    r"\b(?:"
    r"(?:read_|write_|parquet_|sqlite_|postgres_|mysql_|mongo_|delta_|iceberg_|excel_|arrow_"
    r"|http_|azure_|s3_|gcs_|hdfs_)[a-z0-9_]*"
    r"|[a-z0-9_]*(?:_scan|_query|_execute|_glob|_http)"
    r"|glob|query|query_table|json_execute_serialized_sql|write_log"
    r"|sniff_csv|st_read"
    r")\s*\(",
    re.IGNORECASE,
)

# 函数名级黑名单（AST 结构化校验使用）：对解析出的函数名做整名匹配，与上面的
# 调用形态正则同源，保证无论函数是 typed（ReadCSV/ParquetScan）还是 Anonymous
# （parquet_kv_metadata 等），都能被同一套家族规则拦截。
_FORBIDDEN_FUNCTION_RE = re.compile(
    r"^(?:"
    r"(?:read_|write_|parquet_|sqlite_|postgres_|mysql_|mongo_|delta_|iceberg_|excel_|arrow_"
    r"|http_|azure_|s3_|gcs_|hdfs_)[a-z0-9_]*"
    r"|[a-z0-9_]*(?:_scan|_query|_execute|_glob|_http)"
    r"|glob|query|query_table|json_execute_serialized_sql|write_log"
    r"|sniff_csv|st_read"
    r")$",
    re.IGNORECASE,
)

# DuckDB 字符串字面量表引用（FROM '<path>'）：等价直接读取本地文件，拒绝。
_FROM_STRING_TABLE_RE = re.compile(r"""\bFROM\s*['"]""", re.IGNORECASE)

# --------------------------------------------------------------------------- #
# 方言增量黑名单（二十一期 spec D11）：通用黑名单之外按引擎追加
# --------------------------------------------------------------------------- #
# MySQL 语句族：USE 切库 / SHOW 元数据 / KILL 连接 / PREPARE 家族二次注入 /
# LOAD 数据导入 / 事务与锁语句族 / 管理语句（FLUSH/OPTIMIZE/RENAME/TRUNCATE/
# GRANT/REVOKE/HANDLER/DO/SHUTDOWN/PURGE/HELP）；OUTFILE/DUMPFILE 为
# SELECT ... INTO OUTFILE 写服务器文件的目标子句（AST Into 节点另有拦截）
_DIALECT_EXTRA_KEYWORDS: dict[str, frozenset[str]] = {
    "mysql": frozenset(
        {
            "USE",
            "SHOW",
            "KILL",
            "PREPARE",
            "EXECUTE",
            "DEALLOCATE",
            "LOAD",
            "START",
            "XA",
            "FLUSH",
            "ANALYZE",
            "OPTIMIZE",
            "RENAME",
            "TRUNCATE",
            "GRANT",
            "REVOKE",
            "HANDLER",
            "DO",
            "SHUTDOWN",
            "PURGE",
            "HELP",
            "OUTFILE",
            "DUMPFILE",
        }
    ),
}
# MySQL 函数族：load_file 读服务器文件 / sleep、benchmark DoS / 锁滥用族
_DIALECT_EXTRA_FUNC_RE: dict[str, re.Pattern[str]] = {
    "mysql": re.compile(
        r"\b(?:load_file|sleep|benchmark|get_lock|release_lock|is_used_lock|is_free_lock)\s*\(",
        re.IGNORECASE,
    ),
}


def _resolve_dialect(dialect: str | None) -> str:
    """解析执行方言：显式传入优先，缺省取当前后端 adapter 的方言。"""
    if dialect is not None:
        return dialect
    from exec.engine import get_adapter  # 惰性导入：engine 顶层不反向依赖 guards

    return get_adapter().dialect


def _strip_sql_comments(sql: str) -> str:
    """移除 SQL 注释，同时保留字符串内容，避免误判字面量。"""
    out: list[str] = []
    i = 0
    while i < len(sql):
        if sql.startswith("--", i):
            i += 2
            while i < len(sql) and sql[i] != "\n":
                i += 1
        elif sql.startswith("/*", i):
            i += 2
            while i < len(sql) and not sql.startswith("*/", i):
                i += 1
            i = min(i + 2, len(sql))
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def _sql_code_without_literals(sql: str) -> str:
    """将单引号字符串替换为空，供语句结构检查使用。"""
    out: list[str] = []
    i = 0
    while i < len(sql):
        if sql[i] == "'":
            i += 1
            while i < len(sql):
                if sql[i] == "'":
                    if i + 1 < len(sql) and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def _func_name(node: exp.Func) -> str | None:
    """从 sqlglot 函数节点提取规范化函数名（小写）。

    typed 函数（ReadCSV/ParquetScan 等）用 sql_name()；未知函数在 sqlglot 里是
    Anonymous(this=Var)，真实函数名在 this.name。别名/引号包裹不影响提取。
    """
    if isinstance(node, exp.Anonymous):
        name = getattr(node.this, "name", None)
        return name.lower() if isinstance(name, str) else None
    name = node.sql_name()
    return name.lower() if name else None


def _assert_read_only_structure(sql: str, dialect: str = "duckdb") -> None:
    """sqlglot AST 结构化只读校验（P0-1 主防线，取代纯正则黑名单）。

    五个条件（全部 fail-closed）：
    1. 恰好解析为一条语句；
    2. 根节点必须是 SELECT / UNION（WITH 是 Select 的属性，自然覆盖）；
    3. 全树不允许出现被禁函数（read_*/parquet_*/sqlite_*/*_scan/*_query 等家族）；
    4. 全树不允许引号/字符串字面量表引用（FROM '<path>' / FROM "path"）与
       SELECT INTO 写表语句；
    5. MySQL 方言额外拒绝锁语句节点（FOR UPDATE / LOCK IN SHARE MODE——
       读锁同样占用锁资源且形态非纯只读）。
    """
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as exc:
        raise UnsafeSqlError(f"SQL 解析失败，拒绝执行: {exc}") from exc
    if len(statements) != 1:
        raise UnsafeSqlError("拒绝执行多语句 SQL")
    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union)):
        raise UnsafeSqlError(f"仅允许执行 SELECT 只读查询（实际为 {type(root).__name__}）")
    for node in root.walk():
        if isinstance(node, exp.Into):
            raise UnsafeSqlError("拒绝 SELECT INTO 写表语句")
        if dialect == "mysql" and isinstance(node, exp.Lock):
            raise UnsafeSqlError("拒绝 SELECT ... FOR UPDATE / LOCK IN SHARE MODE 锁语句")
        if isinstance(node, exp.Func):
            name = _func_name(node)
            if name and _FORBIDDEN_FUNCTION_RE.fullmatch(name):
                raise UnsafeSqlError(
                    f"检测到被禁止的表函数调用（{name} 可读取外部文件/数据源，已拒绝）"
                )
        elif isinstance(node, exp.Table):
            this = node.this
            if isinstance(this, exp.Identifier) and this.quoted:
                raise UnsafeSqlError(f"拒绝字符串/引号字面量表引用: {node.sql()}")


def assert_read_only_sql(sql: str, dialect: str | None = None) -> None:
    """硬断言 SQL 是单条只读 SELECT/WITH（P0-1 加固；方言感知，spec D11）。

    参数 dialect 缺省取当前后端 adapter 的方言（测试可显式指定）。

    四层防线（全部 fail-closed）：
    1. 语句形态：仅 SELECT/WITH 开头、无分号堆叠；
    2. 语句级黑名单：DDL/DML/COPY/EXPORT/IMPORT/INSTALL/LOAD 等外部资源语句
       （叠加方言增量：MySQL 的 USE/SHOW/KILL/PREPARE/OUTFILE 等）；
    3. 函数调用级黑名单：read_* / *_scan / *_query / parquet_* / glob / query 等
       可读取任意本地文件或外部数据源的表函数（渗透测试已实锤 read_csv 可外泄文件），
       以及 FROM '<path>' 字符串字面量表引用（DuckDB 等价直接读文件）；
       MySQL 增量 load_file/sleep/benchmark/锁族；
    4. sqlglot AST 结构性校验（按方言解析）：根节点必须为 SELECT，全树拒绝
       被禁函数家族、引号字符串表引用、SELECT INTO 与（MySQL）锁语句
       （取代纯正则黑名单，杜绝引号/别名/嵌套绕过）。
    """
    dialect = _resolve_dialect(dialect)
    code = _sql_code_without_literals(_strip_sql_comments(sql))
    tokens = code.strip().split()
    if not tokens or tokens[0].upper() not in {"SELECT", "WITH"}:
        raise UnsafeSqlError("仅允许执行 SELECT/WITH 只读查询")
    if ";" in code:
        raise UnsafeSqlError("拒绝执行多语句 SQL")
    upper = code.upper()
    for keyword in _FORBIDDEN_SQL_KEYWORDS | _DIALECT_EXTRA_KEYWORDS.get(dialect, frozenset()):
        if re.search(rf"\b{keyword}\b", upper):
            raise UnsafeSqlError(f"检测到被禁止的 SQL 关键字: {keyword}")
    extra_func = _DIALECT_EXTRA_FUNC_RE.get(dialect)
    if _FORBIDDEN_FUNCTION_CALL_RE.search(code) or (
        extra_func is not None and extra_func.search(code)
    ):
        raise UnsafeSqlError(
            "检测到被禁止的表函数调用（read_*/ *_scan/glob/query 可读取外部文件，已拒绝）"
        )
    if _FROM_STRING_TABLE_RE.search(_strip_sql_comments(sql)):
        raise UnsafeSqlError("拒绝字符串字面量表引用（FROM '<path>' 等价直接读取本地文件）")
    _assert_read_only_structure(sql, dialect=dialect)


# --------------------------------------------------------------------------- #
# 执行结果
# --------------------------------------------------------------------------- #


@dataclass
class ExecutionResult:
    """一次受控执行的完整结果。"""

    columns: list[str]
    rows: list[list[Any]]
    scan_rows: int = 0
    duration_ms: float = 0.0
    # 执行前审计的 WARNING 级发现（REJECTED 级在执行前已熔断抛 GuardrailRejected）
    findings: list[dict[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# DuckDB 计划文本解析（二十一期迁至 exec.engine.plan_text；本模块 re-export）
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 扫描行预算缓存（整改指令3-3：同一 SQL 自愈重试不重复 EXPLAIN 预检降本）
# --------------------------------------------------------------------------- #
_SCAN_CACHE: dict[str, int] = {}
_SCAN_CACHE_LOCK = threading.Lock()


def _scan_cache_key(sql: str) -> str:
    """SQL 规范化哈希（空白折叠后 sha256），同一编译产物只预检一次。"""
    normalized = " ".join(sql.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def cached_scan_rows(sql: str, cache: dict[str, int] | None = None) -> int | None:
    """读取 SQL 的已缓存扫描行数；未命中返回 None。"""
    store = cache if cache is not None else _SCAN_CACHE
    key = _scan_cache_key(sql)
    lock = None if cache is not None else _SCAN_CACHE_LOCK
    if lock is not None:
        with lock:
            return store.get(key)
    return store.get(key)


def cache_scan_rows(sql: str, scan_rows: int, cache: dict[str, int] | None = None) -> None:
    """写入 SQL 的扫描行数缓存（容量上限读 settings.MAX_SCAN_CACHE_SIZE，超限清空防膨胀）。"""
    store = cache if cache is not None else _SCAN_CACHE
    key = _scan_cache_key(sql)
    lock = None if cache is not None else _SCAN_CACHE_LOCK
    max_size = settings.MAX_SCAN_CACHE_SIZE
    if lock is not None:
        with lock:
            store[key] = scan_rows
            if len(store) > max_size:
                store.clear()
        return
    store[key] = scan_rows
    if len(store) > max_size:
        store.clear()


# --------------------------------------------------------------------------- #
# 超时看门狗（二十一期下沉至 exec.engine.watchdog，线程框架引擎无关；
# 取消手段由 adapter.cancel 承载——DuckDB interrupt / MySQL 服务端自限）
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 受控执行入口
# --------------------------------------------------------------------------- #


def execute_sql(
    conn: Any,
    sql: str,
    *,
    statement_timeout_ms: int | None = None,
    max_scan_rows: int | None = None,
    max_result_rows: int | None = None,
    adapter: EngineAdapter | None = None,
) -> ExecutionResult:
    """带资源护栏执行一条只读 SELECT（引擎无关，差异由 adapter 承载）。

    参数（None 表示不启用该护栏）：
    - statement_timeout_ms：语句超时（毫秒），超时中断并抛 QueryTimeoutError；
    - max_scan_rows：扫描行数上限，预检超过即抛 MaxRowsScannedExceeded；
    - max_result_rows：返回行数硬上限，超过即抛 ResultLimitExceeded；
    - adapter：执行引擎适配器；None 时按 settings.DB_BACKEND 现取。

    执行前审计（GuardrailAgent）：只读断言后追加笛卡尔积 / 无界输出静态审计，
    REJECTED 级发现抛 GuardrailRejected（不执行），WARNING 级随结果输出。
    """
    from exec.engine.watchdog import run_with_timeout  # 惰性导入防循环（同 audit 先例）

    if adapter is None:
        from exec.engine import get_adapter  # 惰性导入：engine 顶层不反向依赖 guards

        adapter = get_adapter()
    try:
        assert_read_only_sql(sql, dialect=adapter.dialect)
    except UnsafeSqlError:
        # 高危拦截（P0 防线）：必须留痕供安全审计，不改变异常传播
        logger.exception("只读白名单拦截非只读/高危 SQL")
        raise
    # 延迟导入：audit.py 依赖本模块异常体系（GuardrailRejected 继承 SqlExecutionError），
    # 顶层互导会形成循环；依赖方向固定为 audit -> guards
    from exec.audit import GuardrailRejected, assert_guardrails

    try:
        guard_warnings = [f.to_dict() for f in assert_guardrails(sql, dialect=adapter.dialect)]
    except GuardrailRejected as exc:
        logger.error("执行前审计拒绝（笛卡尔积/只读结构违规）", extra={"error": str(exc)[:500]})
        raise
    started = time.perf_counter()
    scan_rows = 0

    # 1) 扫描行预算预检（EXPLAIN ANALYZE 在超时看门狗内运行；解析口径由 adapter 决定）
    #    整改指令3-3：同一 SQL（自愈重试同编译产物）命中缓存则跳过重复预检降本
    if max_scan_rows is not None:
        scanned = cached_scan_rows(sql)
        if scanned is None:
            try:
                scanned, measurable = adapter.explain_scan_rows(conn, sql, statement_timeout_ms)
            except QueryTimeoutError:
                raise
            except Exception as exc:
                # 预检阶段暴露的引擎错误同样视为执行失败（可自愈）
                norm = adapter.engine_error(exc)
                if norm is not None:
                    raise norm from exc
                raise
            if measurable:
                cache_scan_rows(sql, scanned)
            else:
                # 计划不含任何算子行数：护栏本轮不可计量（引擎版本/计划格式变更）。
                # 不缓存——避免把"测不到"固化成"扫了 0 行"的假事实，也不静默放行。
                logger.warning(
                    "扫描行数预检不可计量：执行计划未包含算子行数，本轮跳过扫描行熔断",
                    extra={
                        "error": (
                            f"plan 无 'N rows' 渲染，请核验 {adapter.name} 版本与计划格式"
                            "（duckdb 侧 requirements.txt 锁定 <2.0）"
                        )
                    },
                )
        scan_rows = scanned
        if scanned > max_scan_rows:
            logger.error(
                "扫描行数上限熔断",
                extra={"error": f"scanned={scanned}, limit={max_scan_rows}"},
            )
            raise MaxRowsScannedExceeded(
                f"扫描行数上限熔断：本次查询扫描 {scanned} 行，超过上限 {max_scan_rows}"
            )

    # 2) 真实执行（同样受超时护栏保护）；引擎报错统一归一为 SqlExecutionError（可自愈）
    try:
        columns, rows = run_with_timeout(adapter, conn, sql, statement_timeout_ms)
    except QueryTimeoutError:
        raise
    except Exception as exc:
        norm = adapter.engine_error(exc)
        if norm is None:
            raise
        logger.warning(
            "SQL 执行引擎报错（可自愈）",
            extra={"error": str(norm)[:500]},
        )
        raise norm from exc

    # 3) 返回行数硬上限
    if max_result_rows is not None and len(rows) > max_result_rows:
        logger.error(
            "LIMIT 硬上限熔断",
            extra={"error": f"rows={len(rows)}, limit={max_result_rows}"},
        )
        raise ResultLimitExceeded(
            f"LIMIT 硬上限熔断：返回 {len(rows)} 行，超过上限 {max_result_rows}"
        )

    duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return ExecutionResult(
        columns=columns,
        rows=rows,
        scan_rows=scan_rows,
        duration_ms=duration_ms,
        findings=guard_warnings,
    )


__all__ = [
    "ExecutionResult",
    "MaxRowsScannedExceeded",
    "QueryTimeoutError",
    "ResultLimitExceeded",
    "SqlExecutionError",
    "UnsafeSqlError",
    "assert_read_only_sql",
    "execute_sql",
    "parse_scan_rows",
    "plan_rows_measurable",
]
