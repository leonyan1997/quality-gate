"""重复代码检测器测试

用 monkeypatch 模拟 jscpd 行为，覆盖:
  - jscpd 未安装 → 阻塞 + not_found
  - 无新增行 → 跳过（不跑 jscpd）
  - untracked 新文件全文件视为新增（重复 → 阻塞）
  - 重复块在未改动文件（不在 diff）→ 不阻塞（增量门禁核心）
  - 重复率超阈值 → 阻塞
"""

import json
import subprocess
from pathlib import Path

import pytest

from quality_gate.checkers import duplication as dup


def _make_proc(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr="",
    )


def _stub_jscpd(monkeypatch, proc):
    """只拦截 jscpd 子进程调用，git 命令走真实逻辑"""
    real_run = subprocess.run

    def _run(args, *a, **k):
        if isinstance(args, list) and args and args[0] == "jscpd":
            return proc
        return real_run(args, *a, **k)

    monkeypatch.setattr(dup.subprocess, "run", _run)
@pytest.fixture()
def git_repo(tmp_path: Path):
    """真实 git 仓库，含一个已提交文件 main.py（工作区干净基线）"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test",
         "-c", "user.email=t@t.t", "commit", "-qm", "init"],
        check=True,
    )
    return repo


def _write_untracked(repo: Path, name: str, content: str) -> None:
    """写入未跟踪新文件（模拟本地新增，不 add 不 commit）"""
    (repo / name).write_text(content, encoding="utf-8")


def _make_jscpd_json(duplicates: list[dict]) -> str:
    return json.dumps({"duplicates": duplicates, "statistics": {}})


def test_jscpd_missing_blocks(git_repo, monkeypatch):
    """jscpd 不存在 (FileNotFoundError) → 阻塞 + 安装提示"""
    _write_untracked(git_repo, "new.py", "a = 1\nb = 2\n")

    real_run = subprocess.run

    def _raise(args, *a, **k):
        if isinstance(args, list) and args and args[0] == "jscpd":
            raise FileNotFoundError("jscpd")
        return real_run(args, *a, **k)
    monkeypatch.setattr(dup.subprocess, "run", _raise)
    result = dup.check_duplication_incremental(git_repo)
    assert result["blocking"] is True
    assert result["issues"][0]["code"] == "not_found"
    assert "jscpd" in result["issues"][0]["message"]


def test_no_added_lines_skips(git_repo, monkeypatch):
    """工作区干净（无新增行）→ 不跑 jscpd、不阻塞"""
    real_run = subprocess.run
    called = {"n": 0}

    def _run(args, *a, **k):
        if isinstance(args, list) and args and args[0] == "jscpd":
            called["n"] += 1
            return _make_proc(0)
        return real_run(args, *a, **k)
    monkeypatch.setattr(dup.subprocess, "run", _run)
    result = dup.check_duplication_incremental(git_repo)
    assert result["blocking"] is False
    assert result["total_added_lines"] == 0
    assert called["n"] == 0  # jscpd 不应被执行


def test_duplication_untracked_new_file_blocks(git_repo, monkeypatch):
    """untracked 新文件 20 行全部与已提交 base.py 重复 → 100% > 3% 阻塞"""
    # base.py 提交为基线
    base_content = "".join(f"v{i} = {i}\n" for i in range(20))
    (git_repo / "base.py").write_text(base_content, encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "base.py"], check=True)
    subprocess.run(
        ["git", "-C", str(git_repo), "-c", "user.name=Test",
         "-c", "user.email=t@t.t", "commit", "-qm", "add base"],
        check=True,
    )
    # dup.py 是 untracked 新文件：全文件都是新增行
    _write_untracked(git_repo, "dup.py", base_content)

    payload = _make_jscpd_json([{
        "firstFile": {"name": "dup.py", "start": 1, "end": 20},
        "secondFile": {"name": "base.py", "start": 1, "end": 20},
        "lines": 20, "tokens": 60, "format": "python",
    }])
    _stub_jscpd(monkeypatch, _make_proc(1, payload))
    result = dup.check_duplication_incremental(git_repo, threshold=3.0)
    assert result["blocking"] is True
    assert result["total_added_lines"] == 20
    assert result["duplicated_added_lines"] == 20
    assert any(i["code"] == "duplication_threshold" for i in result["issues"])


def test_duplication_below_threshold_passes(git_repo, monkeypatch):
    """untracked 新文件无重复块 → 0% 不阻塞"""
    _write_untracked(
        git_repo, "fresh.py",
        "".join(f"print('line{i}')\n" for i in range(6)),
    )
    payload = _make_jscpd_json([])
    _stub_jscpd(monkeypatch, _make_proc(0, payload))
    result = dup.check_duplication_incremental(git_repo, threshold=3.0)
    assert result["blocking"] is False
    assert result["duplication_rate"] == 0.0
    assert result["total_added_lines"] == 6


def test_duplication_in_untouched_file_not_blocking(git_repo, monkeypatch):
    """重复块位于两个均已提交的未改动文件（不在 diff）→ 不阻塞（增量门禁核心）"""
    old_content = "".join(f"x{i} = {i}\n" for i in range(20))
    (git_repo / "old.py").write_text(old_content, encoding="utf-8")
    (git_repo / "old2.py").write_text(old_content, encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "old.py", "old2.py"],
                   check=True)
    subprocess.run(
        ["git", "-C", str(git_repo), "-c", "user.name=Test",
         "-c", "user.email=t@t.t", "commit", "-qm", "add old pair"],
        check=True,
    )
    # main.py 追加一行（工作区修改；main.py 在 diff 中只含第 2 行）
    with open(git_repo / "main.py", "a", encoding="utf-8") as f:
        f.write("y = 2\n")
    # jscpd 报告的重复块在 old.py / old2.py：两者都不在 diff → overlap 0
    payload = _make_jscpd_json([{
        "firstFile": {"name": "old.py", "start": 1, "end": 20},
        "secondFile": {"name": "old2.py", "start": 1, "end": 20},
        "lines": 20, "tokens": 60, "format": "python",
    }])
    _stub_jscpd(monkeypatch, _make_proc(1, payload))
    result = dup.check_duplication_incremental(git_repo, threshold=3.0)
    assert result["blocking"] is False
    assert result["duplicated_added_lines"] == 0
    assert result["total_added_lines"] == 1


def test_jscpd_v4_name_with_format_suffix_not_untracked(git_repo, monkeypatch):
    """回归：jscpd v4 JSON name 形如 ``<path>:<format>``（如 old.py:python）

    冒号后缀不剥离 → git ls-files 查不到 → 误判 untracked → 已提交文件的
    存量重复被当全文件"新增"阻塞（修复前 bug：增量门禁现场 103% 误报，
    重复块全在 diff 外的存档文件）。
    """
    old_content = "".join(f"v{i} = {i}\n" for i in range(20))
    (git_repo / "old.py").write_text(old_content, encoding="utf-8")
    (git_repo / "old2.py").write_text(old_content, encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "old.py", "old2.py"],
                   check=True)
    subprocess.run(
        ["git", "-C", str(git_repo), "-c", "user.name=Test",
         "-c", "user.email=t@t.t", "commit", "-qm", "add old pair"],
        check=True,
    )
    # main.py 追加一行 → 唯一新增行
    with open(git_repo / "main.py", "a", encoding="utf-8") as f:
        f.write("y = 2\n")

    # 真实 jscpd v4 形态：name 带 ":python" 后缀
    payload = _make_jscpd_json([{
        "firstFile": {"name": "old.py:python", "start": 1, "end": 20},
        "secondFile": {"name": "old2.py:python", "start": 1, "end": 20},
        "lines": 20, "tokens": 60, "format": "python",
    }])
    _stub_jscpd(monkeypatch, _make_proc(1, payload))
    result = dup.check_duplication_incremental(git_repo, threshold=3.0)
    # 存量重复不在 diff → 不阻塞；修复前会误判 20/1 = 2000% 阻塞
    assert result["blocking"] is False
    assert result["duplicated_added_lines"] == 0
