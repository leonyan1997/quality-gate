"""Python 覆盖率增量检查器

基于 coverage.py JSON 输出，检查新增文件的覆盖率是否 > 0%。
共享判定骨架在 coverage_common.py；本文件只留差异点: 报告定位/生成、
JSON 装载、路径匹配。

数据源:
  - 已有 coverage/coverage.json → 直接解析
  - 缺失 → 尝试 `coverage run -m pytest -q` + `coverage json` 生成

返回:
    {"blocking": bool, "issues": [...], "coverage_data": {...}, "skipped": str | None}
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .coverage_common import CoverageScan, scan_added_files_zero_coverage

_DEFAULT_IGNORE = [
    "**/models.py", "**/schemas.py", "**/constants.py",
    "**/migrations/**", "**/generated/**", "**/test_*.py",
    "**/*_test.py", "**/tests/**", "**/.venv/**", "**/venv/**",
]


def _find_coverage_json(repo_root: Path) -> Path | None:
    candidates = [
        repo_root / "coverage" / "coverage.json",
        repo_root / "coverage.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _load_coverage(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "files" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _file_covered(data: dict[str, Any], repo_root: Path, rel_path: str) -> tuple[int, int] | None:
    """取文件的 (covered 行数, 总行数)；文件不在报告中返回 None

    coverage.json 的 key 是运行时的绝对路径，需兼容两种形态。
    """
    files = data.get("files") or {}
    entry = files.get(rel_path)
    if entry is None:
        # 绝对路径 key
        abs_candidates = [
            str(repo_root / rel_path),
            str((repo_root / rel_path).resolve()),
        ]
        for cand in abs_candidates:
            if cand in files:
                entry = files[cand]
                break
        # 后缀匹配兜底（避免 /private 等前缀差异）
        if entry is None:
            for key, val in files.items():
                if key.endswith("/" + rel_path) or key == rel_path:
                    entry = val
                    break
    if not isinstance(entry, dict):
        return None
    summary = entry.get("summary") or {}
    total = summary.get("num_statements", 0)
    covered = summary.get("covered_lines", 0)
    if isinstance(covered, list):  # 兼容部分版本的 covered_lines 为列表
        covered = len(covered)
    return covered, total


def check_python_coverage_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
) -> dict[str, Any]:
    """执行 Python 覆盖率增量检查（新增文件 >0%）"""
    if ignore_paths is None:
        ignore_paths = _DEFAULT_IGNORE

    result: dict[str, Any] = {
        "blocking": False, "issues": [], "coverage_data": {}, "skipped": None,
    }

    cov_path = _ensure_coverage_report(repo_root, verbose, result)
    if cov_path is None:
        return result  # skipped / timeout 已在 result 记录

    data = _load_coverage(cov_path)
    if data is None:
        result["blocking"] = True
        result["issues"].append({
            "file": "coverage.json", "line": 0, "column": 0, "level": "error",
            "code": "parse_error", "message": "coverage.json 解析失败",
        })
        return result

    scan_added_files_zero_coverage(
        repo_root,
        verbose=verbose,
        scan=CoverageScan(
            extensions=(".py",),
            lookup=lambda rel: _file_covered(data, repo_root, rel),
            ignore_paths=ignore_paths,
        ),
        result=result,
    )
    return result


def _ensure_coverage_report(
    repo_root: Path, verbose: bool, result: dict[str, Any],
) -> Path | None:
    """定位 coverage.json；缺失则尝试生成

    返回报告路径；None 表示跳过或超时（已写入 result.skipped / blocking）。
    """
    cov_path = _find_coverage_json(repo_root)
    if cov_path is not None:
        return cov_path

    if verbose:
        print("  未找到 coverage.json，运行 coverage run -m pytest...")
    if not shutil.which("coverage"):
        result["skipped"] = "coverage 不可用且无 coverage.json"
        if verbose:
            print(f"    跳过 Python 覆盖率检查: {result['skipped']}")
        return None
    try:
        proc = subprocess.run(
            ["coverage", "run", "-m", "pytest", "-q"],
            cwd=repo_root, capture_output=True, text=True, timeout=900,
        )
        if verbose and proc.returncode != 0:
            print(f"    pytest 退出码 {proc.returncode}（测试失败但仍生成覆盖率）")
        subprocess.run(
            ["coverage", "json"],
            cwd=repo_root, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        result["blocking"] = True
        result["issues"].append({
            "file": "pytest", "line": 0, "column": 0, "level": "error",
            "code": "timeout", "message": "coverage run -m pytest 执行超时 (>15 分钟)",
        })
        return None

    cov_path = _find_coverage_json(repo_root)
    if cov_path is None:
        result["skipped"] = "coverage json 未产出 coverage.json"
        if verbose:
            print(f"    跳过 Python 覆盖率检查: {result['skipped']}")
    return cov_path
