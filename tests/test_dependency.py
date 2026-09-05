"""架构依赖检查器测试

用 monkeypatch 模拟外部工具行为，覆盖:
  - 无配置文件 → 跳过
  - 工具未安装 → 跳过
  - 工具检出违规 → 阻塞 + issues
  - 工具通过 → 不阻塞
  - JSON 解析失败 → 汇总级 issue 仍阻塞
  - 分发函数错误语言 → ValueError
"""

import json
import subprocess
from pathlib import Path

import pytest

from quality_gate.checkers import dependency as dep


def _make_proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """空仓库：无任何架构配置"""
    return tmp_path


def test_no_config_skips(fake_repo):
    """仓库无架构配置 → 三语言均跳过且不阻塞"""
    for lang in ("rust", "ts", "python"):
        result = dep.check_dependency_incremental(fake_repo, lang)
        assert result["blocking"] is False
        assert result["skipped"] is not None
        assert result["issues"] == []


def test_tool_missing_skips(fake_repo, monkeypatch):
    """有配置但工具未安装 → 跳过"""
    monkeypatch.setattr(dep, "_find_config", lambda repo, cands: fake_repo / "deny.toml")
    monkeypatch.setattr(dep, "_tool_available", lambda name: False)
    result = dep.check_rust_dependency_incremental(fake_repo)
    assert result["blocking"] is False
    assert "未安装" in result["skipped"]


def test_rust_deny_ok(fake_repo, monkeypatch):
    """cargo-deny 通过 → 不阻塞"""
    monkeypatch.setattr(dep, "_find_config", lambda repo, cands: fake_repo / "deny.toml")
    monkeypatch.setattr(dep, "_tool_available", lambda name: True)
    monkeypatch.setattr(dep, "_run", lambda cmd, cwd: _make_proc(0, stdout="{}"))
    result = dep.check_rust_dependency_incremental(fake_repo)
    assert result["blocking"] is False
    assert result["issues"] == []


def test_rust_deny_violation(fake_repo, monkeypatch):
    """cargo-deny 检出 banned crate → 阻塞 + 精确 issue"""
    monkeypatch.setattr(dep, "_find_config", lambda repo, cands: fake_repo / "deny.toml")
    monkeypatch.setattr(dep, "_tool_available", lambda name: True)
    payload = json.dumps({
        "bans": {
            "error": [{"name": "openssl", "version": "3.2.0", "kind": "banned"}],
        },
    })
    monkeypatch.setattr(dep, "_run", lambda cmd, cwd: _make_proc(1, stdout=payload))
    result = dep.check_rust_dependency_incremental(fake_repo)
    assert result["blocking"] is True
    assert len(result["issues"]) == 1
    assert result["issues"][0]["code"] == "deny:banned"
    assert "openssl" in result["issues"][0]["message"]


def test_rust_deny_violation_ndjson(fake_repo, monkeypatch):
    """cargo-deny 0.20+ 真实 NDJSON 行流输出（写 stderr）→ 阻塞 + 精确 crate"""
    monkeypatch.setattr(dep, "_find_config", lambda repo, cands: fake_repo / "deny.toml")
    monkeypatch.setattr(dep, "_tool_available", lambda name: True)
    lines = [
        json.dumps({
            "type": "diagnostic",
            "fields": {
                "code": "banned",
                "severity": "error",
                "message": "crate 'clap = 4.6.6' is explicitly banned",
                "graphs": [{"Krate": {"name": "clap", "version": "4.6.6"}}],
            },
        }),
        json.dumps({
            "type": "diagnostic",
            "fields": {
                "code": "duplicate", "severity": "warning",
                "message": "found 2 duplicate entries for crate 'syn'",
                "graphs": [{"Krate": {"name": "syn", "version": "2.0.0"}}],
            },
        }),
        json.dumps({"type": "summary",
                    "fields": {"bans": {"errors": 1, "warnings": 1}}}),
    ]
    # cargo-deny 0.20+ 实际把 NDJSON 写到 stderr
    monkeypatch.setattr(
        dep, "_run", lambda cmd, cwd: _make_proc(1, stdout="", stderr="\n".join(lines)))
    result = dep.check_rust_dependency_incremental(fake_repo)
    assert result["blocking"] is True
    # 只有 severity=error 的 diagnostic 阻塞；warning（duplicate）不阻塞
    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["code"] == "deny:banned"
    assert "clap@4.6.6" in issue["message"]
    assert "explicitly banned" in issue["message"]


