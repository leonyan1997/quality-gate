"""Rust Lint 增量检查器

使用 clippy + git diff 行过滤，只阻塞新增/修改行上的 lint 错误。
共享骨架（结果/工具错误/diff 上报/去重）在 lint_common.py；本文件只留
差异点: clippy 命令、JSON-Lines 解析、span 归一、绝对路径跳过。
"""

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .git_diff import matches_ignore_patterns
from .lint_common import LintReporter, tool_error


def check_rust_lint_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """执行 Rust Lint 检查

    full=False (默认): 增量模式，clippy + git diff 行过滤，只阻塞新增/修改行。
    full=True: 全仓扫描模式（scan 周报用），不做 diff 过滤，报告全部存量问题。

    返回:
        {
            "blocking": bool,
            "issues": [...],
            "all_issues": [...],
            "diff_ranges": {...},
        }
    """
    if ignore_paths is None:
        ignore_paths = []

    if verbose:
        print("  运行 clippy...")

    try:
        clippy_result = subprocess.run(
            ["cargo", "clippy", "--message-format=json", "--all-targets"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=300
        )
    except subprocess.TimeoutExpired:
        return tool_error("clippy", "timeout", "clippy 执行超时（>5 分钟）")
    except Exception as e:
        return tool_error("clippy", "execution_error", f"clippy 执行失败：{e}")

    if verbose:
        print("  解析 clippy 输出...")

    if verbose:
        print("  应用 diff 行过滤...")
    reporter = LintReporter(repo_root, full=full)
    # clippy 同一 diagnostic 可能含多 span/宏展开重复 → 上报层去重
    for issue in _normalize_clippy_issues(clippy_result.stdout, repo_root, ignore_paths):
        reporter.report(issue, dedupe=True)

    if verbose:
        print(
            f"  发现 {len(reporter.result['all_issues'])} 个问题，其中 "
            f"{len(reporter.result['issues'])} 个在 diff 范围内"
        )
    return reporter.result


def _normalize_clippy_issues(
    stdout: str,
    repo_root: Path,
    ignore_paths: list[str],
) -> Iterator[dict[str, Any]]:
    """clippy JSON-Lines → 归一化 issue（compiler-message/error+warning 过滤）

    逐行容错解析：非 JSON 行/非 compiler-message 行静默跳过（clippy 会混入
    cargo 进度行）。span 取 is_primary，缺失回落首个 span。
    """
    for line in stdout.split("\n"):
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("reason") != "compiler-message":
            continue

        diagnostic = msg["message"]
        issue = _clippy_issue(diagnostic, repo_root, ignore_paths)
        if issue is not None:
            yield issue


def _clippy_issue(diagnostic: dict, repo_root: Path, ignore_paths: list[str]):
    level = diagnostic.get("level", "")
    if level not in ("error", "warning"):
        return None

    spans = diagnostic.get("spans", [])
    if not spans:
        return None
    primary_span = next((s for s in spans if s.get("is_primary")), spans[0])

    filepath = primary_span.get("file_name", "")
    line_num = primary_span.get("line_start", 0)
    column = primary_span.get("column_start", 0)

    # clippy 只输出仓库内相对路径；target/ 产物与绝对路径跳过
    if not filepath or "target/" in filepath or filepath.startswith("/"):
        return None
    rel_path = Path(filepath).as_posix()
    if matches_ignore_patterns(rel_path, ignore_paths):
        return None

    return {
        "file": rel_path,
        "line": line_num,
        "column": column,
        "level": level,
        "code": diagnostic.get("code", {}).get("code", "unknown"),
        "message": diagnostic.get("message", "").strip(),
    }
