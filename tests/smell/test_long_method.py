"""Tests for LongMethodRule."""
from __future__ import annotations

from quality_gate.smell.long_method import LongMethodRule
from quality_gate.smell.types import SmellConfig


def test_long_method_detects_long_function(smelly_source):
    """A function > 40 lines should be flagged."""
    rule = LongMethodRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    # long_function has 40+ lines
    long_fns = [f for f in findings if "long_function" in f.detail.get("function", "")]
    assert len(long_fns) >= 1, f"Expected long_function to be detected: {findings}"


def test_long_method_skips_short_function(smelly_source):
    rule = LongMethodRule()
    source, tree = smelly_source
    # Point.distance_to (4 lines) should not be flagged
    findings = rule.check("test.py", source, tree, SmellConfig(max_function_lines=40))
    short_fns = [f for f in findings if f.detail.get("function") == "compute_area"]
    assert len(short_fns) == 0, f"Short function compute_area should not trigger: {findings}"


def test_long_method_respects_config_threshold(smelly_source):
    """When threshold is 100, long_function should NOT be flagged."""
    rule = LongMethodRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig(max_function_lines=100))
    assert len(findings) == 0, f"No findings expected with 100-line threshold: {findings}"
