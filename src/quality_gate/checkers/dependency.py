"""架构依赖检查器

调用各语言的官方依赖/架构检查工具（全局阻塞，与 diff 无关）：
  - Rust:   cargo-deny check bans  (deny.toml 规则，--format json)
  - TS:     dependency-cruiser     (.dependency-cruiser.js 的 forbidden 规则)
  - Python: import-linter          (.import-linter 契约)

原则：
  - 架构规则属于项目自身工具配置（deny.toml / .dependency-cruiser.js /
    .import-linter），quality-gate 只负责调用与解析，不复制规则 DSL。
  - 工具未安装或项目无配置 → 跳过（skipped），不阻塞、不误报。
  - 工具已配置但违规 → 全局阻塞（新代码与存量都必须遵守架构边界）。

返回统一结构:
    {"blocking": bool, "issues": [...], "skipped": str | None}
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

_RUST_CONFIGS = ["deny.toml", ".cargo-deny.toml"]
_TS_CONFIGS = [".dependency-cruiser.js", ".dependency-cruiser.json", "dependency-cruiser.config.js"]
_PY_CONFIGS = [".import-linter", ".import-linter.toml", ".import-linter.json"]

_TIMEOUT_SECONDS = 300


def _find_config(repo_root: Path, candidates: list[str]) -> Path | None:
    """在仓库中查找第一个存在的配置文件"""
    for name in candidates:
        p = repo_root / name
        if p.exists():
            return p
    return None


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def _issue(file: str, code: str, message: str) -> dict[str, Any]:
    return {
        "file": file,
        "line": 0,
        "column": 0,
        "level": "error",
        "code": code,
        "message": message,
    }


def _parse_cargo_deny_output(raw: str) -> list[dict[str, Any]] | None:
    """解析 cargo-deny JSON 输出 → 违规条目列表 [{name, version, kind, message}]

    cargo-deny 0.20+ 真实输出是 **NDJSON 行流**（写到 stderr），逐行:
        {"type":"diagnostic","fields":{"code":"banned","severity":"error",
         "graphs":[{"Krate":{"name":"clap","version":"4.6.6"},...}],
         "message":"crate 'clap = 4.6.6' is explicitly banned", ...}}
        {"type":"summary","fields":{"bans":{"errors":1,...}}}   # 末尾汇总

    旧版本单对象: {"bans":{"error":[{name,version,kind}],...}}

    只把 severity=error 的 diagnostic 当违规；warning（duplicate 等）仅计数。

    返回:
        list  — 解析出的违规条目（可为空 = 可解析但无 error 级违规）
        None  — 输出完全不可解析（调用方应给汇总级 issue）
    """
    violations: list[dict[str, Any]] = []
    saw_json = False

    # 1) NDJSON 行流优先（cargo-deny 0.20+）
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # 非 JSON 行（进度输出等）跳过
        if not isinstance(obj, dict):
            continue
        if obj.get("type") in ("diagnostic", "summary"):
            saw_json = True  # 确认为 cargo-deny NDJSON 流
        if obj.get("type") != "diagnostic":
            continue
        fields = obj.get("fields") or {}
        if fields.get("severity") != "error":
            continue
        # graphs[0].Krate = 违规 crate（name/version）
        name, version = "?", ""
        graphs = fields.get("graphs") or []
        if graphs and isinstance(graphs[0], dict):
            krate = (graphs[0].get("Krate") or {})
            if isinstance(krate, dict):
                name = krate.get("name") or "?"
                version = krate.get("version") or ""
        violations.append({
            "name": name,
            "version": version,
            "kind": fields.get("code", "violation"),
            "message": fields.get("message", "") or "",
        })
    if saw_json:
        return violations

    # 2) 旧版单对象格式兜底: {"bans": {"error": [...]}}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None  # 完全不可解析
    if not isinstance(data, dict):
        return None
    for section in ("bans", "advisories", "licenses", "sources"):
        section_data = data.get(section)
        if not isinstance(section_data, dict):
            continue
        for level in ("error", "warning"):
            for entry in section_data.get(level, []) or []:
                if isinstance(entry, dict):
                    violations.append({
                        "name": entry.get("name", "?"),
                        "version": entry.get("version", ""),
                        "kind": entry.get("kind", "violation"),
                        "message": "",
                    })
    return violations


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=_TIMEOUT_SECONDS)


def check_rust_dependency_incremental(repo_root: Path, verbose: bool = False) -> dict[str, Any]:
    """Rust: cargo-deny check bans（全局阻塞）"""
    result: dict[str, Any] = {"blocking": False, "issues": [], "skipped": None}

    if not _find_config(repo_root, _RUST_CONFIGS):
        result["skipped"] = "未找到 deny.toml（架构/许可规则未配置）"
        if verbose:
            print(f"    跳过 Rust 依赖检查: {result['skipped']}")
        return result

    if not _tool_available("cargo-deny"):
        result["skipped"] = "cargo-deny 未安装"
        if verbose:
            print(f"    跳过 Rust 依赖检查: {result['skipped']}")
        return result

    if verbose:
        print("  运行 cargo-deny check bans...")
    try:
        proc = _run(["cargo", "deny", "--format", "json", "check", "bans"], repo_root)
    except subprocess.TimeoutExpired:
        result["blocking"] = True
        result["issues"].append(_issue("deny.toml", "timeout",
                                       "cargo-deny 执行超时 (>5 分钟)"))
        return result

    if proc.returncode == 0:
        if verbose:
            print("  cargo-deny: 无违规")
        return result

    # cargo-deny 0.20+ 将 NDJSON 诊断写到 stderr（旧版写 stdout），合并两通道解析
    raw_output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    found = _parse_cargo_deny_output(raw_output)
    if found is None:
        # 输出完全不可解析 → 汇总级 issue（保持阻塞但不过度推断细节）
        stderr_tail = [l for l in (proc.stderr or proc.stdout or "").splitlines()
                       if l.strip()][:5]
        detail = " | ".join(stderr_tail) if stderr_tail else "cargo-deny 报告违规"
        result["issues"].append(_issue("deny.toml", "deny:violation",
                                       f"cargo-deny 检出依赖违规: {detail}"))
        result["blocking"] = True
        return result
    if not found:
        # 可解析但无 error 级违规（仅 warning），exit != 0 → 仍给汇总
        found = [{"name": "cargo-deny", "version": "", "kind": "unknown",
                  "message": "cargo-deny 非零退出但未检出 error 级违规"}]
    for entry in found:
        crate = entry.get("name", "?")
        version = entry.get("version", "")
        kind = entry.get("kind", "violation")
        detail = entry.get("message", "")
        message = f"依赖违规: {crate}@{version} ({kind})"
        if detail:
            message += f" — {detail[:150]}"
        result["issues"].append(_issue("Cargo.toml", f"deny:{kind}", message))

    result["blocking"] = bool(result["issues"])
    return result


def check_ts_dependency_incremental(repo_root: Path, verbose: bool = False) -> dict[str, Any]:
    """TypeScript: dependency-cruiser（forbidden 架构规则，全局阻塞）"""
    result: dict[str, Any] = {"blocking": False, "issues": [], "skipped": None}

    if not _find_config(repo_root, _TS_CONFIGS):
        result["skipped"] = "未找到 .dependency-cruiser.js（架构规则未配置）"
        if verbose:
            print(f"    跳过 TS 依赖检查: {result['skipped']}")
        return result

    if not (_tool_available("depcruise") or _tool_available("dependency-cruiser")):
        result["skipped"] = "dependency-cruiser 未安装"
        if verbose:
            print(f"    跳过 TS 依赖检查: {result['skipped']}")
        return result

    # 推断源码目录（web/ 下常见 src/；也可配置在 .dependency-cruiser.js 中）
    src_dir = repo_root / "src"
    if not src_dir.exists():
        candidates = [p for p in repo_root.iterdir()
                      if p.is_dir() and p.name not in ("node_modules", ".git", "target", ".venv")]
        src_dir = candidates[0] if candidates else repo_root

    if verbose:
        print("  运行 dependency-cruiser...")
    tool = "depcruise" if _tool_available("depcruise") else "dependency-cruiser"
    try:
        proc = _run([tool, str(src_dir), "--output-type", "json"], repo_root)
    except subprocess.TimeoutExpired:
        result["blocking"] = True
        result["issues"].append(_issue(".dependency-cruiser.js", "timeout",
                                       "dependency-cruiser 执行超时 (>5 分钟)"))
        return result

    if proc.returncode == 0:
        if verbose:
            print("  dependency-cruiser: 无违规")
        return result

    try:
        data = json.loads(proc.stdout)
        violations = data.get("violations", []) or []
        if not violations:
            raise ValueError("no violations field")
        for v in violations:
            rule = v.get("rule", {})
            rule_name = rule.get("name", rule if isinstance(rule, str) else "unknown")
            result["issues"].append(_issue(
                str(v.get("to", "?")), f"depcruise:{rule_name}",
                f"架构违规: {v.get('from', '?')} → {v.get('to', '?')} "
                f"(规则 {rule_name})"))
    except (json.JSONDecodeError, ValueError, AttributeError):
        result["issues"].append(_issue(
            ".dependency-cruiser.js", "depcruise:violation",
            "dependency-cruiser 检出架构违规（详见 depcruise 输出）"))

    result["blocking"] = bool(result["issues"])
    return result


def check_python_dependency_incremental(repo_root: Path, verbose: bool = False) -> dict[str, Any]:
    """Python: import-linter（契约检查，全局阻塞）"""
    result: dict[str, Any] = {"blocking": False, "issues": [], "skipped": None}

    if not _find_config(repo_root, _PY_CONFIGS):
        result["skipped"] = "未找到 .import-linter（架构契约未配置）"
        if verbose:
            print(f"    跳过 Python 依赖检查: {result['skipped']}")
        return result

    if not _tool_available("import-linter"):
        result["skipped"] = "import-linter 未安装"
        if verbose:
            print(f"    跳过 Python 依赖检查: {result['skipped']}")
        return result

    if verbose:
        print("  运行 import-linter...")
    try:
        proc = _run(["import-linter"], repo_root)
    except subprocess.TimeoutExpired:
        result["blocking"] = True
        result["issues"].append(_issue(".import-linter", "timeout",
                                       "import-linter 执行超时 (>5 分钟)"))
        return result

    if proc.returncode == 0:
        if verbose:
            print("  import-linter: 无违规")
        return result

    # import-linter 文本输出含违规文件与契约说明
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    saw_issue = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 契约失败通常伴随 "ERROR"/"✖"/"Contract" 行，其后是文件与 import 细节
        if line.startswith(("ERROR", "✖", "Contract")) or "forbidden" in line.lower():
            result["issues"].append(_issue(".import-linter", "import-linter",
                                           f"架构契约违规: {line[:200]}"))
            saw_issue = True
    if not saw_issue:
        detail = output.strip().splitlines()[:5]
        result["issues"].append(_issue(
            ".import-linter", "import-linter",
            "import-linter 检出架构契约违规: "
            + (" | ".join(detail) if detail else "详见输出")))

    result["blocking"] = bool(result["issues"])
    return result


def check_dependency_incremental(repo_root: Path, lang: str, verbose: bool = False) -> dict[str, Any]:
    """按语言分发架构依赖检查"""
    if lang == "rust":
        return check_rust_dependency_incremental(repo_root, verbose)
    if lang == "ts":
        return check_ts_dependency_incremental(repo_root, verbose)
    if lang == "python":
        return check_python_dependency_incremental(repo_root, verbose)
    raise ValueError(f"不支持的架构依赖检查语言: {lang}")
