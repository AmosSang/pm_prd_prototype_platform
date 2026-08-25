"""原型代理：读 /data/projects/{project}/prototype/{path}，HTML 注入 bridge.js。

T8.1 去 Git 本地化：数据源从 /data/repos（clone 目录）切到
/data/projects（上传解压目录），demo 项目特判 fixture 不变。

硬规则（AGENTS.md §3）：
1. 注入只发生在 HTTP 响应中，严禁修改磁盘上项目文件
2. 路径校验防目录穿越
"""
import os
import re

from flask import Blueprint, Response, abort

from server.config import PLATFORM_DIR, PORT
from server.storage import project_dir

bp = Blueprint("proto_proxy", __name__)

SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
PROJECT_ID = re.compile(r"^[a-z0-9-]{1,32}$")

INJECT_TAG = '<script src="/bridge.js"></script>'

# T 增强：早期自愈护栏（注入 <head>）。原型自身的脚本可能在解析/运行时崩溃、改写
# 文档（document.write 整页重写、body.innerHTML 替换）或整页跳走（location.href），
# 导致 </body> 前注入的 bridge.js 标签被销毁/跳过、bridge 永不运行 → 查看器永久
# 「加载中…」且锚点失效。护栏：先把 URL hash 里的 nonce 记到 window.__PP_NONCE__
# 并写入 sessionStorage（跨页跳转后 bridge 仍能从 sessionStorage 恢复 nonce；allow-
# same-origin 下同源共享），再在 DOMContentLoaded 后轮询补挂 bridge.js 直到就绪。
_BRIDGE_GUARD_JS = (
    "(function(){"
    "var m=/(?:^|#)pp-nonce=([A-Za-z0-9_-]+)/.exec(location.hash);"
    "var n=m?m[1]:(window.__PP_NONCE__||null);"
    "if(n){try{window.__PP_NONCE__=n;window.sessionStorage.setItem('pp_nonce',n)}catch(e){}}"
    "var tries=0;"
    "function ensure(){"
    "try{"
    "if(window.__PP_BRIDGE__)return;"
    "var s=document.querySelector('script[data-pp-bridge]');"
    "if(!s){s=document.createElement('script');s.setAttribute('data-pp-bridge','1');"
    "s.src='/bridge.js';var h=document.head||document.documentElement;"
    "(document.body||h).appendChild(s)}"
    "if(!window.__PP_BRIDGE__&&tries++<40)setTimeout(ensure,250)"
    "}catch(e2){}"
    "}"
    "if(document.readyState==='loading'){"
    "document.addEventListener('DOMContentLoaded',ensure)"
    "}else{ensure()}"
    "})();"
)
BRIDGE_GUARD_TAG = "<script>" + _BRIDGE_GUARD_JS + "</script>"

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
    """项目根目录（T8.1）：demo 走 fixture，其余走 /data/projects。"""
    try:
        return os.path.realpath(project_dir(project_id))
    except ValueError:
        abort(404)


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
    """注入两段：
    1) <head> 后注入早期护栏（记住 nonce + 崩溃后自愈补挂 bridge.js）；
    2) </body> 前注入 bridge.js（无 </body> 时追加末尾）。
    幂等：护栏与 bridge 各只注入一次。
    """
    if BRIDGE_GUARD_TAG not in html:
        html = _inject_into_head(html, BRIDGE_GUARD_TAG)
    if INJECT_TAG not in html:
        if "</body>" in html:
            html = html.replace("</body>", INJECT_TAG + "</body>", 1)
        else:
            html = html + INJECT_TAG
    return html


def _inject_into_head(html: str, tag: str) -> str:
    """把 tag 插到 <head> 之后；无 <head> 则跳过（退化仅靠 </body> 前注入）。"""
    m = re.search(r"<head[^>]*>", html, re.I)
    if not m:
        return html
    pos = m.end()
    return html[:pos] + tag + html[pos:]


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
