"""配置文件加载器

加载 quality-gate.yaml，提供阈值、忽略路径等配置。
"""

import copy
from dataclasses import fields
from pathlib import Path
from typing import Any, ClassVar

import yaml

from .smell.types import SmellConfig


class QualityGateConfig:
    """Quality Gate 配置"""

    DEFAULT_CONFIG: ClassVar[dict[str, Any]] = {
        "version": 1,
        "languages": ["rust", "typescript", "python"],
        "thresholds": {
            "crap": 30,
            "cyclomatic_complexity": 15,
            "duplication": 3.0,
            "min_tokens": 50,
            "min_lines": 5,
        },
        "coverage_ignore": {
            "paths": [
                "**/models.py",
                "**/schemas.py",
                "**/constants.py",
                "**/migrations/**",
                "**/*.d.ts",
                "**/generated/**",
            ]
        },
        "lint_ignore": {
            "paths": [
                "**/generated/**",
                "**/node_modules/**",
                "**/target/**",
                "**/.venv/**",
                "**/venv/**",
                "**/dist/**",
                "**/build/**",
                # quality-gate 自身产物（scan 存档目录等）不入库也不参与检查
                ".quality-gate/**",
            ]
        },
        "function_ignore": [
            "legacy_compatibility",
            "deprecated_helper",
        ],
        # smell 引擎（Python 结构坏味道）阈值——键名与 SmellConfig 字段对齐，
        # 默认值以 smell/types.py SmellConfig dataclass 为单一事实源，此处
        # 仅为 example/合并提供完整形态
        "smell": {
            "max_function_lines": 40,
            "max_class_methods": 10,
            "max_class_lines": 300,
            "max_parameters": 5,
            "max_switch_branches": 3,
            "min_lazy_class_methods": 2,
            "enabled_rules": [],
            "disabled_rules": [],
        },
    }

    def __init__(self, config_path: Path | None = None):
        """加载配置
        
        搜索顺序:
        1. 指定的 config_path
        2. 当前目录 quality-gate.yaml
        3. 父目录递归查找
        4. 使用默认配置
        """
        self.config_path = config_path
        # 深拷贝默认配置：嵌套段（thresholds/smell/lint_ignore...）若浅拷贝则与
        # 类级 DEFAULT_CONFIG 共享引用，_merge_config 的 update 会原地污染类级
        # 默认，泄漏给后续实例（配置合并单测可复现）
        self.config = copy.deepcopy(self.DEFAULT_CONFIG)

        if config_path and config_path.exists():
            self._load_from_file(config_path)
        else:
            # 递归查找配置文件
            found_path = self._find_config_file()
            if found_path:
                self._load_from_file(found_path)

    def _load_from_file(self, path: Path):
        """从 YAML 文件加载配置"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_config = yaml.safe_load(f)
            
            if user_config:
                # 深度合并配置
                self._merge_config(user_config)
                self.config_path = path
        except Exception as e:
            # 加载失败使用默认配置
            print(f"⚠️  加载配置文件失败：{e}，使用默认配置")

    def _find_config_file(self) -> Path | None:
        """递归查找配置文件"""
        current = Path.cwd()
        
        # 最多向上查找 5 层
        for _ in range(5):
            config_file = current / "quality-gate.yaml"
            if config_file.exists():
                return config_file
            
            # 检查是否是子项目目录 (如 scraper-rs/)
            if (current.parent / "quality-gate.yaml").exists():
                return current.parent / "quality-gate.yaml"
            
            current = current.parent
            if current == current.parent:  # 到达根目录
                break
        
        return None

    def _merge_config(self, user_config: dict[str, Any]):
        """深度合并用户配置到默认配置"""
        for key, value in user_config.items():
            if key in self.config and isinstance(self.config[key], dict) and isinstance(value, dict):
                self.config[key].update(value)
            else:
                self.config[key] = value

    @property
    def languages(self) -> list[str]:
        """启用的语言列表"""
        return self.config.get("languages", [])

    @property
    def thresholds(self) -> dict[str, Any]:
        """阈值配置"""
        return self.config.get("thresholds", {})

    def get_threshold(self, name: str, default: Any = None) -> Any:
        """获取特定阈值"""
        return self.thresholds.get(name, default)

    @property
    def coverage_ignore_paths(self) -> list[str]:
        """覆盖率忽略路径"""
        return self.config.get("coverage_ignore", {}).get("paths", [])

    @property
    def lint_ignore_paths(self) -> list[str]:
        """Lint 忽略路径"""
        return self.config.get("lint_ignore", {}).get("paths", [])

    @property
    def function_ignore(self) -> list[str]:
        """函数级忽略列表"""
        return self.config.get("function_ignore", [])

    def build_smell_config(self) -> SmellConfig:
        """构建 smell 引擎配置：quality-gate.yaml 的 smell 段覆盖 SmellConfig 默认

        覆盖键直接映射 SmellConfig dataclass 字段（阈值 + enabled/disabled_rules），
        非法/None 键跳过；未覆盖项回落 smell.types.SmellConfig 字段默认值
        （单一事实源在 dataclass，此处不复制默认值防漂移）。
        """
        smell_cfg = self.config.get("smell") or {}
        valid = {f.name for f in fields(SmellConfig)}
        kwargs: dict[str, Any] = {}
        for key, value in smell_cfg.items():
            if key not in valid or value is None:
                continue
            # [] 与 None 同为"不限/不排除"，统一归一为 None（SmellConfig 语义）
            if key in ("enabled_rules", "disabled_rules"):
                value = value or None
            kwargs[key] = value
        return SmellConfig(**kwargs)

    def should_ignore_coverage(self, filepath: str) -> bool:
        """判断文件是否应该忽略覆盖率检查"""
        return self._matches_patterns(filepath, self.coverage_ignore_paths)

    def should_ignore_lint(self, filepath: str) -> bool:
        """判断文件是否应该忽略 lint 检查"""
        return self._matches_patterns(filepath, self.lint_ignore_paths)

    def _matches_patterns(self, filepath: str, patterns: list[str]) -> bool:
        """检查文件路径是否匹配任一 glob 模式 (pathspec gitignore 语义)"""
        from .checkers.git_diff import matches_ignore_patterns

        return matches_ignore_patterns(filepath, patterns)

    def to_dict(self) -> dict[str, Any]:
        """导出配置为字典"""
        return self.config.copy()

    def __repr__(self) -> str:
        return f"QualityGateConfig(path={self.config_path})"
