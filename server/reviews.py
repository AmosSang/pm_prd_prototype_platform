"""评论系统（T4.1 起）。

T4.1：评论 payload（DOM 定位字段组）schema 校验——契约见《产品方案-V1.md》
§3.3 与《一期技术实现方案-V1.md》§2.3。
T4.2：POST /api/projects/{pid}/comments 提交链路——校验 + comment_id 生成
（c-YYYYMMDD-NNN）+ doc 锚点匹配 + DB 落库（展示缓存）+ reviews/ 落仓
（写 JSON/截图 + git commit/push 同步版，T4.3 升级为串行队列）。

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
import json
import os
import re
import shutil

from flask import Blueprint, jsonify, request, session

from server.gitops import commit_and_push
from server.models import Comment, Project, utcnow_str
from server.projects import _list_md_files, _repo_root
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
    """comment_id 生成：c-YYYYMMDD-NNN（当日序号）。

    序号 = DB 当日已有数 + 1；落仓前检查文件是否已存在（外部写入可能，
    如 skill 手工建过同名文件），冲突顺延。
    """
    prefix = f"c-{dt.date.today().strftime('%Y%m%d')}-"
    n = Comment.select().where(Comment.comment_id.startswith(prefix)).count()
    while True:
        n += 1
        cid = f"{prefix}{n:03d}"
        if not os.path.exists(os.path.join(root, "reviews", "comments", f"{cid}.json")):
            return cid


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
        # 文档评论：前端已带 doc_anchor_id/doc_excerpt，服务端补指纹
        anchor = doc["doc_anchor_id"] if doc else str(payload.get("doc_anchor_id") or "")
        excerpt = doc["doc_excerpt"] if doc else str(payload.get("doc_excerpt") or "")
        cj["doc_anchor_id"] = anchor
        cj["doc_excerpt"] = excerpt
        cj["doc_block_fingerprint"] = _fingerprint(
            doc["doc_path"] if doc else "", excerpt
        )
        if doc:
            cj["doc_file"] = doc["doc_file"]
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
        return jsonify(code=410, msg="本地 clone 不存在（可能已被移动或删除），请重新绑定"), 410

    # doc 匹配：DOM/页面评论用候选锚点查 PRD；doc_block 用前端携带的锚点复核
    if payload["target_type"] == "doc_block":
        doc = _match_doc_anchor(root, str(payload.get("doc_anchor_id") or ""))
    else:
        candidate = payload.get("anchor_id") or payload.get("nearest_anchor_id") or ""
        doc = _match_doc_anchor(root, str(candidate))

    # comment_id → 评论 JSON → DB（先落，git 异常不阻塞评论体验）
    cid = _next_comment_id(root)
    shot_rel = f"shots/{cid}.png" if shot_src else None
    cj = _build_comment_json(
        cid, session["name"], payload, content, priority, scope, doc, shot_rel, rect
    )
    Comment.create(
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

    # 落仓：截图搬移（临时区保留供预览）+ 评论 JSON + commit/push
    if shot_src:
        shots_dir = os.path.join(root, "reviews", "shots")
        os.makedirs(shots_dir, exist_ok=True)
        shutil.copyfile(shot_src, os.path.join(shots_dir, f"{cid}.png"))
    comments_dir = os.path.join(root, "reviews", "comments")
    os.makedirs(comments_dir, exist_ok=True)
    with open(os.path.join(comments_dir, f"{cid}.json"), "w", encoding="utf-8") as f:
        json.dump(cj, f, ensure_ascii=False, indent=2)

    git_error = commit_and_push(
        p.project_id,
        p.encrypted_token,
        p.branch,
        f"comment: {cid} 创建",
        session["name"],
        session["email"],
    )
    if git_error:
        p.sync_error = git_error
        p.save()

    return jsonify(code=0, data={**cj, "git_pushed": git_error is None, "git_error": git_error}), 200
