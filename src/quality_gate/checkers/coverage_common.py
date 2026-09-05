"""coverage 检查共享判定骨架（python/rust/ts 三语言覆盖率检查器同构部分）

C1（2026-09-05 自身债务清理）：三兄弟流程同构——定位/生成覆盖率报告 →
load → 路径匹配 → 新增文件 >0% 判定。差异集中在"报告从哪来 + 怎么解析 +
怎么匹配文件"，各自语言文件保留；本模块统一最重、最易漂移的一段：
遍历新增文件 → 忽略过滤 → 覆盖率查找 → 0% 阻塞上报。

行为等效原则：与原三份实现的逐文件语义一致（未收录 → 跳过、total=0 →
跳过、covered=0 → zero_coverage 阻塞）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .git_diff import get_added_files, matches_ignore_patterns

# lookup 回调: (rel_path) -> (covered 行数/布尔, total 行数) | None（None = 未收录）
CoverageLookup = Callable[[str], tuple[Any, int] | None]


@dataclass
class CoverageScan:
    """coverage 判定差异点（C1 参数收拢：同 DupOptions 模式）

    extensions: 纳入判定的新增文件后缀
    lookup: 报告内文件覆盖率查找（语言差异点）
    ignore_paths: 生效豁免集合
    record_coverage: 命中文件写入 result.coverage_data（python/ts = True）
    print_unrecorded: 未收录文件 verbose 提示（python/ts = True，rust 原静默）
    """

    extensions: tuple[str, ...]
    lookup: CoverageLookup
    ignore_paths: list[str] = field(default_factory=list)
    record_coverage: bool = True
    print_unrecorded: bool = True


def scan_added_files_zero_coverage(
    repo_root: Path,
    *,
    verbose: bool,
    scan: CoverageScan,
    result: dict[str, Any],
) -> None:
    """遍历新增文件并上报 0% 覆盖率（增量门禁核心判定，就地写入 result）"""
    added_files = get_added_files(repo_root)
    for filepath in sorted(added_files):
        if not filepath.endswith(scan.extensions):
            continue
        if matches_ignore_patterns(filepath, scan.ignore_paths):
            if verbose:
                print(f"    跳过 (allowlist): {filepath}")
            continue

        cov = scan.lookup(filepath)
        if cov is None:
            if verbose and scan.print_unrecorded:
                print(f"    未收录 (测试未触及): {filepath}")
            continue
        covered, total = cov
        if total == 0:
            continue
        if scan.record_coverage:
            result["coverage_data"][filepath] = {
                "covered": covered, "total_lines": total,
            }
        if covered == 0:
            result["issues"].append({
                "file": filepath, "line": 0, "column": 0, "level": "error",
                "code": "zero_coverage",
                "message": f"新增文件 {filepath} 覆盖率为 0%，请补充测试",
            })
            result["blocking"] = True

    if verbose:
        print(
            f"  检查 {len(added_files)} 个新增文件，"
            f"发现 {len(result['issues'])} 个零覆盖率问题",
        )
