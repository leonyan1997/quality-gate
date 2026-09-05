"""Pytest fixtures for smell engine tests."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from quality_gate.smell import get_enabled_rules
from quality_gate.smell.types import SmellConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def default_config() -> SmellConfig:
    return SmellConfig()


@pytest.fixture
def all_rules(default_config):
    return get_enabled_rules(default_config)


@pytest.fixture
def smelly_source():
    """Parse smelly_code.py and return (source_text, ast_tree)."""
    path = FIXTURES_DIR / "smelly_code.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return source, tree


def parse_source(source: str) -> ast.AST:
    return ast.parse(source)


def read_fixture(name: str) -> str:
    """Read a fixture file by name (without .py suffix)."""
    path = FIXTURES_DIR / f"{name}.py"
    return path.read_text(encoding="utf-8")
