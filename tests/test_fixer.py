"""fixer：quality-gate fix 自动修复闭环（P2）测试

语义:
  - 默认只修 diff 内文件（get_changed_files + 语言后缀 + ignore_paths）
  - python → ruff check --fix；ts → npx oxlint --fix（回退 eslint --fix）
  - rust → 仅建议，不动文件
  - 修完复用 lint checker 复检；工具缺失 → 显式错误并 blocked

子进程用 subprocess.run 路由注入（ruff/oxlint 假输出，git 走真实执行）。
"""

import subprocess

from _git_helpers import commit_all, init_git_repo

from quality_gate import fixer


def _router(monkeypatch, records: dict[str, list], fail: str | None = None):
    """radon 式路由：ruff/npx 拦截记录；其余（git 等）走真实执行"""
    real_run = subprocess.run

    def route(args, *a, **k):
        argv = list(args)
        key = None
        if argv and argv[0] == "ruff":
            key = "ruff"
        elif argv and argv[0] == "npx" and len(argv) > 1 and argv[1] in (
            "oxlint", "eslint",
        ):
            key = argv[1]
        if key is not None:
            records.setdefault(key, []).append(argv)
            if fail == key:
                raise FileNotFoundError(key)
            if key == "oxlint":
                # recheck 走 --format json（v3 dict）；--fix 走文本（空即可）
                stdout = '{"diagnostics": []}' if "--format" in argv else ""
            else:
                stdout = "[]" if "--output-format" in argv else ""
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=stdout,
            )
        return real_run(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", route)


def test_python_fix_diff_files_then_recheck(tmp_path, monkeypatch):
    """python：只把 diff 内 .py 传给 ruff --fix，修复后复检"""
    repo = init_git_repo(tmp_path)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(repo, "base")
    (repo / "app.py").write_text("import os\nx = 1\n", encoding="utf-8")
    (repo / "notes.md").write_text("n\n", encoding="utf-8")  # 非 py 不入

    records: dict[str, list] = {}
    _router(monkeypatch, records)
    outcome = fixer.apply_fix(repo, ["python"])

    ruff_fix_calls = [a for a in records.get("ruff", []) if "--fix" in a]
    assert ruff_fix_calls, "应执行 ruff --fix"
    assert ruff_fix_calls[0][-1] == "app.py", f"只应含 app.py: {ruff_fix_calls[0]}"
    assert outcome["blocked"] is False
    assert any("已自动修复 1 个文件" in s for s in outcome["summary"])


def test_python_fix_missing_tool_reports_and_blocks(tmp_path, monkeypatch):
    """ruff 缺失 → 显式错误行 + blocked（不静默通过）"""
    repo = init_git_repo(tmp_path)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

    records: dict[str, list] = {}
    _router(monkeypatch, records, fail="ruff")
    outcome = fixer.apply_fix(repo, ["python"])

    assert outcome["blocked"] is True
    assert any("ruff 未安装" in s for s in outcome["summary"])


def test_ts_fix_uses_oxlint_with_diff_files(tmp_path, monkeypatch):
    """ts：npx oxlint --fix 收到 diff 内 .ts；复检走 oxlint json"""
    repo = init_git_repo(tmp_path)
    (repo / "app.ts").write_text("export const a = 1\n", encoding="utf-8")
    commit_all(repo, "base")
    (repo / "app.ts").write_text("export const b = 2\n", encoding="utf-8")

    records: dict[str, list] = {}
    _router(monkeypatch, records)
    outcome = fixer.apply_fix(repo, ["ts"])

    ox_fix = [a for a in records.get("oxlint", []) if "--fix" in a]
    assert ox_fix and ox_fix[0][-1] == "app.ts"
    assert outcome["blocked"] is False


def test_whole_mode_walks_tree_and_honors_ignore(tmp_path, monkeypatch):
    """--all 整仓：遍历全部 .py（跳过 node_modules/豁免目录）"""
    repo = init_git_repo(tmp_path)
    (repo / "a1.py").write_text("x = 1\n", encoding="utf-8")
    subdir = repo / "sub"
    subdir.mkdir()
    (subdir / "x.py").write_text("y = 1\n", encoding="utf-8")
    gen = subdir / "generated"
    gen.mkdir()
    (gen / "g.py").write_text("z = 1\n", encoding="utf-8")
    nm = repo / "node_modules"
    nm.mkdir()
    (nm / "zz.py").write_text("w = 1\n", encoding="utf-8")

    records: dict[str, list] = {}
    _router(monkeypatch, records)
    fixer.apply_fix(repo, ["python"], ignore_paths=["**/generated/**"],
                    whole=True)

    ruff_fix = next(a for a in records.get("ruff", []) if "--fix" in a)
    joined = " ".join(ruff_fix)
    assert "a1.py" in joined and "sub/x.py" in joined
    assert "generated" not in joined and "node_modules" not in joined


def test_rust_is_advice_only(tmp_path, monkeypatch):
    """rust：不改文件、不调 ruff，仅给建议"""
    records: dict[str, list] = {}
    _router(monkeypatch, records)
    outcome = fixer.apply_fix(tmp_path, ["rust"])

    assert records.get("ruff") is None
    assert outcome["blocked"] is False
    assert any("Rust 无自动修复" in s for s in outcome["summary"])


def test_cli_fix_exit_0_and_blocking_exit_1(tmp_path, monkeypatch):
    """CLI fix：修复后无阻塞 → exit 0；仍有阻塞 → exit 1"""
    from click.testing import CliRunner

    from quality_gate import cli as cli_mod

    repo = init_git_repo(tmp_path)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

    records: dict[str, list] = {}
    _router(monkeypatch, records)
    monkeypatch.chdir(repo)
    ok = CliRunner().invoke(cli_mod.main, ["fix", "--lang", "python"])
    assert ok.exit_code == 0, ok.output
    assert "自动修复结果" in ok.output
    assert "自动修复完成" in ok.output

    # 复检返回阻塞 → exit 1
    def _blocked_recheck(lang, repo_root, ignore_paths):
        return {"blocking": True, "issues": [
            {"file": "app.py", "line": 1, "code": "F401", "message": "unused"},
        ]}

    monkeypatch.setattr(fixer, "_recheck_lint", _blocked_recheck)
    bad = CliRunner().invoke(cli_mod.main, ["fix", "--lang", "python"])
    assert bad.exit_code == 1, bad.output
    assert "仍有阻塞 lint" in bad.output
