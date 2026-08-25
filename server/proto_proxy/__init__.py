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

# T 增强：沙箱无 allow-same-origin 时（不透明 origin）访问 localStorage/sessionStorage
# 抛 SecurityError。注入内存版垫片（仅当原取用真的会抛错时替换），让使用 storage 的
# 原型不崩，同时保留隔绝宿主 cookie / storage 的隔离设计。纯 ASCII，避免改变文档编码。
_STORAGE_SHIM_JS = (
    "(function(){"
    "function mk(){var d=Object.create(null),keys=[];"
    "return{"
    "getItem:function(k){return k in d?d[k]:null},"
    "setItem:function(k,v){var key=String(k);if(!(key in d))keys.push(key);d[key]=String(v)},"
    "removeItem:function(k){var key=String(k);if(key in d){delete d[key];keys=keys.filter(function(x){return x!==key})}},"
    "clear:function(){d=Object.create(null);keys=[]},"
    "key:function(i){return keys[i]||null},"
    "get length(){return keys.length}"
    "}}"
    "function install(name){var store=mk();"
    "Object.defineProperty(window,name,{get:function(){return store},configurable:true})}"
    "function probe(name){try{void window[name].getItem('__ppp_probe__')}catch(e){return true}return false}"
    "if(probe('localStorage'))install('localStorage');"
    "if(probe('sessionStorage'))install('sessionStorage');"
    "})();"
)
STORAGE_SHIM_TAG = "<script>" + _STORAGE_SHIM_JS + "</script>"

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
    1) 文档最前注入 localStorage/sessionStorage 内存垫片（早于原型脚本，沙箱里读
       storage 不抛 SecurityError；仅在真的会抛错时替换，保留隔离）；
    2) </body> 前注入 bridge.js（无 </body> 时追加末尾）。
    幂等：垫片与 bridge.js 各只注入一次。
    """
    if STORAGE_SHIM_TAG not in html:
        html = _inject_early(html, STORAGE_SHIM_TAG)
    if INJECT_TAG not in html:
        if "</body>" in html:
            html = html.replace("</body>", INJECT_TAG + "</body>", 1)
        else:
            html = html + INJECT_TAG
    return html


def _inject_early(html: str, tag: str) -> str:
    """把 tag 插到 <head> 之后（早于原型脚本）；无 <head> 则插到 <body 前；再无则文档最前。"""
    m = re.search(r"<head[^>]*>", html, re.I)
    if m:
        pos = m.end()
        return html[:pos] + tag + html[pos:]
    m = re.search(r"<body[^>]*>", html, re.I)
    if m:
        pos = m.start()
        return html[:pos] + tag + html[pos:]
    return tag + html


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
