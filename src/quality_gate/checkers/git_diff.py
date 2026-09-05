"""共享 Git Diff 工具

所有检查器共用的 git diff 行号解析、文件分类逻辑。
"""

import os
import re
import subprocess
from pathlib import Path

# untracked 新文件按全文件视为新增行
_FULL_FILE_MAX_LINE = 1_000_000_000


def _diff_base(repo_root: Path) -> str:
    """diff 基线: 默认 HEAD（本地/预提交语义），可用 QUALITY_GATE_BASE 覆盖（CI: origin/main）"""
    return os.environ.get("QUALITY_GATE_BASE", "HEAD")


def _is_untracked(repo_root: Path, filepath: str) -> bool:
    """文件是否未被 git 跟踪（本地新增未 add 的文件）"""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", filepath],
            cwd=repo_root, capture_output=True, text=True, timeout=30,
        )
        return result.returncode != 0
    except Exception:
        return False


def get_git_diff_lines(repo_root: Path, filepath: str, base: str | None = None) -> list[tuple[int, int]]:
    """获取 git diff 中文件的新增/修改行号范围

    解析 `git diff -U0` 输出，返回 [(start_line, end_line), ...]

    特殊语义:
      - untracked 新文件 → 全文件范围 [(1, MAX)]（所有行都是新增行）
      - 已跟踪文件 → 相对基线 (默认 HEAD, 可用 QUALITY_GATE_BASE 覆盖) 的
        diff 中新增/修改行范围

    示例:
        @@ -10,0 +11,4 @@  → 新增行 11-14
        @@ -20,5 +20,6 @@ → 修改行 20-25
    """
    if base is None:
        base = _diff_base(repo_root)

    if _is_untracked(repo_root, filepath):
        return [(1, _FULL_FILE_MAX_LINE)]

    ranges = []

    try:
        result = subprocess.run(
            ["git", "diff", "-U0", base, "--", filepath],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return []

        for line in result.stdout.split("\n"):
            if line.startswith("@@"):
                match = re.search(r"\+(\d+)(?:,(\d+))?", line)
                if match:
                    start = int(match.group(1))
                    count = int(match.group(2)) if match.group(2) else 1
                    ranges.append((start, start + count - 1))

    except Exception:
        return []

    return ranges


def is_line_in_diff(line_num: int, ranges: list[tuple[int, int]]) -> bool:
    """判断行号是否在 diff 的新增/修改范围内"""
    for start, end in ranges:
        if start <= line_num <= end:
            return True
    return False


def matches_ignore_patterns(filepath: str, patterns: list[str]) -> bool:
    """判断文件路径是否匹配任一忽略模式 (.gitignore 风格 glob)

    基于 pathspec (GitWildMatchPattern)，完整支持:
        **/generated/**   — 任意层级下的 generated 目录
        **/models.py      — 任意位置的 models.py
        target/           — 任意层级的 target 目录
        *.min.js          — 后缀匹配
        /anchored/x.ts    — 锚定仓库根
    """
    if not patterns:
        return False

    posix_path = filepath.replace("\\", "/")

    try:
        from pathspec import PathSpec
        from pathspec.patterns import GitWildMatchPattern

        spec = PathSpec.from_lines(GitWildMatchPattern, patterns)
        return spec.match_file(posix_path)
    except Exception:
        # pathspec 不可用时退化到基础匹配
        return _fallback_ignore_match(posix_path, patterns)


def _fallback_ignore_match(posix_path: str, patterns: list[str]) -> bool:
    """基础忽略匹配（pathspec 不可用时的兜底）"""
    import fnmatch

    for pattern in patterns:
        if not pattern:
            continue
        p = pattern.replace("\\", "/").rstrip("/")
        if not p:
            continue

        # 目录模式
        if p.endswith("/**"):
            name = p[:-3]
            if "/" not in name and "*" not in name and "?" not in name:
                name = name.removeprefix("**/")
                if name in posix_path.split("/"):
                    return True
        elif "/" not in p:
            # 裸段: 匹配任意层级
            if fnmatch.fnmatch(posix_path, p) or fnmatch.fnmatch(posix_path, "*/" + p):
                return True
        elif p.startswith("**/"):
            suffix = p[3:]
            if fnmatch.fnmatch(posix_path, suffix) or posix_path.endswith("/" + suffix):
                return True
        else:
            if posix_path == p or posix_path.startswith(p + "/") or posix_path.endswith("/" + p):
                return True
    return False


def get_changed_files(repo_root: Path, base: str | None = None) -> dict[str, str]:
    """获取 git diff 中的文件分类

    返回 {filepath: status}，status 为:
        "added"   — 新增文件（git diff 中 --- /dev/null 或 untracked）
        "modified"— 修改文件

    注意: 未提交(untracked)的新文件也被识别为 "added"——
    本地新增未 add 的文件同样需要全量检查。
    """
    if base is None:
        base = _diff_base(repo_root)

    files: dict[str, str] = {}

    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", base],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t", 1)
                if len(parts) != 2:
                    continue
                status_raw, filepath = parts
                # A=added, M=modified, D=deleted, R100=renamed
                if status_raw.startswith("A"):
                    files[filepath] = "added"
                elif status_raw.startswith("M"):
                    files[filepath] = "modified"
                elif status_raw.startswith("R"):
                    # rename: old\tnew 格式，取新文件路径
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        files[parts[-1]] = "modified"

        # 补充 untracked 文件（未 add 的新文件 = 新增）
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if untracked.returncode == 0:
            for filepath in untracked.stdout.strip().split("\n"):
                if filepath:
                    files[filepath] = "added"
    except Exception:
        pass

    return files


def get_added_files(repo_root: Path) -> set[str]:
    """获取新增文件列表"""
    return {f for f, s in get_changed_files(repo_root).items() if s == "added"}


def get_numstat_added_lines(repo_root: Path, base: str | None = None) -> dict[str, int]:
    """获取每个文件的新增行数（numstat）

    返回 {filepath: added_lines}；untracked 新文件按全文行数统计。
    """
    if base is None:
        base = _diff_base(repo_root)

    stats: dict[str, int] = {}

    try:
        result = subprocess.run(
            ["git", "diff", "--numstat", base],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    added = parts[0]
                    filepath = parts[2]
                    stats[filepath] = int(added) if added != "-" else 0

        # untracked 文件: 全文行数即为新增行数
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if untracked.returncode == 0:
            for filepath in untracked.stdout.strip().split("\n"):
                if not filepath:
                    continue
                full = repo_root / filepath
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                        line_count = sum(1 for _ in f)
                    stats[filepath] = line_count
                except OSError:
                    stats[filepath] = 0

    except Exception:
        pass

    return stats


def get_total_added_lines(repo_root: Path) -> int:
    """获取所有文件新增行数总和"""
    return sum(get_numstat_added_lines(repo_root).values())
