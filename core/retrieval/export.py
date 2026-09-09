"""结果集 -> Parquet 物化导出（沙箱数据交换的上游半程）。

职责：
1. 把受控查询结果（columns + rows）物化为 Parquet 文件，落盘到会话工作区
   ``inputs/`` 目录（沙箱唯一可读的输入位置）；
2. 导出前强制 PII 脱敏（pii.mask_result_set）；
3. 行数上限截断（防"海量原始数据进沙箱"的数据外泄面）；
4. 落盘后读回校验（行数/列数/哈希），产出 ParquetRef。

依赖 pandas + pyarrow（开发依赖栈）；生产检索链路不经过本模块。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from core.retrieval.parquet_ref import ParquetRef
from core.retrieval.pii import mask_result_set

# 默认导出行数上限：沙箱内做聚合足够，杜绝整表搬运
DEFAULT_MAX_EXPORT_ROWS = 50_000


def export_to_parquet(
    columns: list[str],
    rows: list[list],
    out_dir: Path | str,
    name: str,
    *,
    query: str = "",
    dsl: dict | None = None,
    max_rows: int = DEFAULT_MAX_EXPORT_ROWS,
) -> ParquetRef:
    """把结果集脱敏后写为 Parquet，返回 ParquetRef（失败抛异常，不吞错）。

    - ``name``：数据集逻辑名（须为安全文件名形态，正则约束）；
    - ``max_rows``：超过即截断（编排层可据 ref.rows 判断是否截断）。
    """
    if not re_ok_name(name):
        raise ValueError(f"非法数据集名: {name!r}")
    if len(columns) != len({str(c) for c in columns}):
        raise ValueError(f"结果集存在重复列名: {columns!r}")

    capped = False
    if len(rows) > max_rows:
        rows = rows[:max_rows]
        capped = True

    columns, rows, report = mask_result_set(columns, rows)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.parquet"

    frame = pd.DataFrame(rows, columns=[str(c) for c in columns])
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, out_path)

    # 读回校验：物化产物必须与预期一致（不吞错原则）
    verify = pq.read_table(out_path)
    if verify.num_rows != len(rows) or verify.num_columns != len(columns):
        raise RuntimeError(
            f"Parquet 物化校验失败: 期望 {len(rows)}x{len(columns)}, "
            f"实际 {verify.num_rows}x{verify.num_columns}"
        )
    sha = hashlib.sha256(out_path.read_bytes()).hexdigest()

    schema_map = {
        field_name: str(verify.schema.field(field_name).type) for field_name in verify.schema.names
    }
    return ParquetRef(
        name=name,
        path=out_path.name,
        rows=verify.num_rows,
        columns=[str(c) for c in columns],
        schema=schema_map,
        sha256=sha,
        query=query,
        dsl=dsl or {},
        masked_cells=report.total_masked,
    )


def re_ok_name(name: str) -> bool:
    """数据集逻辑名约束：ASCII 字母数字下划线，≤64 字符。"""
    import re

    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name))


__all__ = ["DEFAULT_MAX_EXPORT_ROWS", "export_to_parquet"]
