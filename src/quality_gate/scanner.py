"""scan 全仓扫描（周报）

与 check 的差异：
  - check 是增量门禁（git diff 过滤、只卡新增问题、exit 1 阻塞）
  - scan 是全仓快照（不依赖 diff、报告全部存量问题、不阻塞、exit 0），
    把 JSON 报告存档到 history 目录，供周报趋势对比（CRAP/复杂度等）。

阶段二（2026-09）新增，语义依据方案 v2.0 §4.6（report.json 存档形成趋势）。

输出结构：
    {
        "schema_version": 1,
        "timestamp": "2026-09-04T15:00:00+08:00",
        "cwd": "/repo",
        "results": { ...与 check 相同但 full 模式... },
        "summary": { "lint_issues": N, "duplication_blocks": N, ... },
    }
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .checkers.complexity import (
    check_python_crap_incremental,
    check_rust_complexity_incremental,
)
from .checkers.dependency import check_dependency_incremental
from .checkers.duplication import check_duplication_incremental
from .checkers.python_lint import check_python_lint_incremental
from .checkers.rust_lint import check_rust_lint_incremental
from .checkers.smell import check_smell_incremental
from .checkers.ts_lint import check_ts_lint_incremental
from .config import QualityGateConfig

# 存档目录: <repo>/.quality-gate/history/scan-<timestamp>.json
HISTORY_SUBDIR = ".quality-gate/history"

# scan 周报默认检查: lint + duplication + dependency + complexity(CRAP) + smell
# 不含 coverage —— 覆盖率需要跑测试(重)，check 增量语义已覆盖
DEFAULT_SCAN_CHECKS = ["lint", "duplication", "dependency", "complexity", "smell"]


def _now_iso() -> str:
    """本地时区 ISO 时间戳（+08:00 展示用）"""
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="seconds")


def _count_issues(results: dict[str, Any]) -> int:
    """从 results 中统计 lint issues 总数（去 checks_run 等辅助键）"""
    total = 0
    for key, value in results.items():
        if key in ("checks_run", "duplication") or not isinstance(value, dict):
            continue
        for check_type, check_result in value.items():
            if not isinstance(check_result, dict):
                continue
            if check_result.get("skipped"):
                continue
            if check_type == "lint":
                total += len(check_result.get("issues", []))
    return total


def _count_duplication_blocks(results: dict[str, Any]) -> int:
    dup = results.get("duplication")
    if isinstance(dup, dict):
        return len(dup.get("issues", []))
    return 0


def run_full_scan(
    repo_root: Path,
    lang: str = "all",
    verbose: bool = False,
) -> dict[str, Any]:
    """执行全仓扫描（full 模式），返回与 check 同构的 results 结构"""
    config = QualityGateConfig()
    lint_ignore_paths = config.lint_ignore_paths
    cc_threshold = config.get_threshold("cyclomatic_complexity", 15)
    crap_threshold = config.get_threshold("crap", 30)
    min_tokens = config.get_threshold("min_tokens", 50)
    min_lines = config.get_threshold("min_lines", 5)
    dup_threshold = config.get_threshold("duplication", 3.0)

    results: dict[str, Any] = {"checks_run": DEFAULT_SCAN_CHECKS}

    click_echo = (lambda msg: sys.stdout.write(msg + "\n")) if verbose else (lambda msg: None)

    # Rust
    if lang in ("rust", "all"):
        click_echo("🔍 扫描 Rust 代码...")
        rust_result: dict[str, Any] = {}
        if "lint" in DEFAULT_SCAN_CHECKS:
            rust_result["lint"] = check_rust_lint_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
                full=True,
            )
        if "complexity" in DEFAULT_SCAN_CHECKS:
            rust_result["complexity"] = check_rust_complexity_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
                threshold=cc_threshold,
            )
        if "dependency" in DEFAULT_SCAN_CHECKS:
            rust_result["dependency"] = check_dependency_incremental(
                repo_root, lang="rust", verbose=verbose,
            )
        results["rust"] = rust_result

    # TypeScript
    if lang in ("ts", "all"):
        click_echo("🔍 扫描 TypeScript 代码...")
        ts_result: dict[str, Any] = {}
        if "lint" in DEFAULT_SCAN_CHECKS:
            ts_result["lint"] = check_ts_lint_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
                full=True,
            )
        if "dependency" in DEFAULT_SCAN_CHECKS:
            ts_result["dependency"] = check_dependency_incremental(
                repo_root, lang="ts", verbose=verbose,
            )
        results["ts"] = ts_result

    # Python
    if lang in ("python", "all"):
        click_echo("🔍 扫描 Python 代码...")
        python_result: dict[str, Any] = {}
        if "lint" in DEFAULT_SCAN_CHECKS:
            python_result["lint"] = check_python_lint_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
                full=True,
            )
        if "complexity" in DEFAULT_SCAN_CHECKS:
            python_result["complexity"] = check_python_crap_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
                crap_threshold=crap_threshold,
            )
        if "dependency" in DEFAULT_SCAN_CHECKS:
            python_result["dependency"] = check_dependency_incremental(
                repo_root, lang="python", verbose=verbose,
            )
        if "smell" in DEFAULT_SCAN_CHECKS:
            python_result["smell"] = check_smell_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
                full=True,
            )
        results["python"] = python_result

    # 重复代码（语言无关，跑一次）
    if "duplication" in DEFAULT_SCAN_CHECKS:
        results["duplication"] = check_duplication_incremental(
            repo_root, verbose=verbose,
            threshold=dup_threshold,
            min_tokens=min_tokens,
            min_lines=min_lines,
            ignore_paths=lint_ignore_paths,
            full=True,
        )

    return results


def history_dir_for(repo_root: Path) -> Path:
    """scan 存档目录（周报趋势）"""
    return repo_root / HISTORY_SUBDIR


def save_scan_report(repo_root: Path, report: dict[str, Any]) -> Path:
    """把 scan 报告存档到 history 目录，文件名带时间戳（毫秒级防同秒覆盖）"""
    hdir = history_dir_for(repo_root)
    hdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    path = hdir / f"scan-{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return path


def latest_report_path(repo_root: Path) -> Path | None:
    """最近一份 scan 报告（按文件名时间戳排序）"""
    hdir = history_dir_for(repo_root)
    if not hdir.exists():
        return None
    files = sorted(hdir.glob("scan-*.json"))
    return files[-1] if files else None


def load_report(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def compare_reports(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """对比两份 scan 报告，产出周报趋势摘要

    对比维度（lint / 重复块 / CRAP 超阈值函数 / smell 结构坏味道）:
        lint: {lang: {check: {before, after, delta}}}
        duplication: {before, after, delta}
    """
    trend: dict[str, Any] = {}

    # lint per-language 计数
    lint_trend: dict[str, dict[str, Any]] = {}
    for lang in ("rust", "ts", "python"):
        b_lint = (baseline.get("results", {}).get(lang, {}) or {}).get("lint", {})
        c_lint = (current.get("results", {}).get(lang, {}) or {}).get("lint", {})
        before = len(b_lint.get("issues", [])) if isinstance(b_lint, dict) else 0
        after = len(c_lint.get("issues", [])) if isinstance(c_lint, dict) else 0
        # skipped 报告时视为无数据（before/after 保持 0）
        lint_trend[lang] = {"before": before, "after": after, "delta": after - before}
    trend["lint"] = lint_trend

    # duplication
    b_dup = baseline.get("results", {}).get("duplication", {})
    c_dup = current.get("results", {}).get("duplication", {})
    b_blocks = len(b_dup.get("issues", [])) if isinstance(b_dup, dict) else 0
    c_blocks = len(c_dup.get("issues", [])) if isinstance(c_dup, dict) else 0
    trend["duplication"] = {
        "before": b_blocks, "after": c_blocks, "delta": c_blocks - b_blocks,
    }

    # CRAP 超阈值函数（python complexity 的 issues = 超阈值函数清单）
    b_crap = (baseline.get("results", {}).get("python", {}) or {}).get("complexity", {})
    c_crap = (current.get("results", {}).get("python", {}) or {}).get("complexity", {})
    b_crap_n = len(b_crap.get("issues", [])) if isinstance(b_crap, dict) else 0
    c_crap_n = len(c_crap.get("issues", [])) if isinstance(c_crap, dict) else 0
    trend["crap_functions"] = {
        "before": b_crap_n, "after": c_crap_n, "delta": c_crap_n - b_crap_n,
    }

    # 函数级趋势：新增/消失的高 CRAP 函数（周报真正关心的对象）
    def _fn_key(issue: dict) -> str:
        fn = issue.get("function") or ""
        if fn:
            return f"{issue.get('file', '')}:{fn}"
        # 无 function 字段时退回文件+行（低版本报告）
        return f"{issue.get('file', '')}:{issue.get('line', 0)}"

    def _fn_set(crap_result: Any) -> set[str]:
        if not isinstance(crap_result, dict):
            return set()
        return {_fn_key(i) for i in crap_result.get("issues", [])}

    b_fn = _fn_set(b_crap)
    c_fn = _fn_set(c_crap)
    trend["crap_function_details"] = {
        "new": sorted(c_fn - b_fn),      # 新增超阈值函数（恶化）
        "fixed": sorted(b_fn - c_fn),    # 已消失超阈值函数（好转）
        "persistent": sorted(c_fn & b_fn),
    }

    # smell（python 结构坏味道：P0/P1 阻塞 issues + P2 报告项）
    b_smell = (baseline.get("results", {}).get("python", {}) or {}).get("smell", {})
    c_smell = (current.get("results", {}).get("python", {}) or {}).get("smell", {})
    trend["smell"] = {
        "before": _smell_count(b_smell),
        "after": _smell_count(c_smell),
        "delta": _smell_count(c_smell) - _smell_count(b_smell),
    }

    return trend


def _smell_count(smell_result: Any) -> int:
    """smell checker 计数：issues(P0/P1) + report_only_issues(P2)"""
    if not isinstance(smell_result, dict):
        return 0
    return len(smell_result.get("issues", [])) + len(
        smell_result.get("report_only_issues", []))


def print_trend(trend: dict[str, Any]) -> None:
    """打印周报趋势摘要"""
    sys.stdout.write("\n📈 与上次扫描对比:\n")
    for lang, item in (trend.get("lint") or {}).items():
        delta = item["delta"]
        arrow = "🟢" if delta < 0 else ("🔴" if delta > 0 else "⚪")
        sys.stdout.write(
            f"  {arrow} lint({lang}): {item['before']} → {item['after']} "
            f"({delta:+d})\n"
        )
    dup = trend.get("duplication") or {}
    d = dup.get("delta", 0)
    arrow = "🟢" if d < 0 else ("🔴" if d > 0 else "⚪")
    sys.stdout.write(
        f"  {arrow} 重复块: {dup.get('before', 0)} → {dup.get('after', 0)} ({d:+d})\n"
    )
    crap = trend.get("crap_functions") or {}
    c = crap.get("delta", 0)
    arrow = "🟢" if c < 0 else ("🔴" if c > 0 else "⚪")
    sys.stdout.write(
        f"  {arrow} CRAP 超阈值函数: {crap.get('before', 0)} → "
        f"{crap.get('after', 0)} ({c:+d})\n"
    )
    smell = trend.get("smell") or {}
    s = smell.get("delta", 0)
    arrow = "🟢" if s < 0 else ("🔴" if s > 0 else "⚪")
    sys.stdout.write(
        f"  {arrow} 结构坏味道(smell): {smell.get('before', 0)} → "
        f"{smell.get('after', 0)} ({s:+d})\n"
    )
    # 函数级明细（有则展示新增/消失的具体函数）
    details = trend.get("crap_function_details") or {}
    for fn in details.get("new", [])[:10]:
        sys.stdout.write(f"      🔴 新增: {fn}\n")
    for fn in details.get("fixed", [])[:10]:
        sys.stdout.write(f"      🟢 已修: {fn}\n")
    if len(details.get("new", [])) > 10:
        sys.stdout.write(f"      ... 另有 {len(details['new']) - 10} 个新增\n")
