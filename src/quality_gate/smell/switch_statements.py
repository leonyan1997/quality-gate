from __future__ import annotations

import ast

from .base import BaseRule
from .types import Finding, SmellConfig


def _count_match_cases(tree: ast.AST) -> list[tuple[int, int, int]]:
    """返回所有 match 语句的 (行号, case分支数, end_line)"""
    matches = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Match):
            num_cases = len(node.cases)
            matches.append((node.lineno, num_cases, node.end_lineno or node.lineno))
    return matches


class SwitchStatementsRule(BaseRule):
    rule_id = "switch-statements"
    rule_name = "过多的 match 分支"
    severity = "P2"

    def check(self, file_path: str, source: str, tree: ast.AST, config: SmellConfig) -> list[Finding]:
        findings = []
        matches = _count_match_cases(tree)
        for lineno, num_cases, end_lineno in matches:
            if num_cases > config.max_switch_branches:
                findings.append(Finding(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    file_path=file_path,
                    line=lineno,
                    end_line=end_lineno,
                    message=f"match 表达式 — {num_cases}个分支，考虑用多态替代",
                    detail={"cases": num_cases, "threshold": config.max_switch_branches},
                ))
        return findings
