"""AST 静态安全守卫：沙箱代码执行前的第一道防线（Static Analysis Gate）。

对候选脚本做 AST 全量遍历，按三类黑名单拦截：

1. 危险 import：os / sys / subprocess / socket / pty / signal / ctypes /
   importlib / shutil / threading / multiprocessing / webbrowser 等
   （绝对导入与 ``from X import Y`` 均拦）；
2. 危险调用：eval / exec / compile / __import__ / globals / locals /
   vars / open / input / breakpointhook / getattr(动态属性逃逸) 等；
3. 危险属性/名称访问：``__dunder__`` 属性链（__class__、__subclasses__、
   __builtins__、__globals__ 等逃逸向量）。

设计原则：**白名单哲学的否定式实现**——黑名单只收窄不放宽；未知
内置一律通过运行时限权 builtins 兜底（runner.py）。拦截信息含行号，
供编排层 error_context 记录与 LLM 自愈重写。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# 模块级黑名单（import module / from module import name 均适用）
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "pty",
        "signal",
        "ctypes",
        "importlib",
        "shutil",
        "multiprocessing",
        "threading",
        "asyncio",
        "webbrowser",
        "http",
        "urllib",
        "urllib2",
        "requests",
        "ftplib",
        "telnetlib",
        "smtplib",
        "pickle",
        "shelve",
        "marshal",
        "code",
        "codeop",
        "compileall",
        "distutils",
        "setuptools",
        "pip",
        "venv",
        "pathlib",
        "tempfile",
        "glob",
        "pickletools",
        "runpy",
        "platform",
        "resource",
        "fcntl",
        "msvcrt",
        "winreg",
        "posix",
        "nt",
    }
)

# 调用级黑名单（内建危险函数 + 逃逸向量）
FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "globals",
        "locals",
        "vars",
        "open",
        "input",
        "breakpoint",
        "breakpointhook",
        "exit",
        "quit",
        "help",
        "memoryview",
        "super",
        "delattr",
        "setattr",
    }
)

# 属性/名称访问黑名单（逃逸向量）
FORBIDDEN_ATTRS: frozenset[str] = frozenset(
    {
        "__class__",
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__globals__",
        "__locals__",
        "__builtins__",
        "__code__",
        "__closure__",
        "__dict__",
        "__import__",
        "__getattribute__",
        "__setattr__",
        "__delattr__",
        "__reduce__",
        "__reduce_ex__",
        "__loader__",
        "__spec__",
    }
)


@dataclass
class StaticGuardReport:
    """静态校验报告：通过与否 + 违规明细（行号 + 类别 + 证据）。"""

    ok: bool
    violations: list[dict[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        """单行人读摘要（供 LLM 自愈重写上下文）。"""
        if self.ok:
            return "静态校验通过"
        return "; ".join(
            f"L{v['line']} {v['category']}: {v['detail']}" for v in self.violations[:5]
        )


def _violation(node: ast.AST, category: str, detail: str) -> dict[str, str]:
    return {
        "line": str(getattr(node, "lineno", 0)),
        "category": category,
        "detail": detail,
    }


def _full_module_name(node: ast.Import | ast.ImportFrom) -> str:
    """取 import 语句的顶层模块名。"""
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0]
    names = node.names
    return names[0].name.split(".")[0] if names else ""


class _GuardVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.report = StaticGuardReport(ok=True)

    def _flag(self, node: ast.AST, category: str, detail: str) -> None:
        self.report.ok = False
        self.report.violations.append(_violation(node, category, detail))

    def visit_Import(self, node: ast.Import) -> None:
        module = _full_module_name(node)
        if module in FORBIDDEN_MODULES:
            self._flag(node, "forbidden_import", f"import {module}")
        else:
            self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _full_module_name(node)
        if module in FORBIDDEN_MODULES:
            self._flag(node, "forbidden_import", f"from {module} import ...")
        elif node.level > 0:
            # 相对导入在无包上下文沙箱内无意义且是路径逃逸向量
            self._flag(node, "forbidden_import", "相对导入被禁止")
        else:
            self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
            self._flag(node, "forbidden_call", f"{func.id}() 被禁止")
        elif isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_CALLS:
            self._flag(node, "forbidden_call", f"...{func.attr}() 被禁止")
        elif (
            isinstance(func, ast.Name)
            and func.id in ("getattr", "setattr", "delattr", "hasattr")
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FORBIDDEN_ATTRS
        ):
            self._flag(
                node,
                "forbidden_call",
                f"{func.id}(obj, '{node.args[1].value}') 逃逸向量被禁止",
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRS:
            self._flag(node, "forbidden_attribute", f"属性 .{node.attr} 被禁止")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_ATTRS:
            self._flag(node, "forbidden_name", f"名称 {node.id} 被禁止")
        self.generic_visit(node)


def static_check(code: str) -> StaticGuardReport:
    """对候选代码做 AST 静态安全校验（语法错误按违规返回，不进执行器）。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return StaticGuardReport(
            ok=False,
            violations=[
                {"line": str(exc.lineno or 0), "category": "syntax_error", "detail": str(exc)}
            ],
        )
    visitor = _GuardVisitor()
    visitor.visit(tree)
    return visitor.report
