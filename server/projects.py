"""projects 蓝图（T2.3 / T2.4）：项目绑定、列表、查看器数据。

接口（技术方案 §4）：
- POST /api/projects {name, repo_url, token, branch} → 绑定仓库并 clone（T2.3）
- GET  /api/projects → 列表（绝不含 token/encrypted_token）（T2.3）
- GET  /api/projects/<pid>/overview → 文档列表 + 入口原型页（T2.4）
- GET  /api/projects/<pid>/prd?file=xx.md → markdown 原文（T2.4）
- GET  /api/projects/<pid>/reconcile → 锚点对账明细（T3.3）

project_id：随机短 slug（kebab-case），与数字主键分离——
本地 clone 目录、/proto/ 路径前缀都用它，路径不可猜测遍历（方案 §4 鉴权说明）。
"""
import os
import re
import secrets

from flask import Blueprint, jsonify, request

from server.config import DEMO_REPO_DIR
from server.crypto_util import encrypt_token
from server.gitops import CloneError, clone_project, pull_project, repo_path
from server.models import Project, utcnow_str
from server.page_map import parse_repo_page_map, scan_proto_anchors
from server.reconcile import reconcile_repo

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


@bp.post("/<int:pid>/sync")
def sync_project(pid: int):
    """手动同步（临时按钮，T3.1 插入）：fetch + merge --ff-only 拉最新内容。

    注意：这不是 T5.1 的完整 SYNC_PULL——后者还含 pull --rebase、
    reviews/ 全量比对修正 DB、对账重算。本接口只做最简拉取 +
    last_sync_at/sync_error 维护，T5.1 实现时将被替换。
    """
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    try:
        pull_project(p.project_id, p.encrypted_token, p.branch)
    except CloneError as e:
        p.sync_error = e.hint
        p.save()
        return _err(e.hint, 400)

    p.sync_error = None
    p.last_sync_at = utcnow_str()
    p.save()
    return jsonify(code=0, data=_project_public(p)), 200


# ───────────────────────── T2.4 查看器数据 ─────────────────────────

SAFE_FILE = re.compile(r"^[\w\-./\u4e00-\u9fff ]+$")


def _repo_root(p: Project) -> str:
    """项目仓库根目录：demo 走 fixture，其余走 /data/repos（与 proto_proxy 一致）。"""
    if p.project_id == "demo":
        return os.path.realpath(DEMO_REPO_DIR)
    return os.path.realpath(repo_path(p.project_id))


def _list_md_files(root: str) -> list[str]:
    """列出仓库内 PRD 文档：prd/ 子树优先；无 prd/ 目录时兼容根目录 *.md。

    返回相对仓库根的路径（正斜杠），排序稳定（prd/ 内优先）。
    """
    docs: list[str] = []
    prd_dir = os.path.join(root, "prd")
    if os.path.isdir(prd_dir):
        for dirpath, _dirnames, filenames in os.walk(prd_dir):
            for fn in sorted(filenames):
                if fn.lower().endswith(".md"):
                    rel = os.path.relpath(os.path.join(dirpath, fn), root)
                    docs.append(rel.replace(os.sep, "/"))
        return docs
    # 兼容：仓库根直接放 md（如用户真实仓库「灵雁 · 产品建设思路.md」）
    for fn in sorted(os.listdir(root)):
        fp = os.path.join(root, fn)
        if os.path.isfile(fp) and fn.lower().endswith(".md"):
            docs.append(fn)
    return docs


def _list_proto_entries(root: str) -> list[str]:
    """列出原型入口候选（prototype/index.html 或 pages/*.html，代理路径形式）。"""
    entries: list[str] = []
    proto_dir = os.path.join(root, "prototype")
    if not os.path.isdir(proto_dir):
        return entries
    idx = os.path.join(proto_dir, "index.html")
    if os.path.isfile(idx):
        entries.append("prototype/index.html")
    pages_dir = os.path.join(proto_dir, "pages")
    if os.path.isdir(pages_dir):
        for fn in sorted(os.listdir(pages_dir)):
            if fn.endswith(".html"):
                entries.append(f"prototype/pages/{fn}")
    return entries


def _list_all_proto_html(root: str) -> list[str]:
    """列出 prototype/ 子树全部 HTML（锚点扫描用，含深层目录与子页）。"""
    proto_dir = os.path.join(root, "prototype")
    out: list[str] = []
    if not os.path.isdir(proto_dir):
        return out
    for dirpath, _dirnames, filenames in os.walk(proto_dir):
        for fn in sorted(filenames):
            if fn.lower().endswith(".html"):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                out.append(rel.replace(os.sep, "/"))
    return out


@bp.get("/<int:pid>/overview")
def overview(pid: int):
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    root = _repo_root(p)
    if not os.path.isdir(root):
        return _err("本地 clone 不存在（可能已被移动或删除），请重新绑定", 410)

    docs = _list_md_files(root)
    proto_files = _list_all_proto_html(root)

    def _read_doc(rel: str) -> str:
        full = os.path.realpath(os.path.join(root, *rel.split("/")))
        with open(full, encoding="utf-8") as f:
            return f.read()

    # 反向联动锚点索引：原型 HTML 中的 data-pa → 文件（组件锚点查文件用）
    proto_index = scan_proto_anchors(proto_files, _read_doc)
    # T3.2：页面地图（PRD 第 4 章表格）→ 反向联动查目标原型文件
    page_map = parse_repo_page_map(docs, _read_doc)
    # T3.3：对账（两侧静态解析 + 三态比对），overview 带摘要（明细走 /reconcile）
    recon = reconcile_repo(docs, proto_files, page_map, _read_doc)

    return jsonify(code=0, data={
        "project": _project_public(p),
        "docs": docs,
        "proto_entries": _list_proto_entries(root),
        "page_map": page_map,
        "proto_anchor_index": proto_index,
        "reconcile_summary": recon["summary"],
    }), 200


@bp.get("/<int:pid>/reconcile")
def reconcile_detail(pid: int):
    """对账明细（T3.3）：三态清单 + 重复 ID + 页面地图坏引用。"""
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    root = _repo_root(p)
    if not os.path.isdir(root):
        return _err("本地 clone 不存在（可能已被移动或删除），请重新绑定", 410)

    def _read_doc(rel: str) -> str:
        full = os.path.realpath(os.path.join(root, *rel.split("/")))
        with open(full, encoding="utf-8") as f:
            return f.read()

    recon = reconcile_repo(
        _list_md_files(root),
        _list_all_proto_html(root),
        parse_repo_page_map(_list_md_files(root), _read_doc),
        _read_doc,
    )
    return jsonify(code=0, data=recon), 200


@bp.get("/<int:pid>/prd")
def prd_file(pid: int):
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    file = str(request.args.get("file") or "").strip()
    if not file or ".." in file.split("/") or file.startswith("/"):
        return _err("非法文件路径", 400)

    root = _repo_root(p)
    full = os.path.realpath(os.path.join(root, *file.split("/")))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return _err("文件不存在", 404)
    if not full.lower().endswith(".md"):
        return _err("仅支持 markdown 文档", 400)

    try:
        with open(full, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return _err(f"读取失败：{e}", 500)
    return jsonify(code=0, data={"file": file, "content": content}), 200
