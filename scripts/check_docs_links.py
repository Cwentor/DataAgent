"""文档链接完整性检查：校验仓库内 Markdown 的相对链接与 docs 根相对引用。

用途：文档目录迁移 / 文件改名后，`docs/...` 全路径 grep 覆盖不到**相对链接**
（例如 `../.env.example` 这类跨目录相对引用），必须单独跑本检查器兜底。

检查两类引用：
1. Markdown 相对链接 `[文本](相对路径)`——按所在文件目录解析，须真实存在；
2. 仓库根相对引用 `docs/xxx.md`——任意正文/代码注释中出现即校验存在性。

跳过项：http(s)/mailto/tel/ftp 外链、纯锚点、围栏代码块内的示例文本。
退出码：0 = 全部可达；1 = 存在失效引用（CI lint job 以此判定门禁）。

用法：
    python scripts/check_docs_links.py            # 以仓库根为根扫描
    python scripts/check_docs_links.py --quiet    # 只输出汇总与失效项
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

# 不参与扫描的目录（构建产物 / 运行期日志 / 工具缓存 / 代理工作区）
EXCLUDE_DIRS = {
    ".git",
    ".workbuddy",
    ".workbuddy-ai",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "logs",
    "node_modules",
}

# Markdown 链接：[文本](目标)；目标内不含空白与右括号
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# 仓库根相对引用：docs/ 下的 md 文件（含代码注释中的裸引用）
DOCS_REF_RE = re.compile(r"docs/[A-Za-z0-9_./\-]+\.md")
# 围栏代码块起止（``` 或 ~~~，允许缩进）
FENCE_RE = re.compile(r"^\s*(```|~~~)")
# 视为外链/非文件目标的 scheme
SKIP_SCHEMES = ("http://", "https://", "mailto:", "tel:", "ftp://", "data:")


def iter_markdown_files(root: Path) -> list[Path]:
    """收集参与检查的 Markdown 文件（按路径排序，输出稳定）。"""
    files = []
    for path in root.rglob("*.md"):
        rel_parts = path.relative_to(root).parts
        if any(part in EXCLUDE_DIRS for part in rel_parts[:-1]):
            continue
        files.append(path)
    return sorted(files)


def strip_fenced_blocks(text: str) -> str:
    """剔除围栏代码块内容，避免示例 Markdown 造成误报。"""
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


def check_file(path: Path, root: Path) -> tuple[list[str], list[str]]:
    """检查单个文件，返回 (失效相对链接, 失效 docs 引用)。"""
    text = strip_fenced_blocks(path.read_text(encoding="utf-8", errors="replace"))
    broken_links: list[str] = []
    broken_refs: list[str] = []

    for target in LINK_RE.findall(text):
        if target.startswith(SKIP_SCHEMES) or target.startswith("#"):
            continue
        # 去掉锚点与查询串后按所在目录解析
        bare = unquote(target.split("#")[0].split("?")[0]).replace("\\", "/")
        if not bare:
            continue
        if not (path.parent / bare).exists():
            broken_links.append(target)

    for ref in DOCS_REF_RE.findall(text):
        if not (root / ref).exists():
            broken_refs.append(ref)

    return broken_links, broken_refs


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 Markdown 相对链接与 docs 引用")
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="仓库根目录（默认取脚本上级目录）",
    )
    parser.add_argument("--quiet", action="store_true", help="仅输出汇总与失效项")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    files = iter_markdown_files(root)

    total_links = total_broken_links = total_broken_refs = 0
    failures: list[str] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        broken_links, broken_refs = check_file(path, root)
        total_links += 1
        total_broken_links += len(broken_links)
        total_broken_refs += len(broken_refs)
        for target in broken_links:
            failures.append(f"  [相对链接失效] {rel} -> {target}")
        for ref in broken_refs:
            failures.append(f"  [docs 引用失效] {rel} -> {ref}")

    if not args.quiet:
        print(f"扫描目录：{root}")
    print(f"扫描 Markdown 文件：{total_links}")
    print(f"相对链接失效：{total_broken_links}")
    print(f"docs 引用失效：{total_broken_refs}")

    if failures:
        print("\n失效明细：")
        for line in failures:
            print(line)
        print("\n结论：FAIL（请修正上述引用后重跑）")
        return 1

    print("结论：PASS（全部引用可达）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
