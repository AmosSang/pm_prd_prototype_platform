"""T2.3 / T8.1 项目创建与本地存储单测。

T8.1 去 Git 本地化后的验收点：
- 创建项目：只填名称 → DB 记录（creator_id）+ PROJECTS_DIR 目录骨架
- 目录工具：ensure_project_dirs 骨架齐全、project_dir 拒绝非法 slug
- 列表接口：带创建者信息与 is_creator 标记，无任何 git 字段
- 查看器 API（overview/prd）：读本地目录（prd/ 优先、根目录 md 兼容、防穿越）
- 可评论开关（T4.5）：默认开、PATCH 往返、参数校验

零 git、零网络：项目目录全在 monkeypatch 的临时 PROJECTS_DIR 下。
"""
import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.app import create_app  # noqa: E402
from server.models import Project, User, db, init_tables  # noqa: E402
from server.storage import SLUG_RE, ensure_project_dirs, project_dir  # noqa: E402

# ───────────────────────── fixture：独立 DB + PROJECTS_DIR ─────────────────────────

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
        User.create(email="pm@corp.com", name="产品桑")
        with app.test_client() as c:
            # 模拟已登录（绕过验证码流程；uid=1 对应上面建的 user）
            with c.session_transaction() as sess:
                sess["uid"] = 1
                sess["email"] = "pm@corp.com"
                sess["name"] = "产品桑"
            yield c, str(projects_dir)


def _populate(root: str, files: dict[str, str]) -> None:
    """往项目目录写文件（模拟 T8.2 上传接口落盘的产物）。"""
    for rel, content in files.items():
        full = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)


# ───────────────────────── storage 工具（T8.1） ─────────────────────────

class TestStorage:
    def test_ensure_project_dirs_skeleton(self, tmp_path, monkeypatch):
        monkeypatch.setattr("server.storage.PROJECTS_DIR", str(tmp_path))
        root = ensure_project_dirs("my-proj")
        assert root == os.path.join(str(tmp_path), "my-proj")
        for sub in ("prototype", "prd", "reviews/comments", "reviews/shots"):
            assert os.path.isdir(os.path.join(root, *sub.split("/"))), f"缺 {sub}"
        # 幂等
        ensure_project_dirs("my-proj")

    def test_project_dir_rejects_bad_slug(self, tmp_path, monkeypatch):
        monkeypatch.setattr("server.storage.PROJECTS_DIR", str(tmp_path))
        for bad in ("../etc", "a b", "UPPER", "-lead", "", "x" * 40):
            assert not SLUG_RE.fullmatch(bad)
            with pytest.raises(ValueError):
                project_dir(bad)

    def test_project_dir_demo_uses_fixture(self, monkeypatch):
        """demo 项目特判指向 tests/fixtures（T1.1 以来行为不变）。"""
        monkeypatch.setattr("server.storage.DEMO_REPO_DIR", "/tmp/demo-fixture")
        assert project_dir("demo") == os.path.realpath("/tmp/demo-fixture")


# ───────────────────────── 创建项目（T8.1） ─────────────────────────

class TestCreateProject:
    def test_create_success_dirs_and_creator(self, app):
        """创建成功：DB 记录（creator_id）+ 目录骨架 + 响应带创建者与 is_creator。"""
        client, projects_dir = app

        resp = client.post("/api/projects", json={"name": "演示项目"})
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]

        p = Project.get(Project.project_id == data["project_id"])
        assert p.name == "演示项目"
        assert p.creator_id == 1
        assert p.commentable is True
        assert p.content_updated_at is None

        root = os.path.join(projects_dir, data["project_id"])
        for sub in ("prototype", "prd", "reviews/comments", "reviews/shots"):
            assert os.path.isdir(os.path.join(root, *sub.split("/"))), f"缺 {sub}"

        assert data["creator"]["name"] == "产品桑"
        assert data["creator"]["email"] == "pm@corp.com"
        assert data["is_creator"] is True

    def test_create_generates_unique_slug(self, app):
        """同名项目 slug 不冲突（随机后缀防撞）。"""
        client, _ = app
        r1 = client.post("/api/projects", json={"name": "同名"}).get_json()["data"]
        r2 = client.post("/api/projects", json={"name": "同名"}).get_json()["data"]
        assert r1["project_id"] != r2["project_id"]

    def test_create_missing_name(self, app):
        client, _ = app
        resp = client.post("/api/projects", json={"name": ""})
        assert resp.status_code == 400
        assert "项目名" in resp.get_json()["msg"]

    def test_create_name_too_long(self, app):
        client, _ = app
        resp = client.post("/api/projects", json={"name": "长" * 51})
        assert resp.status_code == 400

    def test_create_requires_login(self, tmp_path, monkeypatch):
        db.close()
        db.init(str(tmp_path / "nologin.db"))
        monkeypatch.setattr("server.storage.PROJECTS_DIR", str(tmp_path / "projects"))
        app = create_app()
        app.config["TESTING"] = True
        app.secret_key = "test-secret"
        with app.test_client() as c:
            resp = c.post("/api/projects", json={"name": "未登录"})
            assert resp.status_code == 401


