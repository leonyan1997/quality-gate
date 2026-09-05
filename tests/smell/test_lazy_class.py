"""Tests for LazyClassRule."""
from __future__ import annotations

from quality_gate.smell.lazy_class import LazyClassRule
from quality_gate.smell.types import SmellConfig


def test_lazy_class_detects_do_nothing(smelly_source):
    """DoNothingClass has 2 trivial methods → lazy class."""
    rule = LazyClassRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    dn = [f for f in findings if f.detail.get("class") == "DoNothingClass"]
    assert len(dn) >= 1, f"Expected DoNothingClass to be detected: {findings}"


def test_lazy_class_skips_dataclass(smelly_source):
    """Point is @dataclass → skipped."""
    rule = LazyClassRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    point = [f for f in findings if f.detail.get("class") == "Point"]
    assert len(point) == 0, f"@dataclass should be skipped: {findings}"


def test_lazy_class_skips_normal_class(smelly_source):
    """Logger has a single non-trivial log() method → skipped."""
    rule = LazyClassRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    logger = [f for f in findings if f.detail.get("class") == "Logger"]
    assert len(logger) == 0, f"Logger is not lazy: {findings}"
