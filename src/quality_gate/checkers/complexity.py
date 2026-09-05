"""复杂度 / CRAP 报告检查器

阶段一:
  - Rust / TS 新增文件: 全文件复杂度检查，超过阈值 (默认 15) 阻塞
  - Rust / TS 修改文件: 仅报告，不阻塞 (阶段三启用函数级阻塞)
  - Python: CRAP 报告 (阶段一仅报告，持续输出数据供周报/阶段三使用)

工具:
  - Rust: cargo clippy -- -W clippy::cyclomatic_complexity
    (需要项目配置 clippy.toml 的 cyclomatic-complexity-threshold)
  - TS/JS: npx eslint --rule 'complexity: [error, 15]' (需要 eslint)
  - Python: radon cc -s -j (复杂度) — CRAP 公式后续接入覆盖率
"""

import json
import subprocess
from pathlib import Path
from typing import Any

from .git_diff import (
    get_added_files,
    get_changed_files,
    matches_ignore_patterns,
)

DEFAULT_COMPLEXITY_THRESHOLD = 15
CRAP_THRESHOLD = 30


def check_rust_complexity_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
    threshold: int = DEFAULT_COMPLEXITY_THRESHOLD,
) -> dict[str, Any]:
    """检查 Rust 新增文件的圈复杂度 (clippy::cyclomatic_complexity)

    修改文件仅报告；新增文件超阈值阻塞。
    注意: clippy 默认阈值 25 起步，要真正按 15 阻塞需在项目根放
    clippy.toml: cyclomatic-complexity-threshold = 15
    """
    if ignore_paths is None:
        ignore_paths = []

    result = {
        "blocking": False,
        "issues": [],
        "report_only_issues": [],  # 修改文件上的复杂度问题（仅报告）
        "tool": "clippy",
        "threshold": threshold,
    }

    added_files = get_added_files(repo_root)
    changed_files = get_changed_files(repo_root)

    if verbose:
        print(f"  新增文件 {len(added_files)} 个，修改文件 {len(changed_files) - len(added_files)} 个")

    # 无新增 Rust 文件时跳过 (减少 clippy 全量跑成本)
    rust_added = [f for f in added_files if f.endswith(".rs")]
    if not rust_added:
        if verbose:
            print("  无新增 Rust 文件，跳过复杂度检查")
        return result

    try:
        clippy_result = subprocess.run(
            [
                "cargo", "clippy", "--message-format=json", "--all-targets",
                "--", "-W", "clippy::cyclomatic_complexity",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except FileNotFoundError:
        result["issues"].append({
            "file": "clippy", "line": 0, "column": 0, "level": "error",
            "code": "not_found",
            "message": "cargo/clippy 未安装，无法执行复杂度检查",
        })
        return result
    except subprocess.TimeoutExpired:
        result["issues"].append({
            "file": "clippy", "line": 0, "column": 0, "level": "error",
            "code": "timeout",
            "message": "clippy 复杂度检查超时 (>10 分钟)",
        })
        return result

    for line in (clippy_result.stdout or "").split("\n"):
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if msg.get("reason") != "compiler-message":
            continue

        diagnostic = msg.get("message", {})
        # 只关心 cyclomatic_complexity lint
        code = diagnostic.get("code", {}).get("code", "")
        if "cyclomatic_complexity" not in code:
            continue

        spans = diagnostic.get("spans", [])
        if not spans:
            continue
        primary = next((s for s in spans if s.get("is_primary")), spans[0])

        filepath = primary.get("file_name", "")
        line_num = primary.get("line_start", 0)
        if not filepath or filepath.endswith(".rs") is False:
            continue
        if filepath.startswith("/") or "target/" in filepath:
            continue

        rel_path = Path(filepath).as_posix()
        if matches_ignore_patterns(rel_path, ignore_paths):
            continue

        message_text = diagnostic.get("message", "")
        # 提取复杂度数值，如 "cyclomatic complexity of `fn` (N)" 或 lint 消息格式
        complexity_val = extract_complexity(message_text)

        issue = {
            "file": rel_path,
            "line": line_num,
            "column": primary.get("column_start", 0),
            "level": "warning",
            "code": code,
            "message": message_text,
            "complexity": complexity_val,
            "function": extract_function_name(message_text),
        }

        if rel_path in added_files:
            # 新增文件: 超阈值阻塞
            if complexity_val is not None and complexity_val > threshold:
                issue["level"] = "error"
                result["issues"].append(issue)
                result["blocking"] = True
            else:
                # clippy 已报警告意味着超 clippy 阈值；保守处理
                result["issues"].append(issue)
                result["blocking"] = True
        else:
            # 修改文件: 仅报告
            result["report_only_issues"].append(issue)

    if verbose:
        print(f"  复杂度问题: {len(result['issues'])} 阻塞 / {len(result['report_only_issues'])} 报告")
    return result


def check_python_crap_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
    crap_threshold: int = CRAP_THRESHOLD,
) -> dict[str, Any]:
    """Python CRAP/圈复杂度报告 (阶段一仅报告，不阻塞)

    用 radon cc -s -j 输出全量函数复杂度，标记超过 CRAP 阈值的函数。
    覆盖率数据接入后计算 CRAP = CC^2 * (1-Cov)^3 + CC。
    """
    if ignore_paths is None:
        ignore_paths = []

    result = {
        "blocking": False,  # 阶段一不阻塞
        "issues": [],       # 超阈值函数 (仅报告)
        "report_only_issues": [],
        "tool": "radon",
        "threshold": crap_threshold,
        "note": "阶段一 CRAP 仅报告，阶段三启用阻塞",
    }

    try:
        radon_result = subprocess.run(
            ["radon", "cc", "-s", "-j", "."],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        if verbose:
            print("  radon 未安装，跳过 CRAP 报告 (pip install radon)")
        return result
    except subprocess.TimeoutExpired:
        return result

    try:
        data = json.loads(radon_result.stdout or "{}")
    except json.JSONDecodeError:
        return result

    for filepath, funcs in data.items():
        rel_path = Path(filepath).as_posix()
        if matches_ignore_patterns(rel_path, ignore_paths):
            continue

        for fn in funcs:
            cc = fn.get("complexity", 0)
            # 无覆盖率数据时按 Cov=0 的 CRAP 下限近似 (仅报告参考)
            if cc >= crap_threshold:
                result["report_only_issues"].append({
                    "file": rel_path,
                    "line": fn.get("lineno", 0),
                    "column": 0,
                    "level": "info",
                    "code": "CRAP",
                    "message": (
                        f"函数 {fn.get('name', '?')} 圈复杂度 {cc} "
                        f"(阈值 {crap_threshold})，CRAP 报告项"
                    ),
                    "complexity": cc,
                    "function": fn.get("name", ""),
                })
                result["issues"].append(result["report_only_issues"][-1])

    if verbose:
        print(f"  CRAP 报告: {len(result['issues'])} 个函数超复杂度阈值 (仅报告)")
    return result


def extract_complexity(message: str) -> int | None:
    """从 clippy 消息文本中提取复杂度数字"""
    import re
    m = re.search(r"\((\d+)\)", message)
    if m:
        return int(m.group(1))
    # 备选: "complexity of N"
    m = re.search(r"complexity of (\d+)", message)
    if m:
        return int(m.group(1))
    return None


def extract_function_name(message: str) -> str:
    """从 clippy 消息文本中提取函数名（反引号包裹，找不到返回空串）

    clippy cyclomatic_complexity 消息常见格式:
      "cyclomatic complexity of `run_pipeline` (12)"
      "the function `process_item` has a cyclomatic complexity of 15"
    """
    import re
    m = re.search(r"`([^`]+)`", message)
    if m:
        return m.group(1)
    return ""
