"""重复代码增量检测器

使用 jscpd + git diff 行过滤，只阻塞新增行上的重复代码。
语言无关（jscpd 支持多语言）。

结构（2026-09-05 自身债务清理 C1）：把原先 254 行单体编排拆成单一职责
链路 —— 运行 jscpd → 解析 JSON → 路径归一 → diff 交集 → 阈值判定；
检测参数（threshold/min_tokens/min_lines/ignore_paths）收拢进 DupOptions。
"""

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .git_diff import (
    get_git_diff_lines,
    get_total_added_lines,
    is_line_in_diff,
)

# jscpd 默认忽略（调用方未显式给 ignore_paths 时）
DEFAULT_IGNORE_PATTERNS: list[str] = [
    "node_modules/**", "target/**", ".venv/**", "dist/**",
    "build/**", "generated/**", "report/**", "coverage/**",
    ".quality-gate/**",
]


@dataclass
class DupOptions:
    """重复检测参数集合（threshold/min_tokens/min_lines/ignore_paths 收拢）"""

    threshold: float = 3.0
    min_tokens: int = 50
    min_lines: int = 5
    ignore_paths: list[str] | None = None  # None = DEFAULT_IGNORE_PATTERNS


def _resolved_ignore_paths(options: DupOptions) -> list[str]:
    if options.ignore_paths is not None:
        return options.ignore_paths
    return list(DEFAULT_IGNORE_PATTERNS)


def _jscpd_error(code: str, message: str) -> dict[str, Any]:
    """jscpd 工具级错误结果（未安装/超时/输出解析失败）"""
    return {
        "blocking": True,
        "issues": [{
            "file": "jscpd",
            "line": 0,
            "column": 0,
            "level": "error",
            "code": code,
            "message": message,
        }],
        "duplication_rate": 0.0,
        "total_added_lines": 0,
        "duplicated_added_lines": 0,
    }


