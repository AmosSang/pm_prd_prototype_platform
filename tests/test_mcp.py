"""T12.3 MCP Server 单测 + 集成测试。

单测（mock 回环）：项目匹配规则（slug 精确 > 名称精确 > 名称包含 / 多命中候选 /
未命中提示）、错误映射三段式、出参整形（角色映射 / 评论同构覆盖 / no_prd）。
集成测试（测试客户端直连）：FastMCP 内存 Client → 工具 → 回环 httpx transport
→ Flask test_client（真实路由 + Bearer 认证 + DB/文件全链路）。
"""
import asyncio
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import dom_payload, make_local_project  # noqa: E402
from fastmcp import Client  # noqa: E402

import server.mcp_loopback as loopback  # noqa: E402
import server.mcp_server as mcp_server  # noqa: E402
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
    def test_工具清单_五个只读(self, env, monkeypatch):
        monkeypatch.setattr(mcp_server, "get_http_headers", lambda **kw: {"authorization": f"Bearer {_auth(env)}"})

        async def go():
            async with Client(mcp_server.mcp) as c:
                return await c.list_tools()

        tools = asyncio.run(go())
        names = sorted(t.name for t in tools)
        assert names == [
            "get_all_comments", "get_prd_content", "get_project_overview", "get_reconcile", "list_projects",
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
