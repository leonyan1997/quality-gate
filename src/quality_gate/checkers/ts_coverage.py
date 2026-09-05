"""TypeScript 覆盖率增量检查器

基于 vitest --coverage (json-summary) 输出，检查新增文件的覆盖率是否 > 0%。
与 rust_coverage.py 同模式；在 web/ 等项目目录内运行:
    quality-gate check --lang ts --checks coverage

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

from .git_diff import get_added_files, matches_ignore_patterns


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
        ignore_paths = [
            "**/*.d.ts", "**/*.test.ts", "**/*.spec.ts",
            "**/*.test.tsx", "**/*.spec.tsx",
            "**/generated/**", "**/dist/**", "**/node_modules/**",
        ]

    result: dict[str, Any] = {
        "blocking": False, "issues": [], "coverage_data": {}, "skipped": None,
    }

    summary_path = _find_coverage_summary(repo_root)
    if summary_path is None:
        # 尝试生成: 需要 vitest
        if verbose:
            print("  未找到 coverage-summary.json，运行 vitest --coverage...")
        if not (shutil.which("vitest") or shutil.which("npx")):
            result["skipped"] = "vitest/npx 不可用且无 coverage-summary.json"
            if verbose:
                print(f"    跳过 TS 覆盖率检查: {result['skipped']}")
            return result
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
            return result
        summary_path = _find_coverage_summary(repo_root)
        if summary_path is None:
            result["skipped"] = (f"vitest 未产出 coverage-summary.json"
                                 f" (exit {proc.returncode})")
            if verbose:
                print(f"    跳过 TS 覆盖率检查: {result['skipped']}")
            return result

    data = _load_summary(summary_path)
    if data is None:
        result["blocking"] = True
        result["issues"].append({
            "file": "coverage-summary.json", "line": 0, "column": 0,
            "level": "error", "code": "parse_error",
            "message": "coverage-summary.json 解析失败",
        })
        return result

    added_files = get_added_files(repo_root)
    for filepath in sorted(added_files):
        if not (filepath.endswith((".ts", ".tsx", ".vue", ".js"))):
            continue
        if matches_ignore_patterns(filepath, ignore_paths):
            if verbose:
                print(f"    跳过 (allowlist): {filepath}")
            continue

        cov = _file_covered(data, filepath, repo_root)
        if cov is None:
            if verbose:
                print(f"    未收录 (测试未触及): {filepath}")
            continue
        covered, total = cov
        if total == 0:
            continue
        result["coverage_data"][filepath] = {"covered": covered, "total_lines": total}
        if covered == 0:
            result["issues"].append({
                "file": filepath, "line": 0, "column": 0, "level": "error",
                "code": "zero_coverage",
                "message": f"新增文件 {filepath} 覆盖率为 0%，请补充测试",
            })
            result["blocking"] = True

    if verbose:
        print(f"  检查 {len(added_files)} 个新增文件，发现 {len(result['issues'])} 个零覆盖率问题")
    return result
