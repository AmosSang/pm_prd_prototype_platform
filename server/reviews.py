"""评论系统（T4.1 起）。

T4.1：评论 payload（DOM 定位字段组）schema 校验——契约见《产品方案-V1.md》
§3.3 与《一期技术实现方案-V1.md》§2.3。
T4.2：POST /api/projects/{pid}/comments 提交链路——校验 + comment_id 生成
（c-YYYYMMDD-NNN）+ doc 锚点匹配 + DB 落库（展示缓存）+ reviews/ 落仓
（T8.1 去 Git 本地化：直接写项目目录，无队列、无 commit/push）。

字段口径（bridge.js collectPayload 同源约定）：
  target_type        dom（原型元素）/ page（页面根）/ doc_block（PRD 块级元素）
  prototype_page     相对 prototype/ 的路径（如 pages/login.html）；doc_block 可空
  anchor_id          目标自身 data-pa，未命中为空串（合法）
  nearest_anchor_id  最近 [data-pa] 祖先，无则空串（合法）
  css_path           锚点祖先用属性选择器短路的结构链；doc_block 可空
  outer_html         目标 outerHTML + ≤2 层祖先开/闭标签，>4KB 截断
  text_excerpt       文本 200 字截断（表单控件取 value/placeholder）
  interaction_state  {modal_open, viewport, scroll_y, route}

落仓文件 reviews/comments/{comment_id}.json 为事实源（AGENTS.md §3.5），
DB comments 表是展示缓存。
"""
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import zipfile

import peewee
from flask import Blueprint, jsonify, request, send_file, session

from server.models import Comment, Project, utcnow_str
from server.projects import _list_md_files, _repo_root, _require_creator
from server.reconcile import extract_prd_anchors
from server.shots import SHOTS_DIR

bp = Blueprint("reviews", __name__)

# target_type 三类评论宿主（产品方案 §3.3）
TARGET_TYPES = ("dom", "page", "doc_block")
PRIORITIES = ("P1", "P2", "P3")
SCOPES = ("prototype", "doc", "both")

# bridge 侧 outer_html 截断 4096 + 尾部省略标记；schema 侧留少量余量
OUTER_HTML_MAX = 4100
# bridge 侧 text_excerpt 截断 200 + 省略号
TEXT_EXCERPT_MAX = 300
CONTENT_MAX = 2000

# interaction_state 必填子字段与类型（bool 是 int 子类，须显式排除）
_INTERACTION_FIELDS = {"modal_open": bool, "viewport": str, "scroll_y": int, "route": str}

_TYPE_NAMES = {bool: "布尔值", str: "字符串", int: "整数"}

_VIEWPORT_RE = re.compile(r"^\d+x\d+$")

_STR_FIELDS = (
    "prototype_page",
    "anchor_id",
    "nearest_anchor_id",
    "css_path",
    "outer_html",
    "text_excerpt",
)

SHOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_comment_payload(payload: object) -> list[str]:
    """校验评论 DOM 定位 payload，返回错误列表（空列表 = 合法）。

    契约漂移由 tests/test_reviews.py 固定用例兜底（make check 必红）。
    """
    if not isinstance(payload, dict):
        return ["payload 必须是 JSON 对象"]

    errors: list[str] = []

    target_type = payload.get("target_type")
    if target_type not in TARGET_TYPES:
        errors.append(f"target_type 必须是 {TARGET_TYPES} 之一，当前：{target_type!r}")

    for field in _STR_FIELDS:
        if field not in payload:
            errors.append(f"缺少必填字段 {field}")
        elif not isinstance(payload[field], str):
            errors.append(f"{field} 必须是字符串")

    # dom/page 类评论必须落在原型侧；doc_block（文档评论）无原型定位，允许为空
    if target_type in ("dom", "page"):
        if isinstance(payload.get("prototype_page"), str) and not payload["prototype_page"]:
            errors.append("target_type 为 dom/page 时 prototype_page 不能为空")
        if isinstance(payload.get("css_path"), str) and not payload["css_path"]:
            errors.append("target_type 为 dom/page 时 css_path 不能为空")

    outer_html = payload.get("outer_html")
    if isinstance(outer_html, str) and len(outer_html) > OUTER_HTML_MAX:
        errors.append(f"outer_html 超长（>{OUTER_HTML_MAX} 字符，bridge 侧应截断）")
    text_excerpt = payload.get("text_excerpt")
    if isinstance(text_excerpt, str) and len(text_excerpt) > TEXT_EXCERPT_MAX:
        errors.append(f"text_excerpt 超长（>{TEXT_EXCERPT_MAX} 字符）")

    ist = payload.get("interaction_state")
    if not isinstance(ist, dict):
        errors.append("interaction_state 必须是对象")
    else:
        for field, typ in _INTERACTION_FIELDS.items():
            if field not in ist:
                errors.append(f"interaction_state 缺少 {field}")
                continue
            value = ist[field]
            if typ is int and isinstance(value, bool):
                errors.append(f"interaction_state.{field} 必须是整数（bool 不算）")
            elif not isinstance(value, typ):
                errors.append(
                    f"interaction_state.{field} 必须是{_TYPE_NAMES[typ]}，"
                    f"当前：{type(value).__name__}"
                )
        viewport = ist.get("viewport")
        if isinstance(viewport, str) and not _VIEWPORT_RE.match(viewport):
            errors.append("interaction_state.viewport 格式应为 宽x高（如 1440x900）")

    return errors


