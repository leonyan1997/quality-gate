"""TypeScript Lint 增量检查器

使用 oxlint（优先）或 eslint + git diff 行过滤
"""

import json
import subprocess
from pathlib import Path
from typing import Any

from .git_diff import get_git_diff_lines, is_line_in_diff, matches_ignore_patterns


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
    
    result = {
        "blocking": False,
        "issues": [],
        "all_issues": [],
        "diff_ranges": {},
    }
    
    if verbose:
        print("  运行 oxlint...")
    
    # 尝试 oxlint（优先，速度更快）
    try:
        lint_result = subprocess.run(
            ["npx", "oxlint", "--format", "json"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120
        )
        lint_tool = "oxlint"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # 回退到 eslint
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
            lint_tool = "eslint"
        except Exception as e:
            return {
                "blocking": True,
                "issues": [{
                    "file": "eslint",
                    "line": 0,
                    "column": 0,
                    "level": "error",
                    "code": "execution_error",
                    "message": f"eslint 执行失败：{e}"
                }],
                "all_issues": [],
                "diff_ranges": {},
            }
    
    # 解析 JSON 输出
    if verbose:
        print(f"  解析 {lint_tool} 输出...")
    
    try:
        if lint_result.stdout.strip():
            diagnostics = json.loads(lint_result.stdout)
        else:
            diagnostics = []
    except json.JSONDecodeError:
        return {
            "blocking": True,
            "issues": [{
                "file": lint_tool,
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "parse_error",
                "message": f"{lint_tool} 输出解析失败"
            }],
            "all_issues": [],
            "diff_ranges": {},
        }
    
    # oxlint 和 eslint 的输出格式不同
    if lint_tool == "oxlint":
        # oxlint v3+ 格式: {diagnostics: [{message, code, severity: "warning"|"error",
        #                    filename, labels: [{span: {line, column}}]}, ...]}
        # 兼容旧版数组格式 (v2)
        if isinstance(diagnostics, dict):
            diagnostics = diagnostics.get("diagnostics", [])
        for diag in diagnostics:
            if not isinstance(diag, dict):
                continue
            filepath = diag.get("filename", "") or diag.get("file", "")
            labels = diag.get("labels") or []
            span = labels[0].get("span", {}) if labels else {}
            line_num = span.get("line", 0)
            column = span.get("column", 0)
            sev = diag.get("severity")
            level = "error" if sev in (2, "error") else "warning"
            code = diag.get("code", "unknown")
            message = diag.get("message", "")
            
            if not filepath or "node_modules/" in filepath:
                continue
            
            rel_path = Path(filepath).as_posix()
            
            if matches_ignore_patterns(rel_path, ignore_paths):
                continue
            
            issue = {
                "file": rel_path,
                "line": line_num,
                "column": column,
                "level": level,
                "code": code,
                "message": message,
            }

            if full:
                # 全仓扫描模式：不做 diff 过滤，所有问题都报告
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
            
            result["all_issues"].append(issue)
            
            if is_incremental:
                result["issues"].append(issue)
                result["blocking"] = True
    
    else:  # eslint
        # eslint 格式：[{filePath, messages: [{line, column, ruleId, message, severity}]}, ...]
        for file_diag in diagnostics:
            filepath = file_diag.get("filePath", "")
            messages = file_diag.get("messages", [])
            
            if not filepath or "node_modules/" in filepath:
                continue
            
            rel_path = Path(filepath).as_posix()
            # eslint filePath 是绝对路径 → 转相对仓库根（镜像 python_lint/rust_lint），
            # 否则 ignore patternspec 与 diff 行判定全部失效
            if rel_path.startswith("/"):
                try:
                    rel_path = Path(filepath).resolve().relative_to(
                        repo_root.resolve()
                    ).as_posix()
                except ValueError:
                    # 仓库外文件（node_modules 等）跳过
                    continue
            
            if matches_ignore_patterns(rel_path, ignore_paths):
                continue
            
            if full:
                # 全仓扫描模式：不做 diff 过滤，所有问题都报告
                for msg in messages:
                    line_num = msg.get("line", 0)
                    column = msg.get("column", 0)
                    level = "error" if msg.get("severity") == 2 else "warning"
                    code = msg.get("ruleId", "unknown")
                    message = msg.get("message", "")
                    issue = {
                        "file": rel_path,
                        "line": line_num,
                        "column": column,
                        "level": level,
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
            
            for msg in messages:
                line_num = msg.get("line", 0)
                column = msg.get("column", 0)
                level = "error" if msg.get("severity") == 2 else "warning"
                code = msg.get("ruleId", "unknown")
                message = msg.get("message", "")
                
                is_incremental = is_line_in_diff(line_num, diff_ranges)
                
                issue = {
                    "file": rel_path,
                    "line": line_num,
                    "column": column,
                    "level": level,
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
