"""Tests for SwitchStatementsRule."""
from __future__ import annotations

from quality_gate.smell.switch_statements import SwitchStatementsRule
from quality_gate.smell.types import SmellConfig


def test_switch_detects_many_branches(smelly_source):
    rule = SwitchStatementsRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    many = [f for f in findings if f.detail.get("cases", 0) >= 4]
    assert len(many) >= 1, f"Expected many-branch match to be detected: {findings}"


def test_switch_skips_few_branches(smelly_source):
    rule = SwitchStatementsRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig(max_switch_branches=10))
    assert len(findings) == 0, f"No findings expected with 10-branch threshold: {findings}"


def test_switch_detects_correct_case_count(smelly_source):
    """many_branches has 5 cases, should report cases=5."""
    rule = SwitchStatementsRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    many = [f for f in findings if f.detail.get("cases", 0) == 5]
    assert len(many) >= 1, f"Expected 5 cases reported: {findings}"