# ───────────────────────── 列表（T8.1：创建者标记 + 无 git 字段） ─────────────────────────

class TestListProjects:
    def test_list_has_creator_no_git_fields(self, app):
        client, _ = app
        client.post("/api/projects", json={"name": "列表项目"})

        resp = client.get("/api/projects")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        for gone in ("repo_url", "encrypted_token", "branch", "last_sync_at", "sync_error", "token"):
            assert gone not in body, f"列表仍泄漏 {gone}"

        data = resp.get_json()["data"]
        assert len(data) == 1
        assert data[0]["creator"]["name"] == "产品桑"
        assert data[0]["is_creator"] is True


# ───────────────────────── T2.4 查看器 API（本地目录数据源） ─────────────────────────

class TestViewerAPI:
    def _make_project(self, client, projects_dir, name, files):
        resp = client.post("/api/projects", json={"name": name})
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        root = os.path.join(projects_dir, data["project_id"])
        _populate(root, files)
        return data["id"]

    def test_overview_docs_and_entries(self, app):
        """overview：prd/ 优先；原型入口列出。"""
        client, projects_dir = app
        pid = self._make_project(client, projects_dir, "查看器", {
            "prototype/index.html": "<html><body>hi</body></html>",
            "prd/a.md": "# PRD\n",
        })

        resp = client.get(f"/api/projects/{pid}/overview")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["docs"] == ["prd/a.md"]
        assert "prototype/index.html" in data["proto_entries"]

    def test_overview_root_md_compat(self, app):
        """项目根直接放 md（prd/ 缺失时）也能列出文档并读取（中文文件名）。

        T8.1 骨架必有 prd/；此处删掉空 prd/ 模拟无 prd/ 的目录（兼容分支）。
        """
        client, projects_dir = app
        pid = self._make_project(client, projects_dir, "扁平项目", {
            "灵雁思路.md": "# 灵雁\n",
        })
        slug = Project.get(Project.id == pid).project_id
        import shutil

        shutil.rmtree(os.path.join(projects_dir, slug, "prd"))

        resp = client.get(f"/api/projects/{pid}/overview")
        assert resp.get_json()["data"]["docs"] == ["灵雁思路.md"]

        resp = client.get(f"/api/projects/{pid}/prd?file=灵雁思路.md")
        assert resp.status_code == 200
        assert "# 灵雁" in resp.get_json()["data"]["content"]

    def test_prd_file_traversal_blocked(self, app):
        """prd 接口防目录穿越（.. / 绝对路径 / 越界路径全拒绝）。"""
        client, projects_dir = app
        pid = self._make_project(client, projects_dir, "穿越", {
            "prd/a.md": "# PRD\n",
        })

        for bad in ["../platform.db", "/etc/passwd", "prd/../../server/app.py", ""]:
            resp = client.get(f"/api/projects/{pid}/prd", query_string={"file": bad})
            assert resp.status_code in (400, 404), f"{bad} 未被拦截"

    def test_prd_normal_read(self, app):
        client, projects_dir = app
        pid = self._make_project(client, projects_dir, "读取", {
            "prd/a.md": "# PRD\n",
        })
        resp = client.get(f"/api/projects/{pid}/prd?file=prd/a.md")
        assert resp.status_code == 200
        assert resp.get_json()["data"]["content"] == "# PRD\n"


# ───────────────────────── T4.5 项目级「可评论」开关 ─────────────────────────

class TestUpdateProject:
    """PATCH /api/projects/{pid} {commentable}（产品方案 §4.5）。"""

    def _make_project(self, client, name: str) -> int:
        resp = client.post("/api/projects", json={"name": name})
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()["data"]["id"]

    def test_commentable_default_on_and_toggle(self, app):
        """默认开启（T2.3 建表预留）；PATCH 关→开往返落库且响应带最新值。"""
        client, _ = app
        pid = self._make_project(client, "开关项目")
        assert Project.get(Project.id == pid).commentable is True

        resp = client.patch(f"/api/projects/{pid}", json={"commentable": False})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["data"]["commentable"] is False
        assert Project.get(Project.id == pid).commentable is False

        resp = client.patch(f"/api/projects/{pid}", json={"commentable": True})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["commentable"] is True
        assert Project.get(Project.id == pid).commentable is True

    def test_commentable_validation(self, app):
        """缺字段 / 非布尔（字符串、数字——bool 是 int 子类须显式排除）/ 项目不存在。"""
        client, _ = app
        pid = self._make_project(client, "开关项目2")
        assert client.patch(f"/api/projects/{pid}", json={}).status_code == 400
        assert client.patch(f"/api/projects/{pid}", json={"commentable": "yes"}).status_code == 400
        assert client.patch(f"/api/projects/{pid}", json={"commentable": 1}).status_code == 400
        assert client.patch("/api/projects/99999", json={"commentable": True}).status_code == 404


# ───────────────────── 内容上传（T8.1 最小版）─────────────────────

