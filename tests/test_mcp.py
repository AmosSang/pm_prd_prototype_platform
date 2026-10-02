"""T12.3/T12.4 MCP Server 单测 + 集成测试。

单测（mock 回环）：项目匹配规则（slug 精确 > 名称精确 > 名称包含 / 多命中候选 /
未命中提示）、错误映射三段式、出参整形（角色映射 / 评论同构覆盖 / no_prd）、
上传工具（base64 解码预检 / 审计日志 / 覆盖语义出参）。
集成测试（测试客户端直连）：FastMCP 内存 Client → 工具 → 回环 httpx transport
→ Flask test_client（真实路由 + Bearer 认证 + DB/文件全链路）。
"""
import asyncio
import base64 as b64
import io
import json
import os
import sys
import zipfile

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import dom_payload, make_local_project  # noqa: E402
from fastmcp import Client  # noqa: E402

import server.mcp_loopback as loopback  # noqa: E402
import server.mcp_server as mcp_server  # noqa: E402
from server import storage  # noqa: E402
from server.app import create_app  # noqa: E402
from server.mcp_loopback import ApiResult  # noqa: E402
from server.models import Project, User, db, init_tables  # noqa: E402

U_OWNER, U_OTHER = 1, 2


# ───────────────────────── 单测辅助：假回环 ─────────────────────────

def _rows():
    return [
        {"id": 11, "project_id": "login-a1b2c3", "name": "登录页", "creator": {"id": 1, "name": "甲", "email": "a@x.com"},
         "is_creator": True, "is_manager": True, "member_count": 1, "comment_count": 3,
         "commentable": True, "content_updated_at": "2026-09-27T08:00:00.000Z", "created_at": "x"},
        {"id": 12, "project_id": "login2-d4e5f6", "name": "登录页二期", "creator": {"id": 1, "name": "甲", "email": "a@x.com"},
         "is_creator": False, "is_manager": True, "member_count": 0, "comment_count": 0,
         "commentable": True, "content_updated_at": None, "created_at": "x"},
        {"id": 13, "project_id": "order-g7h8i9", "name": "订单管理", "creator": {"id": 2, "name": "乙", "email": "b@x.com"},
         "is_creator": False, "is_manager": False, "member_count": 0, "comment_count": 5,
         "commentable": True, "content_updated_at": None, "created_at": "x"},
    ]


def _fake_loopback(monkeypatch, routes: dict):
    """routes: {(method, path): ApiResult}；记录调用明细到 calls 列表。"""
    calls: list[tuple] = []

    def fake(method, path, bearer, **kw):
        calls.append((method, path, bearer, kw))
        key = (method, path)
        if key not in routes:
            raise AssertionError(f"未预置的回环调用：{method} {path}")
        return routes[key]

    monkeypatch.setattr(mcp_server, "call_api", fake)
    return calls


def _ok(data):
    return ApiResult(200, {"code": 0, "data": data})


