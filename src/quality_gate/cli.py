"""Quality Gate CLI 主入口"""

import json
import sys
from pathlib import Path

import click

from .checkers.complexity import (
    check_python_crap_incremental,
    check_rust_complexity_incremental,
)
from .checkers.dependency import (
    check_dependency_incremental,
)
from .checkers.duplication import check_duplication_incremental
from .checkers.python_coverage import check_python_coverage_incremental
from .checkers.python_lint import check_python_lint_incremental
from .checkers.rust_coverage import check_rust_coverage_incremental
from .checkers.rust_lint import check_rust_lint_incremental
from .checkers.smell import check_smell_incremental
from .checkers.ts_coverage import check_ts_coverage_incremental
from .checkers.ts_lint import check_ts_lint_incremental
from .config import QualityGateConfig

CHECK_TYPES = ["lint", "coverage", "duplication", "complexity", "dependency", "smell"]


def ensure_in_git_repo() -> None:
    """校验当前目录在 git 仓库内（quality-gate 是独立工具，服务任意项目）

    diff 门禁依赖 git 历史。无 git 根时明确报错退出，而不是把 cwd 当
    repo_root 导致检查器全目录扫描或自动跑测试卡死（2026-09-04 独立化后
    在 /tmp 实测卡死暴露）。

    注意：repo_root 语义保持 cwd——在 monorepo 子项目目录运行就只检查
    该子项目（配置上溯递归查找根 quality-gate.yaml）。这里仅做存在性校验。
    """
    import subprocess

    cwd = Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, timeout=15,
        )
    except Exception:
        click.echo("❌ 无法执行 git——quality-gate 需要 git 仓库环境", err=True)
        sys.exit(1)
    if result.returncode != 0:
        click.echo(
            f"❌ {cwd} 不在任何 git 仓库内。quality-gate 依赖 git diff 做增量门禁，"
            "请在代码项目根或其子目录运行。", err=True)
        sys.exit(1)


@click.group()
@click.version_option(version="0.1.0")
def main():
    """Quality Gate - 自动化代码质量保证工具

    增量门禁：只卡新增/改动引入的问题，存量债务不阻塞。
    """


