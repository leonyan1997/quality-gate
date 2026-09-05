"""Rust Lint 增量检查器

使用 clippy + git diff 行过滤，只阻塞新增/修改行上的 lint 错误。
"""

import json
import subprocess
from pathlib import Path
from typing import Any

from .git_diff import get_git_diff_lines, is_line_in_diff, matches_ignore_patterns


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
    
    result = {
        "blocking": False,
        "issues": [],
        "all_issues": [],
        "diff_ranges": {},
    }

    # clippy 同一 diagnostic 可能含多 span/宏展开重复 → 去重
    seen: set[tuple] = set()

    def _dedupe(issue: dict) -> bool:
        key = (issue["file"], issue["line"], issue["column"], issue["code"])
        if key in seen:
            return False
        seen.add(key)
        return True
    
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
        return {
            "blocking": True,
            "issues": [{
                "file": "clippy",
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "timeout",
                "message": "clippy 执行超时（>5 分钟）"
            }],
            "all_issues": [],
            "diff_ranges": {},
        }
    except Exception as e:
        return {
            "blocking": True,
            "issues": [{
                "file": "clippy",
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "execution_error",
                "message": f"clippy 执行失败：{e}"
            }],
            "all_issues": [],
            "diff_ranges": {},
        }
    
    if verbose:
        print("  解析 clippy 输出...")
    
    all_diagnostics = []
    
    for line in clippy_result.stdout.split("\n"):
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
            if msg.get("reason") == "compiler-message":
                diagnostic = msg["message"]
                all_diagnostics.append(diagnostic)
        except json.JSONDecodeError:
            continue
    
    if verbose:
        print("  应用 diff 行过滤...")
    
    for diagnostic in all_diagnostics:
        level = diagnostic.get("level", "")
        if level not in ["error", "warning"]:
            continue
        
        spans = diagnostic.get("spans", [])
        if not spans:
            continue
        
        primary_span = None
        for span in spans:
            if span.get("is_primary"):
                primary_span = span
                break
        
        if not primary_span:
            primary_span = spans[0]
        
        filepath = primary_span.get("file_name", "")
        line_num = primary_span.get("line_start", 0)
        column = primary_span.get("column_start", 0)
        
        if not filepath or "target/" in filepath or filepath.startswith("/"):
            continue
        
        rel_path = Path(filepath).as_posix()
        
        # 跳过忽略路径 (lint_ignore)
        if matches_ignore_patterns(rel_path, ignore_paths):
            continue
        
        if full:
            # 全仓扫描模式：不做 diff 过滤，所有问题都报告
            code = diagnostic.get("code", {}).get("code", "unknown")
            message = diagnostic.get("message", "").strip()
            issue = {
                "file": rel_path,
                "line": line_num,
                "column": column,
                "level": level,
                "code": code,
                "message": message,
            }
            if not _dedupe(issue):
                continue
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
        
        code = diagnostic.get("code", {}).get("code", "unknown")
        message = diagnostic.get("message", "").strip()
        
        issue = {
            "file": rel_path,
            "line": line_num,
            "column": column,
            "level": level,
            "code": code,
            "message": message,
        }

        if not _dedupe(issue):
            continue

        result["all_issues"].append(issue)
        
        if is_incremental:
            result["issues"].append(issue)
            result["blocking"] = True
    
    if verbose:
        print(f"  发现 {len(result['all_issues'])} 个问题，其中 {len(result['issues'])} 个在 diff 范围内")
    
    return result
