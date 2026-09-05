"""config 模块测试: 默认值、YAML 加载、深度合并"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quality_gate.config import (
    QualityGateConfig,
    build_smell_config,
    resolve_languages,
    smell_effective_ignore_paths,
)


class TestConfigDefaults:
    def test_default_thresholds(self):
        cfg = QualityGateConfig()
        assert cfg.get_threshold("crap") == 30
        assert cfg.get_threshold("cyclomatic_complexity") == 15
        assert cfg.get_threshold("duplication") == 3.0
        assert cfg.get_threshold("min_tokens") == 50
        assert cfg.get_threshold("min_lines") == 5
        assert cfg.get_threshold("nonexistent", "fallback") == "fallback"

    def test_default_ignore_paths(self):
        cfg = QualityGateConfig()
        assert "**/models.py" in cfg.coverage_ignore_paths
        assert "**/migrations/**" in cfg.coverage_ignore_paths
        assert "**/generated/**" in cfg.lint_ignore_paths
        assert "**/node_modules/**" in cfg.lint_ignore_paths
        # quality-gate 自身产物（scan 存档目录）必须默认忽略，
        # 否则 jscpd/ruff 会把历史 JSON 报告当代码扫（自污染）
        assert ".quality-gate/**" in cfg.lint_ignore_paths
        assert cfg.should_ignore_lint(".quality-gate/history/scan-2026.json") is True

    def test_languages_default(self):
        cfg = QualityGateConfig()
        assert cfg.languages == ["rust", "typescript", "python"]


class TestConfigLoading:
    def test_load_from_file(self, tmp_path):
        config_file = tmp_path / "quality-gate.yaml"
        config_file.write_text(
            "version: 1\n"
            "languages:\n"
            "  - rust\n"
            "thresholds:\n"
            "  duplication: 5\n",  # 覆盖默认 3.0
            encoding="utf-8",
        )
        cfg = QualityGateConfig(config_path=config_file)
        assert cfg.config_path == config_file
        assert cfg.languages == ["rust"]
        # 覆盖生效
        assert cfg.get_threshold("duplication") == 5
        # 未覆盖的保留默认
        assert cfg.get_threshold("crap") == 30

    def test_should_ignore(self, tmp_path):
        config_file = tmp_path / "quality-gate.yaml"
        config_file.write_text(
            "coverage_ignore:\n"
            "  paths:\n"
            "    - '**/models.py'\n",
            encoding="utf-8",
        )
        cfg = QualityGateConfig(config_path=config_file)
        assert cfg.should_ignore_coverage("app/models.py") is True
        assert cfg.should_ignore_coverage("app/views.py") is False
        # lint_ignore 使用默认
        assert cfg.should_ignore_lint("x/generated/y.ts") is True


class TestSmellConfig:
    """smell 段解析：默认值回落 SmellConfig、yaml 覆盖生效、规则开关语义"""

    def test_default_smell_config_matches_dataclass(self):
        """无配置时 build_smell_config == SmellConfig() 默认"""
        from quality_gate.smell.types import SmellConfig

        cfg = QualityGateConfig()
        smell_cfg = build_smell_config(cfg)

        assert isinstance(smell_cfg, SmellConfig)
        assert smell_cfg.max_function_lines == 60
        assert smell_cfg.max_class_methods == 10
        assert smell_cfg.max_class_lines == 300
        assert smell_cfg.max_parameters == 5
        assert smell_cfg.max_switch_branches == 3
        assert smell_cfg.min_lazy_class_methods == 2
        # enabled/disabled 空列表归一为 None（= 全部启用 / 不排除）
        assert smell_cfg.enabled_rules is None
        assert smell_cfg.disabled_rules is None

    def test_yaml_overrides_thresholds_and_keeps_defaults(self, tmp_path):
        config_file = tmp_path / "quality-gate.yaml"
        config_file.write_text(
            "smell:\n"
            "  max_function_lines: 60\n"   # 覆盖默认 40
            "  disabled_rules:\n"
            "    - data-class\n",
            encoding="utf-8",
        )
        cfg = QualityGateConfig(config_path=config_file)
        smell_cfg = build_smell_config(cfg)

        assert smell_cfg.max_function_lines == 60
        # 未覆盖项保留默认
        assert smell_cfg.max_class_methods == 10
        assert smell_cfg.max_switch_branches == 3
        # disabled 生效；enabled 仍为 None
        assert smell_cfg.disabled_rules == ["data-class"]
        assert smell_cfg.enabled_rules is None

    def test_enabled_rules_narrows_rule_set(self, tmp_path):
        config_file = tmp_path / "quality-gate.yaml"
        config_file.write_text(
            "smell:\n"
            "  enabled_rules:\n"
            "    - long-method\n",
            encoding="utf-8",
        )
        cfg = QualityGateConfig(config_path=config_file)
        smell_cfg = build_smell_config(cfg)

        assert smell_cfg.enabled_rules == ["long-method"]

    def test_unknown_keys_ignored(self, tmp_path):
        """smell 段未知键（非 SmellConfig 字段）被静默忽略，不抛错"""
        config_file = tmp_path / "quality-gate.yaml"
        config_file.write_text(
            "smell:\n"
            "  typo_threshold: 99\n"
            "  max_function_lines: 50\n",
            encoding="utf-8",
        )
        cfg = QualityGateConfig(config_path=config_file)
        smell_cfg = build_smell_config(cfg)

        assert smell_cfg.max_function_lines == 50
        assert smell_cfg.max_class_lines == 300

    def test_instances_do_not_leak_overrides(self, tmp_path):
        """实例间配置隔离：前一个实例的 yaml 覆盖不污染后续实例默认

        （回归：浅拷贝 DEFAULT_CONFIG 时 _merge_config update 原地修改类级
        嵌套 dict，覆盖值泄漏给之后所有新实例）
        """
        config_file = tmp_path / "quality-gate.yaml"
        config_file.write_text(
            "smell:\n"
            "  max_function_lines: 50\n"
            "  disabled_rules:\n"
            "    - data-class\n",
            encoding="utf-8",
        )
        first = QualityGateConfig(config_path=config_file)
        assert build_smell_config(first).max_function_lines == 50

        # 同一进程内的新实例必须回落干净默认
        second = QualityGateConfig()
        smell_cfg = build_smell_config(second)
        assert smell_cfg.max_function_lines == 60
        assert smell_cfg.disabled_rules is None
        # 既有嵌套段（thresholds）同样不受前例污染
        assert second.get_threshold("duplication") == 3.0


class TestSmellIgnorePaths:
    """smell.ignore.paths：独立豁免通道（B1 语义解耦）"""

    def test_default_smell_ignore_empty(self, tmp_path, monkeypatch):
        # 隔离仓库根 dogfood 配置：切到无 quality-gate.yaml 的临时目录
        monkeypatch.chdir(tmp_path)
        cfg = QualityGateConfig()
        # 生效集合 = lint ∪ smell = lint（保序去重）
        assert smell_effective_ignore_paths(cfg) == cfg.lint_ignore_paths

    def test_yaml_smell_ignore_loaded(self, tmp_path):
        config_file = tmp_path / "quality-gate.yaml"
        config_file.write_text(
            "lint_ignore:\n"
            "  paths:\n"
            "    - '**/node_modules/**'\n"
            "smell:\n"
            "  ignore:\n"
            "    paths:\n"
            "      - 'tests/smell/fixtures/**'\n",
            encoding="utf-8",
        )
        cfg = QualityGateConfig(config_path=config_file)
        effective = smell_effective_ignore_paths(cfg)
        # smell 专属豁免并入生效集合（lint 豁免保持既有覆盖语义不变）
        assert "tests/smell/fixtures/**" in effective
        assert "**/node_modules/**" in effective
        assert len(effective) == len(set(effective))  # 保序去重

    def test_smell_ignore_not_merged_into_rule_config(self, tmp_path):
        """ignore 是 config 层通道，不泄漏进 SmellConfig（未知键被跳过）"""
        config_file = tmp_path / "quality-gate.yaml"
        config_file.write_text(
            "smell:\n"
            "  ignore:\n"
            "    paths:\n"
            "      - 'tests/smell/fixtures/**'\n"
            "  max_function_lines: 50\n",
            encoding="utf-8",
        )
        cfg = QualityGateConfig(config_path=config_file)
        smell_cfg = build_smell_config(cfg)
        assert smell_cfg.max_function_lines == 50  # 常规键仍生效
        assert not hasattr(smell_cfg, "ignore")  # ignore 不进 SmellConfig


class TestResolveLanguages:
    """resolve_languages：languages 配置决定实际运行语言（A 包）"""

    def test_default_config_all_three(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # 隔离仓库根 dogfood 配置
        cfg = QualityGateConfig()
        assert resolve_languages(cfg) == ["rust", "ts", "python"]
        assert resolve_languages(cfg, "all") == ["rust", "ts", "python"]
        assert resolve_languages(cfg, "python") == ["python"]

    def test_config_languages_restrict(self, tmp_path):
        config_file = tmp_path / "quality-gate.yaml"
        config_file.write_text(
            "languages:\n"
            "  - python\n",
            encoding="utf-8",
        )
        cfg = QualityGateConfig(config_path=config_file)
        assert resolve_languages(cfg) == ["python"]
        # 显式 --lang 覆盖配置
        assert resolve_languages(cfg, "all") == ["rust", "ts", "python"]
        assert resolve_languages(cfg, "rust") == ["rust"]

    def test_typescript_alias_normalized(self, tmp_path):
        """yaml languages 写 typescript（默认/示例形态）→ canonical ts"""
        config_file = tmp_path / "quality-gate.yaml"
        config_file.write_text(
            "languages:\n"
            "  - rust\n"
            "  - typescript\n",
            encoding="utf-8",
        )
        cfg = QualityGateConfig(config_path=config_file)
        assert resolve_languages(cfg) == ["rust", "ts"]
        # 未知语言名忽略不报错
        config_file.write_text(
            "languages:\n"
            "  - python\n"
            "  - cobol\n",
            encoding="utf-8",
        )
        assert resolve_languages(QualityGateConfig(config_path=config_file)) == ["python"]