def check_duplication_incremental(
    repo_root: Path,
    *,
    verbose: bool = False,
    options: DupOptions | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """执行重复代码检测

    full=False (默认): 增量模式，jscpd + git diff 行过滤，只阻塞新增行上的重复。
    full=True: 全仓扫描模式（scan 周报用），报告全部重复块，不按 diff 过滤、不阻塞。
    options: DupOptions（缺省 = 内置默认阈值/忽略）。
    """
    opts = options if options is not None else DupOptions()
    click_echo = __import__('click').echo if verbose else lambda x: None

    # 增量模式无新增行则跳过（避免空跑 jscpd）；全仓扫描模式总是跑
    total_added = get_total_added_lines(repo_root)
    if total_added == 0 and not full:
        click_echo("  无新增行，跳过重复检测")
        return _empty_result(full)

    click_echo("  运行 jscpd...")
    report, error = _collect_jscpd_report(repo_root, opts)
    if error is not None:
        return error

    click_echo("  解析 jscpd 输出...")
    duplications = report.get("duplicates", [])
    click_echo("  计算新增重复行...")

    if full:
        return _full_scan_result(duplications, repo_root, verbose)
    return _incremental_result(
        duplications, repo_root, total_added, opts, verbose,
    )


def _empty_result(full: bool) -> dict[str, Any]:
    """无新增行/无重复时的空结果骨架"""
    return {
        "blocking": False,
        "issues": [],
        "duplication_rate": 0.0,
        "total_added_lines": 0,
        "duplicated_added_lines": 0,
        "scan_mode": full,
    }


def _full_scan_result(
    duplications: list[dict], repo_root: Path, verbose: bool,
) -> dict[str, Any]:
    """全仓模式：全部重复块都报告（不按 diff 交集、不阻塞）"""
    click_echo = __import__('click').echo if verbose else lambda x: None
    issues = _full_scan_issues(duplications, repo_root)
    if verbose:
        click_echo(f"  全仓重复块: {len(issues)} 处")
    result = _empty_result(full=True)
    result["issues"] = issues
    return result


def _incremental_result(
    duplications: list[dict],
    repo_root: Path,
    total_added: int,
    options: DupOptions,
    verbose: bool,
) -> dict[str, Any]:
    """增量模式结果组装：diff 交集 + 占比 + 阈值判定"""
    click_echo = __import__('click').echo if verbose else lambda x: None
    issues, duplicated_added_lines = _incremental_issues(duplications, repo_root)

    rate = (duplicated_added_lines / total_added) * 100 if total_added > 0 else 0.0
    result = _empty_result(full=False)
    result["issues"] = issues
    result["duplicated_added_lines"] = duplicated_added_lines
    result["total_added_lines"] = total_added
    result["duplication_rate"] = round(rate, 2)

    if rate > options.threshold:
        result["blocking"] = True
        result["issues"].append({
            "file": "jscpd",
            "line": 0,
            "column": 0,
            "level": "error",
            "code": "duplication_threshold",
            "message": f"新增重复率 {rate:.2f}% 超过阈值 {options.threshold}% "
                       f"(新增 {duplicated_added_lines}/{total_added} 行重复)",
        })

    if verbose:
        click_echo(
            f"  新增重复率：{rate:.2f}% "
            f"({duplicated_added_lines}/{total_added} 行)",
        )
        if result["blocking"]:
            click_echo(f"  ❌ 超过阈值 {options.threshold}%")
    return result


def _collect_jscpd_report(
    repo_root: Path, options: DupOptions,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """运行 jscpd 并把 JSON 输出解析成报告 dict

    返回 (report, None) 成功；(None, error_result) 失败。
    jscpd 报告输出到临时目录，不污染工作区/新增行统计；目录无论成败都清理。
    """
    jscpd_out_dir = tempfile.mkdtemp(prefix="qg-jscpd-")
    jscpd_args = [
        "jscpd",
        "--reporters", "json",
        "--output", jscpd_out_dir,
        "--min-tokens", str(options.min_tokens),
        "--min-lines", str(options.min_lines),
        "--ignore", ",".join(_resolved_ignore_paths(options)),
        str(repo_root),
    ]
    try:
        try:
            jscpd_result = subprocess.run(
                jscpd_args,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except FileNotFoundError:
            return None, _jscpd_error(
                "not_found", "jscpd 未安装，请先安装：npm install -g jscpd",
            )
        except subprocess.TimeoutExpired:
            return None, _jscpd_error("timeout", "jscpd 执行超时 (>5 分钟)")

        report = _parse_jscpd_output(jscpd_result.stdout, jscpd_out_dir, repo_root)
        if report is None:
            return None, _jscpd_error("parse_error", "jscpd 输出解析失败")
        return report, None
    finally:
        shutil.rmtree(jscpd_out_dir, ignore_errors=True)


def _parse_jscpd_output(stdout: str, out_dir: str, repo_root: Path) -> dict[str, Any] | None:
    """优先解析 stdout；jscpd JSON reporter 实际写文件则回落文件"""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    report_file = Path(out_dir) / "jscpd-report.json"
    if not report_file.exists():
        report_file = repo_root / "report" / "jscpd-report.json"  # 旧版兜底
    if report_file.exists():
        with open(report_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _normalize_side_path(name: str, repo_root: Path) -> str | None:
    """jscpd firstFile/secondFile.name → 相对 repo_root 的 posix 路径

    jscpd v4 JSON reporter 的 name 形如 ``<path>:<format>``
    （实测: ``docs/a.md:markdown``、``src/x.ts:typescript``）——
    冒号后缀是检测出的代码格式，不剥离会令 git ls-files 查不到该
    文件 → 误判为 untracked → 存量重复被全文件当作"新增"阻塞。

    剥离规则: 末段冒号后为纯字母数字（格式名）才剥离；Windows 盘符
    （``C:\\...``）末段含分隔符/非字母数字，不受影响。
    """
    if not name:
        return None
    path_part = name
    candidate, _, fmt = name.rpartition(":")
    if candidate and fmt.isalnum() and len(fmt) <= 20:
        path_part = candidate
    p = Path(path_part)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            # 仓库外文件不参与 diff 判定
            return None
    return p.as_posix()


def _full_scan_issues(duplications: list[dict], repo_root: Path) -> list[dict]:
    """全仓模式：全部重复块都报告（不按 diff 交集、不阻塞）"""
    issues: list[dict] = []
    for dup in duplications:
        dup_lines = dup.get("lines", 0)
        fragment = dup.get("fragment", "")
        seen_sides: set[tuple[str, int, int]] = set()
        for side in ("firstFile", "secondFile"):
            file_info = dup.get(side)
            if not isinstance(file_info, dict):
                continue
            rel_path = _normalize_side_path(str(file_info.get("name", "")), repo_root)
            dup_start = file_info.get("start", 0)
            dup_end = file_info.get("end", 0)
            if rel_path is None or dup_start == 0:
                continue
            key = (rel_path, dup_start, dup_end)
            if key in seen_sides:
                continue
            seen_sides.add(key)
            issues.append({
                "file": rel_path,
                "line": dup_start,
                "column": 0,
                "level": "warning",
                "code": "duplication",
                "message": f"重复代码块 ({dup_lines} 行, {fragment[:60]!r}...)",
            })
    return issues


def _incremental_issues(
    duplications: list[dict], repo_root: Path,
) -> tuple[list[dict], int]:
    """增量模式：重复块任一侧位于 diff 新增范围即计入

    返回 (issues, duplicated_added_lines)。diff 行范围按文件缓存，
    避免同一文件多个重复块重复跑 git。
    """
    issues: list[dict] = []
    duplicated_added_lines = 0
    diff_ranges_cache: dict[str, list[tuple[int, int]]] = {}

    for dup in duplications:
        # jscpd v4 真实结构: {firstFile: {name,start,end}, secondFile: {...},
        #                      fragment, format, lines, tokens}
        dup_lines = dup.get("lines", 0)
        for side in ("firstFile", "secondFile"):
            file_info = dup.get(side)
            if not isinstance(file_info, dict):
                continue
            rel_path = _normalize_side_path(str(file_info.get("name", "")), repo_root)
            dup_start = file_info.get("start", 0)
            dup_end = file_info.get("end", 0)
            if rel_path is None or dup_start == 0:
                continue

            if rel_path not in diff_ranges_cache:
                diff_ranges_cache[rel_path] = get_git_diff_lines(repo_root, rel_path)

            overlap = _count_overlap(dup_start, dup_end, diff_ranges_cache[rel_path])
            if overlap > 0:
                duplicated_added_lines += overlap
                issues.append({
                    "file": rel_path,
                    "line": dup_start,
                    "column": 0,
                    "level": "warning",
                    "code": "duplication",
                    "message": f"重复代码块 ({dup_lines} 行)，{overlap} 行在新增范围内",
                })
    return issues, duplicated_added_lines


def _count_overlap(start: int, end: int, diff_ranges: list[tuple[int, int]]) -> int:
    """重复块 [start, end] 与 diff 新增行范围的交叠行数"""
    overlap = 0
    for line in range(start, end + 1):
        if is_line_in_diff(line, diff_ranges):
            overlap += 1
    return overlap
