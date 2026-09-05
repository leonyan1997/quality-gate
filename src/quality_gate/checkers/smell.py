"""Smell 增量检查器——结构坏味道接入 check --diff 门禁

规则引擎本体在 quality_gate/smell/（8 条 Python AST 规则，自 hermes-smell
迁入）。本模块只做 diff 语义适配，锚点分类（见增量门禁设计）：

  - 行级规则（long-parameter-list / dead-import）
      → finding.line 命中 diff 新增/修改行才报
  - 块级规则（long-method / large-class / lazy-class / data-class /
    switch-statements / dead-code）
      → diff 触及 [line, end_line] 包围的函数/类体才重算
  - 纯新增文件（untracked / git added）
      → git_diff 返回全文件范围，天然全量评估

阻塞策略：P0/P1 → issues（阻塞门禁）；P2 → report_only_issues（仅报告）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ..smell import get_enabled_rules
from ..smell.types import Finding, SmellConfig
from .git_diff import (
    get_changed_files,
    get_git_diff_lines,
    is_line_in_diff,
    matches_ignore_patterns,
)

# 行级锚点规则：finding 报告行（def 行 / import 行）命中 diff 才算新增
_LINE_ANCHORED_RULES = {"long-parameter-list", "dead-import"}

# full 模式遍历时跳过的目录（含 quality-gate 自身存档目录）
_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".quality-gate", "node_modules",
    ".venv", "venv", "target", "dist", "build", "__pycache__",
}


def check_smell_incremental(
    repo_root: Path,
    verbose: bool = False,
    ignore_paths: list[str] | None = None,
    full: bool = False,
    smell_config: SmellConfig | None = None,
) -> dict[str, Any]:
    """结构坏味道增量检查

    full=False (默认): 增量门禁——只评估 diff 触及的函数/类体与命中 diff
    的 def/import 行，P0/P1 阻塞、P2 仅报告。
    full=True: 全仓扫描（scan 周报用），全部存量坏味道都进 issues/report。

    smell_config: 由 quality-gate.yaml 的 smell 段构建的阈值配置
    （QualityGateConfig.build_smell_config()）；缺省用 SmellConfig 默认。
    """
    if ignore_paths is None:
        ignore_paths = []

    result: dict[str, Any] = {
        "blocking": False,
        "issues": [],            # P0/P1 → 阻塞
        "report_only_issues": [],  # P2 → 仅报告
        "files_scanned": 0,
        "tool": "smell-rules",
    }

    config = smell_config if smell_config is not None else SmellConfig()

    if full:
        py_files = sorted(
            p for p in repo_root.rglob("*.py")
            if not _is_skippable(p, repo_root, ignore_paths)
        )
    else:
        changed = get_changed_files(repo_root)
        py_files = sorted(
            (repo_root / rel) for rel in changed
            if rel.endswith(".py") and not matches_ignore_patterns(rel, ignore_paths)
        )

    if not py_files:
        if verbose:
            print("  无待检查 Python 文件，跳过 smell 检查")
        return result

    for path in py_files:
        rel_path = path.relative_to(repo_root).as_posix()
        try:
            source = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            if verbose:
                print(f"  跳过语法错误文件: {rel_path}")
            continue

        result["files_scanned"] += 1

        if full:
            findings = _run_rules(rel_path, source, tree, config)
        else:
            ranges = get_git_diff_lines(repo_root, rel_path)
            # 无新增/修改行（纯删除等）→ 本文件不引入新问题
            if not ranges:
                continue
            findings = [
                f for f in _run_rules(rel_path, source, tree, config)
                if _finding_touched(f, ranges)
            ]

        for finding in findings:
            issue = _issue_from_finding(finding)
            if finding.severity in ("P0", "P1"):
                result["issues"].append(issue)
                result["blocking"] = True
            else:
                result["report_only_issues"].append(issue)

    if verbose:
        print(
            f"  smell: 扫描 {result['files_scanned']} 个文件，"
            f"阻塞 {len(result['issues'])} / 报告 {len(result['report_only_issues'])}"
        )
    return result


def _run_rules(
    file_path: str,
    source: str,
    tree: ast.AST,
    config: SmellConfig,
) -> list[Finding]:
    """对单个文件运行全部启用规则；单条规则异常不拖垮门禁"""
    findings: list[Finding] = []
    for rule in get_enabled_rules(config):
        try:
            findings.extend(rule.check(file_path, source, tree, config))
        except Exception:
            # 单条规则对特殊代码形态的防御：跳过该规则而非误报/拖垮门禁
            pass
    return findings


def _finding_touched(finding: Finding, ranges: list[tuple[int, int]]) -> bool:
    """finding 是否属于 diff 引入（按锚点分类）"""
    if finding.rule_id in _LINE_ANCHORED_RULES:
        return is_line_in_diff(finding.line, ranges)

    # 块级：diff 任意新增/修改行落在 [line, end_line] 内即重算该块
    start, end = finding.line, finding.end_line or finding.line
    for r_start, r_end in ranges:
        if r_start <= end and start <= r_end:
            return True
    return False


def _issue_from_finding(finding: Finding) -> dict[str, Any]:
    """Finding → JSON 可序列化 issue（对齐 lint 检查器的输出契约）"""
    level = {
        "P0": "error",
        "P1": "warning",
        "P2": "info",
    }.get(finding.severity, "info")
    return {
        "file": finding.file_path,
        "line": finding.line,
        "end_line": finding.end_line,
        "column": 0,
        "level": level,
        "code": finding.rule_id,
        "message": finding.message,
        "severity": finding.severity,
        "rule_name": finding.rule_name,
    }


def _is_skippable(path: Path, repo_root: Path, ignore_paths: list[str]) -> bool:
    """full 模式遍历过滤：隐藏/产物目录 + 配置忽略路径"""
    rel = path.relative_to(repo_root)
    if any(part in _SKIP_DIRS for part in rel.parts):
        return True
    return matches_ignore_patterns(rel.as_posix(), ignore_paths)
