"""沙箱 runner（由 api.run_code 落盘为执行脚本，在沙箱进程内运行）。

执行模型（与"清空全局 builtins"方案的区别）：
- **绝不修改真实 builtins 模块**——pandas 等已加载模块的 builtins 查找
  仍指向真实模块，保证库内部正常工作；
- 用户代码通过受限命名空间执行（见 _run_user）：``__builtins__`` 指向
  限权白名单字典，全局/内建查找只看见白名单：
  - ``__import__`` 为模块白名单钩子（pandas/numpy/math/... 放行；
    os/sys/subprocess/socket 等一律 ImportError——与 AST 守卫双保险）；
  - ``open`` 为 workspace 受限版（读限工作区、写限 outputs/）；
  - 动态求值类内建（eval/exec/compile/globals/locals/vars/input/getattr
    等）**不注入**用户命名空间（NameError 即拦截）；
- 用户代码源码经 base64 内嵌（_USER_CODE_B64），避免引号/转义破坏脚本结构。

api.run_code 的调用序：静态校验用户代码 -> 拼接本文件 + base64 代码块 ->
交后端执行。异常向上传播为非零退出码，summary 缺失即失败。
"""

import base64 as _base64
import builtins as _builtins_module
import json as _json
import os as _os
import re as _re

import pandas as _pd

_WORKSPACE = _os.environ.get("SANDBOX_WORKSPACE", ".")
_INPUTS = _os.path.join(_WORKSPACE, "inputs")
_OUTPUTS = _os.path.join(_WORKSPACE, "outputs")
_MAX_SUMMARY_ROWS = 100

# 用户代码可导入的模块白名单（数值/统计栈；任何 IO/进程/网络模块不在列）
_ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "pandas",
        "numpy",
        "math",
        "json",
        "statistics",
        "collections",
        "itertools",
        "functools",
        "datetime",
        "decimal",
        "fractions",
        "random",
        "re",
        "string",
        "textwrap",
        "heapq",
        "bisect",
        "array",
        "operator",
    }
)

# 用户代码可见的内建白名单（含异常体系；动态求值类内建不注入）
_SAFE_BUILTIN_NAMES = (
    "abs",
    "all",
    "any",
    "bool",
    "bytes",
    "chr",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "hash",
    "hex",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "oct",
    "ord",
    "pow",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    "True",
    "False",
    "None",
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "Exception",
    "FloatingPointError",
    "IndexError",
    "KeyError",
    "LookupError",
    "KeyboardInterrupt",
    "NameError",
    "OverflowError",
    "RuntimeError",
    "StopIteration",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
    "OSError",
    "FileNotFoundError",
    "FileExistsError",
    "PermissionError",
    "ImportError",
    "ModuleNotFoundError",
    "NotADirectoryError",
    "IsADirectoryError",
    "ConnectionError",
    "TimeoutError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    "NotImplementedError",
)

_real_import = _builtins_module.__import__
_real_open = _builtins_module.open
_NAME_RE = _re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def _sandbox_import(name, globals=None, locals=None, fromlist=(), level=0):
    """模块白名单 import 钩子（相对导入直接拒绝）。"""
    if level > 0:
        raise ImportError("沙箱禁止相对导入")
    root = str(name).split(".")[0]
    if root not in _ALLOWED_IMPORT_ROOTS:
        raise ImportError(f"沙箱禁止导入模块: {name!r}")
    return _real_import(name, globals, locals, fromlist, level)


def _sandbox_open(file, mode="r", *args, **kwargs):
    """workspace 受限 open：绝对路径解析后必须位于工作区内，写仅限 outputs/。"""
    path = _os.path.abspath(str(file))
    base = _os.path.abspath(_WORKSPACE)
    if not (path == base or path.startswith(base + _os.sep)):
        raise PermissionError(f"沙箱 open 仅允许访问工作区: {file!r}")
    if any(flag in mode for flag in ("w", "a", "x", "+")):
        if not path.startswith(_os.path.abspath(_OUTPUTS) + _os.sep):
            raise PermissionError("沙箱写操作仅允许 outputs/ 目录")
    return _real_open(path, mode, *args, **kwargs)


def read_input(name):
    """读取 inputs/{name}.parquet 为 DataFrame（只读）。"""
    if not _NAME_RE.fullmatch(str(name)):
        raise ValueError(f"非法输入数据集名: {name!r}")
    path = _os.path.join(_INPUTS, f"{name}.parquet")
    if not _os.path.exists(path):
        raise FileNotFoundError(f"输入数据集不存在: {name}")
    return _pd.read_parquet(path)


def list_inputs():
    """列出 inputs/ 下全部数据集名（供脚本按需读取）。"""
    if not _os.path.isdir(_INPUTS):
        return []
    return sorted(p[:-8] for p in _os.listdir(_INPUTS) if p.endswith(".parquet"))


def save_summary(title="", metrics=None, table=None, findings=None, extra=None):
    """写出结构化摘要 outputs/summary.json（table.rows ≤100 行硬限）。"""
    if table and len(table.get("rows", [])) > _MAX_SUMMARY_ROWS:
        raise ValueError(f"table.rows 超过 {_MAX_SUMMARY_ROWS} 行上限，请先聚合")
    payload = {
        "title": str(title),
        "metrics": metrics or {},
        "table": table or {"columns": [], "rows": []},
        "findings": findings or [],
        "extra": extra or {},
    }
    _os.makedirs(_OUTPUTS, exist_ok=True)
    with _real_open(_os.path.join(_OUTPUTS, "summary.json"), "w", encoding="utf-8") as fh:
        _json.dump(payload, fh, ensure_ascii=False, default=str)


def save_echarts_spec(spec):
    """写出 ECharts 规格 outputs/echarts_spec.json（顶层必须为 dict）。"""
    if not isinstance(spec, dict):
        raise TypeError("echarts spec 必须是 dict")
    _os.makedirs(_OUTPUTS, exist_ok=True)
    with _real_open(_os.path.join(_OUTPUTS, "echarts_spec.json"), "w", encoding="utf-8") as fh:
        _json.dump(spec, fh, ensure_ascii=False, default=str)


# ---- 限权内建命名空间（仅对用户代码命名空间生效；真实 builtins 不动） --------
_SAFE = {name: getattr(_builtins_module, name) for name in _SAFE_BUILTIN_NAMES}
_SAFE["__import__"] = _sandbox_import
_SAFE["__build_class__"] = getattr(_builtins_module, "__build_class__", None)
_SAFE["__name__"] = "sandbox"
_SAFE["open"] = _sandbox_open
if _SAFE["__build_class__"] is None:
    del _SAFE["__build_class__"]
_SAFE["read_input"] = read_input
_SAFE["list_inputs"] = list_inputs
_SAFE["save_summary"] = save_summary
_SAFE["save_echarts_spec"] = save_echarts_spec

# 用户代码（base64 内嵌，由 api.run_code 替换占位行）
_USER_CODE_B64 = "__USER_CODE_B64__"


def _run_user() -> None:
    """在限权命名空间内执行内嵌用户代码（经静态校验后的可信代码）。"""
    source = _base64.b64decode(_USER_CODE_B64).decode("utf-8")
    user_ns = {"__builtins__": _SAFE}
    _dynamic_exec = vars(_builtins_module)["exec"]
    _dynamic_exec(_dynamic_compile(source), user_ns)


def _dynamic_compile(source):
    """受限编译：源码已过 AST 静态守卫，此处仅生成代码对象。"""
    _compile = vars(_builtins_module)["compile"]
    return _compile(source, "<sandbox-user>", "exec")


_run_user()
