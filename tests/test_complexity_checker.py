"""complexity checker: Python CRAP 增量阻塞（P1）测试

语义（P1，2026-09）:
  - full=False (check --diff): 只评估 diff 内 Python 文件；
      def 行在 diff 新增行内 ∧ cc ≥ 阈值 ∧ 函数体 ≥5 行 ∧ 不在
      function_ignore → 阻塞；触及但非新增/体短 → 仅报告。
      无 Python 文件改动 → 不跑 radon。
  - full=True (scan 周报): 整仓超阈值函数进 issues（=report_only），仅报告。
  - radon 缺失/超时/解析失败 → 显式 skipped + 原因（B 包语义保留）。

radon 输出由 monkeypatch subprocess.run 注入（真实 git 走原 subprocess）。
"""

import json
import subprocess

from quality_gate.checkers.complexity import (
    CrapOptions,
    check_python_crap_incremental,
)


def _fn(name: str, lineno: int, endline: int, complexity: int) -> dict:
    return {"type": "function", "name": name, "lineno": lineno,
            "endline": endline, "complexity": complexity, "closures": []}


def _init_repo(tmp_path) -> object:
    """最小真实 git 仓库（增量语义依赖 git diff）"""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T",
         "-c", "user.email=t@t.t", "commit", "-qm", "init", "--allow-empty"],
        check=True,
    )
    return repo


def _commit(repo, message: str = "wip") -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T",
         "-c", "user.email=t@t.t", "commit", "-qm", message],
        check=True,
    )


