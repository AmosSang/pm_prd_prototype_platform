"""T2.3 项目绑定单测。

覆盖任务卡验收点：
- 绑定成功：DB 记录 + clone 目录存在 + .git/config 无 token
- 错误 token：认证失败提示
- 仓库不存在：not found 提示
- 列表接口不回传任何 token 字段

远端用本地裸仓库 / 假 HTTP GitLab（401/404），零真实网络。
"""
import http.server
import os
import socketserver
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.app import create_app  # noqa: E402
from server.crypto_util import decrypt_token, encrypt_token  # noqa: E402
from server.models import Project, db, init_tables  # noqa: E402

# ───────────────────────── 工具：本地裸仓库远端 ─────────────────────────

def make_bare_remote(tmp_path, name: str, branch: str = "main") -> str:
    """造一个带 prototype/prd 的裸仓库，返回其路径。"""
    work = tmp_path / f"{name}-work"
    bare = tmp_path / f"{name}.git"
    work.mkdir()
    subprocess.run(["git", "init", "-b", branch, str(work)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.local"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    (work / "prototype").mkdir()
    (work / "prototype" / "index.html").write_text("<html><body>hi</body></html>")
    (work / "prd").mkdir()
    (work / "prd" / "a.md").write_text("# PRD\n")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)], check=True, capture_output=True)
    return str(bare)


class _FakeGitLab(http.server.BaseHTTPRequestHandler):
    """假 GitLab：只返回固定状态码，用于触发 git 真实报错文案。"""

    status = 401

    def do_GET(self):
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture()
def fake_gitlab_401():
    srv = socketserver.TCPServer(("127.0.0.1", 0), _FakeGitLab)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/grp/repo.git"
    srv.shutdown()


@pytest.fixture()
def fake_gitlab_404(fake_gitlab_401):
    # 复用端口不行，单独起 404 服务
    _FakeGitLab.status = 404
    srv = socketserver.TCPServer(("127.0.0.1", 0), _FakeGitLab)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/grp/repo.git"
    srv.shutdown()
    _FakeGitLab.status = 401


# ───────────────────────── fixture：独立 DB + REPOS_DIR ─────────────────────────

@pytest.fixture()
def app(tmp_path, monkeypatch):
    db.close()
    db.init(str(tmp_path / "test.db"))

    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    monkeypatch.setattr("server.gitops.REPOS_DIR", str(repos_dir))

    app = create_app()
    app.config["TESTING"] = True
    app.secret_key = "test-secret"

    with app.app_context():
        init_tables()
        with app.test_client() as c:
            # 模拟已登录（绕过验证码流程）
            with c.session_transaction() as sess:
                sess["uid"] = 1
                sess["email"] = "pm@corp.com"
                sess["name"] = "产品桑"
            yield c, str(repos_dir)


# ───────────────────────── 用例 ─────────────────────────

class TestCreateProject:
    def test_bind_success(self, app, tmp_path):
        """绑定成功：DB 记录 + clone 目录 + .git/config 无 token + 响应无 token。"""
        client, repos_dir = app
        remote = make_bare_remote(tmp_path, "ok")

        resp = client.post("/api/projects", json={
            "name": "演示项目", "repo_url": remote, "token": "glpat-secret-token", "branch": "main",
        })
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]

        # DB 记录
        p = Project.get(Project.project_id == data["project_id"])
        assert p.name == "演示项目"
        # token 加密落库（可解回，但不是明文）
        assert p.encrypted_token != "glpat-secret-token"
        assert decrypt_token(p.encrypted_token) == "glpat-secret-token"
        # clone 目录存在且含内容
        clone = os.path.join(repos_dir, data["project_id"])
        assert os.path.isfile(os.path.join(clone, "prototype", "index.html"))
        # .git/config 无 token（本地路径远端本就不含，此断言防回归）
        cfg = open(os.path.join(clone, ".git", "config"), encoding="utf-8").read()
        assert "glpat-secret-token" not in cfg
        # 响应体无任何 token 字段
        assert "token" not in resp.get_data(as_text=True) or "glpat-secret-token" not in resp.get_data(as_text=True)

    def test_wrong_token_hint(self, app, fake_gitlab_401):
        """错误 token：认证失败提示（走假 GitLab 401）。"""
        client, _ = app
        resp = client.post("/api/projects", json={
            "name": "错token", "repo_url": fake_gitlab_401, "token": "bad-token", "branch": "main",
        })
        assert resp.status_code == 400
        assert "认证失败" in resp.get_json()["msg"]

    def test_repo_not_found_hint(self, app, fake_gitlab_404):
        """仓库不存在：not found 提示。"""
        client, _ = app
        resp = client.post("/api/projects", json={
            "name": "不存在", "repo_url": fake_gitlab_404, "token": "any", "branch": "main",
        })
        assert resp.status_code == 400
        assert "仓库不存在" in resp.get_json()["msg"]

    def test_clone_failure_no_db_record(self, app, fake_gitlab_401):
        """clone 失败不落库、不留半成品目录。"""
        client, repos_dir = app
        before = Project.select().count()
        client.post("/api/projects", json={
            "name": "失败", "repo_url": fake_gitlab_401, "token": "x", "branch": "main",
        })
        assert Project.select().count() == before
        assert os.listdir(repos_dir) == []

    def test_missing_fields(self, app):
        client, _ = app
        resp = client.post("/api/projects", json={"name": "", "repo_url": "", "token": ""})
        assert resp.status_code == 400
        assert "项目名" in resp.get_json()["msg"]


