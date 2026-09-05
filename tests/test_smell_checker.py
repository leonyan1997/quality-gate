"""checkers/smell.py 增量语义测试

覆盖（Phase 2.2 三类场景 + 锚点分类 + CLI 接线）:
  - 新增文件全量评估 → P0/P1 阻塞
  - 修改文件：diff 触及函数体使其超阈值 → 阻塞
  - 存量坏味道（diff 未触及的块 / def 行）→ 不阻塞
  - P2 坏味道 → report_only，永不阻塞
  - 行级锚点：只改函数体不重报长参数列表；改 def 行才报
  - cli: --checks smell 合法 / 未知类型报错并列出 smell
"""

import subprocess
from pathlib import Path

from click.testing import CliRunner

from quality_gate.checkers.smell import check_smell_incremental
from quality_gate.cli import main
from quality_gate.config import build_smell_config

_GIT_CFG = ["-c", "user.name=Test", "-c", "user.email=t@t.t"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *_GIT_CFG, *args],
        check=True, capture_output=True, text=True,
    )


def _write_files(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _init_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_files(repo, files)
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


def _long_func(passes: int = 44) -> str:
    """超过 max_function_lines=40 的函数源码"""
    return "def f():\n" + "    pass\n" * passes


class TestIncrementalScenes:
    def test_added_file_full_scan_blocks(self, tmp_path):
        """新增(untracked)文件：全量评估，P0/P1 进 issues 且阻塞"""
        repo = _init_repo(tmp_path, {"ok.py": "x = 1\n"})
        source = (
            "import os\n\n"
            + _long_func(44)
            + "\n\ndef many(a, b, c, d, e, f, g):\n    return a\n"
        )
        (repo / "new.py").write_text(source, encoding="utf-8")

        res = check_smell_incremental(repo)

        assert res["blocking"] is True
        assert res["report_only_issues"] == []
        codes = {i["code"] for i in res["issues"]}
        assert {"long-method", "long-parameter-list", "dead-import"} <= codes
        assert all(i["file"] == "new.py" for i in res["issues"])
        assert res["files_scanned"] == 1

    def test_modified_file_grown_function_blocks(self, tmp_path):
        """修改文件：函数体被 diff 触及并超过阈值 → long-method 阻塞"""
        repo = _init_repo(tmp_path, {"app.py": _long_func(4) + "\n"})
        # 不提交：增量语义对比 HEAD，working tree 的改动即 diff
        (repo / "app.py").write_text(_long_func(44) + "\n", encoding="utf-8")

        res = check_smell_incremental(repo)

        assert res["blocking"] is True
        long_method = next(i for i in res["issues"] if i["code"] == "long-method")
        assert long_method["file"] == "app.py"
        assert long_method["severity"] == "P0"
        assert long_method["level"] == "error"

    def test_stock_debt_not_blocking(self, tmp_path):
        """存量坏味道：超长函数 + 死导入都在 diff 外 → 不阻塞"""
        base = "import os\n\n" + _long_func(44) + "\n"
        repo = _init_repo(tmp_path, {"app.py": base})
        # 仅在文件尾部追加短函数，不触碰存量 f() 与 import os
        (repo / "app.py").write_text(
            base + "\ndef g():\n    return 1\n", encoding="utf-8",
        )

        res = check_smell_incremental(repo)

        assert res["blocking"] is False
        assert res["issues"] == []
        assert res["report_only_issues"] == []
        assert res["files_scanned"] == 1

    def test_p2_report_only_never_blocks(self, tmp_path):
        """仅含 P2（data-class/lazy-class/switch-statements）→ 不阻塞"""
        repo = _init_repo(tmp_path, {"ok.py": "x = 1\n"})
        source = """class PointHolder:
    x: int
    y: int


class TinyThing:
    def ping(self):
        return 1


def classify(v):
    match v:
        case 1:
            return "a"
        case 2:
            return "b"
        case 3:
            return "c"
        case 4:
            return "d"
"""
        (repo / "p2.py").write_text(source, encoding="utf-8")

        res = check_smell_incremental(repo)

        assert res["blocking"] is False
        assert res["issues"] == []
        assert {i["code"] for i in res["report_only_issues"]} == {
            "data-class", "lazy-class", "switch-statements",
        }
        assert all(i["severity"] == "P2" for i in res["report_only_issues"])


class TestAnchorSemantics:
    def test_body_edit_does_not_retrigger_param_list(self, tmp_path):
        """行级锚点：6 参函数是存量，只改函数体 → long-parameter-list 不重报"""
        base = "def many(a, b, c, d, e, f):\n    return a\n"
        repo = _init_repo(tmp_path, {"app.py": base})
        # def 行未变，仅 body 改动（range 2..3）
        (repo / "app.py").write_text(
            "def many(a, b, c, d, e, f):\n    return a\n    return a\n",
            encoding="utf-8",
        )

        res = check_smell_incremental(repo)

        assert res["blocking"] is False
        assert all(i["code"] != "long-parameter-list" for i in res["issues"])

    def test_def_line_change_retriggers_param_list(self, tmp_path):
        """行级锚点：def 行被 diff 命中 → 超参函数上报并阻塞"""
        base = "def many(a, b, c, d, e, f):\n    return a\n"
        repo = _init_repo(tmp_path, {"app.py": base})
        (repo / "app.py").write_text(
            "def many(a, b, c, d, e, f, g):\n    return a\n",
            encoding="utf-8",
        )

        res = check_smell_incremental(repo)

        assert res["blocking"] is True
        assert any(i["code"] == "long-parameter-list" for i in res["issues"])


class TestFullModeAndCli:
    def test_full_mode_reports_stock_issues_without_git(self, tmp_path):
        """full=True（scan 用）：无需 git，全量报告存量坏味道"""
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "bad.py").write_text(_long_func(44) + "\n", encoding="utf-8")

        res = check_smell_incremental(plain, full=True)

        assert res["blocking"] is True
        assert any(i["code"] == "long-method" for i in res["issues"])
        assert res["files_scanned"] == 1

    def test_cli_accepts_smell_check(self, tmp_path, monkeypatch):
        """check --checks smell --lang python 合法且干净仓库通过"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", *_GIT_CFG, "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", *_GIT_CFG, "commit", "-qm", "init"], cwd=tmp_path, check=True,
        )

        result = CliRunner().invoke(
            main, ["check", "--checks", "smell", "--lang", "python"],
        )

        assert result.exit_code == 0, result.output
        assert "质量门禁通过" in result.output

    def test_cli_rejects_unknown_check_and_lists_smell(self, tmp_path):
        """未知检查类型退出码 2，可用列表包含 smell"""
        result = CliRunner().invoke(main, ["check", "--checks", "bogus"])
        assert result.exit_code == 2
        assert "未知检查类型" in result.output
        assert "smell" in result.output


class TestConfiguredThresholds:
    """quality-gate.yaml 的 smell 段 → 检查器端到端生效"""

    def _build_cfg(self, repo: Path):
        from quality_gate.config import QualityGateConfig

        return QualityGateConfig(config_path=repo / "quality-gate.yaml")

    def _mk_yaml_repo(self, tmp_path: Path, yaml_body: str, source: str) -> Path:
        repo = _init_repo(tmp_path, {"app.py": "x = 1\n"})
        (repo / "quality-gate.yaml").write_text(yaml_body, encoding="utf-8")
        (repo / "app.py").write_text(source, encoding="utf-8")
        return repo

    def test_tightened_threshold_blocks_grown_function(self, tmp_path):
        """yaml 收紧 max_function_lines=5 → 8 行函数被 diff 触及即阻塞"""
        from quality_gate.smell.types import SmellConfig

        repo = self._mk_yaml_repo(
            tmp_path,
            "smell:\n  max_function_lines: 5\n",
            "def f():\n" + "    pass\n" * 8,   # 默认 40 不报，收紧后报
        )
        smell_cfg = build_smell_config(self._build_cfg(repo))
        assert isinstance(smell_cfg, SmellConfig)
        assert smell_cfg.max_function_lines == 5

        res = check_smell_incremental(repo, smell_config=smell_cfg)

        assert res["blocking"] is True
        long_method = next(i for i in res["issues"] if i["code"] == "long-method")
        assert long_method["severity"] == "P0"

    def test_disabled_rule_not_reported(self, tmp_path):
        """yaml disabled_rules 排除 long-method → 同场景不阻塞"""
        repo = self._mk_yaml_repo(
            tmp_path,
            "smell:\n"
            "  max_function_lines: 5\n"
            "  disabled_rules:\n"
            "    - long-method\n",
            "def f():\n" + "    pass\n" * 8,
        )
        smell_cfg = build_smell_config(self._build_cfg(repo))
        assert smell_cfg.disabled_rules == ["long-method"]

        res = check_smell_incremental(repo, smell_config=smell_cfg)

        assert res["blocking"] is False
        assert all(i["code"] != "long-method" for i in res["issues"])
