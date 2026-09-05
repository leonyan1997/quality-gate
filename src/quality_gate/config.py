"""配置文件加载器

加载 quality-gate.yaml，提供阈值、忽略路径等配置。

结构说明：
  QualityGateConfig 是纯配置门面（访问器 + 装载编排，方法数受
  结构坏味道规则约束刻意保持精简）；YAML 查找/读取/合并逻辑在模块级
  函数（_find_config_file / _read_user_yaml / _deep_merge），
  build_smell_config 亦为模块级函数——smell 段 → SmellConfig 的翻译。
"""

import copy
from dataclasses import fields
from pathlib import Path
from typing import Any, ClassVar

import yaml

from .smell.types import SmellConfig


def _find_config_file() -> Path | None:
    """递归查找配置文件（从 cwd 向上最多 5 层，兼容子项目目录）"""
    current = Path.cwd()

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


def _read_user_yaml(path: Path) -> dict[str, Any] | None:
    """读取用户 YAML；失败打印警告并返回 None（调用方回落默认配置）"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f)
    except Exception as e:
        print(f"⚠️  加载配置文件失败：{e}，使用默认配置")
        return None
    return user_config if isinstance(user_config, dict) else None


def _deep_merge(base: dict[str, Any], user: dict[str, Any]) -> None:
    """把用户配置合并进 base（同键 dict 段浅层合并，其余覆盖）"""
    for key, value in user.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key].update(value)
        else:
            base[key] = value


class QualityGateConfig:
    """Quality Gate 配置门面（默认值 + 用户配置合并 + 分段访问器）"""

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
            "max_function_lines": 60,
            "max_class_methods": 10,
            "max_class_lines": 300,
            "max_parameters": 5,
            "max_switch_branches": 3,
            "min_lazy_class_methods": 2,
            "enabled_rules": [],
            "disabled_rules": [],
            # smell 专属豁免（B1）：fixtures/故意样本/测试桩显式豁免通道。
            # 不是 SmellConfig 字段，不参与 build_smell_config 翻译——
            # 经模块级 smell_effective_ignore_paths() 读取（并集 lint）
            "ignore": {
                "paths": [],
            },
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
        # 类级 DEFAULT_CONFIG 共享引用，合并 update 会原地污染类级默认，
        # 泄漏给后续实例（配置合并单测可复现）
        self.config = copy.deepcopy(self.DEFAULT_CONFIG)

        if config_path and config_path.exists():
            found: Path | None = config_path
        else:
            found = _find_config_file()

        if found is not None:
            user_config = _read_user_yaml(found)
            if user_config:
                _deep_merge(self.config, user_config)
                self.config_path = found

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

    def should_ignore_coverage(self, filepath: str) -> bool:
        """判断文件是否应该忽略覆盖率检查"""
        from .checkers.git_diff import matches_ignore_patterns

        return matches_ignore_patterns(filepath, self.coverage_ignore_paths)

    def should_ignore_lint(self, filepath: str) -> bool:
        """判断文件是否应该忽略 lint 检查"""
        from .checkers.git_diff import matches_ignore_patterns

        return matches_ignore_patterns(filepath, self.lint_ignore_paths)

    def __repr__(self) -> str:
        return f"QualityGateConfig(path={self.config_path})"


def build_smell_config(config: QualityGateConfig) -> SmellConfig:
    """构建 smell 引擎配置：quality-gate.yaml 的 smell 段覆盖 SmellConfig 默认

    覆盖键直接映射 SmellConfig dataclass 字段（阈值 + enabled/disabled_rules），
    非法/None 键跳过；未覆盖项回落 smell.types.SmellConfig 字段默认值
    （单一事实源在 dataclass，此处不复制默认值防漂移）。
    """
    smell_cfg = config.config.get("smell") or {}
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


def smell_effective_ignore_paths(config: QualityGateConfig) -> list[str]:
    """smell 检查生效的豁免集合 = lint_ignore.paths ∪ smell.ignore.paths

    B1（2026-09-05 自身债务清理）：smell 不再复用 lint_ignore（语义污染
    ——lint 豁免不该管 smell）；fixtures/故意样本/测试桩经 quality-gate.yaml
    smell.ignore.paths 显式豁免。生效集合取并集向后兼容（既有靠 lint_ignore
    豁免 smell 的项目行为不变），保序去重。

    模块级函数而非 QualityGateConfig 方法：P4.1 裁决过该类刻意保持在
    10 方法阈值下（large-class 规则），新增访问器会让自身 scan 再报大类。
    """
    merged = list(config.lint_ignore_paths)
    merged += config.config.get("smell", {}).get("ignore", {}).get("paths", [])
    return list(dict.fromkeys(merged))