class Test项目匹配:
    def test_slug精确优先于名称(self, monkeypatch):
        _fake_loopback(monkeypatch, {("GET", "/api/projects"): _ok(_rows())})
        hit, err = mcp_server._resolve_project("login-a1b2c3", "ppp_t")
        assert err is None and hit["id"] == 11

    def test_名称精确优先于包含(self, monkeypatch):
        _fake_loopback(monkeypatch, {("GET", "/api/projects"): _ok(_rows())})
        hit, err = mcp_server._resolve_project("登录页", "ppp_t")
        assert err is None and hit["id"] == 11

    def test_名称包含单命中(self, monkeypatch):
        _fake_loopback(monkeypatch, {("GET", "/api/projects"): _ok(_rows())})
        hit, err = mcp_server._resolve_project("订单", "ppp_t")
        assert err is None and hit["id"] == 13

    def test_多命中返回候选不猜测(self, monkeypatch):
        calls = _fake_loopback(monkeypatch, {("GET", "/api/projects"): _ok(_rows())})
        hit, err = mcp_server._resolve_project("登录", "ppp_t")
        assert hit is None
        assert err["error_code"] == "ambiguous_project"
        assert len(err["candidates"]) == 2
        assert {c["project_id"] for c in err["candidates"]} == {"login-a1b2c3", "login2-d4e5f6"}
        # 只调了列表接口，没有继续执行任何下游操作
        assert len(calls) == 1

    def test_未命中提示可用项目(self, monkeypatch):
        _fake_loopback(monkeypatch, {("GET", "/api/projects"): _ok(_rows())})
        hit, err = mcp_server._resolve_project("不存在的项目", "ppp_t")
        assert hit is None and err["error_code"] == "project_not_found"
        assert "登录页" in err["hint"]

    def test_空参数bad_request(self, monkeypatch):
        calls = _fake_loopback(monkeypatch, {})
        hit, err = mcp_server._resolve_project("   ", "ppp_t")
        assert hit is None and err["error_code"] == "bad_request"
        assert len(calls) == 0


class Test错误映射:
    def test_401_token失效(self, monkeypatch):
        _fake_loopback(monkeypatch, {
            ("GET", "/api/projects"): ApiResult(401, {"code": 401, "msg": "Token 已撤销"}),
        })
        out = mcp_server.list_projects()
        assert out["error_code"] == "unauthorized"
        assert out["message"] == "Token 已撤销"
        assert "Agent 接入" in out["hint"]

    def test_403_无权限(self, monkeypatch):
        _fake_loopback(monkeypatch, {
            ("GET", "/api/projects"): _ok(_rows()),
            ("GET", "/api/projects/11/overview"): ApiResult(403, {"code": 403, "msg": "仅管理者可上传"}),
        })
        out = mcp_server.get_project_overview("login-a1b2c3")
        assert out["error_code"] == "no_permission"
        assert out["message"] == "仅管理者可上传"

    def test_429_限流(self, monkeypatch):
        _fake_loopback(monkeypatch, {
            ("GET", "/api/projects"): ApiResult(429, {"code": 429, "msg": "请求过于频繁（Token 限流 60 次/分钟），请稍后重试"}),
        })
        out = mcp_server.list_projects()
        assert out["error_code"] == "rate_limited"

    def test_网络失败_平台不可达(self, monkeypatch):
        _fake_loopback(monkeypatch, {("GET", "/api/projects"): ApiResult(0, None, "ConnectError: refused")})
        out = mcp_server.list_projects()
        assert out["error_code"] == "platform_unavailable"
        assert "稍后重试" in out["hint"]


class Test出参整形:
    def test_角色映射与comment_count(self, monkeypatch):
        _fake_loopback(monkeypatch, {("GET", "/api/projects"): _ok(_rows())})
        out = mcp_server.list_projects()
        assert out["count"] == 3
        roles = {p["project_id"]: p["my_role"] for p in out["projects"]}
        assert roles == {"login-a1b2c3": "创建者", "login2-d4e5f6": "协作者", "order-g7h8i9": "参与者"}
        assert out["projects"][0]["comment_count"] == 3

    def test_评论条目同构_状态与作者以DB为准(self, monkeypatch):
        _fake_loopback(monkeypatch, {
            ("GET", "/api/projects"): _ok(_rows()),
            ("GET", "/api/projects/11/comments"): _ok([
                {
                    "comment_id": "c-1", "author_name": "甲（改名后）", "author_email": "a@x.com",
                    "status": "已修改", "target_type": "dom", "prototype_page": "index.html",
                    "anchor_id": "login-account", "created_at": "2026-09-27T08:00:00.000Z",
                    "payload": {"comment_id": "c-1", "author": "甲", "status": "待确认", "content": "改一下",
                                "created_at": "2026-09-27 16:00:00", "anchor_id": "login-account"},
                }
            ]),
        })
        out = mcp_server.get_all_comments("login-a1b2c3")
        assert out["count"] == 1
        item = out["comments"][0]
        assert item["status"] == "已修改"        # DB 列为准（payload 里是旧状态）
        assert item["author"] == "甲（改名后）"   # DB 列为准
        assert item["content"] == "改一下"
        assert item["created_at"] == "2026-09-27 16:00:00"  # 同构：保留文件口径

    def test_非法状态不发起调用(self, monkeypatch):
        calls = _fake_loopback(monkeypatch, {})
        out = mcp_server.get_all_comments("login-a1b2c3", status="乱写")
        assert out["error_code"] == "bad_request"
        assert len(calls) == 0

    def test_无PRD返回no_prd(self, monkeypatch):
        _fake_loopback(monkeypatch, {
            ("GET", "/api/projects"): _ok(_rows()),
            ("GET", "/api/projects/13/overview"): _ok({"project": _rows()[2], "docs": [], "proto_entries": [], "page_map": [], "proto_anchor_index": {}, "reconcile_summary": None}),
        })
        out = mcp_server.get_prd_content("order-g7h8i9")
        assert out["error_code"] == "no_prd"


