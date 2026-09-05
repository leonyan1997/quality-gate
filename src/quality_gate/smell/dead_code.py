from __future__ import annotations

import ast

from .base import BaseRule
from .types import Finding, SmellConfig


class DeadCodeRule(BaseRule):
    rule_id = "dead-code"
    rule_name = "文件级死代码"
    severity = "P2"

    def check(self, file_path: str, source: str, tree: ast.AST, config: SmellConfig) -> list[Finding]:
        findings = []

        # 收集 __all__
        all_exported: set[str] = set()
        has_all = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        has_all = True
                        if isinstance(node.value, ast.List):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    all_exported.add(elt.value)

        # 收集顶层定义（函数/变量）
        top_level_defs: dict[str, tuple[str, int, int]] = {}  # name -> (kind, lineno, end_lineno)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                # 如果 __all__ 存在且 name 在其中 → 不检查
                if has_all and name in all_exported:
                    continue
                # 如果 __all__ 不存在且不是 _ 开头 → 不检查（可能被外部引用）
                if not has_all and not name.startswith("_"):
                    continue
                top_level_defs[name] = ("function", node.lineno, node.end_lineno or node.lineno)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        if name.startswith("_"):
                            top_level_defs[name] = ("variable", node.lineno, node.end_lineno or node.lineno)

        if not top_level_defs:
            return findings

        # 更精确：统计引用次数（排除定义本身）
        for name, (kind, lineno, end_lineno) in top_level_defs.items():
            ref_count = 0
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == name:
                    # 检查是否在定义语句中（Assign/LHS 或 FunctionDef name）
                    if isinstance(node.ctx, (ast.Store, ast.Del)):
                        continue
                    ref_count += 1
            if ref_count == 0:
                label = "函数" if kind == "function" else "变量"
                findings.append(Finding(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    file_path=file_path,
                    line=lineno,
                    end_line=end_lineno,
                    message=f"{label} \"{name}\" 定义但文件内未引用",
                    detail={"name": name, "kind": kind},
                ))

        return findings
