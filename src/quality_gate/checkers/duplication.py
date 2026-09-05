"""重复代码增量检测器

使用 jscpd + git diff 行过滤，只阻塞新增行上的重复代码。
语言无关（jscpd 支持多语言）。
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .git_diff import (
    get_git_diff_lines,
    get_total_added_lines,
    is_line_in_diff,
)


def check_duplication_incremental(
    repo_root: Path,
    verbose: bool = False,
    threshold: float = 3.0,
    min_tokens: int = 50,
    min_lines: int = 5,
    ignore_paths: list[str] | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """执行重复代码检测

    full=False (默认): 增量模式，jscpd + git diff 行过滤，只阻塞新增行上的重复。
    full=True: 全仓扫描模式（scan 周报用），报告全部重复块，不按 diff 过滤、不阻塞。
    """
    if ignore_paths is None:
        ignore_paths = [
            "node_modules/**", "target/**", ".venv/**", "dist/**",
            "build/**", "generated/**", "report/**", "coverage/**",
            ".quality-gate/**",
        ]

    result = {
        "blocking": False,
        "issues": [],
        "duplication_rate": 0.0,
        "total_added_lines": 0,
        "duplicated_added_lines": 0,
        "scan_mode": full,
    }

    click_echo = __import__('click').echo if verbose else lambda x: None

    # Step 0: 增量模式无新增行则跳过（避免空跑 jscpd）；全仓扫描模式总是跑
    total_added = get_total_added_lines(repo_root)
    if total_added == 0 and not full:
        click_echo("  无新增行，跳过重复检测")
        result["total_added_lines"] = 0
        return result

    click_echo("  运行 jscpd...")

    # Step 1: 运行 jscpd（JSON 报告输出到临时目录，不污染工作区/新增行统计）
    jscpd_out_dir = tempfile.mkdtemp(prefix="qg-jscpd-")
    jscpd_args = [
        "jscpd",
        "--reporters", "json",
        "--output", jscpd_out_dir,
        "--min-tokens", str(min_tokens),
        "--min-lines", str(min_lines),
        "--ignore", ",".join(ignore_paths),
        str(repo_root),
    ]

    try:
        jscpd_result = subprocess.run(
            jscpd_args,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        return {
            "blocking": True,
            "issues": [{
                "file": "jscpd",
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "not_found",
                "message": "jscpd 未安装，请先安装：npm install -g jscpd"
            }],
            "duplication_rate": 0.0,
            "total_added_lines": 0,
            "duplicated_added_lines": 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "blocking": True,
            "issues": [{
                "file": "jscpd",
                "line": 0,
                "column": 0,
                "level": "error",
                "code": "timeout",
                "message": "jscpd 执行超时 (>5 分钟)"
            }],
            "duplication_rate": 0.0,
            "total_added_lines": 0,
            "duplicated_added_lines": 0,
        }

    # Step 2: 解析 JSON 输出
    click_echo("  解析 jscpd 输出...")

    jscpd_json = None
    try:
        jscpd_json = json.loads(jscpd_result.stdout)
    except json.JSONDecodeError:
        pass

    if jscpd_json is None:
        # jscpd JSON reporter 实际写文件而非 stdout
        report_file = Path(jscpd_out_dir) / "jscpd-report.json"
        if not report_file.exists():
            report_file = repo_root / "report" / "jscpd-report.json"  # 旧版兜底
        if report_file.exists():
            with open(report_file, "r", encoding="utf-8") as f:
                jscpd_json = json.load(f)
        else:
            return {
                "blocking": True,
                "issues": [{
                    "file": "jscpd",
                    "line": 0,
                    "column": 0,
                    "level": "error",
                    "code": "parse_error",
                    "message": "jscpd 输出解析失败"
                }],
                "duplication_rate": 0.0,
                "total_added_lines": 0,
                "duplicated_added_lines": 0,
            }

    # Step 3: 提取重复块并与 diff 行范围求交集
    click_echo("  计算新增重复行...")

    duplications = jscpd_json.get("duplicates", [])

    duplicated_added_lines = 0
    diff_ranges_cache: dict[str, list[tuple[int, int]]] = {}

    def _normalize_side_path(name: str) -> str | None:
        """jscpd firstFile/secondFile.name → 相对 repo_root 的 posix 路径

        jscpd v4 JSON reporter 的 name 形如 ``<path>:<format>``
        （实测: ``docs/a.md:markdown``、``src/x.ts:typescript``）——
        冒号后缀是检测出的代码格式，不剥离会令 git ls-files 查不到该
        文件 → 误判为 untracked → 存量重复被全文件当作"新增"阻塞。

        剥离规则: 末段冒号后为纯字母数字（格式名）才剥离；Windows 盘符
        （``C:\\...``）末段含分隔符/非字母数字，不受影响。
        """
        if not name:
            return None
        path_part = name
        candidate, _, fmt = name.rpartition(":")
        if candidate and fmt.isalnum() and len(fmt) <= 20:
            path_part = candidate
        p = Path(path_part)
        if p.is_absolute():
            try:
                return p.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                # 仓库外文件不参与 diff 判定
                return None
        return p.as_posix()

    if full:
        # 全仓扫描模式：全部重复块都报告（不按 diff 交集、不阻塞）
        for dup in duplications:
            dup_lines = dup.get("lines", 0)
            fragment = dup.get("fragment", "")
            seen_sides: set[tuple[str, int, int]] = set()
            for side in ("firstFile", "secondFile"):
                file_info = dup.get(side)
                if not isinstance(file_info, dict):
                    continue
                rel_path = _normalize_side_path(str(file_info.get("name", "")))
                dup_start = file_info.get("start", 0)
                dup_end = file_info.get("end", 0)
                if rel_path is None or dup_start == 0:
                    continue
                key = (rel_path, dup_start, dup_end)
                if key in seen_sides:
                    continue
                seen_sides.add(key)
                result["issues"].append({
                    "file": rel_path,
                    "line": dup_start,
                    "column": 0,
                    "level": "warning",
                    "code": "duplication",
                    "message": f"重复代码块 ({dup_lines} 行, {fragment[:60]!r}...)",
                })
        # 全仓模式重复率口径: duplicated 行 / 涉及文件总行数不可得，
        # 沿用 jscpd 的块计数语义，rate 留给调用方按 issues 数解读
        if verbose:
            click_echo(f"  全仓重复块: {len(result['issues'])} 处")
        return result

    for dup in duplications:
        # jscpd v4 真实结构: {firstFile: {name,start,end}, secondFile: {...},
        #                      fragment, format, lines, tokens}
        # 一个重复块同时出现在两个文件中，任一侧位于 diff 新增范围即阻塞
        dup_lines = dup.get("lines", 0)
        for side in ("firstFile", "secondFile"):
            file_info = dup.get(side)
            if not isinstance(file_info, dict):
                continue
            rel_path = _normalize_side_path(str(file_info.get("name", "")))
            dup_start = file_info.get("start", 0)
            dup_end = file_info.get("end", 0)
            if rel_path is None or dup_start == 0:
                continue

            # 获取该文件的 diff 行范围
            if rel_path not in diff_ranges_cache:
                diff_ranges_cache[rel_path] = get_git_diff_lines(repo_root, rel_path)

            diff_ranges = diff_ranges_cache[rel_path]

            # 计算重复块与 diff 新增行的交集
            overlap_lines = 0
            for line in range(dup_start, dup_end + 1):
                if is_line_in_diff(line, diff_ranges):
                    overlap_lines += 1

            if overlap_lines > 0:
                duplicated_added_lines += overlap_lines
                result["issues"].append({
                    "file": rel_path,
                    "line": dup_start,
                    "column": 0,
                    "level": "warning",
                    "code": "duplication",
                    "message": f"重复代码块 ({dup_lines} 行)，{overlap_lines} 行在新增范围内",
                })

    # Step 4: 计算占比
    rate = (duplicated_added_lines / total_added) * 100 if total_added > 0 else 0.0
    result["duplication_rate"] = round(rate, 2)
    result["total_added_lines"] = total_added
    result["duplicated_added_lines"] = duplicated_added_lines

    # Step 5: 阈值判定
    if rate > threshold:
        result["blocking"] = True
        result["issues"].append({
            "file": "jscpd",
            "line": 0,
            "column": 0,
            "level": "error",
            "code": "duplication_threshold",
            "message": f"新增重复率 {rate:.2f}% 超过阈值 {threshold}% (新增 {duplicated_added_lines}/{total_added} 行重复)",
        })

    if verbose:
        click_echo(f"  新增重复率：{rate:.2f}% ({duplicated_added_lines}/{total_added} 行)")
        if result["blocking"]:
            click_echo(f"  ❌ 超过阈值 {threshold}%")

    shutil.rmtree(jscpd_out_dir, ignore_errors=True)
    return result
