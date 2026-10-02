"""projects 蓝图（T2.3 / T2.4 / T8.1）：项目创建、列表、查看器数据。

接口（技术方案 §4，T8.1 去 Git 本地化修订）：
- POST /api/projects {name} → 创建项目（建目录 + DB 记录；creator=当前用户）
- GET  /api/projects → 列表（含创建者信息与 is_creator 标记）
- PATCH /api/projects/<pid> {commentable} → 可评论开关（T8.4 收权为创建者）
- POST /api/projects/<pid>/prototype → 上传原型 zip（安全校验 + 智能下钻；仅创建者）
- POST /api/projects/<pid>/prd → 上传 PRD markdown（≤5MB，替换旧文档；仅创建者）
- DELETE /api/projects/<pid> → 删除项目（目录 + 评论 + DB；仅创建者）
- GET  /api/projects/<pid>/overview → 文档列表 + 入口原型页（T2.4）
- GET  /api/projects/<pid>/prd?file=xx.md → markdown 原文（T2.4）
- GET  /api/projects/<pid>/reconcile → 锚点对账明细（T3.3）

project_id：随机短 slug（kebab-case），与数字主键分离——
本地项目目录、/proto/ 路径前缀都用它，路径不可猜测遍历（方案 §4 鉴权说明）。
"""
import io
import os
import re
import secrets
import shutil
import tempfile
import zipfile

from flask import Blueprint, jsonify, request, send_file, session

from server.config import (
    PRD_MAX_BYTES,
    PROTO_MAX_DESCEND,
    PROTO_UNZIP_MAX_BYTES,
    PROTO_UNZIP_MAX_FILES,
    PROTO_ZIP_MAX_BYTES,
)
from server.models import Comment, Project, ProjectMember, User, utcnow_str
from server.page_map import parse_repo_page_map, scan_proto_anchors
from server.reconcile import reconcile_repo
from server.storage import delete_project_dirs, ensure_project_dirs, project_root

bp = Blueprint("projects", __name__, url_prefix="/api/projects")


def _err(msg: str, status: int):
    return jsonify(code=status, msg=msg), status