# ───────────────────── 集成测试：测试客户端直连全链路 ─────────────────────

class _FlaskTestTransport(httpx.BaseTransport):
    """httpx transport → Flask test_client（免起端口跑通 回环→Flask 全链路）。"""

    def __init__(self, flask_client):
        self.client = flask_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = self.client.open(
            path=request.url.raw_path.decode(),
            method=request.method,
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            data=request.read(),
        )
        return httpx.Response(
            resp.status_code,
            headers={k: v for k, v in resp.headers.items() if k.lower() != "content-length"},
            content=resp.data,
        )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """独立临时 DB + 本地项目目录 + 已登录创建者/他人用户。"""
    db.close()
    db.init(str(tmp_path / "test.db"))
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr("server.storage.PROJECTS_DIR", str(projects_dir))

    # 假回环 transport（集成核心：工具→httpx→Flask test_client）
    boot = create_app()
    boot.config["TESTING"] = True
    boot.secret_key = "test-secret"
    transport_client = boot.test_client()
    monkeypatch.setattr(loopback, "transport_factory", lambda: _FlaskTestTransport(transport_client))

    with boot.app_context():
        init_tables()
        User.create(id=U_OWNER, email="pm@corp.com", name="创建者桑")
        User.create(id=U_OTHER, email="other@corp.com", name="路人甲")
        make_local_project(projects_dir, "mcp-proj")
        Project.create(id=11, project_id="mcp-proj", name="MCP集成项目", creator_id=U_OWNER)

    client = boot.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = U_OWNER
        sess["email"] = "pm@corp.com"
        sess["name"] = "创建者桑"
    # 建一条评论（真实链路：文件 + DB 缓存）
    res = client.post("/api/projects/11/comments", json={"payload": dom_payload(), "content": "按钮位置要调整"})
    assert res.status_code == 200, res.get_json()
    # 生成真 Token
    res = client.post("/api/tokens", json={"name": "mcp-e2e"})
    plaintext = res.get_json()["data"]["plaintext"]
    return boot, client, transport_client, plaintext


def _call(name: str, args: dict | None = None):
    async def go():
        async with Client(mcp_server.mcp) as c:
            return await c.call_tool(name, args or {})

    return asyncio.run(go())


def _auth(env):
    """集成测试里模拟 MCP 请求头的 Bearer（patch 掉 headers 取用点）。"""
    return env[3]


