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

    stdout, error_issue = _run_clippy_complexity(repo_root)
    if error_issue is not None:
        result["issues"].append(error_issue)
        return result

    for issue in _complexity_issues(stdout, repo_root, ignore_paths):
        if issue["file"] in added_files:
            # 新增文件: 超阈值阻塞；clippy 报警即超其配置阈值 → 保守阻塞
            if issue["complexity"] is not None and issue["complexity"] > threshold:
                issue["level"] = "error"
            result["issues"].append(issue)
            result["blocking"] = True
        else:
            # 修改/存量文件: 仅报告
            result["report_only_issues"].append(issue)

    if verbose:
        print(f"  复杂度问题: {len(result['issues'])} 阻塞 / {len(result['report_only_issues'])} 报告")
    return result


def _run_clippy_complexity(
    repo_root: Path,
) -> tuple[str | None, dict[str, Any] | None]:
    """运行 clippy cyclomatic_complexity；成功 (stdout, None)，失败 (None, issue)"""
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
        return None, {
            "file": "clippy", "line": 0, "column": 0, "level": "error",
            "code": "not_found",
            "message": "cargo/clippy 未安装，无法执行复杂度检查",
        }
    except subprocess.TimeoutExpired:
        return None, {
            "file": "clippy", "line": 0, "column": 0, "level": "error",
            "code": "timeout",
            "message": "clippy 复杂度检查超时 (>10 分钟)",
        }
    return clippy_result.stdout, None


def _complexity_issues(
    stdout: str,
    repo_root: Path,
    ignore_paths: list[str],
) -> list[dict[str, Any]]:
    """clippy JSON-Lines → 复杂度 issue 列表（仅 cyclomatic_complexity lint）"""
    issues: list[dict[str, Any]] = []
    for line in (stdout or "").split("\n"):
        issue = _parse_complexity_line(line, repo_root, ignore_paths)
        if issue is not None:
            issues.append(issue)
    return issues


def _parse_complexity_line(
    line: str, repo_root: Path, ignore_paths: list[str],
) -> dict[str, Any] | None:
    """单行 JSON-Lines → complexity issue；非相关行返回 None"""
    if not line.strip():
        return None
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return None
    if msg.get("reason") != "compiler-message":
        return None

    diagnostic = msg.get("message", {})
    # 只关心 cyclomatic_complexity lint
    code = diagnostic.get("code", {}).get("code", "")
    if "cyclomatic_complexity" not in code:
        return None

    spans = diagnostic.get("spans", [])
    if not spans:
        return None
    primary = next((s for s in spans if s.get("is_primary")), spans[0])

    filepath = primary.get("file_name", "")
    line_num = primary.get("line_start", 0)
    if not filepath or filepath.endswith(".rs") is False:
        return None
    if filepath.startswith("/") or "target/" in filepath:
        return None

    rel_path = Path(filepath).as_posix()
    if matches_ignore_patterns(rel_path, ignore_paths):
        return None

    message_text = diagnostic.get("message", "")
    return {
        "file": rel_path,
        "line": line_num,
        "column": primary.get("column_start", 0),
        "level": "warning",
        "code": code,
        "message": message_text,
        "complexity": extract_complexity(message_text),
        "function": extract_function_name(message_text),
    }


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

    data = _run_radon(repo_root, verbose)
    if data is None:
        return result  # radon 缺失/超时/解析失败 → 无数据返回空

    issues = _crap_report_issues(data, repo_root, ignore_paths, crap_threshold)
    result["report_only_issues"] = issues
    result["issues"] = list(issues)  # issues/report_only 同内容（阶段一只报告）

    if verbose:
        print(f"  CRAP 报告: {len(result['issues'])} 个函数超复杂度阈值 (仅报告)")
    return result


def _run_radon(repo_root: Path, verbose: bool) -> dict[str, Any] | None:
    """运行 radon cc -s -j；成功返回解析后的数据 dict，失败/无数据返回 None"""
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
        return None
    except subprocess.TimeoutExpired:
        return None

    try:
        data = json.loads(radon_result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _crap_report_issues(
    data: dict[str, Any],
    repo_root: Path,
    ignore_paths: list[str],
    crap_threshold: int,
) -> list[dict[str, Any]]:
    """radon 输出 → 超阈值函数报告项（issues/report_only 共用内容）"""
    issues: list[dict[str, Any]] = []
    for filepath, funcs in data.items():
        rel_path = Path(filepath).as_posix()
        if matches_ignore_patterns(rel_path, ignore_paths):
            continue

        for fn in funcs:
            cc = fn.get("complexity", 0)
            # 无覆盖率数据时按 Cov=0 的 CRAP 下限近似 (仅报告参考)
            if cc >= crap_threshold:
                issues.append({
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
    return issues


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
