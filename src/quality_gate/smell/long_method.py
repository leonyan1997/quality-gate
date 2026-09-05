from __future__ import annotations

import ast

from .base import BaseRule
from .types import Finding, SmellConfig


class LongMethodRule(BaseRule):
    rule_id = "long-method"
    rule_name = "长函数"
    severity = "P0"

    def check(self, file_path: str, source: str, tree: ast.AST, config: SmellConfig) -> list[Finding]:
        findings = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines = node.end_lineno - node.lineno
                if lines > config.max_function_lines:
                    findings.append(Finding(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        file_path=file_path,
                        line=node.lineno,
                        end_line=node.end_lineno,
                        message=f"{node.name}() {lines}行 → 建议≤{config.max_function_lines}",
                        detail={"function": node.name, "lines": lines, "threshold": config.max_function_lines},
                    ))
        return findings
