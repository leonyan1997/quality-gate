"""共享 git 测试夹具（避免各测试文件重复 git 样板）

一行一个 git 调用、函数体短小——新增代码同时满足增量门禁 duplication
阈值（min_lines 5 / min_tokens 50），不与各测试文件既有的内联 git
样板拼成重复块。
"""

import subprocess


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True)


def init_git_repo(tmp_path):
    """最小真实 git 仓库（增量语义依赖 git diff；含空 init commit）"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=T", "-c", "user.email=t@t.t",
         "commit", "-qm", "init", "--allow-empty")
    return repo


def commit_all(repo, message="wip"):
    """git add -A + 提交（本地假身份）"""
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=T", "-c", "user.email=t@t.t",
         "commit", "-qm", message)