def _zip_bytes(files: dict[str, str]) -> bytes:
    """内存造 zip（{相对路径: 内容}）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return buf.getvalue()


class TestUploadAPI:
    def _make_project(self, client, projects_dir, name) -> tuple:
        resp = client.post("/api/projects", json={"name": name})
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        return data["id"], os.path.join(projects_dir, data["project_id"])

    def test_upload_prd_writes_and_replaces(self, app):
        """PRD 上传：写入 prd/（保留上传文件名）+ 替换旧文档 + content_updated_at。"""
        client, projects_dir = app
        pid, root = self._make_project(client, projects_dir, "PRD项目")

        r1 = client.post(f"/api/projects/{pid}/prd", data={
            "file": (io.BytesIO("# V1\n".encode()), "需求.md"),
        }, content_type="multipart/form-data")
        assert r1.status_code == 200, r1.get_json()
        assert os.path.isfile(os.path.join(root, "prd", "需求.md"))

        # 再传一份 → 旧文档被替换（prd/ 唯一 markdown 约定）
        r2 = client.post(f"/api/projects/{pid}/prd", data={
            "file": (io.BytesIO("# V2\n".encode()), "需求V2.md"),
        }, content_type="multipart/form-data")
        assert r2.status_code == 200
        assert not os.path.exists(os.path.join(root, "prd", "需求.md"))
        assert os.path.isfile(os.path.join(root, "prd", "需求V2.md"))
        assert Project.get(Project.id == pid).content_updated_at is not None

    def test_upload_prd_rules(self, app):
        """PRD 上传：非 md 拒；文件名穿越被 basename 化；仅创建者。"""
        client, projects_dir = app
        pid, root = self._make_project(client, projects_dir, "PRD规则")
        assert client.post(f"/api/projects/{pid}/prd", data={
            "file": (io.BytesIO(b"x"), "a.txt"),
        }, content_type="multipart/form-data").status_code == 400

        # 换用户 → 403（创建者专属）
        with client.session_transaction() as sess:
            sess["uid"] = 2
        assert client.post(f"/api/projects/{pid}/prd", data={
            "file": (io.BytesIO(b"# x"), "a.md"),
        }, content_type="multipart/form-data").status_code == 403

    def test_upload_prototype_unzips(self, app):
        """原型 zip 上传：解压到 prototype/（含子目录），content_updated_at 刷新。"""
        client, projects_dir = app
        pid, root = self._make_project(client, projects_dir, "原型项目")

        resp = client.post(f"/api/projects/{pid}/prototype", data={
            "zip": (io.BytesIO(_zip_bytes({
                "index.html": "<html><body>hi</body></html>",
                "pages/login.html": "<html><body>login</body></html>",
            })), "proto.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 200, resp.get_json()
        assert os.path.isfile(os.path.join(root, "prototype", "index.html"))
        assert os.path.isfile(os.path.join(root, "prototype", "pages", "login.html"))
        assert Project.get(Project.id == pid).content_updated_at is not None

    def test_upload_prototype_zip_slip_rejected(self, app):
        """zip-slip 条目拒绝：400 + 旧 prototype/ 原样保留（校验过才替换）。"""
        client, projects_dir = app
        pid, root = self._make_project(client, projects_dir, "穿越项目")
        # 先传一版正常内容
        client.post(f"/api/projects/{pid}/prototype", data={
            "zip": (io.BytesIO(_zip_bytes({"index.html": "<html>v1</html>"})), "p.zip"),
        }, content_type="multipart/form-data")

        evil = _zip_bytes({"../../evil.txt": "pwned"})
        resp = client.post(f"/api/projects/{pid}/prototype", data={
            "zip": (io.BytesIO(evil), "evil.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "路径穿越" in resp.get_json()["msg"]
        # 旧版本保留 + 攻击文件未落盘
        assert open(os.path.join(root, "prototype", "index.html"), encoding="utf-8").read() == "<html>v1</html>"
        assert not os.path.exists(os.path.join(root, "..", "..", "evil.txt"))
        # 临时目录不残留
        assert not os.path.exists(os.path.join(root, ".prototype-tmp"))

    def test_upload_prototype_not_zip_rejected(self, app):
        """非 zip 内容拒绝：400。"""
        client, projects_dir = app
        pid, _ = self._make_project(client, projects_dir, "非zip项目")
        resp = client.post(f"/api/projects/{pid}/prototype", data={
            "zip": (io.BytesIO(b"not a zip at all"), "p.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "zip" in resp.get_json()["msg"]

    def test_upload_prototype_creator_only(self, app):
        """原型上传仅创建者：其他登录用户 403。"""
        client, projects_dir = app
        pid, _ = self._make_project(client, projects_dir, "权限项目")
        with client.session_transaction() as sess:
            sess["uid"] = 2
        resp = client.post(f"/api/projects/{pid}/prototype", data={
            "zip": (io.BytesIO(_zip_bytes({"index.html": "x"})), "p.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 403
        assert "仅项目创建者" in resp.get_json()["msg"]
