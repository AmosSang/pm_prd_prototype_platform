"""原型代理：读 /data/repos/{project}/prototype/{path}，HTML 注入 bridge.js。

硬规则（AGENTS.md §3）：
1. 注入只发生在 HTTP 响应中，严禁修改磁盘上仓库文件
2. 路径校验防目录穿越
"""
import os
import re

from flask import Blueprint, Response, abort

from server.config import DATA_DIR, DEMO_REPO_DIR, PLATFORM_DIR, PORT

bp = Blueprint("proto_proxy", __name__)

REPOS_DIR = os.path.join(DATA_DIR, "repos")

SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
PROJECT_ID = re.compile(r"^[a-z0-9-]{1,32}$")

INJECT_TAG = '<script src="/bridge.js"></script>'

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".mjs": "application/javascript; charset=utf-8",
}


def _repo_root(project_id: str) -> str:
    """项目仓库根目录：demo 项目指向 fixture，其余走 /data/repos。"""
    if project_id == "demo":
        return os.path.realpath(DEMO_REPO_DIR)
    return os.path.realpath(os.path.join(REPOS_DIR, project_id))


def _resolve(project_id: str, rel_path: str) -> str:
    """校验并解析原型文件绝对路径，防目录穿越。"""
    if not PROJECT_ID.fullmatch(project_id):
        abort(404)
    parts = rel_path.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        abort(404)
    # T1.1：仅允许 prototype/ 子树
    if parts[0] != "prototype":
        abort(404)
    base = _repo_root(project_id)
    full = os.path.realpath(os.path.join(base, *parts))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        abort(404)
    return full


def inject_bridge(html: str) -> str:
    """在 </body> 前注入 bridge.js；无 </body> 时追加到末尾（遗留决策点 3 的兜底）。"""
    if INJECT_TAG in html:
        return html
    if "</body>" in html:
        return html.replace("</body>", INJECT_TAG + "</body>", 1)
    return html + INJECT_TAG


@bp.get("/bridge.js")
def bridge_js() -> Response:
    path = os.path.join(PLATFORM_DIR, "bridge", "bridge.js")
    with open(path, encoding="utf-8") as f:
        return Response(f.read(), mimetype="application/javascript; charset=utf-8")


@bp.get("/vendor/<filename>")
def vendor_file(filename: str) -> Response:
    """平台自托管的第三方库（modern-screenshot 等），供 bridge 在 iframe 内加载。

    注意：sandbox 不透明 origin 下的 ES Module 动态 import 属跨域 fetch
    （Origin: null），必须返回 CORS 头，否则浏览器静默拒绝加载。
    """
    if not re.fullmatch(r"[A-Za-z0-9._-]+", filename) or ".." in filename:
        abort(404)
    path = os.path.join(PLATFORM_DIR, "bridge", "vendor", filename)
    if not os.path.isfile(path):
        abort(404)
    ctype = CONTENT_TYPES.get(os.path.splitext(filename)[1].lower())
    if not ctype:
        abort(404)
    with open(path, "rb") as f:
        resp = Response(f.read(), mimetype=ctype)
        # 模块内容为平台内置可信库，放行 null origin（沙箱 iframe）
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


@bp.get("/proto/<project_id>/<path:rel_path>")
def proto_file(project_id: str, rel_path: str) -> Response:
    full = _resolve(project_id, rel_path)
    ext = os.path.splitext(full)[1].lower()

    if ext == ".html":
        with open(full, encoding="utf-8") as f:
            html = f.read()
        return Response(inject_bridge(html), mimetype="text/html; charset=utf-8")

    ctype = CONTENT_TYPES.get(ext)
    if not ctype:
        abort(404)
    with open(full, "rb") as f:
        return Response(f.read(), mimetype=ctype)


def proto_origin(request_host: str) -> str:
    """推导原型 origin（宿主页面经 Nginx 时为同 host；开发期为 :8081）。"""
    host = request_host.split(":")[0]
    return f"http://{host}:{PORT}"
