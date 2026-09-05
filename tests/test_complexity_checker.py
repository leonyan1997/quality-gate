"""complexity checker：radon 查不了必须显式跳过（B 包语义可见性）

回归：此前 radon 缺失/超时/解析失败时 check_python_crap_incremental
裸返回空结果（无任何说明）——AI 会误以为"CRAP 已检查且干净"。
现在必须写 result["skipped"] + 可见原因。
"""

import json
import subprocess

from quality_gate.checkers.complexity import check_python_crap_incremental


def _make_proc(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr="",
    )


def test_radon_missing_is_visible_skip(tmp_path, monkeypatch):
    """radon 未安装 → skipped 带原因，不静默空"""
    real_run = subprocess.run

    def _raise(args, *a, **k):
        if isinstance(args, list) and args and args[0] == "radon":
            raise FileNotFoundError("radon")
        return real_run(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", _raise)
    result = check_python_crap_incremental(tmp_path)

    assert result["blocking"] is False
    assert result["issues"] == []
    assert result["skipped"] is not None
    assert "radon" in result["skipped"]


def test_radon_parse_failure_is_visible_skip(tmp_path, monkeypatch):
    """radon 输出不可解析 → skipped 而非静默空"""
    real_run = subprocess.run

    def _fake(args, *a, **k):
        if isinstance(args, list) and args and args[0] == "radon":
            return _make_proc(0, "not json")
        return real_run(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", _fake)
    result = check_python_crap_incremental(tmp_path)

    assert result["issues"] == []
    assert result["skipped"] is not None
    assert "解析失败" in result["skipped"]


def test_radon_report_produces_report_only(tmp_path, monkeypatch):
    """radon 正常输出 → 超阈值函数进 issues（仅报告不阻塞），无 skipped"""
    real_run = subprocess.run
    payload = json.dumps({
        str(tmp_path / "app.py"): [
            {"name": "hot_fn", "lineno": 3, "complexity": 31},
            {"name": "ok_fn", "lineno": 10, "complexity": 5},
        ],
    })

    def _fake(args, *a, **k):
        if isinstance(args, list) and args and args[0] == "radon":
            return _make_proc(0, payload)
        return real_run(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", _fake)
    result = check_python_crap_incremental(tmp_path)

    assert result["blocking"] is False
    assert "skipped" not in result or result["skipped"] is None
    assert len(result["issues"]) == 1
    assert result["issues"][0]["function"] == "hot_fn"
    assert result["issues"] == result["report_only_issues"]
