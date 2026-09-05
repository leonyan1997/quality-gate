"""Rust 覆盖率检查器

基于 cargo-tarpaulin JSON 输出，检查新增文件的覆盖率是否 > 0%。
共享判定骨架在 coverage_common.py；本文件只留差异点: tarpaulin 运行、
trace 统计解析（coverage_data 全量收录）、路径后缀匹配。
"""

import json
import subprocess
from pathlib import Path
from typing import Any

from .coverage_common import CoverageScan, scan_added_files_zero_coverage

_COVERAGE_EXTENSIONS = (".rs",)


def check_rust_coverage_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
) -> dict[str, Any]:
    """执行 Rust 覆盖率增量检查

    1. 运行 cargo-tarpaulin --out json
    2. 解析 JSON 输出 → coverage_data（全报告收录）
    3. 检查新增文件的覆盖率是否 > 0%

    返回:
        {
            "blocking": bool,
            "issues": [...],
            "coverage_data": {...},
        }
    """
    if ignore_paths is None:
        ignore_paths = []

    result = {
        "blocking": False,
        "issues": [],
        "coverage_data": {},
    }

    if verbose:
        print("  运行 cargo-tarpaulin...")
    stdout, error = _run_tarpaulin(repo_root)
    if error is not None:
        return error

    if verbose:
        print("  解析覆盖率数据...")
    coverage_data, parse_error = _parse_tarpaulin_report(stdout)
    if parse_error is not None:
        return parse_error
    result["coverage_data"] = coverage_data

    if verbose:
        print("  检查新增文件覆盖率...")
    scan_added_files_zero_coverage(
        repo_root,
        verbose=verbose,
        scan=CoverageScan(
            extensions=_COVERAGE_EXTENSIONS,
            lookup=lambda rel: _find_in_coverage_data(coverage_data, rel),
            ignore_paths=ignore_paths,
            record_coverage=False,   # coverage_data 已全量收录，骨架不再覆盖
            print_unrecorded=False,  # rust 语义: 未收录文件静默跳过
        ),
        result=result,
    )
    return result


def _run_tarpaulin(
    repo_root: Path,
) -> tuple[str | None, dict[str, Any] | None]:
    """运行 cargo-tarpaulin；成功返回 (stdout, None)，失败 (None, error_result)"""
    try:
        tarpaulin_result = subprocess.run(
            ["cargo", "tarpaulin", "--out", "json", "--output-dir", str(repo_root)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return None, {
            "blocking": True,
            "issues": [{
                "file": "tarpaulin", "line": 0, "column": 0, "level": "error",
                "code": "timeout", "message": "cargo-tarpaulin 执行超时 (>10 分钟)",
            }],
            "coverage_data": {},
        }
    except Exception as e:
        return None, {
            "blocking": True,
            "issues": [{
                "file": "tarpaulin", "line": 0, "column": 0, "level": "error",
                "code": "execution_error",
                "message": f"cargo-tarpaulin 执行失败：{e}",
            }],
            "coverage_data": {},
        }
    return tarpaulin_result.stdout, None


def _parse_tarpaulin_report(
    stdout: str,
) -> tuple[dict[str, dict], dict[str, Any] | None]:
    """解析 tarpaulin JSON → coverage_data（全量收录，covered=是否有点击）

    tarpaulin JSON 顶层可能是文件映射或含 "coverage" 键。
    返回 (coverage_data, None) 成功；(None, parse_error 结果) 失败。
    """
    try:
        coverage_json = json.loads(stdout)
    except json.JSONDecodeError:
        return None, {
            "blocking": True,
            "issues": [{
                "file": "tarpaulin", "line": 0, "column": 0, "level": "error",
                "code": "parse_error", "message": "tarpaulin 输出解析失败",
            }],
            "coverage_data": {},
        }

    file_map = coverage_json.get("coverage", coverage_json) \
        if isinstance(coverage_json, dict) else coverage_json

    coverage_data: dict[str, dict] = {}
    for filepath, file_data in file_map.items():
        if "target/" in filepath:
            continue
        if not isinstance(file_data, dict):
            continue

        total_lines, has_coverage = _count_traces(file_data.get("traces", []))
        if total_lines > 0:
            coverage_data[filepath] = {
                "covered": has_coverage,
                "total_lines": total_lines,
            }
    return coverage_data, None


def _count_traces(traces: list) -> tuple[int, bool]:
    """汇总 traces 统计: (总行数, 是否有覆盖行)"""
    total_lines = 0
    has_coverage = False
    for trace in traces:
        stats = trace.get("stats", {})
        line_count = stats.get("Line", 0)
        covered_count = stats.get("Covered", 0)
        if line_count > 0:
            total_lines += line_count
            if covered_count > 0:
                has_coverage = True
    return total_lines, has_coverage


def _find_in_coverage_data(
    coverage_data: dict[str, dict], rel_path: str,
) -> tuple[int, int] | None:
    """在 coverage_data 中按后缀匹配新增文件（绝对/相对混用）

    返回 (covered 行数, 总行数)；未收录返回 None。
    """
    for cov_path, cov_data in coverage_data.items():
        if cov_path.endswith(rel_path):
            covered = 1 if cov_data.get("covered") else 0
            return covered, cov_data.get("total_lines", 0)
    return None
