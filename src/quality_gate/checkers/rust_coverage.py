"""Rust 覆盖率检查器

基于 cargo-tarpaulin JSON 输出，检查新增文件的覆盖率是否 > 0%。
"""

import json
import subprocess
from pathlib import Path
from typing import Any

from .git_diff import get_added_files, matches_ignore_patterns


def check_rust_coverage_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
) -> dict[str, Any]:
    """执行 Rust 覆盖率增量检查

    1. 运行 cargo-tarpaulin --out json
    2. 解析 JSON 输出
    3. 检查新增文件的覆盖率是否 > 0%
    4. allowlist 豁免纯数据类 (models/schemas/constants 等)

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

    # Step 1: 运行 tarpaulin
    try:
        tarpaulin_result = subprocess.run(
            ["cargo", "tarpaulin", "--out", "json", "--output-dir", str(repo_root)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {
            "blocking": True,
            "issues": [{
                "file": "tarpaulin",
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "timeout",
                "message": "cargo-tarpaulin 执行超时 (>10 分钟)"
            }],
            "coverage_data": {},
        }
    except Exception as e:
        return {
            "blocking": True,
            "issues": [{
                "file": "tarpaulin",
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "execution_error",
                "message": f"cargo-tarpaulin 执行失败：{e}"
            }],
            "coverage_data": {},
        }

    # Step 2: 解析 JSON 输出
    if verbose:
        print("  解析覆盖率数据...")

    try:
        coverage_json = json.loads(tarpaulin_result.stdout)
    except json.JSONDecodeError:
        return {
            "blocking": True,
            "issues": [{
                "file": "tarpaulin",
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "parse_error",
                "message": "tarpaulin 输出解析失败"
            }],
            "coverage_data": {},
        }

    # tarpaulin JSON 顶层可能是文件映射或含 "coverage" 键
    file_map = coverage_json.get("coverage", coverage_json) \
        if isinstance(coverage_json, dict) else coverage_json

    coverage_data: dict[str, dict] = {}
    for filepath, file_data in file_map.items():
        if "target/" in filepath:
            continue
        if not isinstance(file_data, dict):
            continue

        traces = file_data.get("traces", [])
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

        if total_lines > 0:
            coverage_data[filepath] = {
                "covered": has_coverage,
                "total_lines": total_lines,
            }

    # Step 3: 获取新增文件
    if verbose:
        print("  检查新增文件覆盖率...")
    added_files = get_added_files(repo_root)

    # Step 4: 检查新增文件覆盖率
    for filepath in sorted(added_files):
        # 只检查 Rust 文件
        if not filepath.endswith(".rs"):
            continue

        if matches_ignore_patterns(filepath, ignore_paths):
            if verbose:
                print(f"    跳过 (allowlist): {filepath}")
            continue

        # 查找覆盖率数据 (绝对路径匹配)
        file_coverage = None
        for cov_path, cov_data in coverage_data.items():
            if cov_path.endswith(filepath):
                file_coverage = cov_data
                break

        if file_coverage and not file_coverage["covered"]:
            result["issues"].append({
                "file": filepath,
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "zero_coverage",
                "message": f"新增文件 {filepath} 覆盖率为 0%，请补充测试",
            })
            result["blocking"] = True

    if verbose:
        print(f"  检查 {len(added_files)} 个新增文件，发现 {len(result['issues'])} 个零覆盖率问题")

    result["coverage_data"] = coverage_data
    return result
