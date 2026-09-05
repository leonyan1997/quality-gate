"""git_diff 模块测试: ignore 匹配、行号解析、文件分类"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quality_gate.checkers.git_diff import (
    get_added_files,
    get_changed_files,
    get_git_diff_lines,
    get_numstat_added_lines,
    is_line_in_diff,
    matches_ignore_patterns,
)


class TestIgnorePatterns:
    """matches_ignore_patterns 的 .gitignore 风格匹配"""

    @pytest.mark.parametrize(
        "path,patterns,expected",
        [
            # **/generated/**
            ("src/generated/foo.rs", ["**/generated/**"], True),
            ("a/b/c/generated/x.py", ["**/generated/**"], True),
            ("src/foo.rs", ["**/generated/**"], False),
            # **/models.py
            ("app/models.py", ["**/models.py"], True),
            ("models.py", ["**/models.py"], True),
            ("app/views.py", ["**/models.py"], False),
            # 裸目录 target/
            ("scraper-rs/target/debug/x", ["target/"], True),
            ("target/CACHEDIR.TAG", ["target/"], True),
            ("src/target_x/y.rs", ["target/"], False),
            # **/*.ext
            ("index.d.ts", ["**/*.d.ts"], True),
            ("src/types/index.d.ts", ["**/*.d.ts"], True),
            ("index.ts", ["**/*.d.ts"], False),
            # node_modules
            ("src/node_modules/pkg/index.js", ["**/node_modules/**"], True),
            # 组合 + 正常文件不误伤
            (
                "web/src/foo.ts",
                ["**/generated/**", "**/node_modules/**", "target/"],
                False,
            ),
            (
                "web/src/generated/helper.ts",
                ["**/generated/**", "**/node_modules/**", "target/"],
                True,
            ),
            # 空 patterns
            ("src/foo.rs", [], False),
        ],
    )
    def test_match(self, path, patterns, expected):
        assert matches_ignore_patterns(path, patterns) is expected


class TestDiffRangeParsing:
    """git diff -U0 行号解析"""

    def test_is_line_in_diff(self):
        ranges = [(11, 14), (20, 25)]
        assert is_line_in_diff(11, ranges) is True
        assert is_line_in_diff(14, ranges) is True
        assert is_line_in_diff(20, ranges) is True
        assert is_line_in_diff(25, ranges) is True
        assert is_line_in_diff(10, ranges) is False
        assert is_line_in_diff(15, ranges) is False
        assert is_line_in_diff(19, ranges) is False
        assert is_line_in_diff(26, ranges) is False
        assert is_line_in_diff(5, []) is False


@pytest.fixture()
def git_repo(tmp_path):
    """创建临时 git 仓库，含一个已提交文件与未提交修改"""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)

    src = repo / "src"
    src.mkdir()

    # 初始提交
    f = src / "app.py"
    f.write_text("\n".join(str(i) for i in range(1, 31)), encoding="utf-8")  # 30 行
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


class TestGitDiffRealRepo:
    """真实 git diff 行为验证"""

    def test_modified_file_lines(self, git_repo):
        # 修改第 5 行并新增 31-34 行
        src_file = git_repo / "src" / "app.py"
        content = src_file.read_text(encoding="utf-8").split("\n")
        content[4] = "5 changed"
        content.extend(["31", "32", "33", "34"])
        src_file.write_text("\n".join(content), encoding="utf-8")

        ranges = get_git_diff_lines(git_repo, "src/app.py")
        assert ranges, "应解析出 diff 行范围"
        # 收集所有行号
        lines = set()
        for start, end in ranges:
            lines.update(range(start, end + 1))
        assert 5 in lines          # 修改行
        assert 31 in lines         # 新增行
        assert 1 not in lines      # 未改动行

    def test_classify_changed_files(self, git_repo):
        # 新增一个文件 + 修改一个文件
        new_file = git_repo / "src" / "new.py"
        new_file.write_text("x = 1\n", encoding="utf-8")
        (git_repo / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")

        changed = get_changed_files(git_repo)
        assert changed["src/new.py"] == "added"
        assert changed["src/app.py"] == "modified"

        added = get_added_files(git_repo)
        assert "src/new.py" in added
        assert "src/app.py" not in added

        numstat = get_numstat_added_lines(git_repo)
        assert numstat["src/new.py"] == 1


def test_cli_exits_when_outside_git_repo(tmp_path, monkeypatch):
    """无 git 仓库目录运行 check 应明确报错退出（独立工具健壮性，2026-09-04）"""
    from click.testing import CliRunner

    from quality_gate.cli import main

    monkeypatch.chdir(tmp_path)  # tmp_path 默认无 git 仓库
    runner = CliRunner()
    result = runner.invoke(main, ["check", "--lang", "python"])
    assert result.exit_code == 1
    assert "不在任何 git 仓库内" in result.output