def _slug(name: str) -> str:
    """项目名 → kebab-case 短 slug（长度限 24，加随机后缀防撞）。"""
    s = "".join(c if c.isascii() and (c.isalnum() or c == "-") else "-" for c in name.lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")[:12] or "proj"
    return f"{s}-{secrets.token_hex(3)}"


def _creator_public(creator_id: int) -> dict:
    """创建者信息（列表/详情展示用）。"""
    u = User.get_or_none(User.id == creator_id)
    if not u:
        return {"id": creator_id, "name": "（已删除用户）", "email": ""}
    return {"id": u.id, "name": u.name, "email": u.email}


def _is_member(project_id: int, uid: int) -> bool:
    """uid 是否为项目协作者（T9.1）。"""
    return (
        ProjectMember.select()
        .where(ProjectMember.project == project_id, ProjectMember.user == uid)
        .exists()
    )


def _is_manager(p: Project, uid: int | None) -> bool:
    """管理者判定（T9.1 权限矩阵 V2）：创建者或协作者。"""
    if uid is None:
        return False
    return uid == p.creator_id or _is_member(p.id, uid)


def _project_public(p: Project) -> dict:
    """列表/详情对外字段（含创建者、is_creator/is_manager 标记与协作者数）。

    T12.3 新增 comment_count（MCP list_projects 口径要求；对既有消费方为
    新增可选字段，向前兼容）。
    """
    uid = session.get("uid")
    member_count = (
        ProjectMember.select().where(ProjectMember.project == p.id).count()
    )
    comment_count = (
        Comment.select()
        .where(Comment.project == p.id, Comment.deleted == False)  # noqa: E712
        .count()
    )
    return {
        "id": p.id,
        "project_id": p.project_id,
        "name": p.name,
        "creator": _creator_public(p.creator_id),
        "is_creator": uid is not None and uid == p.creator_id,
        "is_manager": _is_manager(p, uid),
        "member_count": member_count,
        "comment_count": comment_count,
        "commentable": p.commentable,
        "content_updated_at": p.content_updated_at,
        "created_at": p.created_at,
    }


@bp.post("")
def create_project():
    """创建项目（T8.1）：只填名称；建目录骨架 + DB 记录，creator=当前用户。

    内容（原型 zip / PRD md）由 T8.2 上传接口补充——创建后项目为空，
    查看器对空项目展示空态。
    """
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()

    if not name or len(name) > 50:
        return _err("项目名必填（50 字内）", 400)

    uid = session.get("uid")
    if not uid:
        return _err("未登录", 401)

    project_id = _slug(name)
    ensure_project_dirs(project_id)
    p = Project.create(
        project_id=project_id,
        name=name,
        creator_id=uid,
    )
    return jsonify(code=0, data=_project_public(p)), 200


@bp.get("")
def list_projects():
    rows = Project.select().order_by(Project.id.desc())
    return jsonify(code=0, data=[_project_public(p) for p in rows]), 200


@bp.patch("/<int:pid>")
def update_project(pid: int):
    """项目设置更新（T4.5 / T9.1 收权扩围）：commentable 可评论开关，仅管理者。

    产品方案 §4.5：默认开启；关闭后全员评论入口置灰、一切写评论操作
    被拦截（已有评论仍可查看）。T9.1 起创建者与协作者均可开关（权限矩阵 V2）。
    """
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    deny = _require_manager(p)
    if deny:
        return deny

    data = request.get_json(silent=True) or {}
    if "commentable" not in data:
        return _err("缺少 commentable 字段", 400)
    val = data["commentable"]
    if not isinstance(val, bool):
        return _err("commentable 必须是布尔值", 400)

    p.commentable = val
    p.save()
    return jsonify(code=0, data=_project_public(p)), 200


# ───────────────────── 内容上传（T8.1 最小版；T8.2 补前端 UI 与部署层限额）─────────────────────


def _require_manager(p: Project):
    """管理者校验（T9.1 权限矩阵 V2：创建者或协作者）。

    覆盖：可评论开关、上传原型/PRD、导出评论、编辑/删除任意评论、
    批量改状态（AGENTS.md 硬规则 6）。
    """
    uid = session.get("uid")
    if not uid or not _is_manager(p, uid):
        return _err("仅项目创建者或协作者可执行此操作", 403)
    return None


def _require_admin_or_creator(p: Project):
    """删除权限（T 增强，维持不变）：项目创建者 或 超级管理员 可删任意项目。

    T9.1 权限矩阵 V2：协作者不可删项目；此函数同时作为协作者管理的
    权限门槛（创建者或超管可增删协作者）。
    """
    uid = session.get("uid")
    if not uid:
        return _err("未登录", 401)
    if uid == p.creator_id:
        return None
    u = User.get_or_none(User.id == uid)
    if u is not None and u.is_admin:
        return None
    return _err("仅项目创建者或超级管理员可执行此操作", 403)


def _is_junk_entry(name: str) -> bool:
    """macOS Finder 压缩产物垃圾条目：__MACOSX/ 资源目录、.DS_Store、
    ._xxx 资源 fork（AppleDouble）。这些不是原型内容，直接跳过不解压。"""
    parts = name.split("/")
    if parts[0] == "__MACOSX":
        return True
    base = parts[-1]
    return base == ".DS_Store" or base.startswith("._")


def _safe_unzip(data: bytes, dest: str) -> None:
    """安全解压（AGENTS.md 硬规则 7）：zip-slip 拒绝 + 软链拒绝 + 限额。

    逐条目 realpath 校验目标在 dest 内——路径穿越条目与软链条目直接拒绝
    （不是净化）；校验全过才解压（防解压炸弹：超限在写盘前拦截）。
    macOS 打包垃圾条目（__MACOSX/.DS_Store/._*）跳过不解压。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError("不是合法的 zip 包") from e

    entries = zf.infolist()
    if len(entries) > PROTO_UNZIP_MAX_FILES:
        raise ValueError(f"压缩包条目数超过 {PROTO_UNZIP_MAX_FILES}")
    total = sum(info.file_size for info in entries)
    if total > PROTO_UNZIP_MAX_BYTES:
        raise ValueError(f"解压总量超过 {PROTO_UNZIP_MAX_BYTES // 1024 // 1024}MB 上限")

    dest_real = os.path.realpath(dest)
    for info in entries:
        if _is_junk_entry(info.filename):
            continue
        # 软链条目拒绝（Unix 属性高 16 位 = 软链标记）：解压后可能指向任意路径
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"压缩包含软链条目：{info.filename}")
        target = os.path.realpath(os.path.join(dest, *info.filename.split("/")))
        if target != dest_real and not target.startswith(dest_real + os.sep):
            raise ValueError(f"压缩包含路径穿越条目：{info.filename}")
    for info in entries:
        if not _is_junk_entry(info.filename):
            zf.extract(info, dest)


def _top_has_html(d: str) -> bool:
    """目录顶层（非递归）是否有 html 页面。"""
    return any(
        fn.lower().endswith(".html")
        for fn in os.listdir(d)
        if os.path.isfile(os.path.join(d, fn))
    )


def _descend_unique_child(dir_root: str) -> str:
    """T8.2 智能下钻：zip 根顶层无 html 时进入唯一子目录（产品常见
    打包形态「dist/index.html」「prototype/index.html」）；子目录不唯一
    或子树无 html 则原样返回（由调用方按顶层无 html 报错）。

    「唯一」按可见内容目录计——点开头目录（.git 等）不参与计数，
    macOS 垃圾条目已在解压时跳过（__MACOSX/.DS_Store/._* 不会落盘）。

    T12.6 修复：原先只下钻一层，但 export_prototype 导出的包形态是
    「<slug>-prototype/prototype/index.html」——**两层**。导出的包自己
    再上传回来会失败（报「未找到 HTML 页面」）。改为逐层下钻（上限
    PROTO_MAX_DESCEND 层），使导出→上传往返自洽。
    """
    if _top_has_html(dir_root):
        return dir_root
    cur = dir_root
    for _ in range(PROTO_MAX_DESCEND):
        children = [
            c for c in os.listdir(cur)
            if os.path.isdir(os.path.join(cur, c)) and not c.startswith(".")
        ]
        if len(children) != 1:
            return cur
        child = os.path.join(cur, children[0])
        if _top_has_html(child):
            return child
        # 该层顶层无 html，但子树里可能有，继续向下（限深，避免深树遍历）
        if not any(
            fn.lower().endswith(".html")
            for _dp, _dn, fns in os.walk(child)
            for fn in fns
        ):
            return cur
        cur = child
    return cur


@bp.post("/<int:pid>/prototype")
def upload_prototype(pid: int):
    """上传原型 zip：校验通过 → 解压临时目录 → 安全校验 → 智能下钻 →
    原子替换 prototype/，失败保留旧版本（AGENTS.md 硬规则 7）。仅创建者。
    """
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    deny = _require_manager(p)
    if deny:
        return deny

    f = request.files.get("zip")
    if f is None:
        return _err("缺少 zip 文件", 400)
    data = f.read(PROTO_ZIP_MAX_BYTES + 1)
    if len(data) > PROTO_ZIP_MAX_BYTES:
        return _err(f"原型包超过 {PROTO_ZIP_MAX_BYTES // 1024 // 1024}MB 上限", 413)

    root = _repo_root(p)
    if not os.path.isdir(root):
        return _err("项目目录不存在", 410)

    tmp_dir = os.path.join(root, ".prototype-tmp")
    old_dir = os.path.join(root, ".prototype-old")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    shutil.rmtree(old_dir, ignore_errors=True)
    try:
        os.makedirs(tmp_dir)
        _safe_unzip(data, tmp_dir)
    except ValueError as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return _err(str(e), 400)

    # 智能下钻：根顶层无 html 时进入唯一子目录一层（如 dist/index.html 打包形态）
    content_dir = _descend_unique_child(tmp_dir)
    if not _top_has_html(content_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return _err("压缩包内未找到 HTML 页面（根目录与唯一一级子目录顶层均无 html）", 400)

    # 原子替换：旧目录先让位，新目录就位后再清旧（中途失败仍有一份完整）
    proto_dir = os.path.join(root, "prototype")
    if os.path.isdir(proto_dir):
        os.rename(proto_dir, old_dir)
    os.rename(content_dir, os.path.join(root, ".prototype-new"))
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.rename(os.path.join(root, ".prototype-new"), proto_dir)
    shutil.rmtree(old_dir, ignore_errors=True)

    p.content_updated_at = utcnow_str()
    p.save()
    return jsonify(code=0, data=_project_public(p)), 200


@bp.get("/<int:pid>/prototype/export")
def export_prototype(pid: int):
    """导出原型 zip（T12.6）：把 prototype/ 打包，供 AI Agent 读源码后改原型。

    权限：任何已登录用户——与 `/proto/` 预览可见性一致（原型对协作方本就可见，
    打包不扩大暴露面）；Bearer 与 session 等价。

    包结构：`{slug}-prototype/prototype/...`（顶层保留一层，解压不污染工作目录），
    过滤 `.DS_Store` / `__MACOSX` / `._*` 垃圾条目与软链。解压后可达 300MB，
    故落临时文件流式返回（不进内存），响应结束即删。
    """
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    root = _repo_root(p)
    proto_dir = os.path.join(root, "prototype")
    if not os.path.isdir(proto_dir):
        return _err("项目还没有原型内容，请先上传原型 zip", 410)

    prefix = f"{p.project_id}-prototype"
    tmp = tempfile.NamedTemporaryFile(prefix="ppp-proto-", suffix=".zip", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirnames, filenames in os.walk(proto_dir):
                dirnames[:] = [d for d in dirnames if d != "__MACOSX" and not d.startswith(".")]
                for fn in filenames:
                    if fn == ".DS_Store" or fn.startswith("._"):
                        continue
                    full = os.path.join(dirpath, fn)
                    if os.path.islink(full) or not os.path.isfile(full):
                        continue
                    rel = os.path.relpath(full, proto_dir)
                    zf.write(full, f"{prefix}/prototype/{rel}")
    except OSError as e:
        _silent_unlink(tmp_path)
        return _err(f"打包失败：{e}", 500)

    resp = send_file(
        tmp_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{prefix}.zip",
    )
    resp.call_on_close(lambda: _silent_unlink(tmp_path))
    return resp


def _silent_unlink(path: str) -> None:
    """删临时文件，失败不抛（响应已发出，不能因清理失败影响调用方）。"""
    try:
        os.unlink(path)
    except OSError:
        pass


@bp.post("/<int:pid>/prd")
def upload_prd(pid: int):
    """上传 PRD markdown（≤5MB）：替换 prd/ 旧文档（唯一一份，保留上传文件名）。仅创建者。"""
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    deny = _require_manager(p)
    if deny:
        return deny

    f = request.files.get("file")
    if f is None:
        return _err("缺少 file 文件", 400)
    filename = os.path.basename(f.filename or "")
    if not filename.lower().endswith(".md"):
        return _err("仅支持 markdown 文档", 400)
    data = f.read(PRD_MAX_BYTES + 1)
    if len(data) > PRD_MAX_BYTES:
        return _err(f"文档超过 {PRD_MAX_BYTES // 1024 // 1024}MB 上限", 413)

    root = _repo_root(p)
    if not os.path.isdir(root):
        return _err("项目目录不存在", 410)
    prd_dir = os.path.join(root, "prd")
    os.makedirs(prd_dir, exist_ok=True)
    # 替换旧文档（prd/ 唯一 markdown 约定，产品方案 §3）
    for old in os.listdir(prd_dir):
        if old.lower().endswith(".md"):
            os.remove(os.path.join(prd_dir, old))
    with open(os.path.join(prd_dir, filename), "wb") as out:
        out.write(data)

    p.content_updated_at = utcnow_str()
    p.save()
    return jsonify(code=0, data=_project_public(p)), 200


@bp.delete("/<int:pid>")
def delete_project(pid: int):
    """删除项目（T8.2；T 增强：创建者或超管）：目录 + 评论（DB）+ 项目记录一并清除。

    硬规则：demo 项目（fixture）不可删；目录删除失败时 DB 记录保留
    （宁可留脏数据也不留无主目录——用户可重试）。
    """
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    deny = _require_admin_or_creator(p)
    if deny:
        return deny
    if p.project_id == "demo":
        return _err("演示项目不可删除", 400)

    delete_project_dirs(p.project_id)
    Comment.delete().where(Comment.project == p.id).execute()
    # T9.1：协作者关系随项目级联删除（权限矩阵 V2 §6 细则）
    ProjectMember.delete().where(ProjectMember.project == p.id).execute()
    p.delete_instance()
    return jsonify(code=0, data={"deleted": True, "project_id": p.project_id}), 200


# ───────────────────────── T9.1 项目协作者（PRD §5.1）─────────────────────────


def _member_public(m: ProjectMember) -> dict:
    """协作者列表项：姓名、邮箱、添加人、添加时间。

    注意：peewee FK 字段属性访问（m.user）返回关联 Model 实例而非
    主键值，用 m.user_id 取 int（peewee 自动生成 <fk>_id 属性）。
    """
    u = User.get_or_none(User.id == m.user_id)
    return {
        "user_id": m.user_id,
        "name": u.name if u else "（已删除用户）",
        "email": u.email if u else "",
        "added_by": _creator_public(m.added_by).get("name", ""),
        "created_at": m.created_at,
    }


@bp.get("/<int:pid>/members")
def list_members(pid: int):
    """协作者列表（T9.1）：管理者（创建者/协作者）可看。

    权限矩阵 V2 §6.1：GET 放宽到管理者（协作者需要知道同项目还有谁），
    增删保持创建者/超管。
    """
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    deny = _require_manager(p)
    if deny:
        return deny
    rows = ProjectMember.select().where(ProjectMember.project == p.id).order_by(ProjectMember.id)
    return jsonify(code=0, data=[_member_public(m) for m in rows]), 200


@bp.post("/<int:pid>/members")
def add_member(pid: int):
    """添加协作者（T9.1）：仅创建者或超管；入参 {email}，从既有用户表匹配。

    重复添加 → 400 提示「已是协作者」；用户表不存在该邮箱 → 404；
    创建者本人不可添加（天然管理者）。
    """
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    deny = _require_admin_or_creator(p)
    if deny:
        return deny

    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip()
    if not email:
        return _err("email 必填", 400)
    u = User.get_or_none(User.email == email)
    if not u:
        return _err(f"用户表中不存在 {email}（先由超管在用户管理中添加）", 404)
    if u.id == p.creator_id:
        return _err("创建者本人天然拥有全部权限，无需添加", 400)
    if u.disabled:
        return _err("该账号已停用，请先启用", 400)
    if _is_member(p.id, u.id):
        return _err(f"{u.name} 已是本项目协作者", 400)

    m = ProjectMember.create(project=p.id, user=u.id, added_by=session.get("uid"))
    return jsonify(code=0, data=_member_public(m)), 200


@bp.delete("/<int:pid>/members/<int:uid>")
def remove_member(pid: int, uid: int):
    """移除协作者（T9.1）：仅创建者或超管。移除后该用户下一次写操作即 403。"""
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return _err("项目不存在", 404)
    deny = _require_admin_or_creator(p)
    if deny:
        return deny
    m = ProjectMember.get_or_none(ProjectMember.project == p.id, ProjectMember.user == uid)
    if not m:
        return _err("该用户不是本项目协作者", 404)
    m.delete_instance()
    return jsonify(code=0, data={"removed": True, "user_id": uid}), 200


# ───────────────────────── T2.4 查看器数据 ─────────────────────────

SAFE_FILE = re.compile(r"^[\w\-./\u4e00-\u9fff ]+$")


def _repo_root(p: Project) -> str:
    """项目根目录（T8.1：demo 走 fixture，其余走 /data/projects）。"""
    return project_root(p.project_id)


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
        return _err("项目目录不存在（可能已被删除），请重新上传内容", 410)

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
        return _err("项目目录不存在（可能已被删除），请重新上传内容", 410)

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
