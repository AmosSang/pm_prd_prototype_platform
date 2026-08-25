"""项目本地存储工具（T8.1 去 Git 本地化）。

项目内容存 PROJECTS_DIR/{project_id}/，三目录约定：
prototype/（上传 zip 解压）、prd/（唯一 markdown）、
reviews/{comments,shots}/（评论 JSON 与截图）。

demo 演示项目特判指向 tests/fixtures（沿用 T1.1 以来行为）。
"""
import os
import re
import shutil

from server.config import DEMO_REPO_DIR, PROJECTS_DIR

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def is_demo(slug: str) -> bool:
    return slug == "demo"


def project_dir(slug: str) -> str:
    """项目根目录（demo 走 fixture）。slug 先校验再拼接，防路径注入。"""
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(f"非法项目 ID：{slug!r}")
    if is_demo(slug):
        return os.path.realpath(DEMO_REPO_DIR)
    return os.path.join(PROJECTS_DIR, slug)


def project_root(slug: str) -> str:
    """project_dir 别名（与既有 _repo_root 语义一致，modules 迁移过渡用）。"""
    return project_dir(slug)


def ensure_project_dirs(slug: str) -> str:
    """创建项目目录骨架（prototype/prd/reviews/comments/reviews/shots）。

    幂等；demo 项目不创建（fixture 只读）。返回项目根路径。
    """
    root = project_dir(slug)
    if is_demo(slug):
        return root
    for sub in ("prototype", "prd", "reviews", "reviews/comments", "reviews/shots"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return root


def delete_project_dirs(slug: str) -> None:
    """删除项目目录（项目删除接口用）。demo 拒绝；不存在时静默。"""
    if is_demo(slug):
        raise ValueError("demo 项目不可删除")
    root = os.path.join(PROJECTS_DIR, slug)
    if os.path.isdir(root):
        shutil.rmtree(root)
