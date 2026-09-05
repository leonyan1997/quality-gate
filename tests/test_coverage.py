"""TS/Python 覆盖率检查器测试

mock 覆盖率报告文件 + 真实 git 仓库，覆盖:
  - 无报告且工具缺失 → 跳过
  - 新增文件 covered=0 → 阻塞
  - 新增文件 covered>0 → 通过
  - ignore 模式豁免
  - 报告解析失败 → 阻塞
"""

import json
import subprocess
from pathlib import Path

import pytest

from quality_gate.checkers import python_coverage as pycov
from quality_gate.checkers import ts_coverage as tscov


@pytest.fixture()
def git_repo(tmp_path: Path):
    """真实 git 仓库，基线含已提交文件"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "main.ts").write_text("export const x = 1;\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test",
         "-c", "user.email=t@t.t", "commit", "-qm", "init"],
        check=True,
    )
    return repo


def _write_untracked(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")


def _ts_summary(covered_files: dict[str, dict]) -> str:
    """构造 vitest coverage-summary.json"""
    out = {"total": {"lines": {"total": 10, "covered": 5, "skipped": 0, "pct": 50}}}
    for path, cov in covered_files.items():
        out[path] = {"lines": {"total": cov["total"], "covered": cov["covered"],
                               "skipped": 0, "pct": 0}}
    return json.dumps(out)


def _py_coverage(files: dict[str, dict]) -> str:
    """构造 coverage.py json"""
    out = {"meta": {"version": "7.0"}, "files": {}}
    for path, cov in files.items():
        out["files"][path] = {
            "summary": {"num_statements": cov["total"],
                        "covered_lines": cov["covered"]},
        }
    return json.dumps(out)


# ---------- TS ----------

def test_ts_no_report_tool_missing_skips(git_repo, monkeypatch):
    _write_untracked(git_repo, "new.ts", "export const y = 2;\n")
    monkeypatch.setattr(tscov.shutil, "which", lambda name: None)
    result = tscov.check_ts_coverage_incremental(git_repo)
    assert result["blocking"] is False
    assert result["skipped"] is not None


def test_ts_zero_coverage_blocks(git_repo):
    _write_untracked(git_repo, "new.ts", "export const y = 2;\n")
    (git_repo / "coverage").mkdir()
    summary = _ts_summary({"new.ts": {"total": 5, "covered": 0}})
    (git_repo / "coverage" / "coverage-summary.json").write_text(summary, encoding="utf-8")
    result = tscov.check_ts_coverage_incremental(git_repo)
    assert result["blocking"] is True
    assert any(i["code"] == "zero_coverage" and "new.ts" in i["message"]
               for i in result["issues"])


def test_ts_covered_passes(git_repo):
    _write_untracked(git_repo, "new.ts", "export const y = 2;\n")
    (git_repo / "coverage").mkdir()
    summary = _ts_summary({"new.ts": {"total": 5, "covered": 5}})
    (git_repo / "coverage" / "coverage-summary.json").write_text(summary, encoding="utf-8")
    result = tscov.check_ts_coverage_incremental(git_repo)
    assert result["blocking"] is False
    assert result["issues"] == []


def test_ts_v8_absolute_path_keys_match(git_repo):
    """vitest coverage-v8 的 summary key 是绝对路径（真实输出格式）→ 必须能匹配新增文件"""
    (git_repo / "src").mkdir(exist_ok=True)
    _write_untracked(git_repo, "src/new.ts", "export const y = 2;\n")
    (git_repo / "coverage").mkdir()
    abs_key = str((git_repo / "src" / "new.ts").resolve())
    summary = _ts_summary({abs_key: {"total": 5, "covered": 3}})
    (git_repo / "coverage" / "coverage-summary.json").write_text(summary, encoding="utf-8")
    result = tscov.check_ts_coverage_incremental(git_repo)
    assert result["blocking"] is False
    assert "src/new.ts" in result["coverage_data"]


def test_ts_v8_absolute_path_zero_coverage_blocks(git_repo):
    """绝对路径 key 下 0 覆盖必须阻塞（此前会漏报为'未收录'）"""
    (git_repo / "src").mkdir(exist_ok=True)
    _write_untracked(git_repo, "src/new.ts", "export const y = 2;\n")
    (git_repo / "coverage").mkdir()
    abs_key = str((git_repo / "src" / "new.ts").resolve())
    summary = _ts_summary({abs_key: {"total": 5, "covered": 0}})
    (git_repo / "coverage" / "coverage-summary.json").write_text(summary, encoding="utf-8")
    result = tscov.check_ts_coverage_incremental(git_repo)
    assert result["blocking"] is True
    assert any(i["code"] == "zero_coverage" and "src/new.ts" in i["message"]
               for i in result["issues"])


def test_ts_ignore_exempts(git_repo):
    _write_untracked(git_repo, "types.d.ts", "export type T = string;\n")
    (git_repo / "coverage").mkdir()
    summary = _ts_summary({"types.d.ts": {"total": 5, "covered": 0}})
    (git_repo / "coverage" / "coverage-summary.json").write_text(summary, encoding="utf-8")
    result = tscov.check_ts_coverage_incremental(git_repo)
    assert result["blocking"] is False  # *.d.ts 默认豁免


def test_ts_bad_report_blocks(git_repo):
    (git_repo / "coverage").mkdir()
    (git_repo / "coverage" / "coverage-summary.json").write_text(
        "not json", encoding="utf-8")
    result = tscov.check_ts_coverage_incremental(git_repo)
    assert result["blocking"] is True
    assert result["issues"][0]["code"] == "parse_error"


# ---------- Python ----------

def test_py_no_report_tool_missing_skips(git_repo, monkeypatch):
    _write_untracked(git_repo, "newmod.py", "a = 1\n")
    monkeypatch.setattr(pycov.shutil, "which", lambda name: None)
    result = pycov.check_python_coverage_incremental(git_repo)
    assert result["blocking"] is False
    assert result["skipped"] is not None


def test_py_zero_coverage_blocks(git_repo):
    _write_untracked(git_repo, "newmod.py", "a = 1\n")
    (git_repo / "coverage").mkdir()
    data = _py_coverage({str((git_repo / "newmod.py").resolve()):
                         {"total": 3, "covered": 0}})
    (git_repo / "coverage" / "coverage.json").write_text(data, encoding="utf-8")
    result = pycov.check_python_coverage_incremental(git_repo)
    assert result["blocking"] is True
    assert any(i["code"] == "zero_coverage" and "newmod.py" in i["message"]
               for i in result["issues"])


def test_py_covered_passes(git_repo):
    _write_untracked(git_repo, "newmod.py", "a = 1\n")
    (git_repo / "coverage").mkdir()
    data = _py_coverage({str((git_repo / "newmod.py").resolve()):
                         {"total": 3, "covered": 3}})
    (git_repo / "coverage" / "coverage.json").write_text(data, encoding="utf-8")
    result = pycov.check_python_coverage_incremental(git_repo)
    assert result["blocking"] is False


def test_py_ignore_exempts_models(git_repo):
    _write_untracked(git_repo, "models.py", "class M: pass\n")
    (git_repo / "coverage").mkdir()
    data = _py_coverage({str((git_repo / "models.py").resolve()):
                         {"total": 3, "covered": 0}})
    (git_repo / "coverage" / "coverage.json").write_text(data, encoding="utf-8")
    result = pycov.check_python_coverage_incremental(git_repo)
    assert result["blocking"] is False  # **/models.py 默认豁免
