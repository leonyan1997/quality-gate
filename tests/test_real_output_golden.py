"""真实工具输出 golden 回归测试（C3）

novel-indexing 实机对拍捕获的真实工具输出（ruff/clippy/oxlint），
存于 tests/fixtures/real_outputs/。历史最严重的解析器 bug（cargo-deny
NDJSON、oxlint v3 对象格式、vitest 绝对路径 key）全部是真实工具输出
漂移才暴露的——mock 近似抓不住字段重命名/结构变化。这些样本锁死
"真实形态能正确归一化"，即使本机无工具链也可回归。

样本内路径为捕获时绝对路径；测试把根前缀改写到 tmp 仓库，
其余字段逐字保持真实形态。
"""

import json
import subprocess
from pathlib import Path

from quality_gate.checkers import python_lint, rust_lint, ts_lint

FIXTURES = Path(__file__).parent / "fixtures" / "real_outputs"
_REAL_ROOT = "/home/leonyan/projects/novel-indexing"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _make_proc(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr="",
    )


def _stub_run(monkeypatch, proc, module) -> None:
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        if isinstance(cmd, list) and cmd and cmd[0] in (
            "ruff", "cargo", "npx",
        ):
            return proc
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(module.subprocess, "run", fake_run)


class TestRuffGolden:
    def test_real_diag_normalized(self, tmp_path, monkeypatch):
        """ruff 0.16.6 真实诊断（I001 import 排序）→ 正确归一化"""
        # 路径前缀改写到 tmp 仓库（其余字段逐字真实）
        diag = json.loads(_read("ruff.json").replace(_REAL_ROOT, str(tmp_path)))
        (tmp_path / "indexer" / "__main__.py").parent.mkdir(parents=True)
        (tmp_path / "indexer" / "__main__.py").write_text("x = 1\n", encoding="utf-8")

        _stub_run(monkeypatch, _make_proc(0, json.dumps([diag])), python_lint)
        result = python_lint.check_python_lint_incremental(tmp_path, full=True)

        assert len(result["issues"]) == 1
        issue = result["issues"][0]
        assert issue["file"] == "indexer/__main__.py"  # 绝对路径 → 相对
        assert issue["code"] == "I001"
        # ruff I001 语义: location.row = import 块起点（3），end_location = 块尾（15）
        assert issue["line"] == 3
        assert result["blocking"] is True


class TestClippyGolden:
    def test_real_jsonline_normalized(self, tmp_path, monkeypatch):
        """clippy 真实 JSON-line（unused_imports，span 相对路径）→ 归一化"""
        line = _read("clippy.jsonl").strip()
        # 样本 span.file_name 是仓库内相对路径，直接可用
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "api.rs").write_text("fn main() {}\n", encoding="utf-8")

        _stub_run(monkeypatch, _make_proc(0, line + "\n"), rust_lint)
        result = rust_lint.check_rust_lint_incremental(tmp_path, full=True)

        assert len(result["issues"]) == 1
        issue = result["issues"][0]
        assert issue["file"] == "src/api.rs"
        assert issue["code"] == "unused_imports"
        assert issue["line"] == 186
        assert issue["level"] == "warning"
        assert result["blocking"] is True

    def test_real_jsonline_dedupes(self, tmp_path, monkeypatch):
        """同一真实行重复 3 次（宏展开场景）→ 去重为 1 条"""
        line = _read("clippy.jsonl").strip()
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "api.rs").write_text("fn main() {}\n", encoding="utf-8")

        _stub_run(monkeypatch, _make_proc(0, "\n".join([line] * 3)), rust_lint)
        result = rust_lint.check_rust_lint_incremental(tmp_path, full=True)

        assert len(result["issues"]) == 1


class TestOxlintGolden:
    def test_real_v3_object_normalized(self, tmp_path, monkeypatch):
        """oxlint v3 真实对象（顶层 {diagnostics:[]}，code 含括号规则名）"""
        diag = json.loads(_read("oxlint.json"))
        (tmp_path / "src" / "components").mkdir(parents=True)
        (tmp_path / "src" / "components" / "EChart.vue").write_text(
            "<template>t</template>\n", encoding="utf-8",
        )

        payload = json.dumps({"diagnostics": [diag]})
        _stub_run(monkeypatch, _make_proc(0, payload), ts_lint)
        result = ts_lint.check_ts_lint_incremental(tmp_path, full=True)

        assert len(result["issues"]) == 1
        issue = result["issues"][0]
        # code 字段是字符串规则名（含括号前缀），非 dict——原样保留
        assert issue["code"] == diag["code"]
        assert issue["level"] == "warning"  # severity=warning 字符串
        assert issue["message"] == diag["message"]
