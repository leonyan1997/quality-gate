"""复杂度 / CRAP 报告检查器

阶段一:
  - Rust / TS 新增文件: 全文件复杂度检查，超过阈值 (默认 15) 阻塞
  - Rust / TS 修改文件: 仅报告，不阻塞 (阶段三启用函数级阻塞)
  - Python (P1, 2026-09): CRAP/圈复杂度增量阻塞——
      check --diff 只卡 diff 内「新增 def」的超阈值函数；
      scan 周报保持整仓仅报告。

工具:
  - Rust: cargo clippy -- -W clippy::cyclomatic_complexity
    (需要项目配置 clippy.toml 的 cyclomatic-complexity-threshold)
  - TS/JS: npx eslint --rule 'complexity: [error, 15]' (需要 eslint)
  - Python: radon cc -s -j (复杂度) — CRAP 公式后续接入覆盖率
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .git_diff import (
    get_added_files,
    get_changed_files,
    get_git_diff_lines,
    is_line_in_diff,
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


@dataclass
class CrapOptions:
    """Python CRAP/复杂度增量检查差异点（同 DupOptions 收拢模式）"""

    threshold: int = CRAP_THRESHOLD
    full: bool = False        # True = scan 周报整仓仅报告；False = check --diff 增量阻塞
    function_ignore: list[str] | None = None


def check_python_crap_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
    options: CrapOptions | None = None,
) -> dict[str, Any]:
    """Python CRAP/圈复杂度增量检查（P1: 入口分发）

    options=None → CrapOptions()（增量阻塞语义）。分发到:
      - _check_crap_diff:  check --diff 增量门禁（只卡 diff 内新增 def）
      - _scan_crap_full:   scan 周报整仓仅报告（P1 前行为逐字节一致）

    注：无覆盖率数据时以 cc ≥ 阈值为 CRAP 近似（Cov=0 下限），
    覆盖率接入后公式 CRAP = CC^2 * (1-Cov)^3 + CC。
    """
    if options is None:
        options = CrapOptions()
    if ignore_paths is None:
        ignore_paths = []
    if options.full:
        return _scan_crap_full(repo_root, verbose, ignore_paths, options.threshold)
    return _check_crap_diff(repo_root, verbose, ignore_paths, options)


def _check_crap_diff(
    repo_root: Path, verbose: bool, ignore_paths: list[str], options: CrapOptions,
) -> dict[str, Any]:
    """check --diff 增量门禁：只卡 diff 内新增 def 的超阈值函数

    阻塞判定: def 行落在 diff 新增行内 ∧ cc ≥ threshold ∧ 函数体 ≥5 行
    （endline - lineno ≥ 5）∧ 函数名不在 function_ignore；
    被触及但非新增/体短的超阈值函数 → report_only（可见不阻塞）。
    无 Python 文件在 diff 中 → 不跑 radon，空结果不阻塞。
    """
    result = {
        "blocking": False,
        "issues": [],
        "report_only_issues": [],
        "tool": "radon",
        "threshold": options.threshold,
        "note": "增量阻塞：仅新增 def 且复杂度≥阈值（体≥5 行）阻塞；触及非新增仅报告",
    }

    py_files = sorted(
        f for f, _s in get_changed_files(repo_root).items()
        if f.endswith(".py") and not matches_ignore_patterns(f, ignore_paths)
    )
    if not py_files:
        if verbose:
            print("  无 Python 文件改动，跳过 CRAP/复杂度增量检查")
        return result

    data, skip_reason = _run_radon(repo_root, py_files)
    if data is None:
        # B 包语义：radon 查不了必须显式跳过（可见原因），不再静默空结果
        result["skipped"] = skip_reason
        if verbose:
            print(f"  跳过 CRAP 检查: {skip_reason}")
        return result

    issues, reports = _eval_diff_functions(
        data, repo_root, py_files, options,
    )
    result["issues"] = issues
    result["report_only_issues"] = reports
    result["blocking"] = bool(issues)

    if verbose:
        print(
            f"  CRAP 增量: {len(issues)} 阻塞 / {len(reports)} 报告 "
            f"(diff 内 {len(py_files)} 个 Python 文件)"
        )
    return result


def _eval_diff_functions(
    data: dict[str, Any], repo_root: Path, py_files: list[str],
    options: CrapOptions,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """radon 输出 → (阻塞 issues, 仅报告 items)

    diff 内文件的函数逐个判定；function_ignore 同名完全豁免（不上报）。
    """
    issues: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    ignore_names = set(options.function_ignore or [])

    for filepath, funcs in data.items():
        rel_path = Path(filepath).as_posix()
        if rel_path not in py_files:
            continue  # radon 键应为传入的相对路径；异常键忽略
        ranges = get_git_diff_lines(repo_root, rel_path)

        for fn in funcs:
            kind, issue = _classify_crap_fn(
                fn, rel_path, ranges, options.threshold, ignore_names,
            )
            if kind == "block":
                issues.append(issue)
            elif kind == "report":
                reports.append(issue)
    return issues, reports


def _classify_crap_fn(
    fn: dict[str, Any], rel_path: str, ranges: list[tuple[int, int]],
    threshold: int, ignore_names: set[str],
) -> tuple[str, dict[str, Any] | None]:
    """单函数判定：→ ("block", issue) / ("report", issue) / ("skip", None)"""
    name = fn.get("name", "")
    cc = fn.get("complexity", 0)
    if cc < threshold or name in ignore_names:
        return "skip", None

    lineno = fn.get("lineno", 0)
    endline = fn.get("endline")
    body_len = (endline - lineno) if endline else 0
    def_added = is_line_in_diff(lineno, ranges)
    touched = _ranges_overlap(ranges, lineno, endline or lineno)

    base = {
        "file": rel_path,
        "line": lineno,
        "column": 0,
        "code": "CRAP",
        "complexity": cc,
        "function": name,
    }
    if def_added and body_len >= 5:
        base["level"] = "error"
        base["message"] = (
            f"函数 {name} 圈复杂度 {cc} (阈值 {threshold})，"
            "请拆分或加入 function_ignore 挂账"
        )
        return "block", base
    if touched:
        base["level"] = "info"
        base["message"] = (
            f"函数 {name} 圈复杂度 {cc} (阈值 {threshold})，"
            "被本次改动触及（非新增，仅报告）"
        )
        return "report", base
    return "skip", None


def _scan_crap_full(
    repo_root: Path,
    verbose: bool,
    ignore_paths: list[str],
    crap_threshold: int,
) -> dict[str, Any]:
    """scan 周报路径：整仓全部超阈值函数仅报告（P1 前行为逐字节一致）"""
    result = {
        "blocking": False,
        "issues": [],
        "report_only_issues": [],
        "tool": "radon",
        "threshold": crap_threshold,
        "note": "scan 周报：整仓 CRAP 超阈值函数仅报告，不阻塞",
    }

    data, skip_reason = _run_radon(repo_root)
    if data is None:
        # B 包语义：radon 查不了必须显式跳过（可见原因），不再静默空结果
        result["skipped"] = skip_reason
        if verbose:
            print(f"  跳过 CRAP 报告: {skip_reason}")
        return result

    issues = _crap_report_issues(data, repo_root, ignore_paths, crap_threshold)
    result["report_only_issues"] = issues
    result["issues"] = list(issues)  # issues/report_only 同内容（仅报告）

    if verbose:
        print(f"  CRAP 报告: {len(result['issues'])} 个函数超复杂度阈值 (仅报告)")
    return result


def _ranges_overlap(
    ranges: list[tuple[int, int]], start: int, end: int,
) -> bool:
    """函数行区间 [start, end] 与任一 diff 新增行区间相交"""
    for lo, hi in ranges:
        if start <= hi and lo <= end:
            return True
    return False


def _run_radon(
    repo_root: Path, files: list[str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """运行 radon cc -s -j [files]

    files=None → 扫整仓 ("."，scan 周报用)；
    files=相对路径列表 → 只扫 diff 内文件（check --diff 增量用）。

    返回 (data, None) 成功；(None, skip_reason) 缺失/超时/解析失败——
    调用方把 skip_reason 写入 result["skipped"]，保证"没查到"对用户可见。
    """
    targets = files if files else ["."]
    try:
        radon_result = subprocess.run(
            ["radon", "cc", "-s", "-j", *targets],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        return None, "radon 未安装 (pip install radon)，跳过 CRAP 报告"
    except subprocess.TimeoutExpired:
        return None, "radon 执行超时 (>5 分钟)，跳过 CRAP 报告"

    try:
        data = json.loads(radon_result.stdout or "{}")
    except json.JSONDecodeError:
        return None, "radon 输出解析失败，跳过 CRAP 报告"
    if not isinstance(data, dict):
        return None, "radon 输出格式异常，跳过 CRAP 报告"
    return data, None


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
