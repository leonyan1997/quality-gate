"""TypeScript Lint 增量检查器

使用 oxlint（优先）或 eslint + git diff 行过滤。共享骨架在 lint_common.py；
本文件只留差异点: oxlint→eslint 回退、双格式（oxlint dict / eslint 数组）
解析与路径归一。
"""

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .git_diff import matches_ignore_patterns
from .lint_common import LintReporter, tool_error


def check_ts_lint_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """执行 TypeScript Lint 检查

    full=False (默认): 增量模式，oxlint/eslint + git diff 行过滤，只阻塞新增/修改行。
    full=True: 全仓扫描模式（scan 周报用），不做 diff 过滤，报告全部存量问题。
    """
    if ignore_paths is None:
        ignore_paths = []

    # 运行 lint 工具：oxlint 优先，不可用/超时回退 eslint
    lint_tool, stdout, error = _run_lint_tool(repo_root, verbose)
    if error is not None:
        return error

    # 解析 JSON 输出
    if verbose:
        print(f"  解析 {lint_tool} 输出...")
    diagnostics = _parse_tool_output(stdout, lint_tool)
    if diagnostics is None:
        return tool_error(lint_tool, "parse_error", f"{lint_tool} 输出解析失败")

    if verbose:
        print("  应用 diff 行过滤...")
    reporter = LintReporter(repo_root, full=full)
    for issue in _normalize_ts_issues(diagnostics, lint_tool, repo_root, ignore_paths):
        reporter.report(issue)

    if verbose:
        print(
            f"  发现 {len(reporter.result['all_issues'])} 个问题，其中 "
            f"{len(reporter.result['issues'])} 个在 diff 范围内"
        )
    return reporter.result


def _run_lint_tool(
    repo_root: Path, verbose: bool,
) -> tuple[str, str, dict[str, Any] | None]:
    """运行 oxlint（优先）；FileNotFound/超时回退 eslint

    返回 (tool, stdout, None)；工具级失败返回 (tool, "", error_result)。
    """
    if verbose:
        print("  运行 oxlint...")
    try:
        lint_result = subprocess.run(
            ["npx", "oxlint", "--format", "json"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120
        )
        return "oxlint", lint_result.stdout, None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # 回退 eslint

    if verbose:
        print("  oxlint 不可用，回退到 eslint...")
    try:
        lint_result = subprocess.run(
            ["npx", "eslint", ".", "--format", "json"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=300
        )
        return "eslint", lint_result.stdout, None
    except Exception as e:
        return "eslint", "", tool_error(
            "eslint", "execution_error", f"eslint 执行失败：{e}",
        )


def _parse_tool_output(stdout: str, lint_tool: str):
    """解析工具 JSON 输出；空白输出视为无诊断；解析失败返回 None"""
    try:
        if stdout.strip():
            return json.loads(stdout)
        return []
    except json.JSONDecodeError:
        return None


def _normalize_ts_issues(
    diagnostics: Any,
    lint_tool: str,
    repo_root: Path,
    ignore_paths: list[str],
) -> Iterator[dict[str, Any]]:
    """按工具格式归一化 issue（oxlint 与 eslint 输出格式不同）"""
    if lint_tool == "oxlint":
        # oxlint v3+ 格式: {diagnostics: [...]}；兼容旧版数组格式 (v2)
        if isinstance(diagnostics, dict):
            diagnostics = diagnostics.get("diagnostics", [])
        for diag in diagnostics:
            issue = _oxlint_issue(diag, ignore_paths)
            if issue is not None:
                yield issue
    else:
        # eslint 格式：[{filePath, messages: [...]}, ...]
        for file_diag in diagnostics:
            yield from _eslint_issues(file_diag, repo_root, ignore_paths)


def _oxlint_issue(diag: Any, ignore_paths: list[str]) -> dict[str, Any] | None:
    if not isinstance(diag, dict):
        return None
    filepath = diag.get("filename", "") or diag.get("file", "")
    if not filepath or "node_modules/" in filepath:
        return None
    rel_path = Path(filepath).as_posix()
    if matches_ignore_patterns(rel_path, ignore_paths):
        return None

    labels = diag.get("labels") or []
    span = labels[0].get("span", {}) if labels else {}
    sev = diag.get("severity")
    return {
        "file": rel_path,
        "line": span.get("line", 0),
        "column": span.get("column", 0),
        "level": "error" if sev in (2, "error") else "warning",
        "code": diag.get("code", "unknown"),
        "message": diag.get("message", ""),
    }


def _eslint_issues(
    file_diag: dict,
    repo_root: Path,
    ignore_paths: list[str],
) -> Iterator[dict[str, Any]]:
    filepath = file_diag.get("filePath", "")
    messages = file_diag.get("messages", [])
    if not filepath or "node_modules/" in filepath:
        return
    rel_path = _eslint_rel_path(repo_root, filepath)
    if rel_path is None or matches_ignore_patterns(rel_path, ignore_paths):
        return

    for msg in messages:
        yield {
            "file": rel_path,
            "line": msg.get("line", 0),
            "column": msg.get("column", 0),
            "level": "error" if msg.get("severity") == 2 else "warning",
            "code": msg.get("ruleId", "unknown"),
            "message": msg.get("message", ""),
        }


def _eslint_rel_path(repo_root: Path, filepath: str) -> str | None:
    """eslint filePath 是绝对路径 → 相对仓库根（否则 ignore/diff 失效）"""
    rel_path = Path(filepath).as_posix()
    if rel_path.startswith("/"):
        try:
            return Path(filepath).resolve().relative_to(
                repo_root.resolve(),
            ).as_posix()
        except ValueError:
            # 仓库外文件（node_modules 等）跳过
            return None
    return rel_path
