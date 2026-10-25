"""编译器异常体系（二十一期自 compiler.sql_compiler 迁出，re-export 保旧路径）。

独立成模块的原因：compiler.dialect 与 compiler.sql_compiler 都需要
CompileError，若定义在 sql_compiler 会与其消费 dialect 形成循环依赖。
"""

from __future__ import annotations


class CompileError(ValueError):
    """DSL 合法但无法编译为 SQL（例如引用了未登记字段）。"""


__all__ = ["CompileError"]
