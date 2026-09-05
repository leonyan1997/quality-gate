"""scan 全仓扫描（周报）测试

覆盖:
  - run_full_scan 编排：full 模式参数透传 + 各语言检查组装
  - 存档/加载：save_scan_report → latest_report_path → load_report 往返
  - compare_reports：lint/重复块/CRAP 计数趋势对比
  - cli._scan_summary：汇总计数提取
"""

import json

from quality_gate import scanner
from quality_gate.cli import _scan_summary
from quality_gate.smell.types import SmellConfig


def _lint_result(n: int, skipped: str | None = None) -> dict:
    """构造 lint checker 返回结构（n 个 issues 或 skipped）"""
    if skipped:
        return {"blocking": False, "issues": [], "skipped": skipped}
    return {
        "blocking": bool(n),
        "issues": [
            {"file": f"f{i}.py", "line": 1, "code": "E001",
             "message": f"issue {i}"}
            for i in range(n)
        ],
    }


def _dup_result(n: int, skipped: str | None = None) -> dict:
    if skipped:
        return {"blocking": False, "issues": [], "skipped": skipped,
                "duplication_rate": 0.0}
    return {
        "blocking": False,
        "issues": [
            {"file": f"d{i}.py", "line": 1, "code": "duplication",
             "message": f"dup {i}"}
            for i in range(n)
        ],
        "duplication_rate": 1.0,
    }


def _dep_result(n: int = 0, skipped: str | None = None) -> dict:
    if skipped:
        return {"blocking": False, "issues": [], "skipped": skipped}
    return {
        "blocking": bool(n),
        "issues": [
            {"file": "Cargo.toml", "line": 0, "code": "deny:x",
             "message": f"dep {i}"}
            for i in range(n)
        ],
    }


def _crap_result(n: int, prefix: str = "f") -> dict:
    return {
        "blocking": False,
        "issues": [
            {"file": "a.py", "line": i, "code": "CRAP",
             "message": f"crap {i}", "complexity": 31 + i,
             "function": f"{prefix}_fn{i}"}
            for i in range(n)
        ],
        "report_only_issues": [],
    }


def _smell_result(n_blocking: int, n_report: int = 0) -> dict:
    """构造 smell checker 返回结构（n_blocking 个 P0/P1 + n_report 个 P2）"""
    return {
        "blocking": bool(n_blocking),
        "issues": [
            {"file": "s.py", "line": i, "code": "long-method",
             "message": f"smell {i}", "severity": "P0"}
            for i in range(n_blocking)
        ],
        "report_only_issues": [
            {"file": "s.py", "line": i, "code": "data-class",
             "message": f"smell {i}", "severity": "P2"}
            for i in range(n_report)
        ],
        "files_scanned": 1,
        "tool": "smell-rules",
    }


