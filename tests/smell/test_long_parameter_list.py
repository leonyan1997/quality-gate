"""Tests for LongParameterListRule."""
from __future__ import annotations

from quality_gate.smell.long_parameter_list import LongParameterListRule
from quality_gate.smell.types import SmellConfig


def test_long_params_detects_many_params(smelly_source):
    rule = LongParameterListRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    params_fns = [f for f in findings if f.detail.get("function") == "lots_of_params"]
    assert len(params_fns) >= 1, f"Expected lots_of_params to be detected: {findings}"


def test_long_params_skips_short(smelly_source):
    rule = LongParameterListRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    short = [f for f in findings if f.detail.get("function") in ("compute_area", "greet")]
    assert len(short) == 0, f"Short param functions should not be flagged: {findings}"


def test_long_params_respects_threshold(smelly_source):
    rule = LongParameterListRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig(max_parameters=10))
    assert len(findings) == 0, f"No findings expected with 10-param threshold: {findings}"
