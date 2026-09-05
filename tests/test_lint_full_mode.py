"""lint checker full 模式测试（scan 周报用）

full=True 时不做 git diff 过滤，全部存量问题都进 issues。
用 ruff/clippy/oxlint 真实输出结构 mock subprocess，锁定解析行为。
"""

import json
import subprocess
from pathlib import Path

import pytest

from quality_gate.checkers import python_lint, rust_lint, ts_lint

_RUFF_DIAG_TMPL = {
    "location": {"row": 10, "column": 5},
    "code": "F401",
    "message": "unused import",
}


def _make_proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


@pytest.fixture()
def clean_git_repo(tmp_path: Path) -> Path:
    """干净 git 仓库：ruff 输出的文件若在 diff 外，增量模式会跳过它"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test",
         "-c", "user.email=t@t.t", "commit", "-qm", "init"],
        check=True,
    )
    return repo


def _ruff_diag(repo: Path, rel: str = "app.py") -> dict:
    """构造 ruff 诊断：filename 用仓库内绝对路径（ruff 真实行为）"""
    diag = dict(_RUFF_DIAG_TMPL)
    diag["filename"] = str(repo / rel)
    return diag


class TestPythonLintFull:
    def test_full_reports_stock_issues(self, clean_git_repo, monkeypatch):
        """文件在 diff 外（无改动），full=True 仍报告其存量问题"""
        monkeypatch.setattr(
            python_lint.subprocess, "run",
            lambda *a, **kw: _make_proc(
                0, stdout=json.dumps([_ruff_diag(clean_git_repo)]),
            ),
        )
        result = python_lint.check_python_lint_incremental(
            clean_git_repo, full=True,
        )
        assert len(result["issues"]) == 1
        assert result["issues"][0]["file"] == "app.py"
        assert result["blocking"] is True

    def test_incremental_skips_clean_file(self, clean_git_repo, monkeypatch):
        """同输出下增量模式：文件无改动 → 存量问题不阻塞"""
        monkeypatch.setattr(
            python_lint.subprocess, "run",
            lambda *a, **kw: _make_proc(
                0, stdout=json.dumps([_ruff_diag(clean_git_repo)]),
            ),
        )
        result = python_lint.check_python_lint_incremental(clean_git_repo)
        assert result["issues"] == []
        assert result["blocking"] is False


class TestRustLintFull:
    def test_full_reports_stock_issues(self, clean_git_repo, monkeypatch):
        """clippy 输出诊断（文件在 diff 外）→ full 模式报告"""
        diagnostic = {
            "reason": "compiler-message",
            "message": {
                "level": "warning",
                "code": {"code": "unused_imports"},
                "message": "unused import",
                "spans": [{
                    "is_primary": True,
                    "file_name": "src/main.rs",
                    "line_start": 3,
                    "column_start": 1,
                }],
            },
        }
        monkeypatch.setattr(
            rust_lint.subprocess, "run",
            lambda *a, **kw: _make_proc(
                0, stdout=json.dumps(diagnostic) + "\n",
            ),
        )
        result = rust_lint.check_rust_lint_incremental(
            clean_git_repo, full=True,
        )
        assert len(result["issues"]) == 1
        assert result["issues"][0]["file"] == "src/main.rs"
        assert result["blocking"] is True

    def test_full_dedupes(self, clean_git_repo, monkeypatch):
        """clippy 同一诊断多 span/重复输出 → full 模式仍去重"""
        diagnostic = {
            "reason": "compiler-message",
            "message": {
                "level": "warning",
                "code": {"code": "unused_imports"},
                "message": "unused import",
                "spans": [{
                    "is_primary": True,
                    "file_name": "src/main.rs",
                    "line_start": 3,
                    "column_start": 1,
                }],
            },
        }
        payload = "\n".join([json.dumps(diagnostic)] * 3)
        monkeypatch.setattr(
            rust_lint.subprocess, "run",
            lambda *a, **kw: _make_proc(0, stdout=payload),
        )
        result = rust_lint.check_rust_lint_incremental(
            clean_git_repo, full=True,
        )
        assert len(result["issues"]) == 1


class TestTsLintFull:
    def test_full_reports_stock_oxlint(self, clean_git_repo, monkeypatch):
        """oxlint v3+ 输出 → full 模式报告存量问题"""
        oxlint_out = {
            "diagnostics": [{
                "message": "no-unused-vars",
                "code": {"code": "no-unused-vars"},
                "severity": "warning",
                "filename": "src/app.ts",
                "labels": [{"span": {"line": 2, "column": 1}}],
            }],
        }
        monkeypatch.setattr(
            ts_lint.subprocess, "run",
            lambda *a, **kw: _make_proc(0, stdout=json.dumps(oxlint_out)),
        )
        result = ts_lint.check_ts_lint_incremental(
            clean_git_repo, full=True,
        )
        assert len(result["issues"]) == 1
        assert result["issues"][0]["file"] == "src/app.ts"

    def test_full_reports_stock_eslint(self, clean_git_repo, monkeypatch):
        """eslint 回退格式 → full 模式报告存量问题"""
        calls: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            if "oxlint" in cmd:
                raise FileNotFoundError("oxlint missing")
            return _make_proc(0, stdout=json.dumps([{
                "filePath": str(clean_git_repo / "src/app.ts"),
                "messages": [{
                    "line": 2, "column": 1,
                    "ruleId": "no-unused-vars",
                    "severity": 2,
                    "message": "unused var",
                }],
            }]))

        monkeypatch.setattr(ts_lint.subprocess, "run", fake_run)
        result = ts_lint.check_ts_lint_incremental(
            clean_git_repo, full=True,
        )
        assert any("eslint" in c for c in calls)
        assert len(result["issues"]) == 1
        assert result["issues"][0]["file"] == "src/app.ts"
        assert result["issues"][0]["level"] == "error"
