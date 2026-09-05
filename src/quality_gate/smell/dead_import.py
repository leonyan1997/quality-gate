from __future__ import annotations

import ast

from .base import BaseRule
from .types import Finding, SmellConfig


class DeadImportRule(BaseRule):
    rule_id = "dead-import"
    rule_name = "未使用的 import"
    severity = "P1"

    def check(self, file_path: str, source: str, tree: ast.AST, config: SmellConfig) -> list[Finding]:
        findings = []

        # 收集所有 import 的名字
        imported_names: dict[str, int] = {}  # name -> lineno
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    imported_names[name] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                # 跳过 __future__ 导入（它们是语法开关而非普通引用）
                if node.module == "__future__":
                    continue
                for alias in node.names:
                    name = alias.asname or alias.name
                    if name != "*":  # 忽略 star import
                        imported_names[name] = node.lineno

        if not imported_names:
            return findings

        # 收集文件中所有实际使用的名字
        used_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used_names.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                # a.b.c -> 'a' 是使用的名字
                used_names.add(node.value.id)

        # 检查哪些 import 的名字从未被使用
        for name, lineno in imported_names.items():
            if name not in used_names:
                findings.append(Finding(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    file_path=file_path,
                    line=lineno,
                    end_line=lineno,
                    message=f"\"{name}\" 导入但文件内未使用",
                    detail={"import_name": name},
                ))

        return findings
