"""T12.1 Token 基建单测（PRD §5.2 / AGENTS.md §4.4）。

覆盖任务卡验收点：
- 哈希校验：明文不落库，库中只存 SHA-256
- 明文不落日志：生成 + Bearer 调用全程 caplog 断言
- 撤销即时生效：撤销后下一次请求即 401
- 限流：单 token 60 次/分钟，超限 429（另一 token 不受影响）
- Bearer 调既有只读接口（projects 列表）返回正确用户数据
- Bearer 响应不回种 session cookie（撤销不被残留 cookie 绕过）
- Bearer 仅 /api/ 生效；session 流程无回归
"""
import hashlib
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.app import create_app  # noqa: E402
from server.auth import bearer_limiter, hash_token  # noqa: E402
from server.models import (  # noqa: E402
    ApiToken,
    User,
    db,
    init_tables,
)

U_A, U_B = 1, 2
BEARER_LIMIT = 60


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db.close()
    db.init(str(tmp_path / "test.db"))
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr("server.storage.PROJECTS_DIR", str(projects_dir))

    app = create_app()
    app.config["TESTING"] = True
    app.secret_key = "test-secret"

    with app.app_context():
        init_tables()
        User.create(id=U_A, email="pm@corp.com", name="甲")
        User.create(id=U_B, email="dev@corp.com", name="乙")
        bearer_limiter.reset()
        yield app
    bearer_limiter.reset()


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, uid: int, email: str, name: str):
    with client.session_transaction() as sess:
        sess["uid"] = uid
        sess["email"] = email
        sess["name"] = name


def _mk_token(client, name="workbuddy-agent", uid=U_A, email="pm@corp.com"):
    """以指定用户身份生成 token，返回 (id, 明文)。"""
    _login(client, uid, email, name)
    res = client.post("/api/tokens", json={"name": name})
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    return d["id"], d["plaintext"]


def _bearer_get(app, plaintext: str, path="/api/projects"):
    """干净无 cookie 客户端 + Bearer 头请求（隔离 session 串扰）。"""
    c = app.test_client()
    return c.get(path, headers={"Authorization": f"Bearer {plaintext}"})


class TestTokenApi:
    def test_生成token_明文一次性返回_库中只有哈希(self, client):
        _login(client, U_A, "pm@corp.com", "甲")
        res = client.post("/api/tokens", json={"name": "workbuddy-agent"})
        assert res.status_code == 200
        d = res.get_json()["data"]
        pt = d["plaintext"]
        assert pt.startswith("ppp_")
        assert len(pt) == len("ppp_") + 64  # 32 字节 hex

        rec = ApiToken.get_by_id(d["id"])
        assert rec.token_hash == hash_token(pt)
        assert rec.token_hash != pt
        assert hashlib.sha256(pt.encode()).hexdigest() == rec.token_hash
        assert rec.revoked is False
        assert rec.last_used_at is None

    def test_重复生成_明文各不相同(self, client):
        _, pt1 = _mk_token(client, name="t1")
        _, pt2 = _mk_token(client, name="t2")
        assert pt1 != pt2

    def test_生成校验_名称必填(self, client):
        _login(client, U_A, "pm@corp.com", "甲")
        res = client.post("/api/tokens", json={"name": "  "})
        assert res.status_code == 400
        res = client.post("/api/tokens", json={"name": "x" * 51})
        assert res.status_code == 400

    def test_列表不含明文与哈希(self, client):
        _mk_token(client, name="t1")
        res = client.get("/api/tokens")
        assert res.status_code == 200
        items = res.get_json()["data"]
        assert len(items) == 1
        item = items[0]
        assert set(item.keys()) == {"id", "name", "created_at", "last_used_at", "revoked"}
        assert item["name"] == "t1"
        assert "plaintext" not in item and "token_hash" not in item

    def test_未登录_401(self, client):
        assert client.post("/api/tokens", json={"name": "x"}).status_code == 401
        assert client.get("/api/tokens").status_code == 401
        assert client.delete("/api/tokens/1").status_code == 401

    def test_不可操作他人token(self, client):
        tid, _ = _mk_token(client, name="甲的", uid=U_A, email="pm@corp.com")
        _login(client, U_B, "dev@corp.com", "乙")
        res = client.delete(f"/api/tokens/{tid}")
        assert res.status_code == 404
        # B 列表为空（看不到他人 token）
        res = client.get("/api/tokens")
        assert res.get_json()["data"] == []


class Test明文不落日志:
    def test_生成与Bearer调用全程无明文(self, app, client, caplog):
        _login(client, U_A, "pm@corp.com", "甲")
        with caplog.at_level(logging.DEBUG):
            res = client.post("/api/tokens", json={"name": "workbuddy"})
            pt = res.get_json()["data"]["plaintext"]
            r2 = _bearer_get(app, pt)
            assert r2.status_code == 200
        joined = " ".join(str(r.getMessage()) for r in caplog.records)
        assert pt not in joined

    def test_撤销请求后日志同样无明文(self, app, client, caplog):
        tid, pt = _mk_token(client, name="t")
        _login(client, U_A, "pm@corp.com", "甲")
        with caplog.at_level(logging.DEBUG):
            res = client.delete(f"/api/tokens/{tid}")
            assert res.status_code == 200
            assert _bearer_get(app, pt).status_code == 401
        joined = " ".join(str(r.getMessage()) for r in caplog.records)
        assert pt not in joined