def _stub_full_scan_checkers(monkeypatch, calls: dict) -> None:
    """把 scanner 的 checker 全部换成记录调用的 stub（run_full_scan 编排测试用）

    stub 形状按真实 checker 签名收窄为最少参数；return 用 *_result helper
    构造固定结构，使 run_full_scan 的组装/透传可被断言而不真正扫描。
    """
    def fake_py_lint(repo_root, verbose=False, ignore_paths=None, full=False):
        calls["python_lint_full"] = full
        return _lint_result(0)

    def fake_rust_lint(repo_root, verbose=False, ignore_paths=None, full=False):
        calls["rust_lint_full"] = full
        return _lint_result(0)

    def fake_ts_lint(repo_root, verbose=False, ignore_paths=None, full=False):
        calls["ts_lint_full"] = full
        return _lint_result(0)

    def fake_dup(repo_root, verbose=False, options=None, full=False):
        calls["dup_full"] = full
        return _dup_result(0)

    def fake_dep(repo_root, lang, verbose=False):
        calls["dep_lang"] = lang
        return _dep_result()

    def fake_crap(repo_root, verbose=False, ignore_paths=None,
                  crap_threshold=30):
        return _crap_result(0)

    def fake_complexity(repo_root, verbose=False, ignore_paths=None,
                        threshold=15):
        return {"blocking": False, "issues": [], "report_only_issues": []}

    def fake_smell(repo_root, verbose=False, ignore_paths=None, full=False,
                   smell_config=None):
        calls["smell_full"] = full
        calls["smell_cfg"] = smell_config
        return _smell_result(0)

    monkeypatch.setattr(scanner, "check_python_lint_incremental", fake_py_lint)
    monkeypatch.setattr(scanner, "check_rust_lint_incremental", fake_rust_lint)
    monkeypatch.setattr(scanner, "check_ts_lint_incremental", fake_ts_lint)
    monkeypatch.setattr(scanner, "check_duplication_incremental", fake_dup)
    monkeypatch.setattr(scanner, "check_dependency_incremental", fake_dep)
    monkeypatch.setattr(scanner, "check_python_crap_incremental", fake_crap)
    monkeypatch.setattr(scanner, "check_rust_complexity_incremental",
                        fake_complexity)
    monkeypatch.setattr(scanner, "check_smell_incremental", fake_smell)