# ───────────────────────── 评论提交（T4.2）─────────────────────────

# _list_md_files / _repo_root 从 projects 导入：文档发现规则（prd/ 优先 +
# 根目录 *.md 兼容）与仓库根解析保持单一事实源，避免两处口径漂移


def _read_file(root: str, rel: str) -> str:
    full = os.path.realpath(os.path.join(root, *rel.split("/")))
    with open(full, encoding="utf-8") as f:
        return f.read()


def _anchor_excerpt(text: str, line_no: int, limit: int = 200) -> str:
    """锚点注释所在块的文本摘录（产品方案 §3.3 doc_excerpt 口径）。

    行尾注释（`- 账号输入 <!-- pa: x -->：支持手机号`）取本行剔注释后文本
    （注释及前导空白一并剔除，避免「账号输入 ：支持」的残缺空格）；
    独立注释行（`<!-- pa: x -->`）锚点归属下一个内容块，取下一非空行。
    markdown 前缀标记（#/列表符）剔除，保持摘录干净。
    """
    lines = text.split("\n")
    line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
    # 注释 + 前导空白一起删（行尾注释场景）；锚点 ID 同 PRD_ANCHOR_RE 口径
    stripped = re.sub(r"\s*<!--\s*pa:\s*[a-z0-9-]+\s*-->", "", line).strip()
    stripped = re.sub(r"^#{1,6}\s*", "", stripped)
    stripped = re.sub(r"^[-*+]\s+", "", stripped).strip()
    if stripped:
        return stripped[:limit]
    for nxt in lines[line_no:]:
        t = nxt.strip()
        if t:
            t = re.sub(r"^#{1,6}\s*", "", t)
            t = re.sub(r"^[-*+]\s+", "", t).strip()
            return t[:limit]
    return ""


def _match_doc_anchor(root: str, anchor_id: str) -> dict | None:
    """在 PRD 锚点清单里查 anchor_id（技术方案 §2.3 平台侧完成）。

    命中返回 {doc_anchor_id, doc_excerpt, doc_path, doc_file}；未命中 None。
    """
    if not anchor_id:
        return None
    docs = _list_md_files(root)
    try:
        anchors = extract_prd_anchors(docs, lambda rel: _read_file(root, rel))
    except OSError:
        return None
    for a in anchors:
        if a["id"] == anchor_id:
            try:
                excerpt = _anchor_excerpt(_read_file(root, a["file"]), a["line"])
            except OSError:
                excerpt = ""
            return {
                "doc_anchor_id": a["id"],
                "doc_excerpt": excerpt,
                "doc_path": a["doc_path"],
                "doc_file": a["file"],
            }
    return None


def _fingerprint(doc_path: str, excerpt: str) -> str:
    """doc_block_fingerprint：标题路径 + 段落文本的 sha1 前 16 位（§2.3）。"""
    return hashlib.sha1(f"{doc_path}|{excerpt}".encode("utf-8")).hexdigest()[:16]