class TestBearerAuth:
    def test_Bearer调只读接口_返回正确用户数据(self, client):
        # 甲建项目；乙持 token 调 projects 列表
        _login(client, U_A, "pm@corp.com", "甲")
        assert client.post("/api/projects", json={"name": "甲项目"}).status_code == 200
        _, pt_b = _mk_token(client, name="乙的", uid=U_B, email="dev@corp.com")

        res = _bearer_get(app=client.application, plaintext=pt_b)
        assert res.status_code == 200
        rows = res.get_json()["data"]
        assert len(rows) == 1
        assert rows[0]["name"] == "甲项目"
        assert rows[0]["is_creator"] is False
        assert rows[0]["is_manager"] is False

        # 甲的 session 视角：is_creator True（session 流程无回归）
        _login(client, U_A, "pm@corp.com", "甲")
        res = client.get("/api/projects")
        assert res.get_json()["data"][0]["is_creator"] is True

    def test_无效token_401(self, app):
        # 不存在的 token
        fake = "ppp_" + "0" * 64
        assert _bearer_get(app, fake).status_code == 401
        # 前缀不对
        assert _bearer_get(app, "xxx_not_a_token").status_code == 401
        # 非 Bearer 方案：不进 Bearer 分支，session 未登录 → 401
        c = app.test_client()
        res = c.get("/api/projects", headers={"Authorization": "Basic dXNlcjpwd2Q="})
        assert res.status_code == 401

    def test_撤销即时生效(self, app, client):
        tid, pt = _mk_token(client, name="t")
        assert _bearer_get(app, pt).status_code == 200
        _login(client, U_A, "pm@corp.com", "甲")
        res = client.delete(f"/api/tokens/{tid}")
        assert res.status_code == 200
        assert res.get_json()["data"]["revoked"] is True
        # 撤销后下一次请求即 401
        assert _bearer_get(app, pt).status_code == 401

    def test_停用用户token_401(self, app, client):
        _, pt = _mk_token(client, name="t", uid=U_B, email="dev@corp.com")
        assert _bearer_get(app, pt).status_code == 200
        u = User.get_by_id(U_B)
        u.disabled = True
        u.save()
        res = _bearer_get(app, pt)
        assert res.status_code == 401
        assert "停用" in res.get_json()["msg"]

    def test_Bearer响应不回种session_cookie(self, app):
        """撤销即时生效的前提：Bearer 响应绝不携带可复用的登录 cookie。"""
        _, pt = _mk_token(app.test_client(), name="t")
        c = app.test_client()
        res = c.get("/api/projects", headers={"Authorization": f"Bearer {pt}"})
        assert res.status_code == 200
        assert res.headers.get("Set-Cookie") is None
        # 同一客户端（无 Bearer）再请求 → 未登录 401（未获得会话）
        assert c.get("/api/projects").status_code == 401

    def test_Bearer仅api生效(self, app):
        # 非 /api/ 路径带无效 Bearer 不拦截（Bearer 仅 /api/ 生效）
        c = app.test_client()
        res = c.get("/", headers={"Authorization": "Bearer ppp_" + "0" * 64})
        assert res.status_code == 200

    def test_last_used_at_首次调用即记录(self, app, client):
        _, pt = _mk_token(client, name="t")
        assert _bearer_get(app, pt).status_code == 200
        rec = ApiToken.select().order_by(ApiToken.id.desc()).first()
        assert rec.last_used_at is not None


class TestRateLimit:
    def test_单token_60次每分钟_超限429(self, app, client):
        _, pt_a = _mk_token(client, name="a")
        c = app.test_client()
        headers = {"Authorization": f"Bearer {pt_a}"}
        # 前 60 次全部放行
        for i in range(BEARER_LIMIT):
            res = c.get("/api/projects", headers=headers)
            assert res.status_code == 200, f"第 {i + 1} 次应放行"
        # 第 61 次 429
        res = c.get("/api/projects", headers=headers)
        assert res.status_code == 429
        assert "限流" in res.get_json()["msg"]

    def test_限流按token隔离(self, app, client):
        _, pt_a = _mk_token(client, name="a")
        _, pt_b = _mk_token(client, name="b", uid=U_B, email="dev@corp.com")
        c = app.test_client()
        ha = {"Authorization": f"Bearer {pt_a}"}
        for _ in range(BEARER_LIMIT):
            assert c.get("/api/projects", headers=ha).status_code == 200
        assert c.get("/api/projects", headers=ha).status_code == 429
        # 另一 token 不受影响
        res = c.get("/api/projects", headers={"Authorization": f"Bearer {pt_b}"})
        assert res.status_code == 200

    def test_429后撤销再建新token不受旧限流影响(self, app, client):
        _, pt_a = _mk_token(client, name="a")
        c = app.test_client()
        ha = {"Authorization": f"Bearer {pt_a}"}
        for _ in range(BEARER_LIMIT):
            c.get("/api/projects", headers=ha)
        assert c.get("/api/projects", headers=ha).status_code == 429
        # 新 token 独立额度
        _, pt_a2 = _mk_token(client, name="a2")
        res = c.get("/api/projects", headers={"Authorization": f"Bearer {pt_a2}"})
        assert res.status_code == 200


class TestSession无回归:
    def test_session登录流程不受Bearer改造影响(self, client):
        _login(client, U_A, "pm@corp.com", "甲")
        assert client.get("/api/auth/me").status_code == 200
        res = client.post("/api/projects", json={"name": "回归项目"})
        assert res.status_code == 200
        # 未登录（无 Bearer 无 session）仍 401
        fresh = client.application.test_client()
        assert fresh.get("/api/projects").status_code == 401

    def test_直接建库记录_兼容旧库迁移(self, app):
        """init_tables 幂等建 api_tokens 表（旧库启动自动补表）。"""
        db.create_tables([ApiToken], safe=True)
        assert (
            ApiToken.select()
            .join(User)
            .where(User.email == "pm@corp.com")
            .count()
            == 0
        )
