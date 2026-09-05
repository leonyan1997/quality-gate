from __future__ import annotations

import ast

from .base import BaseRule
from .types import Finding, SmellConfig


class LargeClassRule(BaseRule):
    rule_id = "large-class"
    rule_name = "大类"
    severity = "P1"

    def check(self, file_path: str, source: str, tree: ast.AST, config: SmellConfig) -> list[Finding]:
        findings = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                num_methods = len(methods)
                class_lines = node.end_lineno - node.lineno
                if num_methods > config.max_class_methods:
                    findings.append(Finding(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        file_path=file_path,
                        line=node.lineno,
                        end_line=node.end_lineno,
                        message=f"{node.name} {num_methods}个方法 → 建议≤{config.max_class_methods}",
                        detail={"class": node.name, "methods": num_methods, "threshold": config.max_class_methods},
                    ))
                elif class_lines > config.max_class_lines:
                    findings.append(Finding(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        file_path=file_path,
                        line=node.lineno,
                        end_line=node.end_lineno,
                        message=f"{node.name} {class_lines}行 → 建议≤{config.max_class_lines}",
                        detail={"class": node.name, "lines": class_lines, "threshold": config.max_class_lines},
                    ))
        return findings
