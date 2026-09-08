"""T9.1 项目协作单测：权限矩阵 V2 逐格断言（PRD §6.1 / AGENTS.md §6）。

四角色 × 全部操作的矩阵验证：
- 创建者：全部操作 ✓
- 协作者：上传/导出/开关/任意评论管理/状态流转 ✓；管理协作者 ✗、删项目 ✗
- 超管：管协作者 ✓、删任意项目 ✓；不上传/不导出/不管评论状态（✗）
- 普通用户：仅浏览 + 自己的评论

外加：members API 全链路（加人/重复/移除/权限）、is_manager 字段、
移除即时性、项目删除级联清理。
"""
import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.app import create_app  # noqa: E402
from server.models import (  # noqa: E402
    Comment,
    Project,
    ProjectMember,
    User,
    db,
    init_tables,
)

# 用户固定 id：1=创建者(pm)、2=协作者(collab)、3=普通用户(other)、999=超管(admin)
U_CREATOR, U_COLLAB, U_OTHER, U_ADMIN = 1, 2, 3, 999


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
        User.create(id=U_CREATOR, email="pm@corp.com", name="创建者桑")
        User.create(id=U_COLLAB, email="collab@corp.com", name="协作者林")
        User.create(id=U_OTHER, email="other@corp.com", name="路人甲")
        User.create(id=U_ADMIN, email="boss@corp.com", name="超管", is_admin=True)
        with app.test_client() as c:
            _login(c, U_CREATOR, "pm@corp.com", "创建者桑")
            yield c, str(projects_dir)


def _login(client, uid: int, email: str, name: str):
    with client.session_transaction() as sess:
        sess["uid"] = uid
        sess["email"] = email
        sess["name"] = name


def _make_project(client, name="协作项目") -> tuple[int, str]:
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 200, resp.get_json()
    d = resp.get_json()["data"]
    return d["id"], d["project_id"]


def _add_collab(client, pid: int, email="collab@corp.com"):
    return client.post(f"/api/projects/{pid}/members", json={"email": email})


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return buf.getvalue()


