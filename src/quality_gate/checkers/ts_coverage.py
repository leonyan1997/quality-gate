"""TypeScript 覆盖率增量检查器

基于 vitest --coverage (json-summary) 输出，检查新增文件的覆盖率是否 > 0%。
共享判定骨架在 coverage_common.py；本文件只留差异点: 报告定位/生成、
JSON 装载、路径匹配。

数据源:
  - 已有 coverage/coverage-summary.json → 直接解析
  - 缺失 → 运行 `npx vitest run --coverage --coverage.reporter=json-summary`
    生成后解析

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
    "**/*.d.ts", "**/*.test.ts", "**/*.spec.ts",
    "**/*.test.tsx", "**/*.spec.tsx",
    "**/generated/**", "**/dist/**", "**/node_modules/**",
]


def _find_coverage_summary(repo_root: Path) -> Path | None:
    """定位 coverage-summary.json（vitest 默认输出在 coverage/ 下）"""
    candidates = [
        repo_root / "coverage" / "coverage-summary.json",
        repo_root / "coverage-summary.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _load_summary(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "total" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _file_covered(data: dict[str, Any], rel_path: str,
                  repo_root: Path) -> tuple[int, int] | None:
    """取文件的 (covered 行数, 总行数)；文件不在报告中返回 None

    vitest coverage-v8 的 json-summary 用**绝对路径**作 key
    (如 /repo/web/src/App.vue)，而 git diff/untracked 给的是相对路径。
    依次尝试: 相对路径 → ./相对路径 → 绝对路径 → 后缀匹配（兜底）。
    """
    candidates = [
        rel_path,
        "./" + rel_path,
        str(repo_root / rel_path),
    ]
    entry = None
    for cand in candidates:
        got = data.get(cand)
        if isinstance(got, dict):
            entry = got
            break
    if entry is None:
        # 兜底: v8 报告 key 与 rel_path 基准不一致时按后缀匹配
        # （覆盖 key 绝对/仓库根相对混用场景）
        for key, got in data.items():
            if isinstance(got, dict) and key.endswith("/" + rel_path):
                entry = got
                break
    if not isinstance(entry, dict):
        return None
    lines = entry.get("lines") or {}
    total = lines.get("total", 0)
    covered = lines.get("covered", 0)
    return covered, total


def check_ts_coverage_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
) -> dict[str, Any]:
    """执行 TS 覆盖率增量检查（新增文件 >0%）"""
    if ignore_paths is None:
        ignore_paths = _DEFAULT_IGNORE

    result: dict[str, Any] = {
        "blocking": False, "issues": [], "coverage_data": {}, "skipped": None,
    }

    summary_path = _ensure_coverage_summary(repo_root, verbose, result)
    if summary_path is None:
        return result  # skipped / timeout 已在 result 记录

    data = _load_summary(summary_path)
    if data is None:
        result["blocking"] = True
        result["issues"].append({
            "file": "coverage-summary.json", "line": 0, "column": 0,
            "level": "error", "code": "parse_error",
            "message": "coverage-summary.json 解析失败",
        })
        return result

    scan_added_files_zero_coverage(
        repo_root,
        verbose=verbose,
        scan=CoverageScan(
            extensions=(".ts", ".tsx", ".vue", ".js"),
            lookup=lambda rel: _file_covered(data, rel, repo_root),
            ignore_paths=ignore_paths,
        ),
        result=result,
    )
    return result


def _ensure_coverage_summary(
    repo_root: Path, verbose: bool, result: dict[str, Any],
) -> Path | None:
    """定位 coverage-summary.json；缺失则尝试生成（需要 vitest）

    返回报告路径；None 表示跳过或超时（已写入 result.skipped / blocking）。
    """
    summary_path = _find_coverage_summary(repo_root)
    if summary_path is not None:
        return summary_path

    if verbose:
        print("  未找到 coverage-summary.json，运行 vitest --coverage...")
    if not (shutil.which("vitest") or shutil.which("npx")):
        result["skipped"] = "vitest/npx 不可用且无 coverage-summary.json"
        if verbose:
            print(f"    跳过 TS 覆盖率检查: {result['skipped']}")
        return None
    try:
        proc = subprocess.run(
            ["npx", "vitest", "run", "--coverage",
             "--coverage.reporter=json-summary"],
            cwd=repo_root, capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        result["blocking"] = True
        result["issues"].append({
            "file": "vitest", "line": 0, "column": 0, "level": "error",
            "code": "timeout", "message": "vitest --coverage 执行超时 (>15 分钟)",
        })
        return None

    summary_path = _find_coverage_summary(repo_root)
    if summary_path is None:
        result["skipped"] = (f"vitest 未产出 coverage-summary.json"
                             f" (exit {proc.returncode})")
        if verbose:
            print(f"    跳过 TS 覆盖率检查: {result['skipped']}")
    return summary_path
