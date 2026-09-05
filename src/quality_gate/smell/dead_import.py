from __future__ import annotations

import ast

from .base import BaseRule
from .types import Finding, SmellConfig


def _module_all_names(tree: ast.AST) -> set[str]:
    """收集模块级 `__all__ = [...]` 字面量中声明的名字。

    只认模块顶层直接赋值的 __all__（Assign/AnnAssign），值取字符串常量
    （list/tuple/set 字面量）。名字一经声明即视为该模块公共 API 的一部分：
    若有同名 import，则该 import 是 re-export，不算 dead import。
    非字面量/无法静态解析的 __all__ 一律忽略（不猜，宁可保守）。
    """
    names: set[str] = set()
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            if (
                len(targets) == 1
                and isinstance(targets[0], ast.Name)
                and targets[0].id == "__all__"
            ):
                value = node.value
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "__all__"
                and node.value is not None
            ):
                value = node.value
        if value is None:
            continue
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.add(elt.value)
    return names


def _is_reexport_alias(alias: ast.alias) -> bool:
    """PEP 484 显式 re-export 标记：`import x as x` / `from y import x as x`。

    同名 alias 是模块把导入名原样转出公开 API 的社区标准写法
    （pyflakes/ruff F401 均豁免），与 __all__ 声明同属 re-export 语义。
    """
    return alias.asname is not None and alias.asname == alias.name


class DeadImportRule(BaseRule):
    rule_id = "dead-import"
    rule_name = "未使用的 import"
    severity = "P1"

    def check(self, file_path: str, source: str, tree: ast.AST, config: SmellConfig) -> list[Finding]:
        findings = []

        # 模块级 __all__ = [...] 中声明的名字 = 公共 API（PEP 484 re-export 语义，
        # 对齐 pyflakes/ruff F401：__all__ 中的 import 名不算 dead import）
        exported = _module_all_names(tree)

        # 收集所有 import 的名字
        imported_names: dict[str, int] = {}  # name -> lineno
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_reexport_alias(alias):
                        # import x as x：PEP 484 显式 re-export 标记，跳过
                        continue
                    name = alias.asname or alias.name.split(".")[0]
                    imported_names[name] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                # 跳过 __future__ 导入（它们是语法开关而非普通引用）
                if node.module == "__future__":
                    continue
                for alias in node.names:
                    if _is_reexport_alias(alias):
                        # from y import x as x：同上，显式 re-export
                        continue
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
        # __all__ 声明视同使用（re-export 的名字以字符串字面量形式存在，
        # 不在 ast.Name/Attribute 节点中——引擎盲点修复，见 2026-09-05 方案 A1）
        used_names |= exported

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
