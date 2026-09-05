"""quality-gate fix：自动修复闭环（P2，2026-09）

fix 只处理 lint 可自动修复项（文件级增量：默认只动 diff 内文件）：
  - Python: ruff check --fix <files>
  - TS:     oxlint --fix（与 ts lint 引擎选择一致；缺失/超时回退 eslint --fix）
  - Rust:   仅建议（不自动改文件，避免引入机械性改动）

修完复用对应 lint checker 复检，剩余阻塞决定退出码（编排在 cli.fix，
本模块只做「收集待修文件 → 跑工具修复 → 复检」三件事，每步独立函数
防自身 long-method / long-parameter-list 回潮）。
"""

import os
import subprocess
from pathlib import Path
from typing import Any

from .checkers.git_diff import get_changed_files, matches_ignore_patterns
from .checkers.python_lint import check_python_lint_incremental
from .checkers.ts_lint import check_ts_lint_incremental

# 按语言的可修复后缀（与 lint 引擎实际覆盖一致；.vue 等 SFC 由 eslint
# 引擎兜底时才会出 issue，oxlint 主引擎下不产生 vue lint 问题）
_EXT: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "ts": (".ts", ".tsx", ".js", ".jsx"),
}
# 整仓遍历（--all）时跳过的目录
_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "target", "dist", "build",
    "__pycache__", ".quality-gate", ".ruff_cache", ".pytest_cache",
    ".mypy_cache", ".coverage", "htmlcov",
}

_RUST_ADVICE = (
    "Rust 无自动修复（建议人工）：可试 cargo clippy --fix --allow-dirty，"
    "或按 clippy 建议手改后重跑 quality-gate check --diff"
)


def apply_fix(
    repo_root: Path,
    langs: list[str],
    *,
    ignore_paths: list[str] | None = None,
    whole: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """逐语言执行自动修复并复检

    返回 {"summary": [str,...], "blocked": bool}——blocked = 修复后仍有
    阻塞 lint 问题（或工具缺失未能修复），由 cli.fix 决定 exit code。
    """
    if ignore_paths is None:
        ignore_paths = []
    summary: list[str] = []
    blocked = False

    for lang in langs:
        if lang == "rust":
            summary.append(f"{lang}: {_RUST_ADVICE}")
            continue

        files = _collect_files(repo_root, lang, ignore_paths, whole)
        if not files:
            summary.append(f"{lang}: 无待修文件（无改动或全被豁免）")
            continue

        error = _run_tool_fix(lang, repo_root, files, verbose)
        if error is not None:
            summary.append(f"{lang}: {error}")
            blocked = True
            continue

        remaining = _recheck_lint(lang, repo_root, ignore_paths)
        n_remain = len(remaining.get("issues", []))
        summary.append(
            f"{lang}: 已自动修复 {len(files)} 个文件；复检剩余阻塞 lint "
            f"{n_remain} 个（明细见 quality-gate check --diff）"
        )
        blocked = blocked or bool(remaining.get("blocking"))

    return {"summary": summary, "blocked": blocked}


def _collect_files(
    repo_root: Path, lang: str, ignore_paths: list[str], whole: bool,
) -> list[str]:
    """收集待修复文件（相对仓库根 posix；默认 diff 内，whole=True 全量）"""
    exts = _EXT[lang]
    if not whole:
        return sorted(
            f for f, _s in get_changed_files(repo_root).items()
            if f.endswith(exts) and not matches_ignore_patterns(f, ignore_paths)
        )

    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(exts):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), repo_root)
            rel = rel.replace(os.sep, "/")
            if not matches_ignore_patterns(rel, ignore_paths):
                files.append(rel)
    return sorted(files)


def _run_tool_fix(
    lang: str, repo_root: Path, files: list[str], verbose: bool,
) -> str | None:
    """跑语言对应的自动修复工具；None=已执行，str=失败原因"""
    if lang == "python":
        return _run_ruff_fix(repo_root, files, verbose)
    return _run_ts_fix(repo_root, files, verbose)


def _run_ruff_fix(
    repo_root: Path, files: list[str], verbose: bool,
) -> str | None:
    """ruff check --fix（默认只修安全修复；不安全修复留给人工/AI）"""
    try:
        subprocess.run(
            ["ruff", "check", "--fix", *files],
            cwd=repo_root, capture_output=True, text=True, timeout=120,
        )
        return None
    except FileNotFoundError:
        return "ruff 未安装（pip install ruff），跳过 Python 自动修复"
    except subprocess.TimeoutExpired:
        return "ruff --fix 超时（>2 分钟）"


def _run_ts_fix(
    repo_root: Path, files: list[str], verbose: bool,
) -> str | None:
    """oxlint --fix 优先（与 ts lint 引擎一致）；缺失/超时回退 eslint --fix"""
    try:
        subprocess.run(
            ["npx", "oxlint", "--fix", *files],
            cwd=repo_root, capture_output=True, text=True, timeout=120,
        )
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        subprocess.run(
            ["npx", "eslint", "--fix", *files],
            cwd=repo_root, capture_output=True, text=True, timeout=300,
        )
        return None
    except Exception as e:
        return f"TS 自动修复失败（oxlint 与 eslint 均不可用）：{e}"


def _recheck_lint(
    lang: str, repo_root: Path, ignore_paths: list[str],
) -> dict[str, Any]:
    """修复后复用 lint checker 复检（增量语义，与 check --diff 同源）"""
    if lang == "python":
        return check_python_lint_incremental(
            repo_root, ignore_paths=ignore_paths,
        )
    return check_ts_lint_incremental(repo_root, ignore_paths=ignore_paths)