def _radon_router(monkeypatch, payload: dict | None = None,
                  raise_radon: bool = False, calls: list | None = None):
    """subprocess.run 路由：radon → 注入 payload；其余（git 等）走真实执行"""
    real_run = subprocess.run

    def router(args, *a, **k):
        if isinstance(args, (list, tuple)) and args and args[0] == "radon":
            if calls is not None:
                calls.append(list(args))
            if raise_radon:
                raise FileNotFoundError("radon")
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(payload or {}),
            )
        return real_run(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", router)
    return calls


def test_no_python_diff_skips_radon(tmp_path, monkeypatch):
    """无 Python 文件在 diff → radon 不被调用，空结果不阻塞"""
    repo = _init_repo(tmp_path)
    (repo / "notes.md").write_text("todo\n", encoding="utf-8")  # 仅非 py 改动

    calls: list = []
    _radon_router(monkeypatch, calls=calls)
    result = check_python_crap_incremental(repo)

    assert calls == [], "无 Python 改动不应运行 radon"
    assert result["blocking"] is False
    assert result["issues"] == []
    assert result["report_only_issues"] == []
    assert "skipped" not in result


def test_new_untracked_file_high_cc_blocks(tmp_path, monkeypatch):
    """untracked 新文件 = 全文件新增；cc≥阈值 且 体≥5 的新函数阻塞"""
    repo = _init_repo(tmp_path)
    # def 第 1 行 → 函数体 5 行（行 2-6），endline=6 → body_len=5 达阻塞门槛
    (repo / "app.py").write_text(
        "def hot_fn():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n"
        "    return a\n",
        encoding="utf-8",
    )
    _radon_router(
        monkeypatch,
        payload={"app.py": [_fn("hot_fn", 1, 6, 31)]},
    )

    result = check_python_crap_incremental(repo)

    assert result["blocking"] is True
    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["code"] == "CRAP"
    assert issue["file"] == "app.py"
    assert issue["function"] == "hot_fn"
    assert issue["complexity"] == 31


def test_existing_function_body_touched_not_blocked(tmp_path, monkeypatch):
    """存量超阈值函数仅改函数体（def 行未新增）→ 不阻塞，仅报告"""
    repo = _init_repo(tmp_path)
    (repo / "app.py").write_text(
        "def hot_fn():\n    x = 1\n    y = 2\n    z = 3\n    return x\n\n"
        "def other():\n    return 1\n",
        encoding="utf-8",
    )
    _commit(repo, "base")
    # 修改函数体第 2 行（def 行 1 不在新增范围）
    text = (repo / "app.py").read_text(encoding="utf-8").replace(
        "    x = 1\n", "    x = 10  # touched\n",
    )
    (repo / "app.py").write_text(text, encoding="utf-8")

    _radon_router(
        monkeypatch,
        payload={"app.py": [_fn("hot_fn", 1, 5, 31)]},
    )

    result = check_python_crap_incremental(repo)

    assert result["blocking"] is False
    assert result["issues"] == []
    assert len(result["report_only_issues"]) == 1
    assert result["report_only_issues"][0]["function"] == "hot_fn"


def test_appended_new_def_blocks(tmp_path, monkeypatch):
    """存量文件末尾新增 def（def 行在新增行内）→ 阻塞"""
    repo = _init_repo(tmp_path)
    base = (
        "def existing():\n    x = 1\n    y = 2\n    z = 3\n    return x\n"
    )
    (repo / "app.py").write_text(base, encoding="utf-8")
    _commit(repo, "base")

    appended = (
        "def new_hot():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n"
        "    return a\n"
    )
    (repo / "app.py").write_text(base + appended, encoding="utf-8")

    # base 5 行 + appended 6 行 → new_hot def 在第 6 行、endline=11（体 5 行）
    payload = {
        "app.py": [
            _fn("existing", 1, 5, 10),
            _fn("new_hot", 6, 11, 31),
        ],
    }
    _radon_router(monkeypatch, payload=payload)

    result = check_python_crap_incremental(repo)

    assert result["blocking"] is True
    assert [i["function"] for i in result["issues"]] == ["new_hot"]
    # existing 未被新增行触及 → 不进任何列表
    assert result["report_only_issues"] == []


def test_short_function_exempt_from_blocking(tmp_path, monkeypatch):
    """函数体 <5 行豁免：不阻塞，进 report_only 保持透明"""
    repo = _init_repo(tmp_path)
    # def 在第 1 行、endline=2 → 函数体 1 行
    (repo / "app.py").write_text(
        "def terse():\n    return 1\n", encoding="utf-8",
    )
    _radon_router(
        monkeypatch,
        payload={"app.py": [_fn("terse", 1, 2, 40)]},
    )

    result = check_python_crap_incremental(repo)

    assert result["blocking"] is False
    assert result["issues"] == []
    assert len(result["report_only_issues"]) == 1


def test_function_ignore_exempts(tmp_path, monkeypatch):
    """function_ignore 同名豁免：完全不上报"""
    repo = _init_repo(tmp_path)
    (repo / "app.py").write_text(
        "def hot_legacy():\n    a = 1\n    b = 2\n    c = 3\n    return a\n",
        encoding="utf-8",
    )
    _radon_router(
        monkeypatch,
        payload={"app.py": [_fn("hot_legacy", 1, 5, 45)]},
    )

    result = check_python_crap_incremental(
        repo, options=CrapOptions(function_ignore=["hot_legacy"]),
    )

    assert result["blocking"] is False
    assert result["issues"] == []
    assert result["report_only_issues"] == []


def test_radon_missing_is_visible_skip(tmp_path, monkeypatch):
    """diff 有 Python 文件但 radon 缺失 → skipped 带原因，不静默"""
    repo = _init_repo(tmp_path)
    (repo / "app.py").write_text(
        "def hot_fn():\n    a = 1\n    b = 2\n    c = 3\n    return a\n",
        encoding="utf-8",
    )
    _radon_router(monkeypatch, raise_radon=True)

    result = check_python_crap_incremental(repo)

    assert result["blocking"] is False
    assert result["issues"] == []
    assert result["skipped"] is not None
    assert "radon" in result["skipped"]


def test_radon_parse_failure_is_visible_skip(tmp_path, monkeypatch):
    """radon 输出不可解析 → skipped 而非静默空"""
    repo = _init_repo(tmp_path)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    real_run = subprocess.run

    def router(args, *a, **k):
        if isinstance(args, (list, tuple)) and args and args[0] == "radon":
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="not json",
            )
        return real_run(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", router)
    result = check_python_crap_incremental(repo)

    assert result["issues"] == []
    assert result["blocking"] is False
    assert result["skipped"] is not None
    assert "解析失败" in result["skipped"]


def test_full_mode_reports_whole_repo_only(tmp_path, monkeypatch):
    """full=True（scan 周报）: 整仓超阈值函数仅报告，不阻塞（P1 前行为）"""
    _radon_router(
        monkeypatch,
        payload={
            "a.py": [_fn("a_hot", 1, 20, 31), _fn("a_ok", 22, 30, 10)],
            "b.py": [_fn("b_hot", 3, 40, 35)],
        },
    )

    result = check_python_crap_incremental(
        tmp_path, options=CrapOptions(full=True),
    )

    assert result["blocking"] is False
    assert len(result["issues"]) == 2
    assert result["issues"] == result["report_only_issues"]
    assert "skipped" not in result
    funcs = sorted(i["function"] for i in result["issues"])
    assert funcs == ["a_hot", "b_hot"]
