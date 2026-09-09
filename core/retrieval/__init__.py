"""retrieval 门面：既有 ChatBI 防御链路的 typed Tool 化封装。

本包把既有高完整性只读链路（semantic DSL 契约 -> compiler 确定性编译 ->
exec 受控执行 -> security RLS 守卫）保留为内部工具，不改变其任何行为：

- ``tools.execute_dsl_query(dsl_payload) -> ParquetRef``：标准 typed 查询工具，
  结果物化为 Parquet 数据集供沙箱消费；
- ``tools.compile_dsl_to_duckdb(dsl_payload) -> str``：纯编译（Dry-run 语义）；
- ``guardrails.reject_raw_sql(...)``：网关层守卫，任何 SQL 形态在进入 DSL
  链路前被立即拒绝（"LLM 永不产出裸 SQL"的强制点）；
- ``pii.py``：Parquet 导出时的 PII 列脱敏。

模块映射（外部规格中的旧名 -> 本仓库实际位置，经本门面统一收口）：
- dsl_compiler  -> compiler.sql_compiler（DSL -> SQL 确定性编译器）
- duckdb_engine -> exec.guards / exec.pool（受控只读执行层）
- contracts     -> semantic.dsl_schema（DSL 强契约，extra="forbid"）
"""
