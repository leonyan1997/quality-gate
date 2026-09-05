from __future__ import annotations

import ast

from .base import BaseRule
from .types import Finding, SmellConfig


class LazyClassRule(BaseRule):
    rule_id = "lazy-class"
    rule_name = "懒惰类"
    severity = "P2"

    def check(self, file_path: str, source: str, tree: ast.AST, config: SmellConfig) -> list[Finding]:
        findings = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # 跳过已有 @dataclass 装饰器的类（支持 @dataclass 和 @dataclasses.dataclass）
                deco_ids = set()
                for d in node.decorator_list:
                    if isinstance(d, ast.Name):
                        deco_ids.add(d.id)
                    elif isinstance(d, ast.Attribute) and isinstance(d.value, ast.Name):
                        deco_ids.add(d.attr)
                if "dataclass" in deco_ids:
                    continue

                # 跳过抽象基类（ABC）和 mixin 类
                if "ABC" in deco_ids or "mixin" in node.name.lower():
                    continue

                # 跳过规则类（单一 check 方法是规则基类的标准形式）
                method_names = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                if method_names == {"check"} or method_names == {"check", "__init__"}:
                    continue

                # 只检查非数据类（有定义方法的类）
                methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if len(methods) <= config.min_lazy_class_methods:
                    # 检查方法是否过于简单
                    simple_methods = True
                    for m in methods:
                        body_lines = m.end_lineno - m.lineno if m.end_lineno else 0
                        if body_lines > 3:  # 三行以上的方法说明有实际逻辑
                            simple_methods = False
                            break

                    if methods and simple_methods:
                        findings.append(Finding(
                            rule_id=self.rule_id,
                            rule_name=self.rule_name,
                            severity=self.severity,
                            file_path=file_path,
                            line=node.lineno,
                            end_line=node.end_lineno or node.lineno,
                            message=f"{node.name} 仅含 {len(methods)} 个简单方法，功能薄弱",
                            detail={"class": node.name, "methods": len(methods)},
                        ))
        return findings
