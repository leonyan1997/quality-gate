from __future__ import annotations

import abc
import ast
from typing import ClassVar

from .types import Finding, SmellConfig


class BaseRule(abc.ABC):
    rule_id: str = ""
    rule_name: str = ""
    severity: str = "P2"
    lang: ClassVar[list[str]] = ["python"]

    @abc.abstractmethod
    def check(self, file_path: str, source: str, tree: ast.AST | None, config: SmellConfig) -> list[Finding]:
        ...
