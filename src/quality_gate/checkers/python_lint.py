"""Python Lint 增量检查器

使用 ruff + git diff 行过滤。共享骨架（结果/工具错误/diff 上报）在
lint_common.py；本文件只留差异点: ruff 命令、JSON 解析、路径归一。
"""

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .git_diff import matches_ignore_patterns
from .lint_common import LintReporter, tool_error


def check_python_lint_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """执行 Python Lint 检查

    full=False (默认): 增量模式，ruff + git diff 行过滤，只阻塞新增/修改行。
    full=True: 全仓扫描模式（scan 周报用），不做 diff 过滤，报告全部存量问题。
    """
    if ignore_paths is None:
        ignore_paths = []

    if verbose:
        print("  运行 ruff...")

    # 运行 ruff
    try:
        ruff_result = subprocess.run(
            ["ruff", "check", "--output-format", "json", "."],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120
        )
    except FileNotFoundError:
        return tool_error("ruff", "not_found", "ruff 未安装，请先安装：pip install ruff")
    except subprocess.TimeoutExpired:
        return tool_error("ruff", "timeout", "ruff 执行超时（>2 分钟）")

    # 解析 JSON 输出
    if verbose:
        print("  解析 ruff 输出...")
    try:
        diagnostics = json.loads(ruff_result.stdout)
    except json.JSONDecodeError:
        return tool_error("ruff", "parse_error", "ruff 输出解析失败")

    if verbose:
        print("  应用 diff 行过滤...")
    reporter = LintReporter(repo_root, full=full)
    for issue in _normalize_ruff_issues(diagnostics, repo_root, ignore_paths):
        reporter.report(issue)

    if verbose:
        print(
            f"  发现 {len(reporter.result['all_issues'])} 个问题，其中 "
            f"{len(reporter.result['issues'])} 个在 diff 范围内"
        )
    return reporter.result


def _normalize_ruff_issues(
    diagnostics: list[dict],
    repo_root: Path,
    ignore_paths: list[str],
) -> Iterator[dict[str, Any]]:
    """ruff 诊断 → 归一化 issue（相对路径/忽略过滤/venv 跳过）"""
    for diag in diagnostics:
        # ruff JSON: 顶层 filename (绝对路径) + location.row/column
        filepath = diag.get("filename", "") or diag.get("location", {}).get("filepath", "")
        if not filepath:
            continue

        rel_path = _rel_path(repo_root, filepath)
        if rel_path is None:
            continue
        if matches_ignore_patterns(rel_path, ignore_paths):
            continue
        if "venv/" in rel_path or ".venv/" in rel_path or "node_modules/" in rel_path:
            continue

        loc = diag.get("location", {})
        yield {
            "file": rel_path,
            "line": loc.get("row", 0),
            "column": loc.get("column", 0),
            "level": "warning",  # ruff 默认都是 warning
            "code": diag.get("code", "unknown"),
            "message": diag.get("message", ""),
        }


def _rel_path(repo_root: Path, filepath: str) -> str | None:
    """绝对路径 → 相对仓库根 posix；仓库外文件返回 None"""
    p = Path(filepath)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return None
    return p.as_posix()