def test_rust_deny_ndjson_no_error_passes(fake_repo, monkeypatch):
    """NDJSON 全为 warning/无 error → 不阻塞（exit 0 场景由 returncode 短路，此处测纯解析）"""
    monkeypatch.setattr(dep, "_find_config", lambda repo, cands: fake_repo / "deny.toml")
    monkeypatch.setattr(dep, "_tool_available", lambda name: True)
    lines = [
        json.dumps({
            "type": "diagnostic",
            "fields": {
                "code": "duplicate", "severity": "warning",
                "message": "found 2 duplicate entries for crate 'syn'",
                "graphs": [{"Krate": {"name": "syn", "version": "2.0.0"}}],
            },
        }),
        json.dumps({"type": "summary",
                    "fields": {"bans": {"errors": 0, "warnings": 1}}}),
    ]
    monkeypatch.setattr(
        dep, "_run", lambda cmd, cwd: _make_proc(0, stdout="", stderr="\n".join(lines)))
    result = dep.check_rust_dependency_incremental(fake_repo)
    assert result["blocking"] is False
    assert result["issues"] == []


def test_rust_deny_unparseable_still_blocks(fake_repo, monkeypatch):
    """cargo-deny 非零退出但 JSON 解析失败 → 汇总 issue 仍阻塞"""
    monkeypatch.setattr(dep, "_find_config", lambda repo, cands: fake_repo / "deny.toml")
    monkeypatch.setattr(dep, "_tool_available", lambda name: True)
    monkeypatch.setattr(dep, "_run", lambda cmd, cwd: _make_proc(1, stdout="not json at all"))
    result = dep.check_rust_dependency_incremental(fake_repo)
    assert result["blocking"] is True
    assert len(result["issues"]) == 1
    assert result["issues"][0]["code"] == "deny:violation"


def test_ts_depcruise_violation(fake_repo, monkeypatch):
    """depcruise 检出架构违规 → 阻塞 + from/to/规则名"""
    monkeypatch.setattr(dep, "_find_config", lambda repo, cands: fake_repo / ".dependency-cruiser.js")
    monkeypatch.setattr(dep, "_tool_available", lambda name: True)
    payload = json.dumps({
        "violations": [
            {"from": "src/ui/App.ts", "to": "src/domain/model.ts",
             "rule": {"name": "no-ui-to-domain"}},
        ],
    })
    monkeypatch.setattr(dep, "_run", lambda cmd, cwd: _make_proc(1, stdout=payload))
    # src/ 目录需存在（src_dir 推断）
    (fake_repo / "src").mkdir()
    result = dep.check_ts_dependency_incremental(fake_repo)
    assert result["blocking"] is True
    issue = result["issues"][0]
    assert issue["code"] == "depcruise:no-ui-to-domain"
    assert "src/ui/App.ts" in issue["message"]


def test_python_import_linter_violation(fake_repo, monkeypatch):
    """import-linter 文本输出违规 → 阻塞"""
    monkeypatch.setattr(dep, "_find_config", lambda repo, cands: fake_repo / ".import-linter")
    monkeypatch.setattr(dep, "_tool_available", lambda name: True)
    out = "ERROR contract 'layers' violated\n  foo.py imports bar.py (forbidden)\n"
    monkeypatch.setattr(dep, "_run", lambda cmd, cwd: _make_proc(1, stdout=out))
    result = dep.check_python_dependency_incremental(fake_repo)
    assert result["blocking"] is True
    assert any("layers" in i["message"] for i in result["issues"])


def test_dispatch_unknown_lang(fake_repo):
    """分发到不支持的语言 → ValueError"""
    with pytest.raises(ValueError):
        dep.check_dependency_incremental(fake_repo, "cobol")
