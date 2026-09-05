"""Tests for DeadCodeRule (file-level only)."""
from __future__ import annotations

from quality_gate.smell.dead_code import DeadCodeRule
from quality_gate.smell.types import SmellConfig


def test_dead_code_detects_unused_private_fn(smelly_source):
    """_unused_private_func is defined but never called."""
    rule = DeadCodeRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    func_findings = [f for f in findings if f.detail.get("name") == "_unused_private_func"]
    assert len(func_findings) >= 1, f"Expected _unused_private_func to be detected: {findings}"


def test_dead_code_detects_unused_private_var(smelly_source):
    """_variable_unused is defined but never referenced."""
    rule = DeadCodeRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    var_findings = [f for f in findings if f.detail.get("name") == "_variable_unused"]
    assert len(var_findings) >= 1, f"Expected _variable_unused to be detected: {findings}"


def test_dead_code_skips_exported_functions(smelly_source):
    """Public functions (no _ prefix) with no __all__ should be skipped."""
    rule = DeadCodeRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    exported = [f for f in findings if f.detail.get("name") in ("long_function", "uses_os")]
    assert len(exported) == 0, f"Public functions should not be flagged: {findings}"
