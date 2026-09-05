"""Tests for LargeClassRule."""
from __future__ import annotations

from quality_gate.smell.large_class import LargeClassRule
from quality_gate.smell.types import SmellConfig


def test_large_class_detects_huge_class(smelly_source):
    rule = LargeClassRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    huge = [f for f in findings if f.detail.get("class") == "HugeClass"]
    assert len(huge) >= 1, f"Expected HugeClass to be detected: {findings}"


def test_large_class_skips_small_class(smelly_source):
    rule = LargeClassRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    small = [f for f in findings if f.detail.get("class") in ("Point", "Logger")]
    assert len(small) == 0, f"Small classes should not be flagged: {findings}"


def test_large_class_respects_threshold(smelly_source):
    rule = LargeClassRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig(max_class_methods=20))
    assert len(findings) == 0, f"No findings expected with 20-method threshold: {findings}"
