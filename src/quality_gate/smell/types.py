from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass
class Finding:
    rule_id: str
    rule_name: str
    severity: str          # "P0" | "P1" | "P2"
    file_path: str
    line: int
    end_line: int
    message: str
    detail: dict[str, Any] | None = None

    def __post_init__(self):
        if self.detail is None:
            self.detail = {}


@dataclasses.dataclass
class ScanResult:
    findings: list[Finding]
    total_files: int
    duration_ms: float


@dataclasses.dataclass
class SmellConfig:
    max_function_lines: int = 60
    max_class_methods: int = 10
    max_class_lines: int = 300
    max_parameters: int = 5
    min_duplicate_lines: int = 6
    max_switch_branches: int = 3
    min_lazy_class_methods: int = 2
    enabled_rules: list[str] | None = None       # None = all enabled
    disabled_rules: list[str] | None = None      # None = none disabled
    output_format: str = "terminal"              # terminal | json
    show_noqa: bool = False
    min_severity: str = "P2"                     # P0 | P1 | P2
    exclude_dirs: list[str] | None = None        # 额外排除的目录名
    baseline_file: str | None = None             # --baseline 路径
    function_ignore: list[str] | None = None     # 函数级豁免（C2，quality-gate.yaml function_ignore）
