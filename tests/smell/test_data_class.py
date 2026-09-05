"""Tests for DataClassRule."""
from __future__ import annotations

from quality_gate.smell.data_class import DataClassRule
from quality_gate.smell.types import SmellConfig


def test_data_class_detects_plain_bag(smelly_source):
    """PlainDataBag has fields but no behavior → data class candidate."""
    rule = DataClassRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    bag = [f for f in findings if f.detail.get("class") == "PlainDataBag"]
    assert len(bag) >= 1, f"Expected PlainDataBag to be detected: {findings}"


def test_data_class_skips_dataclass_decorated(smelly_source):
    """Point uses @dataclasses.dataclass → should be skipped."""
    rule = DataClassRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    point = [f for f in findings if f.detail.get("class") == "Point"]
    assert len(point) == 0, f"@dataclass class should be skipped: {findings}"


def test_data_class_skips_pydantic_basemodel(smelly_source):
    """UserModel(BaseModel) → should be skipped (not a data class candidate)."""
    rule = DataClassRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    user = [f for f in findings if f.detail.get("class") == "UserModel"]
    assert len(user) == 0, f"Pydantic BaseModel should be skipped: {findings}"


def test_data_class_skips_pydantic_dotted_basemodel(smelly_source):
    """AdminModel(pydantic.BaseModel) → should be skipped."""
    rule = DataClassRule()
    source, tree = smelly_source
    findings = rule.check("test.py", source, tree, SmellConfig())
    admin = [f for f in findings if f.detail.get("class") == "AdminModel"]
    assert len(admin) == 0, f"Pydantic dotted BaseModel should be skipped: {findings}"