class TestRunFullScan:
    """run_full_scan 编排：monkeypatch checker 返回值，验证组装与 full 透传"""

    def test_calls_checkers_with_full_true(self, tmp_path, monkeypatch):
        """lint/duplication 以 full=True 调用；dependency 无 full 参数"""
        calls: dict[str, bool | None] = {}
        _stub_full_scan_checkers(monkeypatch, calls)

        results = scanner.run_full_scan(tmp_path)

        # 三语言 lint + duplication 均为 full 模式
        assert calls["python_lint_full"] is True
        assert calls["rust_lint_full"] is True
        assert calls["ts_lint_full"] is True
        assert calls["dup_full"] is True
        # smell 挂 python 分支，full=True（无需 git diff，全量评估）
        assert calls["smell_full"] is True
        assert "smell" in results["python"]
        # yaml smell 段配置贯通：build_smell_config() 结果传入 checker
        assert isinstance(calls["smell_cfg"], SmellConfig)
        assert calls["smell_cfg"].max_function_lines == 60
        # dependency 按语言分发
        assert calls["dep_lang"] in ("rust", "ts", "python")
        # 结构: rust/ts/python 各含 lint(+dependency/complexity), duplication 顶层
        assert "rust" in results and "ts" in results and "python" in results
        assert "duplication" in results
        assert results["checks_run"] == scanner.DEFAULT_SCAN_CHECKS

    def test_lang_filter_python_only(self, tmp_path, monkeypatch):
        """--lang python 只跑 Python checker"""
        called: list[str] = []

        def fake_py_lint(*a, **kw):
            called.append("python")
            return _lint_result(0)

        def fake_rust_lint(*a, **kw):
            called.append("rust")
            return _lint_result(0)

        def fake_ts_lint(*a, **kw):
            called.append("ts")
            return _lint_result(0)

        def fake_dep(*a, **kw):
            return _dep_result()

        def fake_crap(*a, **kw):
            return _crap_result(0)

        def fake_smell(*a, **kw):
            return _smell_result(0)

        monkeypatch.setattr(scanner, "check_python_lint_incremental", fake_py_lint)
        monkeypatch.setattr(scanner, "check_rust_lint_incremental", fake_rust_lint)
        monkeypatch.setattr(scanner, "check_ts_lint_incremental", fake_ts_lint)
        monkeypatch.setattr(scanner, "check_dependency_incremental", fake_dep)
        monkeypatch.setattr(scanner, "check_python_crap_incremental", fake_crap)
        monkeypatch.setattr(scanner, "check_smell_incremental", fake_smell)

        results = scanner.run_full_scan(tmp_path, lang="python")
        assert called == ["python"]
        assert "rust" not in results
        assert "ts" not in results
        assert "python" in results
        # smell 只挂 python 分支
        assert "smell" in results["python"]

    def test_config_languages_restrict_default_scan(self, tmp_path, monkeypatch):
        """config languages=[python] → 缺省 lang(None) 只跑 python（A 包）"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "quality-gate.yaml").write_text(
            "languages:\n  - python\n", encoding="utf-8",
        )
        called: list[str] = []

        def fake_py_lint(*a, **kw):
            called.append("python")
            return _lint_result(0)

        def fake_rust_lint(*a, **kw):
            called.append("rust")
            return _lint_result(0)

        def fake_ts_lint(*a, **kw):
            called.append("ts")
            return _lint_result(0)

        def fake_dep(*a, **kw):
            return _dep_result()

        def fake_crap(*a, **kw):
            return _crap_result(0)

        def fake_smell(*a, **kw):
            return _smell_result(0)

        monkeypatch.setattr(scanner, "check_python_lint_incremental", fake_py_lint)
        monkeypatch.setattr(scanner, "check_rust_lint_incremental", fake_rust_lint)
        monkeypatch.setattr(scanner, "check_ts_lint_incremental", fake_ts_lint)
        monkeypatch.setattr(scanner, "check_dependency_incremental", fake_dep)
        monkeypatch.setattr(scanner, "check_python_crap_incremental", fake_crap)
        monkeypatch.setattr(scanner, "check_smell_incremental", fake_smell)

        results = scanner.run_full_scan(tmp_path)  # lang=None → config.languages

        assert called == ["python"]
        assert "rust" not in results and "ts" not in results
        assert "python" in results


class TestArchive:
    """存档/加载往返"""

    def test_save_and_load_roundtrip(self, tmp_path):
        report = {"timestamp": "x", "results": {}}
        path = scanner.save_scan_report(tmp_path, report)
        assert path.exists()
        assert path.name.startswith("scan-")
        loaded = scanner.load_report(path)
        assert loaded == report
        # history 目录位于仓库下 .quality-gate/history
        assert path.parent == tmp_path / ".quality-gate" / "history"

    def test_latest_report_returns_newest(self, tmp_path):
        old = tmp_path / ".quality-gate" / "history"
        old.mkdir(parents=True)
        (old / "scan-20260901-000000.json").write_text(
            json.dumps({"ts": 1}), encoding="utf-8")
        (old / "scan-20260902-000000.json").write_text(
            json.dumps({"ts": 2}), encoding="utf-8")
        latest = scanner.latest_report_path(tmp_path)
        assert latest is not None
        assert "20260902" in latest.name

    def test_load_broken_report_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert scanner.load_report(p) is None


class TestCompareReports:
    """周报趋势对比"""

    def _report(self, results: dict) -> dict:
        return {"schema_version": 1, "results": results}

    def test_all_clean_trend_zero(self, tmp_path):
        base = self._report({
            "rust": {"lint": _lint_result(0)},
            "ts": {"lint": _lint_result(0)},
            "python": {"lint": _lint_result(0), "complexity": _crap_result(0)},
            "duplication": _dup_result(0),
        })
        cur = self._report({
            "rust": {"lint": _lint_result(0)},
            "ts": {"lint": _lint_result(0)},
            "python": {"lint": _lint_result(0), "complexity": _crap_result(0)},
            "duplication": _dup_result(0),
        })
        trend = scanner.compare_reports(base, cur)
        assert trend["lint"]["rust"] == {"before": 0, "after": 0, "delta": 0}
        assert trend["duplication"]["delta"] == 0
        assert trend["crap_functions"]["delta"] == 0

    def test_trend_detects_degradation(self, tmp_path):
        base = self._report({
            "rust": {"lint": _lint_result(1)},
            "ts": {"lint": _lint_result(0)},
            "python": {"lint": _lint_result(2), "complexity": _crap_result(3)},
            "duplication": _dup_result(1),
        })
        cur = self._report({
            "rust": {"lint": _lint_result(4)},
            "ts": {"lint": _lint_result(0)},
            "python": {"lint": _lint_result(2), "complexity": _crap_result(5)},
            "duplication": _dup_result(2),
        })
        trend = scanner.compare_reports(base, cur)
        assert trend["lint"]["rust"] == {"before": 1, "after": 4, "delta": 3}
        assert trend["lint"]["python"] == {"before": 2, "after": 2, "delta": 0}
        assert trend["duplication"] == {"before": 1, "after": 2, "delta": 1}
        assert trend["crap_functions"] == {"before": 3, "after": 5, "delta": 2}

    def test_trend_detects_function_level_changes(self, tmp_path):
        """函数级趋势：新增/消失的高 CRAP 函数分别列出"""
        base = self._report({
            "rust": {"lint": _lint_result(0)},
            "ts": {"lint": _lint_result(0)},
            "python": {"lint": _lint_result(0), "complexity": _crap_result(2)},
            "duplication": _dup_result(0),
        })
        # base: f_fn0, f_fn1；cur: f_fn1, f_fn2 → new=[f_fn2], fixed=[f_fn0]
        cur = self._report({
            "rust": {"lint": _lint_result(0)},
            "ts": {"lint": _lint_result(0)},
            "python": {"lint": _lint_result(0), "complexity": _crap_result(2)},
            "duplication": _dup_result(0),
        })
        # 覆盖 cur 的 issues 模拟函数替换
        cur_issues = cur["results"]["python"]["complexity"]["issues"]
        cur_issues[0] = dict(cur_issues[0], function="f_fn1")
        cur_issues[1] = dict(cur_issues[1], function="f_fn2")
        trend = scanner.compare_reports(base, cur)
        details = trend["crap_function_details"]
        assert details["new"] == ["a.py:f_fn2"]
        assert details["fixed"] == ["a.py:f_fn0"]
        assert details["persistent"] == ["a.py:f_fn1"]
        # 计数趋势不变（2→2）
        assert trend["crap_functions"]["delta"] == 0

    def test_skipped_counts_as_zero(self, tmp_path):
        """工具跳过 → 计数 0，不崩溃"""
        base = self._report({"rust": {"lint": _lint_result(0, skipped="未装")}})
        cur = self._report({"rust": {"lint": _lint_result(2)}})
        trend = scanner.compare_reports(base, cur)
        assert trend["lint"]["rust"] == {"before": 0, "after": 2, "delta": 2}

    def test_trend_detects_smell_change(self, tmp_path):
        """smell 维度：P0/P1 issues 与 P2 报告项都计入趋势"""
        base = self._report({
            "rust": {"lint": _lint_result(0)},
            "ts": {"lint": _lint_result(0)},
            "python": {"lint": _lint_result(0), "complexity": _crap_result(0),
                       "smell": _smell_result(1, 2)},
            "duplication": _dup_result(0),
        })
        cur = self._report({
            "rust": {"lint": _lint_result(0)},
            "ts": {"lint": _lint_result(0)},
            "python": {"lint": _lint_result(0), "complexity": _crap_result(0),
                       "smell": _smell_result(2, 3)},
            "duplication": _dup_result(0),
        })
        trend = scanner.compare_reports(base, cur)
        # 3(=1+2) → 5(=2+3)
        assert trend["smell"] == {"before": 3, "after": 5, "delta": 2}

    def test_smell_missing_in_baseline_counts_zero(self, tmp_path):
        """旧存档无 smell 维度 → before=0，不崩溃（向后兼容）"""
        base = self._report({
            "python": {"lint": _lint_result(0), "complexity": _crap_result(0)},
        })
        cur = self._report({
            "python": {"lint": _lint_result(0), "complexity": _crap_result(0),
                       "smell": _smell_result(1, 1)},
        })
        trend = scanner.compare_reports(base, cur)
        assert trend["smell"] == {"before": 0, "after": 2, "delta": 2}


class TestScanSummary:
    """cli._scan_summary 汇总提取"""

    def test_summary_counts(self):
        results = {
            "rust": {"lint": _lint_result(3), "dependency": _dep_result(1)},
            "ts": {"lint": _lint_result(0), "dependency": _dep_result(0)},
            "python": {"lint": _lint_result(2),
                       "dependency": _dep_result(0),
                       "complexity": _crap_result(4),
                       "smell": _smell_result(1, 2)},
            "duplication": _dup_result(5),
        }
        s = _scan_summary(results)
        assert s["lint_issues"] == 5
        assert s["duplication_blocks"] == 5
        assert s["dependency_issues"] == 1
        assert s["crap_functions"] == 4
        # smell = P0/P1 issues(1) + P2 报告项(2)
        assert s["smell_issues"] == 3

    def test_summary_skips_missing_keys(self):
        assert _scan_summary({}) == {
            "lint_issues": 0, "duplication_blocks": 0,
            "dependency_issues": 0, "crap_functions": 0,
            "smell_issues": 0,
        }


class TestCliLanguages:
    """cli.check 尊重 config.languages（A 包：声明语言才跑）"""

    def _git_repo(self, tmp_path):
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=T",
             "-c", "user.email=t@t.t", "commit", "-qm", "init"],
            check=True,
        )
        return repo

    def test_python_only_config_skips_rust_ts_checkers(self, tmp_path, monkeypatch):
        """languages=[python] + 缺省 --lang → 只跑 python，不调 rust/ts runner"""
        from click.testing import CliRunner

        from quality_gate import cli as cli_mod

        repo = self._git_repo(tmp_path)
        (repo / "quality-gate.yaml").write_text(
            "languages:\n  - python\n", encoding="utf-8",
        )
        called: list[str] = []

        def fake_rust(repo_root, *, config, checks_list, verbose):
            called.append("rust")
            return {}, False

        def fake_ts(repo_root, *, config, checks_list, verbose):
            called.append("ts")
            return {}, False

        def fake_python(repo_root, *, config, checks_list, verbose):
            called.append("python")
            return {}, False

        monkeypatch.setattr(cli_mod, "_run_rust_checks", fake_rust)
        monkeypatch.setattr(cli_mod, "_run_ts_checks", fake_ts)
        monkeypatch.setattr(cli_mod, "_run_python_checks", fake_python)

        monkeypatch.chdir(repo)
        result = CliRunner().invoke(
            cli_mod.main, ["check", "--checks", "lint"],
        )

        assert result.exit_code == 0, result.output
        assert called == ["python"], f"应只跑 python: {called}"

    def test_explicit_all_still_runs_everything(self, tmp_path, monkeypatch):
        """--lang all 显式 → 覆盖配置跑全部（向后兼容）"""
        from click.testing import CliRunner

        from quality_gate import cli as cli_mod

        repo = self._git_repo(tmp_path)
        (repo / "quality-gate.yaml").write_text(
            "languages:\n  - python\n", encoding="utf-8",
        )
        called: list[str] = []

        def fake_rust(repo_root, *, config, checks_list, verbose):
            called.append("rust")
            return {}, False

        def fake_ts(repo_root, *, config, checks_list, verbose):
            called.append("ts")
            return {}, False

        def fake_python(repo_root, *, config, checks_list, verbose):
            called.append("python")
            return {}, False

        monkeypatch.setattr(cli_mod, "_run_rust_checks", fake_rust)
        monkeypatch.setattr(cli_mod, "_run_ts_checks", fake_ts)
        monkeypatch.setattr(cli_mod, "_run_python_checks", fake_python)

        monkeypatch.chdir(repo)
        result = CliRunner().invoke(
            cli_mod.main, ["check", "--checks", "lint", "--lang", "all"],
        )

        assert result.exit_code == 0, result.output
        assert called == ["rust", "ts", "python"], f"应跑全部: {called}"