class Test集成直连:
    def test_工具清单_八工具(self, env, monkeypatch):
        monkeypatch.setattr(mcp_server, "get_http_headers", lambda **kw: {"authorization": f"Bearer {_auth(env)}"})

        async def go():
            async with Client(mcp_server.mcp) as c:
                return await c.list_tools()

        tools = asyncio.run(go())
        names = sorted(t.name for t in tools)
        assert names == [
            "download_prototype", "get_all_comments", "get_prd_content", "get_project_overview",
            "get_reconcile", "list_projects", "upload_prd", "upload_prototype",
        ]
        # instructions 已配置（概念速览 + 意图映射）
        assert "概念速览" in mcp_server.MCP_INSTRUCTIONS

    def test_全链路_列表与评论与PRD与对账(self, env, monkeypatch):
        monkeypatch.setattr(mcp_server, "get_http_headers", lambda **kw: {"authorization": f"Bearer {_auth(env)}"})

        out = _call("list_projects").data
        assert out["count"] == 1
        assert out["projects"][0]["project_id"] == "mcp-proj"
        assert out["projects"][0]["my_role"] == "创建者"
        assert out["projects"][0]["comment_count"] == 1

        # 名称模糊命中（含「集成」）
        out = _call("get_all_comments", {"project": "集成"}).data
        assert out["count"] == 1
        c = out["comments"][0]
        assert c["content"] == "按钮位置要调整"
        assert c["status"] == "待确认"
        assert c["anchor_id"] == "login-account"
        assert c["author"] == "创建者桑"

        # 状态筛选
        out = _call("get_all_comments", {"project": "集成", "status": "已确认待修改"}).data
        assert out["count"] == 0

        out = _call("get_prd_content", {"project": "mcp-proj"}).data
        assert out["file"] == "prd/需求.md"  # 项目内相对路径（overview.docs 口径）
        assert "登录页" in out["content"]

        out = _call("get_project_overview", {"project": "mcp-proj"}).data
        assert out["docs"] == ["prd/需求.md"]
        assert out["proto_entries"] == ["prototype/index.html"]
        assert out["proto_anchor_index"].get("page-login") == "prototype/index.html"

        out = _call("get_reconcile", {"project": "mcp-proj"}).data
        assert out["summary"]["matched"] >= 1

    def test_多命中候选_集成(self, env, monkeypatch):
        monkeypatch.setattr(mcp_server, "get_http_headers", lambda **kw: {"authorization": f"Bearer {_auth(env)}"})
        boot, _, _, _ = env
        with boot.app_context():
            Project.create(project_id="mcp-proj-2", name="MCP集成项目二期", creator_id=U_OWNER)
        # 名称精确（「MCP集成项目」）：单命中，正常返回
        out = _call("get_project_overview", {"project": "MCP集成项目"}).data
        assert out["project"]["project_id"] == "mcp-proj"
        # 名称包含（「集成」）：多命中 → 候选列表，不猜测
        out = _call("get_project_overview", {"project": "集成"}).data
        assert out["error_code"] == "ambiguous_project"
        assert len(out["candidates"]) == 2

    def test_撤销后调用即unauthorized(self, env, monkeypatch):
        monkeypatch.setattr(mcp_server, "get_http_headers", lambda **kw: {"authorization": f"Bearer {_auth(env)}"})
        boot, client, _, _ = env
        assert _call("list_projects").data["count"] >= 1
        res = client.get("/api/tokens")
        tid = res.get_json()["data"][0]["id"]
        assert client.delete(f"/api/tokens/{tid}").status_code == 200
        out = _call("list_projects").data
        assert out["error_code"] == "unauthorized"

    def test_他人token_权限与网页一致(self, env, monkeypatch):
        """路人甲（非成员）：可读（登录用户），但没有管理者写入路径——T12.3 只读工具
        以 404/200 行为为主，写入权限矩阵由 T12.4 单测覆盖。"""
        boot, client, _, _ = env
        with client.session_transaction() as sess:
            sess["uid"] = U_OTHER
            sess["email"] = "other@corp.com"
            sess["name"] = "路人甲"
        res = client.post("/api/tokens", json={"name": "mcp-other"})
        pt_other = res.get_json()["data"]["plaintext"]
        monkeypatch.setattr(mcp_server, "get_http_headers", lambda **kw: {"authorization": f"Bearer {pt_other}"})
        out = _call("list_projects").data
        assert out["count"] == 1
        assert out["projects"][0]["my_role"] == "参与者"
        out = _call("get_all_comments", {"project": "mcp-proj"}).data
        assert out["count"] == 1  # 与网页评论列表可见性一致（登录用户可见）


