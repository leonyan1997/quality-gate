from __future__ import annotations

import ast

from .base import BaseRule
from .types import Finding, SmellConfig


class DataClassRule(BaseRule):
    rule_id = "data-class"
    rule_name = "数据类候选"
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
                        deco_ids.add(d.attr)  # dataclasses.dataclass -> .dataclass
                if "dataclass" in deco_ids:
                    continue

                # 跳过 Pydantic BaseModel / pydantic.BaseModel 子类
                is_pydantic_model = any(
                    (isinstance(b, ast.Name) and b.id in ("BaseModel",))
                    or (isinstance(b, ast.Attribute)
                        and isinstance(b.value, ast.Name)
                        and b.value.id == "pydantic"
                        and b.attr == "BaseModel")
                    for b in node.bases
                )
                if is_pydantic_model:
                    continue

                # 检查是否有非 magic 的有行为方法
                methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                non_magic_methods = [m for m in methods if not m.name.startswith("__")]

                # 类属性/字段
                attributes = [n for n in node.body if isinstance(n, ast.AnnAssign) or
                             (isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) for t in n.targets))]

                # 如果只有 __init__ + attribute assignments → 数据类
                if not non_magic_methods and attributes:
                    findings.append(Finding(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        file_path=file_path,
                        line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        message=f"{node.name} 仅含字段赋值，考虑转为 @dataclass",
                        detail={"class": node.name, "attributes": len(attributes)},
                    ))

        return findings
