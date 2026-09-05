"""Tests for DeadImportRule."""
from __future__ import annotations

from quality_gate.smell.dead_import import DeadImportRule
from quality_gate.smell.types import SmellConfig


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
