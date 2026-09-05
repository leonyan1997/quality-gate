"""config 模块测试: 默认值、YAML 加载、深度合并"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quality_gate.config import QualityGateConfig


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
