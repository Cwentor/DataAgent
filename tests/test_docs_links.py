"""文档链接检查器（scripts/check_docs_links.py）的行为锁定测试。

覆盖三类判定：全部可达应 PASS；相对链接失效应 FAIL；docs 根相对引用失效应 FAIL。
用例全部在 tmp_path 内构造，不依赖仓库真实文档内容。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_docs_links.py"


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    """以指定根目录运行检查器，返回完成进程。"""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--quiet"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_all_links_reachable_passes(tmp_path: Path) -> None:
    """链接与 docs 引用全部可达时应 PASS 并返回 0。"""
    (tmp_path / "docs" / "designs").mkdir(parents=True)
    (tmp_path / "docs" / "designs" / "a-design.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("X=1\n", encoding="utf-8")
    (tmp_path / "docs" / "guides").mkdir()
    (tmp_path / "docs" / "guides" / "conf.md").write_text(
        "见 [模板](../../.env.example) 与 [设计](../designs/a-design.md)，"
        "另见 `docs/designs/a-design.md`。\n",
        encoding="utf-8",
    )

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_broken_relative_link_fails(tmp_path: Path) -> None:
    """相对链接指向不存在文件时应 FAIL 并返回 1。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("[坏](../nope/missing.md)\n", encoding="utf-8")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "相对链接失效" in result.stdout
    assert "../nope/missing.md" in result.stdout


def test_broken_docs_reference_fails(tmp_path: Path) -> None:
    """正文中的 docs 根相对引用指向不存在文件时应 FAIL。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "y.md").write_text("引用 `docs/designs/ghost.md`\n", encoding="utf-8")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "docs 引用失效" in result.stdout
    assert "docs/designs/ghost.md" in result.stdout


def test_fenced_code_block_is_ignored(tmp_path: Path) -> None:
    """围栏代码块内的示例 Markdown 不应触发失效判定。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "z.md").write_text(
        "示例：\n\n```markdown\n[示例](../not/exist.md)\n```\n",
        encoding="utf-8",
    )

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr


def test_repository_docs_are_clean() -> None:
    """仓库真实文档必须全部可达（守住既有基线，防止后续改名漏改）。"""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--quiet"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stdout + result.stderr