# ───────────────────── T12.4 上传工具：单测（mock 回环）─────────────────────

def _read_audit(tmp_path) -> list:
    path = os.path.join(str(tmp_path / "audit"), "logs", "mcp-ops.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class Testbase64解码:
    def test_正常解码(self):
        raw = os.urandom(1000)
        got, err = mcp_server._decode_base64(b64.b64encode(raw).decode(), 10_000)
        assert err is None and got == raw

    def test_容忍折行与空白(self):
        raw = os.urandom(1000)
        s = b64.b64encode(raw).decode()
        folded = "\n".join(s[i:i + 76] for i in range(0, len(s), 76)) + "\n"
        got, err = mcp_server._decode_base64(folded, 10_000)
        assert err is None and got == raw

    def test_非法编码(self):
        got, err = mcp_server._decode_base64("!!!not-base64!!!", 10_000)
        assert got is None and err["error_code"] == "bad_request"

    def test_空内容(self):
        got, err = mcp_server._decode_base64("  \n ", 10_000)
        assert got is None and err["error_code"] == "bad_request"

    def test_超限预检_不解码(self):
        s = b64.b64encode(os.urandom(2000)).decode()
        got, err = mcp_server._decode_base64(s, 1000)
        assert got is None and err["error_code"] == "too_large"


class Test上传工具单测:
    def test_upload_prototype_成功与审计(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_server, "DATA_DIR", str(tmp_path / "audit"))
        routes = {
            ("GET", "/api/projects"): _ok(_rows()),
            ("POST", "/api/projects/11/prototype"): _ok(_rows()[0]),
            ("GET", "/api/me"): _ok({"id": 1, "email": "a@x.com", "name": "甲"}),
        }
        _fake_loopback(monkeypatch, routes)
        payload = b"dummy-zip-bytes"
        out = mcp_server.upload_prototype("login-a1b2c3", b64.b64encode(payload).decode(), "p.zip")
        assert out["file_size"] == len(payload)
        assert "覆盖" in out["note"]
        audit = _read_audit(tmp_path)
        assert len(audit) == 1
        assert audit[0]["tool"] == "upload_prototype" and audit[0]["ok"] is True
        assert audit[0]["user"] == "a@x.com" and audit[0]["project"] == "login-a1b2c3"
        assert audit[0]["file_size"] == len(payload)

    def test_upload_prototype_权限拒绝映射与审计(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_server, "DATA_DIR", str(tmp_path / "audit"))
        routes = {
            ("GET", "/api/projects"): _ok(_rows()),
            ("POST", "/api/projects/11/prototype"): ApiResult(403, {"code": 403, "msg": "仅管理者可上传原型（创建者或协作者）"}),
            ("GET", "/api/me"): _ok({"id": 3, "email": "c@x.com", "name": "丙"}),
        }
        _fake_loopback(monkeypatch, routes)
        out = mcp_server.upload_prototype("login-a1b2c3", b64.b64encode(b"x").decode())
        assert out["error_code"] == "no_permission"
        audit = _read_audit(tmp_path)
        assert len(audit) == 1 and audit[0]["ok"] is False and audit[0]["error_code"] == "no_permission"

    def test_upload_prd_文件名校验(self, monkeypatch, tmp_path):
        calls = _fake_loopback(monkeypatch, {})
        out = mcp_server.upload_prd("login-a1b2c3", b64.b64encode(b"# doc").decode(), "x.txt")
        assert out["error_code"] == "bad_request"
        assert len(calls) == 0  # 未发起任何回环调用

    def test_upload_prd_成功(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_server, "DATA_DIR", str(tmp_path / "audit"))
        routes = {
            ("GET", "/api/projects"): _ok(_rows()),
            ("POST", "/api/projects/11/prd"): _ok(_rows()[0]),
            ("GET", "/api/me"): _ok({"id": 1, "email": "a@x.com", "name": "甲"}),
        }
        calls = _fake_loopback(monkeypatch, routes)
        content = "# 需求\n\n内容".encode()
        out = mcp_server.upload_prd("login-a1b2c3", b64.b64encode(content).decode(), "需求.md")
        assert out["file_name"] == "需求.md" and out["file_size"] == len(content)
        post = [c for c in calls if c[0] == "POST"]
        assert post and post[0][3]["files"]["file"][0] == "需求.md"


# ───────────────────── T12.4 上传工具：集成（测试客户端直连）─────────────────

def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class Test上传集成直连:
    def test_上传原型全链路_文件替换与审计(self, env, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_server, "get_http_headers", lambda **kw: {"authorization": f"Bearer {_auth(env)}"})
        monkeypatch.setattr(mcp_server, "DATA_DIR", str(tmp_path / "audit"))
        zip_bytes = _zip_bytes({"index.html": "<html><body>新版原型内容</body></html>"})
        out = _call("upload_prototype", {
            "project": "mcp-proj",
            "file_base64": b64.b64encode(zip_bytes).decode(),
            "file_name": "new.zip",
        }).data
        assert out["file_size"] == len(zip_bytes)
        assert "覆盖" in out["note"]
        # 文件已原子替换（查看器口径：新内容就位）
        root = os.path.join(str(storage.PROJECTS_DIR), "mcp-proj")
        with open(os.path.join(root, "prototype", "index.html"), encoding="utf-8") as f:
            assert "新版原型内容" in f.read()
        # 审计日志（时间/用户/项目/工具/文件大小）
        audit = _read_audit(tmp_path)
        assert len(audit) == 1
        line = audit[0]
        assert line["user"] == "pm@corp.com"
        assert line["project"] == "mcp-proj"
        assert line["tool"] == "upload_prototype"
        assert line["file_size"] == len(zip_bytes)
        assert line["ok"] is True and line["ts"]

    def test_上传PRD全链路_替换与可见(self, env, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_server, "get_http_headers", lambda **kw: {"authorization": f"Bearer {_auth(env)}"})
        monkeypatch.setattr(mcp_server, "DATA_DIR", str(tmp_path / "audit"))
        content = "# 新需求文档\n\n全新内容。".encode()
        out = _call("upload_prd", {
            "project": "mcp-proj",
            "file_base64": b64.b64encode(content).decode(),
            "file_name": "新需求.md",
        }).data
        assert out["file_name"] == "新需求.md"
        root = os.path.join(str(storage.PROJECTS_DIR), "mcp-proj")
        assert os.listdir(os.path.join(root, "prd")) == ["新需求.md"]  # 旧文档被替换
        got = _call("get_prd_content", {"project": "mcp-proj"}).data
        assert got["file"] == "prd/新需求.md"
        assert "全新内容" in got["content"]

    def test_上传_非成员被拒_旧版完好(self, env, monkeypatch, tmp_path):
        boot, client, _, _ = env
        with client.session_transaction() as sess:
            sess["uid"] = U_OTHER
            sess["email"] = "other@corp.com"
            sess["name"] = "路人甲"
        res = client.post("/api/tokens", json={"name": "mcp-other-upload"})
        pt_other = res.get_json()["data"]["plaintext"]
        monkeypatch.setattr(mcp_server, "get_http_headers", lambda **kw: {"authorization": f"Bearer {pt_other}"})
        monkeypatch.setattr(mcp_server, "DATA_DIR", str(tmp_path / "audit"))
        zip_bytes = _zip_bytes({"index.html": "<html><body>越权内容</body></html>"})
        out = _call("upload_prototype", {
            "project": "mcp-proj",
            "file_base64": b64.b64encode(zip_bytes).decode(),
        }).data
        assert out["error_code"] == "no_permission"
        # 旧版本完好（未被替换）
        root = os.path.join(str(storage.PROJECTS_DIR), "mcp-proj")
        with open(os.path.join(root, "prototype", "index.html"), encoding="utf-8") as f:
            text = f.read()
        assert "data-pa" in text and "越权内容" not in text
        audit = _read_audit(tmp_path)
        assert audit and audit[-1]["ok"] is False

    def test_api_me_Bearer可用(self, env, monkeypatch):
        """审计取身份的接口：/api/me 在 Bearer 下返回绑定用户。"""
        r = loopback.call_api("GET", "/api/me", _auth(env))
        assert r.ok and r.data["email"] == "pm@corp.com"
        assert r.data["name"] == "创建者桑"


# ───────────────────── T12.6 原型下载：单测（mock 回环）─────────────────────

def _overview_with_proto() -> dict:
    row = _rows()[0]
    return {
        "project": row,
        "docs": ["prd/需求.md"],
        "proto_entries": ["prototype/index.html", "prototype/pages/settings.html"],
        "page_map": [],
        "proto_anchor_index": {"page-login": "prototype/index.html"},
        "reconcile_summary": None,
    }


class Test下载工具单测:
    def test_出参与curl占位符(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_server, "DATA_DIR", str(tmp_path / "audit"))
        routes = {
            ("GET", "/api/projects"): _ok(_rows()),
            ("GET", "/api/projects/11/overview"): _ok(_overview_with_proto()),
            ("GET", "/api/me"): _ok({"id": 1, "email": "a@x.com", "name": "甲"}),
        }
        _fake_loopback(monkeypatch, routes)
        out = mcp_server.download_prototype("login-a1b2c3", base_url="https://plan.example.com/")
        assert out["api_path"] == "/api/projects/11/prototype/export"
        assert out["download_url"] == "https://plan.example.com/api/projects/11/prototype/export"
        assert out["file_name"] == "login-a1b2c3-prototype.zip"
        assert "<你的Token>" in out["curl"]  # 默认不回显凭证
        assert "curl_ready" not in out
        assert out["entry_count"] == 2
        audit = _read_audit(tmp_path)
        assert audit[-1]["tool"] == "download_prototype" and audit[-1]["ok"] is True

    def test_include_token_回填可执行命令(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_server, "DATA_DIR", str(tmp_path / "audit"))
        monkeypatch.setattr(
            mcp_server, "get_http_headers", lambda **kw: {"authorization": "Bearer ppp_secrettoken"}
        )
        routes = {
            ("GET", "/api/projects"): _ok(_rows()),
            ("GET", "/api/projects/11/overview"): _ok(_overview_with_proto()),
            ("GET", "/api/me"): _ok({"id": 1, "email": "a@x.com", "name": "甲"}),
        }
        _fake_loopback(monkeypatch, routes)
        out = mcp_server.download_prototype("login-a1b2c3", base_url="https://plan.example.com", include_token=True)
        assert "Bearer ppp_secrettoken" in out["curl_ready"]
        assert "login-a1b2c3-prototype.zip" in out["curl_ready"]

    def test_空原型报prototype_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_server, "DATA_DIR", str(tmp_path / "audit"))
        empty = _overview_with_proto()
        empty["proto_entries"] = []
        routes = {
            ("GET", "/api/projects"): _ok(_rows()),
            ("GET", "/api/projects/11/overview"): _ok(empty),
            ("GET", "/api/me"): _ok({"id": 1, "email": "a@x.com", "name": "甲"}),
        }
        _fake_loopback(monkeypatch, routes)
        out = mcp_server.download_prototype("login-a1b2c3", base_url="https://plan.example.com")
        assert out["error_code"] == "prototype_empty"
        audit = _read_audit(tmp_path)
        assert audit[-1]["ok"] is False and audit[-1]["error_code"] == "prototype_empty"

    def test_无法确定基址时提示显式传入(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PLATFORM_PUBLIC_ORIGIN", raising=False)
        monkeypatch.setattr(mcp_server, "get_http_request", lambda: (_ for _ in ()).throw(RuntimeError("no request")))
        routes = {
            ("GET", "/api/projects"): _ok(_rows()),
            ("GET", "/api/projects/11/overview"): _ok(_overview_with_proto()),
        }
        _fake_loopback(monkeypatch, routes)
        out = mcp_server.download_prototype("login-a1b2c3")
        assert out["error_code"] == "base_url_unknown"
        assert "base_url" in out["hint"]

    def test_平台基址优先级_env优先(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_PUBLIC_ORIGIN", "https://plan.example.com/")
        assert mcp_server._platform_base() == "https://plan.example.com"

    def test_平台基址回落请求host(self, monkeypatch):
        monkeypatch.delenv("PLATFORM_PUBLIC_ORIGIN", raising=False)

        class _Req:
            headers = {"host": "plan.example.com"}
            url = type("U", (), {"scheme": "https"})()

        monkeypatch.setattr(mcp_server, "get_http_request", lambda: _Req())
        assert mcp_server._platform_base() == "https://plan.example.com"


# ───────────────────── T12.6 原型下载：集成（测试客户端直连）─────────────────

class Test原型下载集成直连:
    def test_导出端点_zip结构与内容(self, env):
        """Flask 导出端点：包内保留 prototype/ 一层，内容与磁盘一致。"""
        _boot, _client, transport_client, pt = env
        res = transport_client.get(
            "/api/projects/11/prototype/export",
            headers={"Authorization": f"Bearer {pt}"},
        )
        assert res.status_code == 200
        assert res.headers["Content-Type"] == "application/zip"
        assert "mcp-proj-prototype.zip" in res.headers["Content-Disposition"]
        zf = zipfile.ZipFile(io.BytesIO(res.data))
        names = zf.namelist()
        assert "mcp-proj-prototype/prototype/index.html" in names
        html = zf.read("mcp-proj-prototype/prototype/index.html").decode()
        assert 'data-pa="page-login"' in html

    def test_导出端点_未登录401(self, env):
        _boot, _client, transport_client, _pt = env
        assert transport_client.get("/api/projects/11/prototype/export").status_code == 401

    def test_导出端点_无原型内容410(self, env):
        boot, _client, transport_client, pt = env
        with boot.app_context():
            Project.create(project_id="mcp-empty", name="空项目", creator_id=U_OWNER)
        res = transport_client.get(
            "/api/projects/12/prototype/export",
            headers={"Authorization": f"Bearer {pt}"},
        )
        assert res.status_code == 410

    def test_工具返回的地址可直接下载(self, env, monkeypatch, tmp_path):
        """工具给出的 api_path + 当前 Token → 真的能下载到合法 zip（闭环可执行）。"""
        monkeypatch.setattr(mcp_server, "get_http_headers", lambda **kw: {"authorization": f"Bearer {_auth(env)}"})
        monkeypatch.setenv("PLATFORM_PUBLIC_ORIGIN", "http://127.0.0.1:8081")
        monkeypatch.setattr(mcp_server, "DATA_DIR", str(tmp_path / "audit"))
        out = _call("download_prototype", {"project": "mcp-proj"}).data
        assert out["api_path"] == "/api/projects/11/prototype/export"
        _boot, _client, transport_client, pt = env
        res = transport_client.get(out["api_path"], headers={"Authorization": f"Bearer {pt}"})
        assert res.status_code == 200
        assert zipfile.ZipFile(io.BytesIO(res.data)).namelist() == [
            "mcp-proj-prototype/prototype/index.html",
        ]