class TestListProjects:
    def test_list_no_token_leak(self, app, tmp_path):
        """列表接口不回传 token/encrypted_token。"""
        client, _ = app
        remote = make_bare_remote(tmp_path, "list")
        client.post("/api/projects", json={
            "name": "列表项目", "repo_url": remote, "token": "glpat-leak-check", "branch": "main",
        })

        resp = client.get("/api/projects")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "glpat-leak-check" not in body
        assert "encrypted_token" not in body
        data = resp.get_json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "列表项目"
        assert data[0]["branch"] == "main"


class TestCrypto:
    def test_roundtrip_and_mask(self):
        enc = encrypt_token("glpat-abcdef1234")
        assert decrypt_token(enc) == "glpat-abcdef1234"
        assert enc != "glpat-abcdef1234"
        from server.crypto_util import mask_token

        assert mask_token("glpat-abcdef1234") == "****1234"
        assert mask_token("short") == "****"


# ───────────────────────── T2.4 查看器 API ─────────────────────────

class TestViewerAPI:
    def test_overview_docs_and_entries(self, app, tmp_path):
        """overview：prd/ 优先；无 prd/ 时兼容根目录 md；原型入口列出。"""
        client, repos_dir = app
        remote = make_bare_remote(tmp_path, "viewer")
        resp = client.post("/api/projects", json={
            "name": "查看器", "repo_url": remote, "token": "tk", "branch": "main",
        })
        pid = resp.get_json()["data"]["id"]

        resp = client.get(f"/api/projects/{pid}/overview")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["docs"] == ["prd/a.md"]
        assert "prototype/index.html" in data["proto_entries"]

    def test_overview_root_md_compat(self, app, tmp_path):
        """仓库无 prd/ 目录（根目录直接放 md）也能列出文档。"""
        client, repos_dir = app
        # 手工构造一个「根目录放 md」的裸仓库
        work = tmp_path / "flat-work"
        bare = tmp_path / "flat.git"
        work.mkdir()
        subprocess.run(["git", "init", "-b", "main", "-q", str(work)], check=True)
        subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.local"], check=True)
        subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
        (work / "灵雁思路.md").write_text("# 灵雁\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"], check=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, capture_output=True)

        resp = client.post("/api/projects", json={
            "name": "扁平仓库", "repo_url": str(bare), "token": "tk", "branch": "main",
        })
        pid = resp.get_json()["data"]["id"]
        resp = client.get(f"/api/projects/{pid}/overview")
        assert resp.get_json()["data"]["docs"] == ["灵雁思路.md"]

        # prd 接口能读根目录 md（中文文件名）
        resp = client.get(f"/api/projects/{pid}/prd?file=灵雁思路.md")
        assert resp.status_code == 200
        assert "# 灵雁" in resp.get_json()["data"]["content"]

    def test_prd_file_traversal_blocked(self, app, tmp_path):
        """prd 接口防目录穿越（.. / 绝对路径 / 越界路径全拒绝）。"""
        client, _ = app
        remote = make_bare_remote(tmp_path, "trav")
        resp = client.post("/api/projects", json={
            "name": "穿越", "repo_url": remote, "token": "tk", "branch": "main",
        })
        pid = resp.get_json()["data"]["id"]

        for bad in ["../platform.db", "/etc/passwd", "prd/../../server/app.py", ""]:
            resp = client.get(f"/api/projects/{pid}/prd", query_string={"file": bad})
            assert resp.status_code in (400, 404), f"{bad} 未被拦截"

    def test_prd_normal_read(self, app, tmp_path):
        client, _ = app
        remote = make_bare_remote(tmp_path, "read")
        resp = client.post("/api/projects", json={
            "name": "读取", "repo_url": remote, "token": "tk", "branch": "main",
        })
        pid = resp.get_json()["data"]["id"]

        resp = client.get(f"/api/projects/{pid}/prd?file=prd/a.md")
        assert resp.status_code == 200
        assert resp.get_json()["data"]["content"] == "# PRD\n"


# ───────────────────────── T3.1 手动同步（临时按钮） ─────────────────────────