def _mk_comment(pid: int, cid="c-20260907-001") -> str:
    """直接落一条他人评论（模拟路人甲已提交）。"""
    import json

    payload = {
        "comment_id": cid,
        "author": "路人甲",
        "status": "待确认",
        "content": "测试评论",
        "target_type": "dom",
        "prototype_page": "index.html",
        "anchor_id": "login-form",
        "created_at": "2026-09-07T00:00:00Z",
    }
    Comment.create(
        comment_id=cid,
        project=pid,
        author_email="other@corp.com",
        author_name="路人甲",
        status="待确认",
        target_type="dom",
        prototype_page="index.html",
        anchor_id="login-form",
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    return cid


# ───────────────────────── members API 全链路 ─────────────────────────

class TestMembersAPI:
    def test_add_and_list_and_remove(self, app):
        """创建者：加人 → 列表含姓名/邮箱/添加人 → 移除 → 列表空。"""
        client, _ = app
        pid, _ = _make_project(client)
        resp = _add_collab(client, pid)
        assert resp.status_code == 200, resp.get_json()
        m = resp.get_json()["data"]
        assert m["name"] == "协作者林"
        assert m["email"] == "collab@corp.com"
        assert m["added_by"] == "创建者桑"

        resp = client.get(f"/api/projects/{pid}/members")
        assert resp.status_code == 200
        rows = resp.get_json()["data"]
        assert len(rows) == 1 and rows[0]["user_id"] == U_COLLAB

        resp = client.delete(f"/api/projects/{pid}/members/{U_COLLAB}")
        assert resp.status_code == 200
        assert client.get(f"/api/projects/{pid}/members").get_json()["data"] == []

    def test_add_duplicate_rejected(self, app):
        client, _ = app
        pid, _ = _make_project(client)
        assert _add_collab(client, pid).status_code == 200
        resp = _add_collab(client, pid)
        assert resp.status_code == 400
        assert "已是本项目协作者" in resp.get_json()["msg"]

    def test_add_unknown_email_404(self, app):
        client, _ = app
        pid, _ = _make_project(client)
        resp = _add_collab(client, pid, email="nobody@corp.com")
        assert resp.status_code == 404

    def test_add_creator_self_rejected(self, app):
        """创建者本人天然管理者，不可也不需添加。"""
        client, _ = app
        pid, _ = _make_project(client)
        resp = _add_collab(client, pid, email="pm@corp.com")
        assert resp.status_code == 400

    def test_add_by_collab_forbidden(self, app):
        """协作者不能管理协作者（矩阵：管理协作者仅创建者/超管）。"""
        client, _ = app
        pid, _ = _make_project(client)
        assert _add_collab(client, pid).status_code == 200
        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        resp = _add_collab(client, pid, email="other@corp.com")
        assert resp.status_code == 403
        # 移除同样 403
        resp = client.delete(f"/api/projects/{pid}/members/{U_CREATOR}")
        assert resp.status_code == 403

    def test_add_by_admin_allowed(self, app):
        """超管可为任意项目管理协作者。"""
        client, _ = app
        pid, _ = _make_project(client)
        _login(client, U_ADMIN, "boss@corp.com", "超管")
        resp = _add_collab(client, pid)
        assert resp.status_code == 200
        m = resp.get_json()["data"]
        assert m["added_by"] == "超管"

    def test_members_list_by_participant_forbidden(self, app):
        """普通参与者看不了协作者列表（403）。"""
        client, _ = app
        pid, _ = _make_project(client)
        _login(client, U_OTHER, "other@corp.com", "路人甲")
        assert client.get(f"/api/projects/{pid}/members").status_code == 403

    def test_members_list_by_collab_allowed(self, app):
        """协作者可见协作者列表（GET 放宽到管理者）。"""
        client, _ = app
        pid, _ = _make_project(client)
        assert _add_collab(client, pid).status_code == 200
        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        assert client.get(f"/api/projects/{pid}/members").status_code == 200


# ───────────────────────── 权限矩阵逐格 ─────────────────────────

class TestPermissionMatrix:
    """矩阵按操作逐格断言：每格验证协作者 ✓ 与关键 ✗ 格。"""

    def _setup_with_collab(self, client):
        pid, _ = _make_project(client)
        assert _add_collab(client, pid).status_code == 200
        return pid

    # ── 上传原型 / 上传 PRD（创建者 ✓ / 协作者 ✓ / 超管 ✗ / 路人 ✗）──

    def test_upload_prototype_matrix(self, app):
        client, _ = app
        pid = self._setup_with_collab(client)

        # 协作者 ✓
        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        resp = client.post(f"/api/projects/{pid}/prototype", data={
            "zip": (io.BytesIO(_zip_bytes({"index.html": "x"})), "p.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 200, resp.get_json()

        # 超管 ✗（不参与内容生产）
        _login(client, U_ADMIN, "boss@corp.com", "超管")
        resp = client.post(f"/api/projects/{pid}/prototype", data={
            "zip": (io.BytesIO(_zip_bytes({"index.html": "x"})), "p.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 403

        # 路人 ✗
        _login(client, U_OTHER, "other@corp.com", "路人甲")
        resp = client.post(f"/api/projects/{pid}/prototype", data={
            "zip": (io.BytesIO(_zip_bytes({"index.html": "x"})), "p.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 403

    def test_upload_prd_matrix(self, app):
        client, _ = app
        pid = self._setup_with_collab(client)

        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        resp = client.post(f"/api/projects/{pid}/prd", data={
            "file": (io.BytesIO(b"# v2"), "需求.md"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 200, resp.get_json()

        _login(client, U_ADMIN, "boss@corp.com", "超管")
        resp = client.post(f"/api/projects/{pid}/prd", data={
            "file": (io.BytesIO(b"# v2"), "需求.md"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 403

    # ── 导出评论（创建者 ✓ / 协作者 ✓ / 路人 ✗）──

    def test_export_comments_matrix(self, app):
        client, _ = app
        pid = self._setup_with_collab(client)

        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        resp = client.get(f"/api/projects/{pid}/comments/export?scope=all")
        assert resp.status_code == 200

        _login(client, U_OTHER, "other@corp.com", "路人甲")
        resp = client.get(f"/api/projects/{pid}/comments/export?scope=all")
        assert resp.status_code == 403

    # ── 可评论开关（创建者 ✓ / 协作者 ✓ / 路人 ✗）──

    def test_commentable_toggle_matrix(self, app):
        client, _ = app
        pid = self._setup_with_collab(client)

        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        resp = client.patch(f"/api/projects/{pid}", json={"commentable": False})
        assert resp.status_code == 200
        assert Project.get(Project.id == pid).commentable is False
        # 还原，避免影响后续
        client.patch(f"/api/projects/{pid}", json={"commentable": True})

        _login(client, U_OTHER, "other@corp.com", "路人甲")
        resp = client.patch(f"/api/projects/{pid}", json={"commentable": False})
        assert resp.status_code == 403

    # ── 编辑评论（T10.3 收紧：仅作者本人，创建者/协作者也不可编辑他人）──

    def test_edit_own_comment_only_matrix(self, app):
        """T10.3（2026-09-07 用户决策）：编辑他人评论内容一律 403——
        创建者/协作者/超管都不例外（编辑留痕完整性优先）；
        作者本人编辑自己的评论规则不变（限待确认/已确认态）。"""
        client, _ = app
        pid = self._setup_with_collab(client)
        cid = _mk_comment(pid)  # 作者=路人甲(other@corp.com)

        # 创建者 ✗ 编辑他人评论（T10.3 收紧，原 V2 允许）
        _login(client, U_CREATOR, "pm@corp.com", "创建者桑")
        resp = client.patch(f"/api/comments/{cid}", json={"content": "创建者改的"})
        assert resp.status_code == 403, resp.get_json()

        # 协作者 ✗ 编辑他人评论（T10.3 收紧，原 V2 允许）
        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        resp = client.patch(f"/api/comments/{cid}", json={"content": "协作者改的"})
        assert resp.status_code == 403, resp.get_json()

        # 超管 ✗ 编辑他人评论
        _login(client, U_ADMIN, "boss@corp.com", "超管")
        resp = client.patch(f"/api/comments/{cid}", json={"content": "超管改的"})
        assert resp.status_code == 403

        # 路人（作者本人）✓ 编辑自己的待确认评论（既有规则不变）
        _login(client, U_OTHER, "other@corp.com", "路人甲")
        resp = client.patch(f"/api/comments/{cid}", json={"content": "作者改自己的"})
        assert resp.status_code == 200

    def test_delete_any_comment_matrix(self, app):
        client, _ = app
        pid = self._setup_with_collab(client)
        cid = _mk_comment(pid, "c-20260907-002")

        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        resp = client.delete(f"/api/comments/{cid}")
        assert resp.status_code == 200, resp.get_json()

    # ── 批量状态流转（创建者 ✓ / 协作者 ✓ / 路人 ✗）──

    def test_batch_status_matrix(self, app):
        client, _ = app
        pid = self._setup_with_collab(client)
        cid = _mk_comment(pid, "c-20260907-003")

        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        resp = client.post("/api/comments/batch-status", json={
            "cids": [cid], "status": "已确认待修改",
        })
        assert resp.status_code == 200
        assert resp.get_json()["data"]["updated"] == [cid]
        assert Comment.get(Comment.comment_id == cid).status == "已确认待修改"

        _login(client, U_OTHER, "other@corp.com", "路人甲")
        resp = client.post("/api/comments/batch-status", json={
            "cids": [cid], "status": "已修改",
        })
        assert resp.status_code == 200
        assert resp.get_json()["data"]["updated"] == []
        assert resp.get_json()["data"]["skipped"][0]["reason"].startswith("仅项目管理者")

    # ── 删除项目（创建者 ✓ / 协作者 ✗ / 超管 ✓）──

    def test_delete_project_matrix(self, app):
        client, _ = app
        # 协作者 ✗
        pid, slug = _make_project(client, "删项目A")
        assert _add_collab(client, pid).status_code == 200
        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 403
        assert Project.get_or_none(Project.id == pid) is not None
        _login(client, U_CREATOR, "pm@corp.com", "创建者桑")

        # 超管 ✓（他人创建的也能删）
        pid2, slug2 = _make_project(client, "删项目B")
        _login(client, U_ADMIN, "boss@corp.com", "超管")
        resp = client.delete(f"/api/projects/{pid2}")
        assert resp.status_code == 200
        assert Project.get_or_none(Project.id == pid2) is None


# ───────────────────────── 字段扩展与即时性 ─────────────────────────

class TestManagerFieldsAndTimeliness:
    def test_project_public_fields(self, app):
        """/api/projects 返回 is_manager 与 member_count。"""
        client, _ = app
        pid, _ = _make_project(client)
        assert _add_collab(client, pid).status_code == 200

        # 创建者视角
        rows = client.get("/api/projects").get_json()["data"]
        mine = next(r for r in rows if r["id"] == pid)
        assert mine["is_creator"] is True and mine["is_manager"] is True
        assert mine["member_count"] == 1

        # 协作者视角
        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        rows = client.get("/api/projects").get_json()["data"]
        mine = next(r for r in rows if r["id"] == pid)
        assert mine["is_creator"] is False and mine["is_manager"] is True

        # 路人视角
        _login(client, U_OTHER, "other@corp.com", "路人甲")
        rows = client.get("/api/projects").get_json()["data"]
        mine = next(r for r in rows if r["id"] == pid)
        assert mine["is_creator"] is False and mine["is_manager"] is False

    def test_removal_takes_effect_immediately(self, app):
        """移除协作者后，下一次写操作立即 403（逐请求校验）。"""
        client, _ = app
        pid, _ = _make_project(client)
        assert _add_collab(client, pid).status_code == 200

        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        assert client.patch(f"/api/projects/{pid}", json={"commentable": True}).status_code == 200

        _login(client, U_CREATOR, "pm@corp.com", "创建者桑")
        assert client.delete(f"/api/projects/{pid}/members/{U_COLLAB}").status_code == 200

        _login(client, U_COLLAB, "collab@corp.com", "协作者林")
        resp = client.patch(f"/api/projects/{pid}", json={"commentable": False})
        assert resp.status_code == 403

    def test_delete_project_cascades_members(self, app):
        """项目删除 → project_members 级联清理。"""
        client, _ = app
        pid, _ = _make_project(client)
        assert _add_collab(client, pid).status_code == 200
        assert (
            ProjectMember.select().where(ProjectMember.project == pid).count() == 1
        )
        resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 200
        assert (
            ProjectMember.select().where(ProjectMember.project == pid).count() == 0
        )

    def test_existing_creator_permissions_unchanged(self, app):
        """存量兼容：无协作者时创建者/路人行为与一期完全一致。"""
        client, _ = app
        pid, _ = _make_project(client)
        # 创建者照常可开关
        assert client.patch(
            f"/api/projects/{pid}", json={"commentable": False}
        ).status_code == 200
        client.patch(f"/api/projects/{pid}", json={"commentable": True})
        # 路人照常 403
        _login(client, U_OTHER, "other@corp.com", "路人甲")
        assert client.patch(
            f"/api/projects/{pid}", json={"commentable": False}
        ).status_code == 403
