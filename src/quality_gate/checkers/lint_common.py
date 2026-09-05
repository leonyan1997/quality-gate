"""lint 检查共享骨架（python/rust/ts 三语言增量 lint 检查器同构部分）

C1（2026-09-05 自身债务清理）：三个 lint 检查器入口结构高度同构——
工具调用→JSON 解析→路径归一→行过滤→去重→报告。本模块统一:
  - 结果骨架（blocking/issues/all_issues/diff_ranges）
  - 工具级错误结果（未安装/超时/解析失败/执行异常）
  - diff 行过滤上报（增量门禁核心，含可选去重与 diff 范围缓存）

各语言文件只留差异点: 命令/超时/解析器/路径归一/去重开关。
行为等效原则：上报语义与原三份实现逐字节一致（full 全量 / 增量按行过滤）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .git_diff import get_git_diff_lines, is_line_in_diff


def new_lint_result() -> dict[str, Any]:
    """lint checker 统一结果骨架（python/rust/ts 原实现逐字段一致）"""
    return {
        "blocking": False,
        "issues": [],
        "all_issues": [],
        "diff_ranges": {},
    }


def tool_error(tool: str, code: str, message: str) -> dict[str, Any]:
    """工具级失败结果：阻塞 + 单条工具级 issue（file=tool 名）"""
    result = new_lint_result()
    result["blocking"] = True
    result["issues"] = [{
        "file": tool,
        "line": 0,
        "column": 0,
        "level": "error",
        "code": code,
        "message": message,
    }]
    return result


class LintReporter:
    """统一问题上报：full 全量报告 / 增量按 diff 行过滤；可选 (file,line,col,code) 去重

    增量语义（与原三份实现一致）:
      - 文件不在 diff（无新增/修改行）→ 存量问题完全跳过（不进 all_issues）
      - 文件在 diff 但该行不在新增范围 → 进 all_issues 不阻塞
      - 行命中新增范围 → 进 issues 并阻塞
    """

    def __init__(self, repo_root: Path, *, full: bool):
        self.repo_root = repo_root
        self.full = full
        self.result = new_lint_result()
        self._seen: set[tuple] = set()
        self._diff_ranges_cache: dict[str, list[tuple[int, int]]] = {}

    def report(self, issue: dict[str, Any], *, dedupe: bool = False) -> None:
        if dedupe:
            key = (issue["file"], issue["line"], issue["column"], issue["code"])
            if key in self._seen:
                return
            self._seen.add(key)

        if self.full:
            # 全仓扫描模式：不做 diff 过滤，所有问题都报告
            self.result["all_issues"].append(issue)
            self.result["issues"].append(issue)
            self.result["blocking"] = True
            return

        rel_path = issue["file"]
        if rel_path not in self._diff_ranges_cache:
            self._diff_ranges_cache[rel_path] = get_git_diff_lines(
                self.repo_root, rel_path,
            )
        diff_ranges = self._diff_ranges_cache[rel_path]
        # 文件在 diff 中无新增/修改 → 存量问题不阻塞（增量门禁核心）
        if not diff_ranges:
            return

        self.result["all_issues"].append(issue)
        if is_line_in_diff(issue["line"], diff_ranges):
            self.result["issues"].append(issue)
            self.result["blocking"] = True