def _next_comment_id(root: str) -> str:
    """comment_id 生成：c-YYYYMMDD-NNN（当日序号，全局唯一）。

    已占用集合 = DB 当日全部 cid（含软删行——UNIQUE 约束不豁免）+ 本项目
    仓库已存在的同名文件（外部写入可能，如 skill 手工建过）。从 1 起找
    第一个空位——不能用 count+1：cid 有空洞时（删行/软删/外部写入）会
    生成已占用的高位号（实测：004-007 与 008-010 并存，count=7 → 008 撞）。
    """
    prefix = f"c-{dt.date.today().strftime('%Y%m%d')}-"
    used = {
        row[0]
        for row in Comment.select(Comment.comment_id)
        .where(Comment.comment_id.startswith(prefix))
        .tuples()
    }
    n = 0
    while True:
        n += 1
        cid = f"{prefix}{n:03d}"
        if cid not in used and not os.path.exists(
            os.path.join(root, "reviews", "comments", f"{cid}.json")
        ):
            return cid


def _comment_file(root: str, cid: str) -> str:
    """评论 JSON 文件路径（reviews/comments/{cid}.json，事实源 AGENTS.md §3.5）。"""
    return os.path.join(root, "reviews", "comments", f"{cid}.json")


def _write_comment_file(root: str, cid: str, cj: dict) -> None:
    """T8.1 去 Git 本地化：评论 JSON 直接写项目目录（创建/编辑/状态流转共用）。"""
    path = _comment_file(root, cid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cj, f, ensure_ascii=False, indent=2)


def _remove_comment_files(root: str, cid: str) -> None:
    """删除评论 JSON 与关联截图（T8.1：删除直接落文件系统，无 git rm 队列）。"""
    for rel in (f"reviews/comments/{cid}.json", f"reviews/shots/{cid}.png"):
        path = os.path.join(root, *rel.split("/"))
        if os.path.isfile(path):
            os.remove(path)


def _build_comment_json(
    cid: str,
    author_name: str,
    payload: dict,
    content: str,
    priority: str,
    scope: str,
    doc: dict | None,
    shot_rel: str | None,
    rect: dict | None,
) -> dict:
    """组装评论 JSON（产品方案 §3.3 完整字段组）。"""
    cj = {
        # 元信息
        "comment_id": cid,
        "author": author_name,
        "status": "待确认",
        "priority": priority,
        "scope": scope,
        "content": content,
        "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # DOM 定位（bridge 采集原样落）
        "target_type": payload["target_type"],
        "prototype_page": payload.get("prototype_page", ""),
        "anchor_id": payload.get("anchor_id", ""),
        "nearest_anchor_id": payload.get("nearest_anchor_id", ""),
        "css_path": payload.get("css_path", ""),
        "outer_html": payload.get("outer_html", ""),
        "text_excerpt": payload.get("text_excerpt", ""),
        "interaction_state": payload.get("interaction_state", {}),
    }
    # 视觉上下文（截图在提交时生成，T4.2 链路）
    if shot_rel:
        cj["screenshot"] = shot_rel
        cj["highlight_rect"] = rect
    # 文档关联（产品方案派生规则：候选锚点查 PRD 命中段落；未命中标记）
    if payload["target_type"] == "doc_block":
        # 文档评论：任意段落可评（无锚点段落用指纹定位，产品方案 §3.3）。
        # 有锚点 → 服务端复核命中的段落（doc_excerpt/doc_path 以服务端为准）；
        # 无锚点 → 前端现采的 doc_excerpt + doc_path（标题链）直接用于指纹。
        anchor = doc["doc_anchor_id"] if doc else str(payload.get("doc_anchor_id") or "")
        excerpt = doc["doc_excerpt"] if doc else str(payload.get("doc_excerpt") or "")
        doc_path = doc["doc_path"] if doc else str(payload.get("doc_path") or "")
        cj["doc_anchor_id"] = anchor
        cj["doc_excerpt"] = excerpt
        cj["doc_block_fingerprint"] = _fingerprint(doc_path, excerpt)
        if doc:
            cj["doc_file"] = doc["doc_file"]
        elif str(payload.get("doc_file") or ""):
            # 无锚点段落：前端携带当前文档路径（定位/文档角标匹配用）
            cj["doc_file"] = str(payload["doc_file"])
    elif doc:
        # DOM/页面评论：用 anchor_id（或 nearest）查 PRD 锚点表命中段落
        cj["doc_anchor_id"] = doc["doc_anchor_id"]
        cj["doc_excerpt"] = doc["doc_excerpt"]
        cj["doc_file"] = doc["doc_file"]
    else:
        # 派生规则 1：评论落在非锚点区域 → 标记「无 PRD 锚点关联」
        cj["doc_anchor_id"] = ""
        cj["doc_note"] = "无 PRD 锚点关联"
    return cj