@main.command()
@click.option(
    "--diff",
    is_flag=True,
    help="增量检查模式（只检查 diff 中的新增/修改行）"
)
@click.option(
    "--lang",
    type=click.Choice(["rust", "ts", "python", "all"]),
    default="all",
    help="检查的语言"
)
@click.option(
    "--checks",
    default="all",
    help="检查类型，逗号分隔: lint,coverage,duplication,complexity,dependency,smell (默认 all)"
)
@click.option(
    "--output",
    type=click.Path(),
    default=None,
    help="输出报告路径（JSON 格式）"
)
@click.option(
    "--verbose",
    is_flag=True,
    help="显示详细输出"
)
def check(diff: bool, lang: str, checks: str, output: str | None, verbose: bool):
    """执行质量门禁检查

    示例:
        quality-gate check --diff                          # 增量检查所有
        quality-gate check --diff --lang rust              # 只检查 Rust
        quality-gate check --diff --checks lint            # 只检查 lint
        quality-gate check --checks lint,duplication       # lint + 重复代码
        quality-gate check --output report.json            # 输出 JSON 报告
    """
    # 解析检查类型
    checks_list = [c.strip() for c in checks.split(",") if c.strip()]
    if "all" in checks_list:
        checks_list = list(CHECK_TYPES)
    unknown = [c for c in checks_list if c not in CHECK_TYPES]
    if unknown:
        click.echo(f"❌ 未知检查类型: {', '.join(unknown)}（可用: {', '.join(CHECK_TYPES)}）")
        sys.exit(2)
    want_lint = "lint" in checks_list
    want_coverage = "coverage" in checks_list
    want_dup = "duplication" in checks_list
    want_complexity = "complexity" in checks_list
    want_dependency = "dependency" in checks_list
    want_smell = "smell" in checks_list

    # 加载配置
    config = QualityGateConfig()
    if verbose:
        click.echo(f"📄 使用配置：{config.config_path or '默认配置'}")

    ensure_in_git_repo()
    repo_root = Path.cwd()
    results: dict = {"checks_run": checks_list}

    exit_code = 0

    # 获取配置中的阈值
    duplication_threshold = config.get_threshold("duplication", 3.0)
    min_tokens = config.get_threshold("min_tokens", 50)
    min_lines = config.get_threshold("min_lines", 5)
    cc_threshold = config.get_threshold("cyclomatic_complexity", 15)
    crap_threshold = config.get_threshold("crap", 30)
    lint_ignore_paths = config.lint_ignore_paths
    coverage_ignore_paths = config.coverage_ignore_paths

    # Rust 检查
    if lang in ["rust", "all"]:
        click.echo("🔍 检查 Rust 代码...")
        rust_result = {}

        if want_lint:
            rust_result["lint"] = check_rust_lint_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
            )
            if rust_result["lint"]["blocking"]:
                exit_code = 1

        if want_coverage:
            rust_result["coverage"] = check_rust_coverage_incremental(
                repo_root, verbose=verbose, ignore_paths=coverage_ignore_paths,
            )
            if rust_result["coverage"]["blocking"]:
                exit_code = 1

        if want_complexity:
            rust_result["complexity"] = check_rust_complexity_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
                threshold=cc_threshold,
            )
            if rust_result["complexity"]["blocking"]:
                exit_code = 1

        if want_dependency:
            rust_result["dependency"] = check_dependency_incremental(
                repo_root, lang="rust", verbose=verbose,
            )
            if rust_result["dependency"]["blocking"]:
                exit_code = 1

        results["rust"] = rust_result

    # TypeScript 检查
    if lang in ["ts", "all"]:
        click.echo("🔍 检查 TypeScript 代码...")
        ts_result = {}
        if want_lint:
            ts_result["lint"] = check_ts_lint_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
            )
            if ts_result["lint"]["blocking"]:
                exit_code = 1
        if want_coverage:
            ts_result["coverage"] = check_ts_coverage_incremental(
                repo_root, verbose=verbose, ignore_paths=coverage_ignore_paths,
            )
            if ts_result["coverage"]["blocking"]:
                exit_code = 1
        if want_dependency:
            ts_result["dependency"] = check_dependency_incremental(
                repo_root, lang="ts", verbose=verbose,
            )
            if ts_result["dependency"]["blocking"]:
                exit_code = 1
        results["ts"] = ts_result

    # Python 检查
    if lang in ["python", "all"]:
        click.echo("🔍 检查 Python 代码...")
        python_result = {}
        if want_lint:
            python_result["lint"] = check_python_lint_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
            )
            if python_result["lint"]["blocking"]:
                exit_code = 1
        if want_coverage:
            python_result["coverage"] = check_python_coverage_incremental(
                repo_root, verbose=verbose, ignore_paths=coverage_ignore_paths,
            )
            if python_result["coverage"]["blocking"]:
                exit_code = 1
        if want_complexity:
            python_result["complexity"] = check_python_crap_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
                crap_threshold=crap_threshold,
            )
            # 阶段一不阻塞
        if want_dependency:
            python_result["dependency"] = check_dependency_incremental(
                repo_root, lang="python", verbose=verbose,
            )
            if python_result["dependency"]["blocking"]:
                exit_code = 1
        if want_smell:
            python_result["smell"] = check_smell_incremental(
                repo_root, verbose=verbose, ignore_paths=lint_ignore_paths,
            )
            if python_result["smell"]["blocking"]:
                exit_code = 1
        results["python"] = python_result

    # 重复代码检查（语言无关，跑一次）
    if want_dup:
        click.echo("🔍 检查重复代码 (jscpd)...")
        dup_result = check_duplication_incremental(
            repo_root, verbose=verbose,
            threshold=duplication_threshold,
            min_tokens=min_tokens,
            min_lines=min_lines,
            ignore_paths=lint_ignore_paths,
        )
        results["duplication"] = dup_result
        if dup_result["blocking"]:
            exit_code = 1

    # 输出报告
    report = {
        "success": exit_code == 0,
        "diff_mode": diff,
        "results": results,
    }

    if output:
        output_path = Path(output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        click.echo(f"📄 报告已保存到：{output_path}")
    else:
        click.echo("\n" + "=" * 60)
        click.echo("质量门禁检查结果:")
        _print_summary(results)

    if exit_code == 0:
        click.echo("\n✅ 质量门禁通过")
    else:
        click.echo("\n❌ 质量门禁失败 - 请修复上述问题")

    sys.exit(exit_code)


def _print_summary(results: dict):
    """打印各检查项摘要"""
    for key, value in results.items():
        if key == "checks_run":
            continue
        if value is None or not isinstance(value, dict):
            continue
        if not value:
            continue

        # duplication 是扁平结构
        if key == "duplication":
            status = "✅ 通过" if not value["blocking"] else "❌ 阻塞"
            click.echo(f"  {key.upper()}: {status}")
            if value.get("duplication_rate") is not None:
                click.echo(
                    f"    新增重复率: {value['duplication_rate']}% "
                    f"({value.get('duplicated_added_lines', 0)}/"
                    f"{value.get('total_added_lines', 0)} 行)"
                )
            _print_issues(value.get("issues", []))
            continue

        # rust/ts/python: {check_type: result}
        for check_type, check_result in value.items():
            if not check_result or not isinstance(check_result, dict):
                continue
            if check_result.get("skipped"):
                click.echo(f"  {key.upper()} ({check_type}): ⏭️ 跳过"
                           f" ({check_result['skipped']})")
                continue
            status = "✅ 通过" if not check_result["blocking"] else "❌ 阻塞"
            click.echo(f"  {key.upper()} ({check_type}): {status}")
            _print_issues(check_result.get("issues", []))
            # 报告项 (不阻塞的复杂度/CRAP)
            report_only = check_result.get("report_only_issues", [])
            if report_only:
                click.echo(f"    (报告项 {len(report_only)} 个，不阻塞)")


def _print_issues(issues: list, max_show: int = 5):
    """打印问题列表前 max_show 条"""
    if not issues:
        return
    click.echo(f"    发现问题：{len(issues)} 个")
    for issue in issues[:max_show]:
        loc = f"{issue['file']}:{issue['line']}"
        click.echo(f"      - {loc} [{issue.get('code', '')}] {issue['message']}")
    if len(issues) > max_show:
        click.echo(f"      ... 还有 {len(issues) - max_show} 个")


@main.command()
@click.option(
    "--lang",
    type=click.Choice(["rust", "ts", "python", "all"]),
    default="all",
    help="扫描的语言"
)
@click.option(
    "--output",
    type=click.Path(),
    default=None,
    help="输出报告路径（JSON 格式，默认 stdout 摘要 + 存档 history）"
)
@click.option(
    "--no-archive",
    is_flag=True,
    help="不存档到 .quality-gate/history（默认每次 scan 都存档供周报趋势）"
)
@click.option(
    "--verbose",
    is_flag=True,
    help="显示详细输出"
)
def scan(lang: str, output: str | None, no_archive: bool, verbose: bool):
    """全仓扫描（周报）

    与 check 增量门禁不同，scan 是全仓快照:
      - 不做 git diff 过滤，报告全部存量问题（lint/重复/依赖/CRAP）
      - 不阻塞：无论发现多少问题 exit code 都是 0（工具级错误除外）
      - 每次结果存档到 .quality-gate/history/scan-<ts>.json，
        与上次扫描对比输出周报趋势

    示例:
        quality-gate scan                        # 全语言全仓扫描 + 存档 + 趋势
        quality-gate scan --lang python          # 只扫 Python
        quality-gate scan --output report.json   # 报告写到指定文件（不存档）
    """
    from .scanner import (
        compare_reports,
        latest_report_path,
        load_report,
        print_trend,
        run_full_scan,
        save_scan_report,
    )

    ensure_in_git_repo()
    repo_root = Path.cwd()
    click.echo("📊 全仓扫描（周报模式）...")
    results = run_full_scan(repo_root, lang=lang, verbose=verbose)

    report = {
        "schema_version": 1,
        "timestamp": _scan_now_iso(),
        "cwd": str(repo_root),
        "scan_lang": lang,
        "results": results,
    }
    # 汇总计数（供 stdout 摘要与 JSON 消费方）
    summary = _scan_summary(results)
    report["summary"] = summary

    if output:
        output_path = Path(output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        click.echo(f"📄 报告已保存到：{output_path}")
        click.echo(f"   摘要: lint 问题 {summary['lint_issues']} 个 / "
                   f"重复块 {summary['duplication_blocks']} 处 / "
                   f"依赖违规 {summary['dependency_issues']} 个 / "
                   f"CRAP 超阈值 {summary['crap_functions']} 个")
    else:
        click.echo("\n" + "=" * 60)
        click.echo("全仓扫描结果（存量问题总量，不阻塞）:")
        for key, value in results.items():
            if key == "checks_run":
                continue
            _print_scan_block(key, value)

        # 存档 + 趋势（baseline 必须在保存前取，否则刚存的报告恒为"最新"）
        if not no_archive:
            baseline_path = latest_report_path(repo_root)
            path = save_scan_report(repo_root, report)
            click.echo(f"\n📁 报告已存档: {path.relative_to(repo_root)}")
            if baseline_path and baseline_path != path:
                baseline = load_report(baseline_path)
                if baseline:
                    trend = compare_reports(baseline, report)
                    print_trend(trend)
                else:
                    click.echo("   (上一份存档无法解析，跳过趋势对比)")

    # scan 不阻塞：问题数不决定退出码；仅工具级错误由 checkers 记录但同样 exit 0
    sys.exit(0)


def _scan_now_iso() -> str:
    """本地时区 ISO 时间戳（scanner 内复用避免循环 import）"""
    from .scanner import _now_iso
    return _now_iso()


def _scan_summary(results: dict) -> dict:
    """从 scan results 提取汇总计数"""
    lint_issues = 0
    dependency_issues = 0
    crap_functions = 0
    for lang in ("rust", "ts", "python"):
        lang_res = results.get(lang) or {}
        if not isinstance(lang_res, dict):
            continue
        lint = lang_res.get("lint") or {}
        if isinstance(lint, dict):
            lint_issues += len(lint.get("issues", []))
        dep = lang_res.get("dependency") or {}
        if isinstance(dep, dict):
            dependency_issues += len(dep.get("issues", []))
        if lang == "python":
            complexity = lang_res.get("complexity") or {}
            if isinstance(complexity, dict):
                crap_functions += len(complexity.get("issues", []))
    dup = results.get("duplication") or {}
    dup_blocks = len(dup.get("issues", [])) if isinstance(dup, dict) else 0
    return {
        "lint_issues": lint_issues,
        "duplication_blocks": dup_blocks,
        "dependency_issues": dependency_issues,
        "crap_functions": crap_functions,
    }


def _print_scan_block(key: str, value: dict, max_show: int = 3):
    """打印 scan 单个语言/检查块摘要（存量问题只显示条数 + 前几例）"""
    if not value or not isinstance(value, dict):
        return
    if key == "duplication":
        click.echo(f"  重复代码: 全仓 {len(value.get('issues', []))} 处重复块")
        for issue in value.get("issues", [])[:max_show]:
            loc = f"{issue['file']}:{issue['line']}"
            click.echo(f"      - {loc} {issue['message'][:80]}")
        return
    for check_type, check_result in value.items():
        if not isinstance(check_result, dict):
            continue
        if check_result.get("skipped"):
            click.echo(f"  {key.upper()} ({check_type}): ⏭️ 跳过 ({check_result['skipped']})")
            continue
        issues = check_result.get("issues", [])
        status = f"发现 {len(issues)} 个存量问题" if issues else "干净"
        click.echo(f"  {key.upper()} ({check_type}): {status}")
        for issue in issues[:max_show]:
            loc = f"{issue['file']}:{issue['line']}"
            click.echo(f"      - {loc} [{issue.get('code', '')}] {issue['message'][:80]}")
        report_only = check_result.get("report_only_issues", [])
        if report_only:
            click.echo(f"    (另有报告项 {len(report_only)} 个，不阻塞)")


@main.command()
def fix():
    """自动修复可修复的问题"""
    click.echo("🔧 自动修复功能开发中...")
    sys.exit(0)


if __name__ == "__main__":
    main()
