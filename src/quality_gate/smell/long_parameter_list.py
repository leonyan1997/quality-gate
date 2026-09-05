from __future__ import annotations

import ast

from .base import BaseRule
from .types import Finding, SmellConfig


class LongParameterListRule(BaseRule):
    rule_id = "long-parameter-list"
    rule_name = "长参数列表"
    severity = "P1"

    def check(self, file_path: str, source: str, tree: ast.AST, config: SmellConfig) -> list[Finding]:
        findings = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 排除 self, cls
                args = [a for a in node.args.args if a.arg not in ("self", "cls")]
                num_params = len(args) + len(node.args.kwonlyargs)
                if node.args.vararg:
                    num_params += 1
                if node.args.kwarg:
                    num_params += 1
                if num_params > config.max_parameters:
                    findings.append(Finding(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        file_path=file_path,
                        line=node.lineno,
                        end_line=node.end_lineno,
                        message=f"{node.name}() {num_params}个参数 → 建议≤{config.max_parameters}",
                        detail={"function": node.name, "parameters": num_params, "threshold": config.max_parameters},
                    ))
        return findings
