"""Tests for DeadImportRule."""
from __future__ import annotations

import ast

from quality_gate.smell.dead_import import DeadImportRule
from quality_gate.smell.types import SmellConfig


def _parse(source: str) -> tuple[str, ast.AST]:
    return source, ast.parse(source)


def test_dead_import_detects_unused(smelly_source):
    """sys is imported but not used in smelly_code."""
    rule = DeadImportRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    sys_findings = [f for f in findings if f.detail.get("import_name") == "sys"]
    assert len(sys_findings) >= 1, f"Expected sys to be flagged as unused: {findings}"


def test_dead_import_skips_used(smelly_source):
    """os is imported and used via uses_os()."""
    rule = DeadImportRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    os_findings = [f for f in findings if f.detail.get("import_name") == "os"]
    assert len(os_findings) == 0, f"os is used and should not be flagged: {findings}"


def test_dead_import_skips_future(smelly_source):
    """from __future__ import annotations should never be flagged."""
    rule = DeadImportRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    annotations_findings = [f for f in findings if f.detail.get("import_name") == "annotations"]
    assert len(annotations_findings) == 0, f"__future__ import should be skipped: {findings}"


# ── A1: __all__ / PEP 484 显式 re-export 识别（2026-09-05 自身债务清理） ──────


def test_all_reexport_not_reported():
    """名字进了模块级 __all__ 的 import 是 re-export，不报 dead-import。

    标准 re-export 模式（quality-gate 自身 checkers/__init__.py 等）：
    名字只以字符串字面量形式出现在 __all__，文件内无符号引用。
    """
    rule = DeadImportRule()
    source, tree = _parse(
        '"""pkg facade"""\n'
        "import os\n"
        "from .util import run\n"
        "\n"
        '__all__ = ["os", "run"]\n'
    )
    findings = rule.check("src/pkg/__init__.py", source, tree, SmellConfig())
    assert findings == [], f"__all__ 中声明的 import 不应报 dead-import: {findings}"


def test_all_missing_name_still_reported():
    """__all__ 只保护声明过的名字；未声明的死 import 照报。"""
    rule = DeadImportRule()
    source, tree = _parse(
        "import os\n"
        "import sys\n"
        "\n"
        '__all__ = ["os"]\n'
    )
    findings = rule.check("src/pkg/module.py", source, tree, SmellConfig())
    flagged = {f.detail.get("import_name") for f in findings}
    assert "sys" in flagged, f"不在 __all__ 的死 import 应报: {findings}"
    assert "os" not in flagged, f"__all__ 中声明的名字不应报: {findings}"


def test_all_applies_to_non_init_files():
    """__all__ re-export 语义与文件路径无关（普通模块同样适用）。"""
    rule = DeadImportRule()
    source, tree = _parse(
        "import json\n"
        "import os\n"
        "\n"
        '__all__ = ["json"]\n'
    )
    findings = rule.check("src/pkg/plain_module.py", source, tree, SmellConfig())
    flagged = {f.detail.get("import_name") for f in findings}
    assert flagged == {"os"}, f"普通模块也应只报不在 __all__ 的死 import: {findings}"


def test_same_name_alias_is_explicit_reexport():
    """import x as x / from y import x as x = PEP 484 显式 re-export，不报。"""
    rule = DeadImportRule()
    source, tree = _parse(
        "import os as os\n"
        "from .helpers import load as load\n"
        "from .other import load_impl\n"
        "import sys\n"
    )
    findings = rule.check("src/pkg/__init__.py", source, tree, SmellConfig())
    flagged = {f.detail.get("import_name") for f in findings}
    assert "os" not in flagged and "load" not in flagged, (
        f"同名 alias re-export 不应报: {findings}"
    )
    # 非同名 alias / 普通 import 仍照报
    assert "load_impl" in flagged and "sys" in flagged, f"真死 import 应报: {findings}"
