"""T2.1 用户管理增强单测：超管种子 / 停用拦截 / users CRUD 权限。

覆盖用户需求验收点：
- ADMIN_EMAIL 启动种子超管（name=admin，is_admin=True，幂等）
- 停用账号：request-code / verify 均拒（403「账号已停用」）
- 已登录停用账号：任意 /api/ 调用被 before_request 401 强制登出
- /api/users CRUD 仅超管可用（非超管 403）
- 停用超管本人拒绝（400）
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.app import create_app  # noqa: E402
from server.models import User, db, init_tables, seed_admin  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """独立临时 DB + mock SMTP + 超管种子（admin@corp.com）。

    用户（白名单）：pm@corp.com 为普通用户。
    """
    db_path = tmp_path / "test.db"
    db.close()
    db.init(str(db_path))

    monkeypatch.setattr("server.auth.SMTP_HOST", "smtp.test.local")
    sent: list[tuple[str, str]] = []

    def fake_send(to_email, code):
        sent.append((to_email, code))

    monkeypatch.setattr("server.auth._send_code_email", fake_send)
    monkeypatch.setattr("server.models.ADMIN_EMAIL", "admin@corp.com")

    app = create_app()
    app.config["TESTING"] = True
    app.secret_key = "test-secret"

    with app.app_context():
        init_tables()
        User.create(email="pm@corp.com", name="产品桑")
        yield app, sent


@pytest.fixture()
def client(app):
    return app[0].test_client()


def _login(client, sent, email):
    """走真实请求码+验证码登录（read code from sent list）。返回 session 化 client。"""
    client.post("/api/auth/request-code", json={"email": email})
    code = [c for to, c in sent if to == email][-1]
    res = client.post("/api/auth/verify", json={"email": email, "code": code})
    assert res.status_code == 200, res.get_json()
    return res


class TestSeedAdmin:
    def test_种子超管(self, app):
        _, _sent = app
        admin = User.get_or_none(User.email == "admin@corp.com")
        assert admin is not None
        assert admin.name == "admin"
        assert admin.is_admin is True
        assert admin.disabled is False

    def test_幂等重复init(self, app):
        _app, _sent = app
        init_tables()
        assert User.select().where(User.is_admin == True).count() == 1  # noqa: E712

    def test_seed_promotes_existing_non_admin_no_unique_error(self, app, monkeypatch):
        """已存在普通用户（同 ADMIN_EMAIL）→ seed_admin 仅提升 is_admin，不抛 UNIQUE。

        对应多 worker 并发启动：get_or_create 撞唯一约束会回退查询而非崩溃。
        """
        _app, _sent = app
        u = User.get_or_none(User.email == "pm@corp.com")
        assert u is not None and u.is_admin is False
        monkeypatch.setattr("server.models.ADMIN_EMAIL", "pm@corp.com")
        seed_admin()  # 已存在 → 提升，不抛 IntegrityError
        u2 = User.get_or_none(User.email == "pm@corp.com")
        assert u2.is_admin is True


class TestDisabledLogin:
    def test_停用后不发码(self, client, app):
        _app, _sent = app
        u = User.get_or_none(User.email == "pm@corp.com")
        u.disabled = True
        u.save()
        res = client.post("/api/auth/request-code", json={"email": "pm@corp.com"})
        assert res.status_code == 403
        assert "已停用" in res.get_json()["msg"]

    def test_停用后verify拒(self, client, app):
        _app, sent = app
        client.post("/api/auth/request-code", json={"email": "pm@corp.com"})
        u = User.get_or_none(User.email == "pm@corp.com")
        u.disabled = True
        u.save()
        code = [c for to, c in sent if to == "pm@corp.com"][-1]
        res = client.post("/api/auth/verify", json={"email": "pm@corp.com", "code": code})
        assert res.status_code == 403
        assert "已停用" in res.get_json()["msg"]

    def test_已登录停用任意接口401(self, client, app):
        _app, sent = app
        _login(client, sent, "pm@corp.com")
        assert client.get("/api/projects").status_code == 200
        u = User.get_or_none(User.email == "pm@corp.com")
        u.disabled = True
        u.save()
        res = client.get("/api/projects")
        assert res.status_code == 401
        assert "已停用" in res.get_json()["msg"]
        # session 已被清空
        assert client.get("/api/projects").status_code == 401


class TestUsersCrud:
    def test_非超管访问403(self, client, app):
        _app, sent = app
        _login(client, sent, "pm@corp.com")
        assert client.get("/api/users").status_code == 403
        assert client.post("/api/users", json={"email": "x@corp.com", "name": "X"}).status_code == 403

    def test_超管建用户(self, client, app):
        _app, sent = app
        _login(client, sent, "admin@corp.com")
        res = client.post("/api/users", json={"email": "zhang@corp.com", "name": "张三"})
        assert res.status_code == 200
        body = res.get_json()["data"]
        assert body["email"] == "zhang@corp.com"
        assert body["name"] == "张三"
        assert body["is_admin"] is False
        assert body["disabled"] is False

    def test_重复邮箱409(self, client, app):
        _app, sent = app
        _login(client, sent, "admin@corp.com")
        client.post("/api/users", json={"email": "dup@corp.com", "name": "甲"})
        res = client.post("/api/users", json={"email": "dup@corp.com", "name": "乙"})
        assert res.status_code == 409

    def test_改名包括本人(self, client, app):
        _app, sent = app
        _login(client, sent, "admin@corp.com")
        admin = User.get_or_none(User.email == "admin@corp.com")
        res = client.patch(f"/api/users/{admin.id}", json={"name": "首席管理员"})
        assert res.status_code == 200
        assert res.get_json()["data"]["name"] == "首席管理员"
        # 改本人 → session name 同步，/me 返回新姓名
        me = client.get("/api/auth/me")
        assert me.get_json()["data"]["user"]["name"] == "首席管理员"

    def test_停用启用(self, client, app):
        _app, sent = app
        _login(client, sent, "admin@corp.com")
        client.post("/api/users", json={"email": "zz@corp.com", "name": "小张"})
        u = User.get_or_none(User.email == "zz@corp.com")
        res = client.patch(f"/api/users/{u.id}/status", json={"disabled": True})
        assert res.status_code == 200
        assert res.get_json()["data"]["disabled"] is True
        res2 = client.patch(f"/api/users/{u.id}/status", json={"disabled": False})
        assert res2.status_code == 200
        assert res2.get_json()["data"]["disabled"] is False

    def test_停用超管本人拒(self, client, app):
        _app, sent = app
        _login(client, sent, "admin@corp.com")
        admin = User.get_or_none(User.email == "admin@corp.com")
        res = client.patch(f"/api/users/{admin.id}/status", json={"disabled": True})
        assert res.status_code == 400
        assert "超级管理员" in res.get_json()["msg"]
