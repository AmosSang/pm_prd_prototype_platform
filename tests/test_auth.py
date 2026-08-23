"""T2.1 验证码登录单测。

覆盖任务卡验收点：过期码拒绝、重放拒绝、60s 频控生效、SMTP 失败明确报错。
SMTP 全程 mock（不依赖真实邮件服务）。
"""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.app import create_app  # noqa: E402
from server.auth import CODE_TTL_SECONDS  # noqa: E402
from server.models import (  # noqa: E402
    User,
    VerificationCode,
    db,
    init_tables,
    parse_utc,
)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """独立临时 DB + mock SMTP 的应用实例。

    注意：models.db 是模块级单例，改 config.DB_PATH 无效，
    必须用 db.init() 重定向实例本身。
    """
    db_path = tmp_path / "test.db"
    db.close()
    db.init(str(db_path))

    # mock SMTP 配置（视为已配置）
    monkeypatch.setattr("server.auth.SMTP_HOST", "smtp.test.local")

    # mock 发送函数：记录调用，默认成功
    sent: list[tuple[str, str]] = []

    def fake_send(to_email, code):
        sent.append((to_email, code))

    monkeypatch.setattr("server.auth._send_code_email", fake_send)

    app = create_app()
    app.config["TESTING"] = True
    # 测试期固定 secret，session 可控
    app.secret_key = "test-secret"

    with app.app_context():
        init_tables()
        User.create(email="pm@corp.com", name="产品桑")
        yield app, sent


@pytest.fixture()
def client(app):
    return app[0].test_client()


def _request_code(client, email="pm@corp.com"):
    return client.post("/api/auth/request-code", json={"email": email})


def _verify(client, email, code):
    return client.post("/api/auth/verify", json={"email": email, "code": code})


class TestRequestCode:
    def test_正常发码(self, client, app):
        _, sent = app
        res = _request_code(client)
        assert res.status_code == 200
        assert res.get_json()["code"] == 0
        assert len(sent) == 1
        to, code = sent[0]
        assert to == "pm@corp.com"
        assert len(code) == 6 and code.isdigit()

    def test_白名单外邮箱拒绝(self, client):
        res = _request_code(client, "stranger@corp.com")
        assert res.status_code == 403
        assert "未开通" in res.get_json()["msg"]

    def test_邮箱格式校验(self, client):
        res = _request_code(client, "not-an-email")
        assert res.status_code == 400

    def test_60s频控(self, client, app):
        _, sent = app
        assert _request_code(client).status_code == 200
        res = _request_code(client)
        assert res.status_code == 429
        assert "频繁" in res.get_json()["msg"]
        assert len(sent) == 1  # 第二条没有真发

    def test_smtp失败明确报错且不落库(self, client, app, monkeypatch):
        def broken_send(to_email, code):
            raise ConnectionRefusedError("connect refused")

        monkeypatch.setattr("server.auth._send_code_email", broken_send)
        res = _request_code(client)
        assert res.status_code == 502
        assert "发送失败" in res.get_json()["msg"]
        # 失败不落库
        assert VerificationCode.select().count() == 0

    def test_smtp未配置503(self, client, app, monkeypatch):
        monkeypatch.setattr("server.auth.SMTP_HOST", "")
        res = _request_code(client)
        assert res.status_code == 503


class TestVerify:
    def test_正常登录建session(self, client, app):
        _, sent = app
        _request_code(client)
        code = sent[0][1]
        res = _verify(client, "pm@corp.com", code)
        assert res.status_code == 200
        assert res.get_json()["data"]["user"]["name"] == "产品桑"
        # session 生效：/me 可访问
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.get_json()["data"]["user"]["email"] == "pm@corp.com"

    def test_错误码拒绝(self, client, app):
        _, sent = app
        _request_code(client)
        res = _verify(client, "pm@corp.com", "000000")
        assert res.status_code == 400
        assert res.get_json()["msg"] == "验证码错误"
        # 错误码不核销，还能继续试
        res2 = _verify(client, "pm@corp.com", sent[0][1])
        assert res2.status_code == 200

    def test_过期码拒绝(self, client, app):
        _, sent = app
        _request_code(client)
        # 直接把 DB 里的码改成已过期
        vc = VerificationCode.get()
        expired = (
            parse_utc(vc.expires_at) - dt.timedelta(seconds=CODE_TTL_SECONDS + 60)
        ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        vc.expires_at = expired
        vc.save()
        res = _verify(client, "pm@corp.com", sent[0][1])
        assert res.status_code == 400
        assert "过期" in res.get_json()["msg"]

    def test_重放拒绝(self, client, app):
        _, sent = app
        _request_code(client)
        code = sent[0][1]
        assert _verify(client, "pm@corp.com", code).status_code == 200
        res = _verify(client, "pm@corp.com", code)
        assert res.status_code == 400
        assert "已使用" in res.get_json()["msg"]

    def test_登出后me返回401(self, client, app):
        _, sent = app
        _request_code(client)
        _verify(client, "pm@corp.com", sent[0][1])
        assert client.get("/api/auth/me").status_code == 200
        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").status_code == 401


class TestCli:
    def test_user_add_list_del(self, tmp_path, monkeypatch, capsys):
        # 同样必须 db.init() 重定向单例
        db.close()
        db.init(str(tmp_path / "cli.db"))
        from server import cli

        assert cli.main(["user-add", "a@corp.com", "甲"]) == 0
        assert cli.main(["user-add", "a@corp.com", "重复"]) == 1  # 重复添加失败
        assert cli.main(["user-add", "b@corp.com", "乙", "--admin"]) == 0
        assert cli.main(["user-list"]) == 0
        out = capsys.readouterr().out
        assert "a@corp.com" in out and "b@corp.com" in out
        assert cli.main(["user-del", "a@corp.com"]) == 0
        assert cli.main(["user-del", "a@corp.com"]) == 1  # 不存在
