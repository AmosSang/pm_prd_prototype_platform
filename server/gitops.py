"""git 操作（T2.3）：clone 到 /data/repos/{project_id}。

凭据规则（技术方案 §2.7 / AGENTS.md §3.6）：
- token 只在内存中拼接使用，通过 GIT_ASKPASS 环境变量注入给 git 子进程
- clone 完成后立即 `git remote set-url` 为干净 URL（无 token）
- token 不进代码、不进日志、不进 .git/config

错误分类（前端展示用，文案集中在 ERROR_HINT）：
- 认证失败（错误 token / 无权限）→ 401
- 仓库不存在 / 无访问权 → 404
- 其余（网络、超时等）→ 上抛原始信息
"""
import os
import re
import stat
import tempfile

from git import Actor, GitCommandError
from git import Repo as GitRepo

from server.config import REPOS_DIR
from server.crypto_util import decrypt_token


class CloneError(Exception):
    """clone 失败（带分类 code 与用户可读提示）。"""

    def __init__(self, hint: str, reason: str = ""):
        super().__init__(hint)
        self.hint = hint
        self.reason = reason


def _write_askpass(token: str) -> tuple[str, str]:
    """生成临时 GIT_ASKPASS 脚本，返回 (脚本路径, 环境变量名)。

    脚本从环境变量 PPP_GIT_TOKEN 取凭据输出给 git（用户名固定 oauth2，
    密码为 token——GitLab project access token 惯例）。
    文件 0700、用完即删；token 不写入脚本、不进命令行。
    """
    fd, path = tempfile.mkstemp(prefix="ppp-askpass-")
    with os.fdopen(fd, "w") as f:
        f.write("#!/bin/sh\nprintf '%s\\n' \"$PPP_GIT_TOKEN\"\n")
    os.chmod(path, stat.S_IRWXU)
    return path, "PPP_GIT_TOKEN"


def _askpass_env(token: str) -> dict[str, str]:
    """构造带 GIT_ASKPASS 的子进程环境（token 经环境变量传给脚本，不出现在命令行）。"""
    script, _ = _write_askpass(token)
    env = os.environ.copy()
    env["GIT_ASKPASS"] = script
    env["PPP_GIT_TOKEN"] = token
    env["GIT_TERMINAL_PROMPT"] = "0"  # 禁止交互式输密码（卡死防护）
    return env, script


def _cleanup_askpass(script: str) -> None:
    try:
        os.unlink(script)
    except OSError:
        pass


def _classify_error(e: GitCommandError, repo_url: str) -> CloneError:
    """把 GitCommandError 翻译成用户可读的中文提示。

    真实报错文案（本地假 GitLab 实测，git 2.x）：
    - 401 → "fatal: Authentication failed for '<url>'"
    - 404 → "fatal: repository '<url>' not found"
    """
    raw = (e.stderr or "") + (e.stdout or "")

    if "Authentication failed" in raw or "401" in raw or "could not read Username" in raw:
        return CloneError("认证失败：token 无效或已过期，请检查后重试", raw)
    if "not found" in raw.lower() or "404" in raw or "does not appear to be a git repository" in raw:
        return CloneError(f"仓库不存在或无访问权限：{repo_url}", raw)
    if "timed out" in raw.lower() or "timeout" in raw.lower() or "Could not resolve host" in raw:
        return CloneError("无法访问仓库地址：请检查 URL、网络或内网 GitLab 可达性", raw)
    return CloneError(f"克隆失败：{e.command} 退出码 {e.status}", raw)


def repo_path(project_id: str) -> str:
    return os.path.join(REPOS_DIR, project_id)


def clone_project(project_id: str, repo_url: str, encrypted_token: str, branch: str) -> None:
    """clone 指定分支到 REPOS_DIR/{project_id}。

    成功后 remote url 保持干净（不含 token）；失败时清理半成品目录。
    抛 CloneError（调用方转 4xx/5xx 文案）。
    """
    dest = repo_path(project_id)
    if os.path.exists(dest):
        raise CloneError(f"项目目录已存在：{project_id}", "dest exists")

    os.makedirs(REPOS_DIR, exist_ok=True)
    env, script = _askpass_env(decrypt_token(encrypted_token))
    try:
        GitRepo.clone_from(
            repo_url,
            dest,
            branch=branch,
            multi_options=["--depth=50", "--single-branch"],
            env=env,
        )
        # 保险：确保 remote url 干净（clone_from 本身没把 token 放 URL，这里显式确认）
        repo = GitRepo(dest)
        clean = repo.remote().url
        if "://" in clean and "@" in clean.split("://", 1)[1]:
            repo.git.remote("set-url", re.sub(r"(://[^/]+)@", r"\1", clean))
    except GitCommandError as e:
        _remove_tree(dest)
        raise _classify_error(e, repo_url) from e
    except Exception as e:  # noqa: BLE001 — 兜底清理
        _remove_tree(dest)
        raise CloneError(f"克隆失败：{e}", str(e)) from e
    finally:
        _cleanup_askpass(script)


