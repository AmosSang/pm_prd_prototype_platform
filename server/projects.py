"""projects 蓝图（T2.3）：项目绑定（clone）+ 列表。

接口（技术方案 §4）：
- POST /api/projects {name, repo_url, token, branch} → 绑定仓库并 clone
- GET  /api/projects → 列表（绝不含 token/encrypted_token）

project_id：随机短 slug（kebab-case），与数字主键分离——
本地 clone 目录、/proto/ 路径前缀都用它，路径不可猜测遍历（方案 §4 鉴权说明）。
"""
import secrets

from flask import Blueprint, jsonify, request

from server.crypto_util import encrypt_token
from server.gitops import CloneError, clone_project
from server.models import Project, utcnow_str

bp = Blueprint("projects", __name__, url_prefix="/api/projects")


def _err(msg: str, status: int):
    return jsonify(code=status, msg=msg), status


def _slug(name: str) -> str:
    """项目名 → kebab-case 短 slug（长度限 24，加随机后缀防撞）。"""
    s = "".join(c if c.isascii() and (c.isalnum() or c == "-") else "-" for c in name.lower())
    import re

    s = re.sub(r"-{2,}", "-", s).strip("-")[:12] or "proj"
    return f"{s}-{secrets.token_hex(3)}"


def _project_public(p: Project) -> dict:
    """列表/详情对外字段（无 token 类字段）。"""
    return {
        "id": p.id,
        "project_id": p.project_id,
        "name": p.name,
        "repo_url": p.repo_url,
        "branch": p.branch,
        "commentable": p.commentable,
        "last_sync_at": p.last_sync_at,
        "sync_error": p.sync_error,
        "created_at": p.created_at,
    }


@bp.post("")
def create_project():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    repo_url = str(data.get("repo_url") or "").strip()
    token = str(data.get("token") or "").strip()
    branch = str(data.get("branch") or "main").strip() or "main"

    if not name or len(name) > 50:
        return _err("项目名必填（50 字内）", 400)
    if not repo_url.startswith(("http://", "https://", "/")):
        return _err("仓库地址须为 http(s) URL 或本地绝对路径", 400)
    if not token:
        return _err("git token 必填（GitLab project access token）", 400)

    # 先 clone（失败不落库），成功再建 DB 记录
    project_id = _slug(name)
    encrypted = encrypt_token(token)
    try:
        clone_project(project_id, repo_url, encrypted, branch)
    except CloneError as e:
        return _err(e.hint, 400)

    p = Project.create(
        project_id=project_id,
        name=name,
        repo_url=repo_url,
        encrypted_token=encrypted,
        branch=branch,
        last_sync_at=utcnow_str(),
        sync_error=None,
    )
    return jsonify(code=0, data=_project_public(p)), 200


@bp.get("")
def list_projects():
    rows = Project.select().order_by(Project.id.desc())
    return jsonify(code=0, data=[_project_public(p) for p in rows]), 200


@bp.get("/<int:pid>/git-status")
def git_status(pid: int):
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    return jsonify(code=0, data={"last_sync_at": p.last_sync_at, "sync_error": p.sync_error}), 200