def _validate_rect(raw: object) -> dict | None:
    """highlight_rect 校验：{x,y,w,h} 非负整数。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("highlight_rect 必须是对象")
    rect = {}
    for k in ("x", "y", "w", "h"):
        v = raw.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError("highlight_rect 须含 x/y/w/h 非负整数")
        rect[k] = v
    return rect


@bp.post("/api/projects/<int:pid>/comments")
def create_comment(pid: int):
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return jsonify(code=404, msg="项目不存在"), 404
    if not p.commentable:
        return jsonify(code=400, msg="项目已关闭评论，请管理员开启后再试"), 400

    data = request.get_json(silent=True) or {}
    payload = data.get("payload")
    errors = validate_comment_payload(payload)
    if errors:
        return jsonify(code=400, msg="；".join(errors)), 400

    content = str(data.get("content") or "").strip()
    if not content:
        return jsonify(code=400, msg="评论内容不能为空"), 400
    if len(content) > CONTENT_MAX:
        return jsonify(code=400, msg=f"评论内容超过 {CONTENT_MAX} 字"), 400

    priority = str(data.get("priority") or "P2")
    if priority not in PRIORITIES:
        return jsonify(code=400, msg=f"priority 必须是 {PRIORITIES} 之一"), 400
    scope = str(data.get("scope") or "")
    if scope not in SCOPES:
        return jsonify(code=400, msg=f"scope 必须是 {SCOPES} 之一"), 400

    try:
        rect = _validate_rect(data.get("highlight_rect"))
    except ValueError as e:
        return jsonify(code=400, msg=str(e)), 400

    shot_id = str(data.get("shot_id") or "")
    shot_src = None
    if shot_id:
        if not SHOT_ID_RE.fullmatch(shot_id):
            return jsonify(code=400, msg="非法 shot_id"), 400
        shot_src = os.path.join(SHOTS_DIR, p.project_id, f"{shot_id}.png")
        if not os.path.isfile(shot_src):
            return jsonify(code=400, msg="截图不存在或已过期，请重新提交"), 400

    root = _repo_root(p)
    if not os.path.isdir(root):
        return jsonify(code=410, msg="项目目录不存在（可能已被删除），请联系创建者"), 410

    # doc 匹配：DOM/页面评论用候选锚点查 PRD；doc_block 用前端携带的锚点复核
    if payload["target_type"] == "doc_block":
        doc = _match_doc_anchor(root, str(payload.get("doc_anchor_id") or ""))
    else:
        candidate = payload.get("anchor_id") or payload.get("nearest_anchor_id") or ""
        doc = _match_doc_anchor(root, str(candidate))

    # comment_id → DB 先落（UNIQUE 兜底重试）→ 文件写盘。
    # 并发提交（多 worker E2E / 多用户）下 count 读到相同值会生成重复 cid，
    # UNIQUE 约束拦截后重算顺延（最多 5 次；极端并发下仍失败返回 500）
    cj = None
    row = None
    for _ in range(5):
        cid = _next_comment_id(root)
        shot_rel = f"shots/{cid}.png" if shot_src else None
        cj = _build_comment_json(
            cid, session["name"], payload, content, priority, scope, doc, shot_rel, rect
        )
        try:
            row = Comment.create(
                comment_id=cid,
                project=p.id,
                author_email=session["email"],
                author_name=session["name"],
                status=cj["status"],
                priority=priority,
                scope=scope,
                target_type=cj["target_type"],
                prototype_page=cj["prototype_page"] or None,
                anchor_id=cj["anchor_id"] or None,
                payload_json=json.dumps(cj, ensure_ascii=False),
                created_at=utcnow_str(),
                updated_at=utcnow_str(),
            )
            break
        except peewee.IntegrityError:
            continue
    if row is None or cj is None:
        return jsonify(code=500, msg="评论 ID 生成冲突，请重试"), 500
    cid = cj["comment_id"]

    # T8.1 去 Git 本地化：评论直接写项目目录（无队列、无 commit/push）。
    # 截图从临时目录复制到项目 reviews/shots/（导出包天然同构）。
    if shot_src:
        shots_dir = os.path.join(root, "reviews", "shots")
        os.makedirs(shots_dir, exist_ok=True)
        shutil.copyfile(shot_src, os.path.join(shots_dir, f"{cid}.png"))
    _write_comment_file(root, cid, cj)

    return jsonify(code=0, data=cj), 200


# ───────────────────────── 评论列表 / 编辑 / 删除 / 批量状态（T4.4）──────────

# 状态四态（产品方案 §3.4）：待确认 → 已确认待修改 → 已修改；忽略为旁路。
# 可编辑/可删除态：待确认、已确认待修改（已修改/忽略只读，返工走 rework）
EDITABLE_STATUSES = ("待确认", "已确认待修改")

# 批量动作的合法源状态（batch-status 状态机；T8.4 收口为仅创建者 + 状态闭环）
BATCH_ACTIONS = {
    "confirm": {"from": ("待确认",), "to": "已确认待修改"},
    "ignore": {"from": ("待确认", "已确认待修改"), "to": "忽略"},
    "mark_done": {"from": ("已确认待修改",), "to": "已修改"},
    "rework": {"from": ("已修改",), "to": "已确认待修改"},
}


def _comment_public(c: Comment) -> dict:
    """列表条目：筛选列 + payload 全量（抽屉分组/角标/详情用）。"""
    cj = json.loads(c.payload_json)
    return {
        "comment_id": c.comment_id,
        "author_name": c.author_name,
        "author_email": c.author_email,
        "status": c.status,
        "priority": c.priority,
        "scope": c.scope,
        "target_type": c.target_type,
        "prototype_page": c.prototype_page or "",
        "anchor_id": c.anchor_id or "",
        "created_at": c.created_at,
        "payload": cj,
    }


@bp.get("/api/projects/<int:pid>/comments")
def list_comments(pid: int):
    """评论列表（T4.4 抽屉数据源）。

    筛选参数：status（四态）、target_type（dom/page/doc_block）。
    前端抽屉也做本地筛选（数据量小即时切换）；服务端参数供 API 消费方。
    排序：创建时间正序（新评论在组内靠后，符合阅读直觉）。
    """
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return jsonify(code=404, msg="项目不存在"), 404

    q = Comment.select().where(Comment.project == p.id, Comment.deleted == False)  # noqa: E712
    status = request.args.get("status", "").strip()
    if status:
        q = q.where(Comment.status == status)
    tt = request.args.get("target_type", "").strip()
    if tt:
        q = q.where(Comment.target_type == tt)
    rows = q.order_by(Comment.id.asc())
    return jsonify(code=0, data=[_comment_public(c) for c in rows]), 200


def _get_live_comment(cid: str) -> Comment | None:
    """按 comment_id 查未删除评论；不存在/已删返回 None。"""
    return Comment.get_or_none(Comment.comment_id == cid, Comment.deleted == False)  # noqa: E712


@bp.patch("/api/comments/<cid>")
def edit_comment(cid: str):
    """作者编辑（产品方案 §4.5 编辑规则）：仅作者 + 待确认/已确认待修改态。

    可改 content / priority / scope；DB 先更新，T8.1 去 Git 本地化后直接
    改写项目目录内评论 JSON（无队列）。项目关闭可评论时拒绝（写 reviews/
    的操作都在拦截范围——开关的目的是消除双写窗口）。
    """
    c = _get_live_comment(cid)
    if not c:
        return jsonify(code=404, msg="评论不存在"), 404
    if not c.project.commentable:
        return jsonify(code=400, msg="项目已关闭评论，无法编辑"), 400
    # T8.4 权限收口（§6）：创建者编辑任意评论不受状态限制；作者限自己的
    # 评论且仅待确认/已确认待修改态。
    is_creator = session.get("uid") == c.project.creator_id
    if not is_creator:
        if c.author_email != session.get("email"):
            return jsonify(code=403, msg="仅评论作者或项目创建者可编辑"), 403
        if c.status not in EDITABLE_STATUSES:
            return jsonify(code=400, msg=f"「{c.status}」状态的评论不可编辑"), 400

    data = request.get_json(silent=True) or {}
    fields: dict = {}
    if "content" in data:
        content = str(data.get("content") or "").strip()
        if not content:
            return jsonify(code=400, msg="评论内容不能为空"), 400
        if len(content) > CONTENT_MAX:
            return jsonify(code=400, msg=f"评论内容超过 {CONTENT_MAX} 字"), 400
        fields["content"] = content
    if "priority" in data:
        priority = str(data.get("priority") or "")
        if priority not in PRIORITIES:
            return jsonify(code=400, msg=f"priority 必须是 {PRIORITIES} 之一"), 400
        fields["priority"] = priority
    if "scope" in data:
        scope = str(data.get("scope") or "")
        if scope not in SCOPES:
            return jsonify(code=400, msg=f"scope 必须是 {SCOPES} 之一"), 400
        fields["scope"] = scope
    if not fields:
        return jsonify(code=400, msg="没有可更新字段（content/priority/scope）"), 400

    c.payload_json = json.dumps({**json.loads(c.payload_json), **fields}, ensure_ascii=False)
    if "priority" in fields:
        c.priority = fields["priority"]
    if "scope" in fields:
        c.scope = fields["scope"]
    c.updated_at = utcnow_str()
    c.save()

    # T8.1 去 Git 本地化：DB 更新后直接改写项目目录内评论 JSON（事实源）
    root = _repo_root(c.project)
    if os.path.isdir(root):
        _write_comment_file(root, cid, json.loads(c.payload_json))
    return jsonify(code=0, data={"comment_id": cid, "updated": list(fields.keys())}), 200


@bp.delete("/api/comments/<cid>")
def delete_comment(cid: str):
    """作者删除（仅待确认/已确认待修改态）：软删 DB + 删项目目录内文件。
    项目关闭可评论时拒绝（同编辑：写 reviews/ 的操作全部拦截）。"""
    c = _get_live_comment(cid)
    if not c:
        return jsonify(code=404, msg="评论不存在"), 404
    if not c.project.commentable:
        return jsonify(code=400, msg="项目已关闭评论，无法删除"), 400
    # T8.4 权限收口（§6）：创建者删除任意评论不受状态限制；作者限自己的
    # 评论且仅待确认/已确认待修改态。
    is_creator = session.get("uid") == c.project.creator_id
    if not is_creator:
        if c.author_email != session.get("email"):
            return jsonify(code=403, msg="仅评论作者或项目创建者可删除"), 403
        if c.status not in EDITABLE_STATUSES:
            return jsonify(code=400, msg=f"「{c.status}」状态的评论不可删除"), 400

    c.deleted = True
    c.updated_at = utcnow_str()
    c.save()

    # T8.1 去 Git 本地化：软删 DB 后直接删项目目录内评论 JSON 与截图
    root = _repo_root(c.project)
    if os.path.isdir(root):
        _remove_comment_files(root, cid)
    return jsonify(code=0, data={"comment_id": cid, "deleted": True}), 200


@bp.post("/api/comments/batch-status")
def batch_status():
    """批量状态流转（产品方案 §4.5：PM 批量确认/忽略；T8.4 收口为仅创建者）。

    状态机：confirm（待确认 → 已确认待修改）、ignore（待确认/已确认待修改
    → 忽略）、mark_done（已确认待修改 → 已修改）、rework（已修改 → 已确认
    待修改，返工闭环）。逐条：合法则 DB 更新 + 直接改写项目目录内评论 JSON
    （T8.1 去 Git 本地化），非法（状态不符/不存在/跨项目/非创建者）跳过并报告。
    """
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "")
    cids = data.get("cids")
    if action not in BATCH_ACTIONS:
        return jsonify(code=400, msg=f"action 必须是 {tuple(BATCH_ACTIONS)} 之一"), 400
    if not isinstance(cids, list) or not cids or len(cids) > 100:
        return jsonify(code=400, msg="cids 须为非空数组（≤100 条）"), 400

    rule = BATCH_ACTIONS[action]
    updated: list[str] = []
    skipped: list[dict] = []
    for cid in cids[:100]:
        c = _get_live_comment(str(cid))
        if not c:
            skipped.append({"comment_id": str(cid), "reason": "不存在"})
            continue
        # T8.4 权限收口（§6）：状态流转仅创建者可操作；且批量条目须属创建者项目
        if session.get("uid") != c.project.creator_id:
            skipped.append({"comment_id": c.comment_id, "reason": "仅项目创建者可操作状态"})
            continue
        if not c.project.commentable:
            skipped.append({"comment_id": c.comment_id, "reason": "项目已关闭评论"})
            continue
        if c.status not in rule["from"]:
            skipped.append({"comment_id": c.comment_id, "reason": f"「{c.status}」状态不可{action}"})
            continue
        c.status = rule["to"]
        c.updated_at = utcnow_str()
        # payload 同步（评论 JSON 全量与列冗余一致）+ 直写文件（事实源）
        cj = {**json.loads(c.payload_json), "status": rule["to"]}
        c.payload_json = json.dumps(cj, ensure_ascii=False)
        c.save()
        # T8.1 去 Git 本地化：直接改写项目目录内评论 JSON（无队列）
        root = _repo_root(c.project)
        if os.path.isdir(root):
            _write_comment_file(root, c.comment_id, cj)
        updated.append(c.comment_id)

    return jsonify(code=0, data={
        "action": action, "to": rule["to"],
        "updated": updated, "skipped": skipped,
    }), 200


# ───────────────────────── 评论导出（T8.3）─────────────────────────

EXPORT_SCOPES = ("all", "confirmed")


@bp.get("/api/projects/<int:pid>/comments/export")
def export_comments(pid: int):
    """导出评论 zip（T8.3，产品方案 §4.7 / 技术方案 §2.8）：仅创建者。

    scope=all → 未删除评论全量（四态）；confirmed → 「已确认待修改」
    （交付修改的标准范围，产品方案 §3.4）。评论 JSON 与截图取自项目目录
    reviews/（事实源），导出包与之同构（AGENTS.md 硬规则 5）：
    {project_id}-comments-{yyyymmdd}-{HHmm}/manifest.json + comments/ + shots/。
    无评论时返回空包（manifest total=0）。
    """
    p = Project.get_or_none(Project.id == pid)
    if not p:
        return jsonify(code=404, msg="项目不存在"), 404
    deny = _require_creator(p)
    if deny:
        return deny

    scope = (request.args.get("scope") or "").strip()
    if scope not in EXPORT_SCOPES:
        return jsonify(code=400, msg="scope 必须是 all 或 confirmed"), 400

    q = Comment.select().where(Comment.project == p.id, Comment.deleted == False)  # noqa: E712
    if scope == "confirmed":
        q = q.where(Comment.status == "已确认待修改")
    rows = list(q.order_by(Comment.id.asc()))

    root = _repo_root(p)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    prefix = f"{p.project_id}-comments-{stamp}"

    manifest_comments: list[dict] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for c in rows:
            # 评论 JSON 以项目目录文件为准（事实源）；文件缺失时兜底 DB 缓存
            cj: dict | None = None
            fpath = os.path.join(root, "reviews", "comments", f"{c.comment_id}.json")
            if os.path.isfile(fpath):
                try:
                    with open(fpath, encoding="utf-8") as f:
                        cj = json.load(f)
                except (OSError, ValueError):
                    cj = None
            if cj is None:
                cj = json.loads(c.payload_json)
            zf.writestr(
                f"{prefix}/comments/{c.comment_id}.json",
                json.dumps(cj, ensure_ascii=False, indent=2),
            )
            # 截图：仅被导出评论引用且实际存在的（has_shot = 实际入包）
            has_shot = False
            if cj.get("screenshot"):
                shot_path = os.path.join(root, "reviews", "shots", f"{c.comment_id}.png")
                if os.path.isfile(shot_path):
                    with open(shot_path, "rb") as sf:
                        zf.writestr(f"{prefix}/shots/{c.comment_id}.png", sf.read())
                    has_shot = True
            manifest_comments.append({
                "comment_id": c.comment_id,
                "status": c.status,
                "priority": c.priority,
                "scope": c.scope,
                "has_shot": has_shot,
            })

        manifest = {
            "exported_at": utcnow_str(),
            "project": {"id": p.project_id, "name": p.name},
            "scope": scope,
            "total": len(rows),
            "comments": manifest_comments,
        }
        zf.writestr(
            f"{prefix}/manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{prefix}.zip",
    )


@bp.get("/api/comments/<cid>/shot")
def comment_shot(cid: str):
    """评论截图（T8.5 抽屉缩略图用）：项目目录 reviews/shots/{cid}.png。

    评论列表接口已返回 payload.screenshot（"shots/{cid}.png"），但项目目录
    截图无静态代理——渲染缩略图需本接口按 comment_id 定位文件。任何已登录
    用户可查看（浏览评论不受权限限制，§6）。
    """
    c = Comment.get_or_none(Comment.comment_id == cid)
    if not c:
        return jsonify(code=404, msg="评论不存在"), 404
    path = os.path.join(_repo_root(c.project), "reviews", "shots", f"{cid}.png")
    if not os.path.isfile(path):
        return jsonify(code=404, msg="评论无截图"), 404
    return send_file(path, mimetype="image/png")