def _remove_tree(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def pull_project(project_id: str, encrypted_token: str, branch: str) -> None:
    """手动同步：fetch + merge --ff-only（临时同步按钮，T3.1 插入）。

    T5.1 的完整 SYNC_PULL（pull --rebase + reviews/ 全量比对修正 DB +
    对账重算）上线后本函数会被替换——此处只做最简拉取，不解析 reviews/。

    抛 CloneError（沿用 clone 的错误分类文案）。
    """
    dest = repo_path(project_id)
    if not os.path.isdir(dest):
        raise CloneError("本地 clone 不存在，请重新绑定项目", "no local clone")

    env, script = _askpass_env(decrypt_token(encrypted_token))
    try:
        repo = GitRepo(dest)
        repo.git.fetch("origin", branch, env=env)
        # ff-only：本地有未推提交时拒绝合并（报冲突提示，不破坏工作区）
        repo.git.merge(f"origin/{branch}", "--ff-only", env=env)
    except GitCommandError as e:
        raw = (e.stderr or "") + (e.stdout or "")
        if "Not possible to fast-forward" in raw or "divergent branches" in raw:
            raise CloneError(
                "本地与远端分叉，无法快进合并；请在仓库里手动处理后再同步", raw
            ) from e
        raise _classify_error(e, "pull") from e
    finally:
        _cleanup_askpass(script)


def commit_and_push(
    project_id: str,
    encrypted_token: str,
    branch: str,
    message: str,
    author_name: str,
    author_email: str,
    paths: list[str] | None = None,
    remove: bool = False,
) -> tuple[str | None, int]:
    """落仓提交（T4.3 队列 worker 的执行单元）。

    add/rm 指定路径（精确到评论自己的文件，git log 每条 commit 对应一条
    评论，diff 清晰无夹带）→ commit（作者=操作人）→ push。

    push 冲突重试（技术方案 §2.7）：push 失败（远端有新提交，典型为 skill
    回写后平台不知情）→ pull --rebase → 再 push，最多 3 次 push；rebase
    本身失败（冲突/网络）→ abort 留干净现场并置错误。

    返回 (错误描述 | None, push 尝试次数)。remove=True 用 git rm
    --ignore-unmatch（COMMIT_DELETE；文件不存在不算错）。
    """
    dest = repo_path(project_id)
    if not os.path.isdir(dest):
        return f"本地 clone 不存在：{project_id}", 0

    env, script = _askpass_env(decrypt_token(encrypted_token))
    try:
        repo = GitRepo(dest)
        if remove:
            repo.git.rm("-f", "--ignore-unmatch", *(paths or []), env=env)
        else:
            # 只 add 指定路径——评论与截图是平台在仓库的唯一写入口，
            # 精确路径防止误提交工作区其他未知变更（如残留调试文件）
            repo.git.add(*(paths or ["reviews"]), env=env)
        actor = Actor(author_name, author_email)
        repo.index.commit(message, author=actor, committer=actor)

        attempts = 0
        while attempts < 3:
            attempts += 1
            try:
                repo.git.push("origin", branch, env=env)
                return None, attempts
            except GitCommandError as push_err:
                if attempts >= 3:
                    raw = ((push_err.stderr or "") + " " + (push_err.stdout or "")).strip()
                    return f"push 重试 3 次仍失败：{raw[:300]}", attempts
                # 远端有新提交 → rebase 到远端最新后重试
                try:
                    repo.git.pull("--rebase", "origin", branch, env=env)
                except GitCommandError:
                    # rebase 冲突/网络失败：中止 rebase 留干净现场（下次
                    # 手动同步可恢复），不再重试（同样结果）
                    try:
                        repo.git.rebase("--abort")
                    except GitCommandError:
                        pass
                    return "push 冲突且 pull --rebase 失败：请在仓库手动处理后重新同步", attempts
        return "push 重试次数用尽", attempts
    except GitCommandError as e:
        raw = ((e.stderr or "") + " " + (e.stdout or "")).strip()
        return f"落仓失败：{raw[:300]}", 0
    finally:
        _cleanup_askpass(script)
