from __future__ import annotations

import ast

from .base import BaseRule
from .types import Finding, SmellConfig


class DeadCodeRule(BaseRule):
    rule_id = "dead-code"
    rule_name = "文件级死代码"
    severity = "P2"

    def check(self, file_path: str, source: str, tree: ast.AST, config: SmellConfig) -> list[Finding]:
        """文件级死代码检测（__all__ 收集 / 顶层定义收集 / 引用统计三段

        拆分自 2026-09-05 单体内联（radon 报 check 30 / 类 31 圈复杂度）：
        逻辑等价重构——私有辅助为模块级函数，类方法数不受影响。
        """
        findings: list[Finding] = []

        all_exported, has_all = _collect_all_exports(tree)
        top_level_defs = _collect_top_level_defs(tree, all_exported, has_all)
        if not top_level_defs:
            return findings

        # 统计引用次数（排除定义本身），零引用 = 死代码
        for name, (kind, lineno, end_lineno) in top_level_defs.items():
            if _reference_count(tree, name) == 0:
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


def _collect_all_exports(tree: ast.AST) -> tuple[set[str], bool]:
    """收集模块级 __all__ = [...] 中导出的名字（ast.walk 遍历整棵）"""
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
    return all_exported, has_all


def _collect_top_level_defs(
    tree: ast.AST, all_exported: set[str], has_all: bool,
) -> dict[str, tuple[str, int, int]]:
    """收集需要检查的顶层定义（函数/下划线变量）

    name -> (kind, lineno, end_lineno)
    - 函数: __all__ 声明或非 _ 开头（可能外部引用）都跳过
    - 变量: 只查 _ 开头（其余可能是模块公共数据，无法证明死）
    """
    top_level_defs: dict[str, tuple[str, int, int]] = {}
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
    return top_level_defs


def _reference_count(tree: ast.AST, name: str) -> int:
    """统计 name 在文件中的引用次数（排除 Store/Del 定义位置）"""
    ref_count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            # 检查是否在定义语句中（Assign/LHS 或 FunctionDef name）
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            ref_count += 1
    return ref_count
