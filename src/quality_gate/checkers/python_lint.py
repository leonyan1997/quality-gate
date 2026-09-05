"""Python Lint 增量检查器

使用 ruff + git diff 行过滤
"""

import json
import subprocess
from pathlib import Path
from typing import Any

from .git_diff import get_git_diff_lines, is_line_in_diff, matches_ignore_patterns


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
    
    result = {
        "blocking": False,
        "issues": [],
        "all_issues": [],
        "diff_ranges": {},
    }
    
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
        return {
            "blocking": True,
            "issues": [{
                "file": "ruff",
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "not_found",
                "message": "ruff 未安装，请先安装：pip install ruff"
            }],
            "all_issues": [],
            "diff_ranges": {},
        }
    except subprocess.TimeoutExpired:
        return {
            "blocking": True,
            "issues": [{
                "file": "ruff",
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "timeout",
                "message": "ruff 执行超时（>2 分钟）"
            }],
            "all_issues": [],
            "diff_ranges": {},
        }
    
    # 解析 JSON 输出
    if verbose:
        print("  解析 ruff 输出...")
    
    try:
        diagnostics = json.loads(ruff_result.stdout)
    except json.JSONDecodeError:
        return {
            "blocking": True,
            "issues": [{
                "file": "ruff",
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "parse_error",
                "message": "ruff 输出解析失败"
            }],
            "all_issues": [],
            "diff_ranges": {},
        }
    
    # 过滤增量问题
    if verbose:
        print("  应用 diff 行过滤...")
    
    for diag in diagnostics:
        # ruff JSON: 顶层 filename (绝对路径) + location.row/column
        filepath = diag.get("filename", "")
        line_num = diag.get("location", {}).get("row", 0)
        column = diag.get("location", {}).get("column", 0)
        code = diag.get("code", "unknown")
        message = diag.get("message", "")
        
        if not filepath:
            # 兼容旧格式 (location.filepath)
            filepath = diag.get("location", {}).get("filepath", "")
            if not filepath:
                continue
        
        rel_path = Path(filepath).as_posix()
        # ruff 可能输出绝对路径，转为相对仓库根
        if rel_path.startswith("/"):
            try:
                rel_path = Path(filepath).resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                # 不在仓库内的文件 (如 ~/.local/...)，跳过
                continue
        
        # 跳过忽略路径 (lint_ignore) 与虚拟环境
        if matches_ignore_patterns(rel_path, ignore_paths):
            continue
        if "venv/" in rel_path or ".venv/" in rel_path or "node_modules/" in rel_path:
            continue
        
        if full:
            # 全仓扫描模式：不做 diff 过滤，所有问题都报告
            issue = {
                "file": rel_path,
                "line": line_num,
                "column": column,
                "level": "warning",  # ruff 默认都是 warning
                "code": code,
                "message": message,
            }
            result["all_issues"].append(issue)
            result["issues"].append(issue)
            result["blocking"] = True
            continue

        if rel_path not in result["diff_ranges"]:
            result["diff_ranges"][rel_path] = get_git_diff_lines(repo_root, rel_path)
        
        diff_ranges = result["diff_ranges"][rel_path]
        # 文件在 diff 中无新增/修改 → 存量问题不阻塞（增量门禁核心）
        if not diff_ranges:
            continue
        is_incremental = is_line_in_diff(line_num, diff_ranges)
        
        issue = {
            "file": rel_path,
            "line": line_num,
            "column": column,
            "level": "warning",  # ruff 默认都是 warning
            "code": code,
            "message": message,
        }
        
        result["all_issues"].append(issue)
        
        if is_incremental:
            result["issues"].append(issue)
            result["blocking"] = True
    
    if verbose:
                    print(f"  发现 {len(result['all_issues'])} 个问题，其中 {len(result['issues'])} 个在 diff 范围内")
    
    return result
