"""验证：2023 年销售量 Top10 商品（DSL -> 编译 -> 治理执行 确定性管道）。"""

from __future__ import annotations

from datetime import date

from compiler.sql_compiler import compile_sql
from config import settings
from exec.pool import ReadOnlyConnectionPool
from semantic.dsl_schema import (
    AbsoluteTime,
    AggregateMetric,
    Dimension,
    Filter,
    FilterOperator,
    OrderBy,
    QueryDSL,
    SortDirection,
    TimeFilter,
    TimeRangeType,
)

# 1) 构造合法 DSL（契约字段全部在语义目录登记）
dsl = QueryDSL(
    metrics=[AggregateMetric(field="sku_num", agg="sum", alias="sales_volume")],
    dimensions=[Dimension(field="sku_name")],
    time_filter=TimeFilter(
        range_type=TimeRangeType.ABSOLUTE,
        absolute=AbsoluteTime(start=date(2023, 1, 1), end=date(2024, 1, 1)),
        time_field="order_time",
    ),
    filters=[Filter(field="order_status", operator=FilterOperator.EQ, value="1002")],
    order_by=[OrderBy(field="sales_volume", direction=SortDirection.DESC)],
    limit=10,
)

# 2) 确定性编译
sql = compile_sql(dsl)
print("=== compiled sql ===")
print(sql)

# 3) 治理执行：只读连接池 + LIMIT 硬上限（P0 资源治理）
pool = ReadOnlyConnectionPool(settings.DB_PATH, max_connections=2)
conn = pool.acquire()
try:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
finally:
    pool.release(conn)
    pool.close()

print("=== columns ===")
print(cols)
print("=== top10 ===")
for r in rows:
    print(r)