class TestSyncProject:
    def test_sync_pulls_new_commit(self, app, tmp_path):
        """远端 push 新提交 → sync → 本地 clone 更新 + last_sync_at 刷新。"""
        client, repos_dir = app
        remote = make_bare_remote(tmp_path, "sync")
        resp = client.post("/api/projects", json={
            "name": "同步项目", "repo_url": remote, "token": "tk", "branch": "main",
        })
        slug = resp.get_json()["data"]["project_id"]
        pid = resp.get_json()["data"]["id"]
        clone = os.path.join(repos_dir, slug)

        # 远端加新文档 push（往裸仓库推：临时 work clone）
        pushdir = tmp_path / "push-work"
        subprocess.run(["git", "clone", "-q", remote, str(pushdir)], check=True, capture_output=True)
        (pushdir / "prd" / "new.md").write_text("# 新文档\n")
        subprocess.run(["git", "-C", str(pushdir), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(pushdir), "commit", "-qm", "add doc"], check=True)
        subprocess.run(["git", "-C", str(pushdir), "push", "-q"], check=True, capture_output=True)

        # 同步前本地无新文档
        assert not os.path.exists(os.path.join(clone, "prd", "new.md"))

        resp = client.post(f"/api/projects/{pid}/sync")
        assert resp.status_code == 200, resp.get_json()
        assert os.path.isfile(os.path.join(clone, "prd", "new.md"))
        p = Project.get(Project.id == pid)
        assert p.sync_error is None
        assert p.last_sync_at is not None

    def test_sync_already_up_to_date(self, app, tmp_path):
        """无新提交时 sync 也成功（Already up to date 不算错误）。"""
        client, _ = app
        remote = make_bare_remote(tmp_path, "uptodate")
        resp = client.post("/api/projects", json={
            "name": "最新项目", "repo_url": remote, "token": "tk", "branch": "main",
        })
        pid = resp.get_json()["data"]["id"]

        resp = client.post(f"/api/projects/{pid}/sync")
        assert resp.status_code == 200

    def test_sync_auth_error_sets_sync_error(self, app, tmp_path, fake_gitlab_401):
        """远端不可达/认证失败：400 + 中文提示 + sync_error 落库。

        注意：fetch 走本地 .git/config 里的 origin URL（不是 DB repo_url），
        所以模拟方式是直接改 clone 的 remote URL（等价于远端后来挂了/token 过期）。
        """
        client, repos_dir = app
        remote = make_bare_remote(tmp_path, "dead")
        resp = client.post("/api/projects", json={
            "name": "坏远端", "repo_url": remote, "token": "tk", "branch": "main",
        })
        pid = resp.get_json()["data"]["id"]
        slug = Project.get(Project.id == pid).project_id
        subprocess.run(
            ["git", "-C", os.path.join(repos_dir, slug), "remote", "set-url", "origin", fake_gitlab_401],
            check=True, capture_output=True,
        )

        resp = client.post(f"/api/projects/{pid}/sync")
        assert resp.status_code == 400
        assert "认证失败" in resp.get_json()["msg"]
        assert "认证失败" in Project.get(Project.id == pid).sync_error

    def test_sync_no_local_clone(self, app, tmp_path):
        """本地 clone 被删：明确提示重新绑定。"""
        client, repos_dir = app
        remote = make_bare_remote(tmp_path, "gone")
        resp = client.post("/api/projects", json={
            "name": "被删项目", "repo_url": remote, "token": "tk", "branch": "main",
        })
        pid = resp.get_json()["data"]["id"]
        slug = Project.get(Project.id == pid).project_id
        import shutil

        shutil.rmtree(os.path.join(repos_dir, slug))

        resp = client.post(f"/api/projects/{pid}/sync")
        assert resp.status_code == 400
        assert "重新绑定" in resp.get_json()["msg"]


# ───────────────────────── T4.5 项目级「可评论」开关 ─────────────────────────

class TestUpdateProject:
    """PATCH /api/projects/{pid} {commentable}（产品方案 §4.5）。"""

    def _make_project(self, client, tmp_path, name: str) -> int:
        remote = make_bare_remote(tmp_path, name)
        resp = client.post("/api/projects", json={
            "name": name, "repo_url": remote, "token": "tk", "branch": "main",
        })
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()["data"]["id"]

    def test_commentable_default_on_and_toggle(self, app, tmp_path):
        """默认开启（T2.3 建表预留）；PATCH 关→开往返落库且响应带最新值。"""
        client, _ = app
        pid = self._make_project(client, tmp_path, "开关项目")
        assert Project.get(Project.id == pid).commentable is True

        resp = client.patch(f"/api/projects/{pid}", json={"commentable": False})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["data"]["commentable"] is False
        assert Project.get(Project.id == pid).commentable is False

        resp = client.patch(f"/api/projects/{pid}", json={"commentable": True})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["commentable"] is True
        assert Project.get(Project.id == pid).commentable is True

    def test_commentable_validation(self, app, tmp_path):
        """缺字段 / 非布尔（字符串、数字——bool 是 int 子类须显式排除）/ 项目不存在。"""
        client, _ = app
        pid = self._make_project(client, tmp_path, "开关项目2")
        assert client.patch(f"/api/projects/{pid}", json={}).status_code == 400
        assert client.patch(f"/api/projects/{pid}", json={"commentable": "yes"}).status_code == 400
        assert client.patch(f"/api/projects/{pid}", json={"commentable": 1}).status_code == 400
        assert client.patch("/api/projects/99999", json={"commentable": True}).status_code == 404
