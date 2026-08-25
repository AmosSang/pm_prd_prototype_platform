"""评论测试（T4.1 schema 契约 + T4.2 提交链路；T8.1 去 Git 本地化修订）。

契约锚点：《产品方案-V1.md》§3.3 评论数据结构（DOM 定位字段组）+
《一期技术实现方案-V1.md》§2.3 payload 采集表。
任务卡验收：T4.1 payload 字段完整性；T4.2 三类评论提交后
DB 与 reviews/ 文件均出现（API 级验证，界面级走 E2E）。
T8.1：落仓 = 直写项目目录（无队列），提交返回即断言文件存在；
截图从临时区复制进项目 reviews/shots/。
"""
import datetime as dt
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.reviews import validate_comment_payload  # noqa: E402


def dt_str_today() -> str:
    """当日 YYYYMMDD（comment_id 前缀用）。"""
    return dt.date.today().strftime("%Y%m%d")

# 产品方案 §3.3 JSON 示例的 DOM 定位部分——契约基准样例，schema 必须放行
BASE_PAYLOAD = {
    "target_type": "dom",
    "prototype_page": "pages/login.html",
    "anchor_id": "login-captcha",
    "nearest_anchor_id": "login-form",
    "css_path": "#login-form > .form-group:nth-child(3) > input",
    "outer_html": '<div class="form-group"><input type="text" name="captcha"></div>',
    "text_excerpt": "验证码输入框",
    "interaction_state": {
        "modal_open": False,
        "viewport": "1440x900",
        "scroll_y": 320,
        "route": "pages/login.html",
    },
}


def test_contract_sample_valid():
    """契约基准样例（产品方案 §3.3 示例）必须合法。"""
    assert validate_comment_payload(BASE_PAYLOAD) == []


def test_missing_each_required_field():
    """每个契约字段缺失都须报错（字段完整性兜底）。"""
    for field in [
        "target_type",
        "prototype_page",
        "anchor_id",
        "nearest_anchor_id",
        "css_path",
        "outer_html",
        "text_excerpt",
        "interaction_state",
    ]:
        payload = dict(BASE_PAYLOAD)
        del payload[field]
        errors = validate_comment_payload(payload)
        assert any(field in e for e in errors), f"缺 {field} 未被检出"


def test_target_type_enum():
    payload = dict(BASE_PAYLOAD, target_type="element")
    assert validate_comment_payload(payload)
    # 三类宿主均合法
    for tt in ("dom", "page", "doc_block"):
        assert validate_comment_payload(dict(BASE_PAYLOAD, target_type=tt)) == []


def test_str_field_type_mismatch():
    payload = dict(BASE_PAYLOAD, css_path=123)
    assert validate_comment_payload(payload)


def test_dom_requires_page_and_css_path():
    """dom/page 评论必须有原型侧定位；doc_block（文档评论）允许为空。"""
    payload = dict(BASE_PAYLOAD, prototype_page="")
    assert validate_comment_payload(payload)
    payload = dict(BASE_PAYLOAD, css_path="")
    assert validate_comment_payload(payload)
    payload = dict(BASE_PAYLOAD, target_type="doc_block", prototype_page="", css_path="")
    assert validate_comment_payload(payload) == []


def test_empty_anchor_ids_valid():
    """未命中锚点区域是合法场景（anchor_id / nearest_anchor_id 允许空串）。"""
    payload = dict(BASE_PAYLOAD, anchor_id="", nearest_anchor_id="")
    assert validate_comment_payload(payload) == []


def test_interaction_state_subfields():
    for bad in [
        {"modal_open": "no"},  # 非布尔
        {"viewport": 1440},  # 非字符串
        {"scroll_y": "320"},  # 非整数
        {"scroll_y": True},  # bool 是 int 子类，须显式排除
        {"route": None},  # 非字符串
    ]:
        ist = dict(BASE_PAYLOAD["interaction_state"])
        ist.update(bad)
        payload = dict(BASE_PAYLOAD, interaction_state=ist)
        assert validate_comment_payload(payload), f"{bad} 未被检出"
    # 缺子字段
    ist = dict(BASE_PAYLOAD["interaction_state"])
    del ist["modal_open"]
    assert validate_comment_payload(dict(BASE_PAYLOAD, interaction_state=ist))


def test_viewport_format():
    payload = dict(BASE_PAYLOAD)
    payload["interaction_state"] = dict(BASE_PAYLOAD["interaction_state"], viewport="1440×900")
    assert validate_comment_payload(payload)
    payload["interaction_state"] = dict(BASE_PAYLOAD["interaction_state"], viewport="")
    assert validate_comment_payload(payload)


def test_length_limits():
    """bridge 截断后的长度合法；超限拒绝（防超长注入）。"""
    payload = dict(BASE_PAYLOAD, outer_html="x" * 5000)
    assert validate_comment_payload(payload)
    payload = dict(BASE_PAYLOAD, text_excerpt="x" * 500)
    assert validate_comment_payload(payload)
    # bridge 截断产物（截到 4096 含省略标记）
    payload = dict(BASE_PAYLOAD, outer_html="x" * 4091 + "…(截断)")
    assert len(payload["outer_html"]) == 4096
    assert validate_comment_payload(payload) == []


def test_non_dict_rejected():
    assert validate_comment_payload(None)
    assert validate_comment_payload("payload")
    assert validate_comment_payload([BASE_PAYLOAD])


# ═══════════════════════ T4.2 提交链路（POST /comments）═══════════════════════

from server.app import create_app  # noqa: E402
from server.models import Comment, Project, db  # noqa: E402
from tests.conftest import dom_payload as _dom_payload  # noqa: E402
from tests.conftest import make_local_project  # noqa: E402
from tests.conftest import submit_comment as _submit  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db.close()
    db.init(str(tmp_path / "test.db"))
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr("server.storage.PROJECTS_DIR", str(projects_dir))
    monkeypatch.setattr("server.reviews.SHOTS_DIR", str(tmp_path / "shots"))

    app = create_app()
    app.config["TESTING"] = True
    app.secret_key = "test-secret"
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["uid"] = 1
            sess["email"] = "pm@corp.com"
            sess["name"] = "产品桑"
        yield c, str(projects_dir), str(tmp_path / "shots")
    db.close()


@pytest.fixture()
def project(app):
    """建项目 + 填充带锚点内容（模拟 T8.2 上传），返回 (client, project_row)。"""
    client, projects_dir, _ = app
    resp = client.post("/api/projects", json={"name": "评论单测项目"})
    assert resp.status_code == 200, resp.get_json()
    p = Project.get(Project.name == "评论单测项目")
    make_local_project(projects_dir, p.project_id)
    return client, p


def _cj_path(projects_dir, p, cid) -> Path:
    return Path(projects_dir) / p.project_id / "reviews" / "comments" / f"{cid}.json"


def _read_cj(projects_dir, p, cid) -> dict:
    return json.loads(_cj_path(projects_dir, p, cid).read_text(encoding="utf-8"))


class TestCreateComment:
    def test_dom_comment_full_chain(self, app, project):
        """DOM 评论全链路：DB + reviews/ 文件直写（提交返回即存在）+ doc 锚点匹配。"""
        client, p = project
        _, projects_dir, _ = app

        resp = _submit(client, p, _dom_payload(), priority="P1", scope="both")
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        cid = data["comment_id"]
        assert cid.startswith("c-")
        assert data["status"] == "待确认"
        assert data["author"] == "产品桑"
        # T8.1：无 git 落仓任务，响应不带 git_task 字段
        assert "git_task" not in data

        # DB 落库（展示缓存）
        row = Comment.get(Comment.comment_id == cid)
        assert row.project.id == p.id
        assert row.author_email == "pm@corp.com"
        cj = json.loads(row.payload_json)
        assert cj["content"] == "测试评论内容"
        assert cj["priority"] == "P1"

        # reviews/ 文件直写（事实源，同步完成）
        fj = _read_cj(projects_dir, p, cid)
        assert fj["comment_id"] == cid
        assert fj["status"] == "待确认"
        assert fj["anchor_id"] == "login-account"
        # doc 匹配：候选锚点 login-account 命中 PRD 锚点
        assert fj["doc_anchor_id"] == "login-account"
        assert "账号输入" in fj["doc_excerpt"]
        assert fj["doc_file"] == "prd/需求.md"

    def test_shot_copied_into_project(self, app, project):
        """截图从临时区复制进项目 reviews/shots/（T8.1：导出包天然同构）。"""
        client, p = project
        _, projects_dir, shots_dir = app
        # 造临时截图（模拟 /api/projects/{slug}/shots 上传产物）
        tmp_shot = Path(shots_dir) / p.project_id / "shot-abc.png"
        tmp_shot.parent.mkdir(parents=True, exist_ok=True)
        tmp_shot.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        resp = _submit(client, p, _dom_payload(), shot_id="shot-abc",
                       highlight_rect={"x": 1, "y": 2, "w": 3, "h": 4})
        assert resp.status_code == 200, resp.get_json()
        cid = resp.get_json()["data"]["comment_id"]

        root = Path(projects_dir) / p.project_id
        assert (root / "reviews" / "shots" / f"{cid}.png").read_bytes() == tmp_shot.read_bytes()
        fj = _read_cj(projects_dir, p, cid)
        assert fj["screenshot"] == f"shots/{cid}.png"
        assert fj["highlight_rect"] == {"x": 1, "y": 2, "w": 3, "h": 4}

    def test_dom_comment_without_anchor_no_doc_link(self, app, project):
        """非锚点区域（候选锚点查 PRD 未命中）→ 标记「无 PRD 锚点关联」。"""
        client, p = project
        _, projects_dir, _ = app
        resp = _submit(client, p, _dom_payload(anchor_id="", nearest_anchor_id=""))
        assert resp.status_code == 200
        cid = resp.get_json()["data"]["comment_id"]
        fj = _read_cj(projects_dir, p, cid)
        assert fj["doc_anchor_id"] == ""
        assert fj["doc_note"] == "无 PRD 锚点关联"

    def test_page_comment(self, app, project):
        """页面评论（target_type=page）：正常提交落文件；outer_html 为空
        （T4.2 修订：页面根的整页 HTML 无定位意义，bridge 不采集）。"""
        client, p = project
        _, projects_dir, _ = app
        resp = _submit(client, p, _dom_payload(target_type="page", anchor_id="",
                                               nearest_anchor_id="", css_path="body",
                                               outer_html=""))
        assert resp.status_code == 200
        cid = resp.get_json()["data"]["comment_id"]
        fj = _read_cj(projects_dir, p, cid)
        assert fj["target_type"] == "page"
        assert fj["css_path"] == "body"
        assert fj["outer_html"] == ""

    def test_doc_comment_with_fingerprint(self, app, project):
        """文档评论（doc_block）：fingerprint = sha1(doc_path|excerpt)[:16]。"""
        client, p = project
        _, projects_dir, _ = app
        import hashlib

        resp = _submit(client, p, _dom_payload(
            target_type="doc_block", prototype_page="", anchor_id="",
            nearest_anchor_id="", css_path="", outer_html="",
            text_excerpt="账号输入：支持手机号",
            doc_anchor_id="login-account", doc_excerpt="账号输入：支持手机号",
        ), scope="doc")
        assert resp.status_code == 200
        cid = resp.get_json()["data"]["comment_id"]
        fj = _read_cj(projects_dir, p, cid)
        assert fj["target_type"] == "doc_block"
        assert fj["doc_anchor_id"] == "login-account"
        # 服务端按 PRD 命中结果计算指纹（doc_path 来自锚点标题链）
        expect = hashlib.sha1("5.1 登录页|账号输入：支持手机号".encode()).hexdigest()[:16]
        assert fj["doc_block_fingerprint"] == expect
        assert "screenshot" not in fj  # 文档评论无截图

    def test_doc_comment_without_anchor_fingerprint(self, app, project):
        """无锚点段落也可评论（T4.2 修订）：doc_anchor_id 空，指纹用前端
        现采的 doc_path（标题链）+ doc_excerpt 计算。"""
        client, p = project
        _, projects_dir, _ = app
        import hashlib

        resp = _submit(client, p, _dom_payload(
            target_type="doc_block", prototype_page="", anchor_id="",
            nearest_anchor_id="", css_path="", outer_html="",
            text_excerpt="这段没有锚点，验证任意段落可评论。",
            doc_anchor_id="", doc_excerpt="这段没有锚点，验证任意段落可评论。",
            doc_path="5.1 登录页",
        ), scope="doc")
        assert resp.status_code == 200
        cid = resp.get_json()["data"]["comment_id"]
        fj = _read_cj(projects_dir, p, cid)
        assert fj["doc_anchor_id"] == ""
        expect = hashlib.sha1("5.1 登录页|这段没有锚点，验证任意段落可评论。".encode()).hexdigest()[:16]
        assert fj["doc_block_fingerprint"] == expect
        # 无锚点是正常场景，不标「无 PRD 锚点关联」（那是 DOM 评论的派生规则）
        assert "doc_note" not in fj

    def test_comment_id_daily_sequence(self, app, project):
        """同日两条：序号递增 001 → 002。"""
        client, p = project
        r1 = _submit(client, p, _dom_payload())
        r2 = _submit(client, p, _dom_payload(anchor_id="", nearest_anchor_id=""))
        c1 = r1.get_json()["data"]["comment_id"]
        c2 = r2.get_json()["data"]["comment_id"]
        assert c1.endswith("-001")
        assert c2.endswith("-002")

    def test_comment_id_fills_gaps(self, app, project):
        """cid 空洞场景：高位号已占（软删行/他项目），新评论填第一个空位，
        不与已占用冲突（count+1 策略的实测 bug：004-007+008-010 并存时
        count=7 → 008 撞已有行）。"""
        client, p = project
        # 预置：001-002 存在，003 缺失，004-005 存在（含一条软删行）
        def _row(cid: str, deleted: bool = False) -> None:
            Comment.create(
                comment_id=cid, project=p.id,
                author_email="pm@corp.com", author_name="产品桑",
                status="待确认", priority="P2", scope="prototype", target_type="dom",
                payload_json="{}", deleted=deleted,
            )

        _row(f"c-{dt_str_today()}-001")
        _row(f"c-{dt_str_today()}-002")
        _row(f"c-{dt_str_today()}-004")
        _row(f"c-{dt_str_today()}-005", deleted=True)  # 软删行也占用 cid

        resp = _submit(client, p, _dom_payload())
        assert resp.status_code == 200, resp.get_json()
        cid = resp.get_json()["data"]["comment_id"]
        assert cid.endswith("-003")  # 填第一个空位，不撞 004/005（软删行也占用）

    def test_invalid_payload_rejected(self, app, project):
        """payload 缺字段：400 且 DB 无行。"""
        client, p = project
        bad = _dom_payload()
        del bad["css_path"]
        resp = _submit(client, p, bad)
        assert resp.status_code == 400
        assert Comment.select().count() == 0

    def test_empty_content_rejected(self, app, project):
        client, p = project
        resp = _submit(client, p, _dom_payload(), content="  ")
        assert resp.status_code == 400
        assert Comment.select().count() == 0

    def test_bad_priority_scope_rejected(self, app, project):
        client, p = project
        assert _submit(client, p, _dom_payload(), priority="P0").status_code == 400
        assert _submit(client, p, _dom_payload(), scope="everywhere").status_code == 400

    def test_missing_shot_rejected(self, app, project):
        """shot_id 对应截图不存在：400（客户端流程 bug 才会发生）。"""
        client, p = project
        resp = _submit(client, p, _dom_payload(), shot_id="shot-not-exist")
        assert resp.status_code == 400
        assert Comment.select().count() == 0

    def test_commentable_off_rejected(self, app, project):
        """项目可评论开关关闭：拒绝提交（T4.5 完整接入，接口侧先兜底）。"""
        client, p = project
        p.commentable = False
        p.save()
        resp = _submit(client, p, _dom_payload())
        assert resp.status_code == 400
        assert Comment.select().count() == 0


# ═══════════════════════ T4.4 列表 / 编辑 / 删除 / 批量状态 ═══════════════════════


def _submit_simple(client, p, **over) -> str:
    """快捷提交一条 dom 评论，返回 comment_id（T8.1：直写文件，无队列等待）。"""
    resp = _submit(client, p, _dom_payload(**over))
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["data"]["comment_id"]


class TestListComments:
    def test_list_fields_and_filters(self, app, project):
        """列表：字段完整 + 筛选参数（status/target_type）+ 软删不返回。"""
        client, p = project
        c1 = _submit_simple(client, p)                       # dom 待确认
        c2 = _submit_simple(client, p, anchor_id="", nearest_anchor_id="")  # dom 无锚点
        c3 = _submit_simple(client, p, target_type="page", anchor_id="",
                            nearest_anchor_id="", css_path="body", outer_html="")

        resp = client.get(f"/api/projects/{p.id}/comments")
        assert resp.status_code == 200
        items = resp.get_json()["data"]
        assert [i["comment_id"] for i in items] == [c1, c2, c3]
        item = items[0]
        assert item["status"] == "待确认"
        assert item["target_type"] == "dom"
        assert item["prototype_page"] == "index.html"
        assert item["anchor_id"] == "login-account"
        assert item["payload"]["content"] == "测试评论内容"

        # 筛选：target_type
        resp = client.get(f"/api/projects/{p.id}/comments?target_type=page")
        assert [i["comment_id"] for i in resp.get_json()["data"]] == [c3]
        # 筛选：status（先流转 c1 再验）
        client.post("/api/comments/batch-status", json={"cids": [c1], "action": "confirm"})
        resp = client.get(f"/api/projects/{p.id}/comments?status=已确认待修改")
        assert [i["comment_id"] for i in resp.get_json()["data"]] == [c1]

        # 软删不返回
        client.delete(f"/api/comments/{c2}")
        resp = client.get(f"/api/projects/{p.id}/comments")
        assert [i["comment_id"] for i in resp.get_json()["data"]] == [c1, c3]


class TestBatchStatus:
    def test_confirm_full_chain(self, app, project):
        """批量确认（任务卡验收）：DB 流转 + 评论 JSON 直写 status 变更。"""
        client, p = project
        _, projects_dir, _ = app
        c1 = _submit_simple(client, p)
        c2 = _submit_simple(client, p, anchor_id="", nearest_anchor_id="")

        resp = client.post("/api/comments/batch-status", json={
            "cids": [c1, c2], "action": "confirm",
        })
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["to"] == "已确认待修改"
        assert sorted(data["updated"]) == sorted([c1, c2])
        assert data["skipped"] == []

        # DB 流转 + 文件直写（T8.1：无队列，响应返回即完成）
        for cid in (c1, c2):
            assert Comment.get(Comment.comment_id == cid).status == "已确认待修改"
            fj = _read_cj(projects_dir, p, cid)
            assert fj["status"] == "已确认待修改"

    def test_ignore_from_both_states(self, app, project):
        """忽略：待确认与已确认待修改都可忽略（旁路）。"""
        client, p = project
        c1 = _submit_simple(client, p)
        c2 = _submit_simple(client, p, anchor_id="", nearest_anchor_id="")
        client.post("/api/comments/batch-status", json={"cids": [c2], "action": "confirm"})

        resp = client.post("/api/comments/batch-status", json={
            "cids": [c1, c2], "action": "ignore",
        })
        data = resp.get_json()["data"]
        assert sorted(data["updated"]) == sorted([c1, c2])
        for cid in (c1, c2):
            assert Comment.get(Comment.comment_id == cid).status == "忽略"

    def test_invalid_transitions_skipped(self, app, project):
        """状态机：已忽略不可再 confirm/ignore（跳过并报告）。"""
        client, p = project
        c1 = _submit_simple(client, p)
        client.post("/api/comments/batch-status", json={"cids": [c1], "action": "ignore"})

        resp = client.post("/api/comments/batch-status", json={"cids": [c1], "action": "confirm"})
        data = resp.get_json()["data"]
        assert data["updated"] == []
        assert len(data["skipped"]) == 1
        assert "不可confirm" in data["skipped"][0]["reason"]

        # 不存在的 cid 跳过
        resp = client.post("/api/comments/batch-status", json={
            "cids": ["c-20990101-999"], "action": "confirm",
        })
        assert resp.get_json()["data"]["skipped"][0]["reason"] == "不存在"

    def test_bad_request_rejected(self, app, project):
        client, p = project
        assert client.post("/api/comments/batch-status", json={
            "cids": ["x"], "action": "reopen",
        }).status_code == 400
        assert client.post("/api/comments/batch-status", json={
            "cids": [], "action": "confirm",
        }).status_code == 400


class TestEditComment:
    def test_edit_by_author(self, app, project):
        """作者编辑：DB + payload 更新，项目目录评论 JSON 直写同步。"""
        client, p = project
        _, projects_dir, _ = app
        c1 = _submit_simple(client, p)

        resp = client.patch(f"/api/comments/{c1}", json={
            "content": "改后的评论内容", "priority": "P1", "scope": "both",
        })
        assert resp.status_code == 200, resp.get_json()
        row = Comment.get(Comment.comment_id == c1)
        assert row.priority == "P1"
        assert row.scope == "both"
        assert json.loads(row.payload_json)["content"] == "改后的评论内容"

        fj = _read_cj(projects_dir, p, c1)
        assert fj["content"] == "改后的评论内容"
        assert fj["priority"] == "P1"

    def test_edit_rules(self, app, project):
        """编辑规则：非作者拒；已确认待修改可编辑；忽略态拒；空内容拒。"""
        client, p = project
        c1 = _submit_simple(client, p)

        # 已确认待修改 → 可编辑
        client.post("/api/comments/batch-status", json={"cids": [c1], "action": "confirm"})
        assert client.patch(f"/api/comments/{c1}", json={"content": "确认后编辑"}).status_code == 200

        # 忽略 → 不可编辑
        client.post("/api/comments/batch-status", json={"cids": [c1], "action": "ignore"})
        resp = client.patch(f"/api/comments/{c1}", json={"content": "x"})
        assert resp.status_code == 400
        assert "不可编辑" in resp.get_json()["msg"]

        # 非作者（换 session 用户）
        c2 = _submit_simple(client, p, anchor_id="", nearest_anchor_id="")
        with client.session_transaction() as sess:
            sess["uid"] = 2
            sess["email"] = "other@corp.com"
            sess["name"] = "其他人"
        resp = client.patch(f"/api/comments/{c2}", json={"content": "别人改"})
        assert resp.status_code == 403
        with client.session_transaction() as sess:
            sess["uid"] = 1
            sess["email"] = "pm@corp.com"
            sess["name"] = "产品桑"

        # 空内容 / 无字段
        assert client.patch(f"/api/comments/{c2}", json={"content": " "}).status_code == 400
        assert client.patch(f"/api/comments/{c2}", json={}).status_code == 400


class TestDeleteComment:
    def test_delete_by_author(self, app, project):
        """作者删除：软删 DB + 项目目录评论 JSON/截图直接删除。"""
        client, p = project
        _, projects_dir, _ = app
        c1 = _submit_simple(client, p)

        resp = client.delete(f"/api/comments/{c1}")
        assert resp.status_code == 200
        # 软删：行还在但 deleted=1，列表不返回
        row = Comment.get(Comment.comment_id == c1)
        assert row.deleted is True
        assert client.get(f"/api/projects/{p.id}/comments").get_json()["data"] == []

        # T8.1：文件同步删除
        assert not _cj_path(projects_dir, p, c1).exists()
        assert not (Path(projects_dir) / p.project_id / "reviews" / "shots" / f"{c1}.png").exists()

    def test_delete_rules(self, app, project):
        """删除规则：忽略态拒；非作者拒；重复删 404。"""
        client, p = project
        c1 = _submit_simple(client, p)
        client.post("/api/comments/batch-status", json={"cids": [c1], "action": "ignore"})
        assert client.delete(f"/api/comments/{c1}").status_code == 400

        c2 = _submit_simple(client, p, anchor_id="", nearest_anchor_id="")
        with client.session_transaction() as sess:
            sess["uid"] = 2
            sess["email"] = "other@corp.com"
            sess["name"] = "其他人"
        assert client.delete(f"/api/comments/{c2}").status_code == 403
        with client.session_transaction() as sess:
            sess["uid"] = 1
            sess["email"] = "pm@corp.com"
            sess["name"] = "产品桑"

        assert client.delete(f"/api/comments/{c2}").status_code == 200
        assert client.delete(f"/api/comments/{c2}").status_code == 404


class TestCommentableGuard:
    """T4.5 修订：关闭可评论后，一切写 reviews/ 的操作全部拦截
    （创建 T4.2 已拦；本次补批量状态/编辑/删除——开关的目的是消除
    双写窗口，这些操作都会写项目目录）。查看不受影响。"""

    def test_all_write_ops_blocked_when_off(self, app, project):
        client, p = project
        c1 = _submit_simple(client, p)

        p.commentable = False
        p.save()

        # 批量确认：跳过并报告「项目已关闭评论」
        resp = client.post("/api/comments/batch-status", json={
            "cids": [c1], "action": "confirm",
        })
        data = resp.get_json()["data"]
        assert data["updated"] == []
        assert data["skipped"][0]["reason"] == "项目已关闭评论"
        assert Comment.get(Comment.comment_id == c1).status == "待确认"

        # 编辑：400
        resp = client.patch(f"/api/comments/{c1}", json={"content": "改不动"})
        assert resp.status_code == 400
        assert "已关闭评论" in resp.get_json()["msg"]

        # 删除：400
        resp = client.delete(f"/api/comments/{c1}")
        assert resp.status_code == 400
        assert "已关闭评论" in resp.get_json()["msg"]
        assert Comment.get(Comment.comment_id == c1).deleted is False

        # 查看不受影响
        items = client.get(f"/api/projects/{p.id}/comments").get_json()["data"]
        assert [i["comment_id"] for i in items] == [c1]

        # 重新开启后恢复可操作
        p.commentable = True
        p.save()
        assert client.patch(f"/api/comments/{c1}", json={"content": "恢复了"}).status_code == 200


# ═══════════════════════ T8.3 评论导出 ═══════════════════════

def _open_export_zip(resp) -> tuple[zipfile.ZipFile, dict, str]:
    """解导出响应为 (ZipFile, manifest, 顶层目录名)。"""
    assert resp.status_code == 200, resp.get_json() if resp.mimetype != "application/zip" else resp.data[:100]
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = zf.namelist()
    prefixes = {n.split("/")[0] for n in names}
    assert len(prefixes) == 1
    prefix = prefixes.pop()
    manifest = json.loads(zf.read(f"{prefix}/manifest.json"))
    return zf, manifest, prefix


class TestExportComments:
    def _seed(self, app, project) -> tuple:
        """铺 3 条评论（1 带截图 dom + 1 文档 + 1 待确认）+ 1 条软删，
        其中 dom 那条流转为「已确认待修改」。返回 (dom_cid, doc_cid, pend_cid, deleted_cid)。"""
        client, p = project
        _, projects_dir, shots_dir = app
        # 临时截图（dom 评论引用）
        tmp_shot = Path(shots_dir) / p.project_id / "shot-exp.png"
        tmp_shot.parent.mkdir(parents=True, exist_ok=True)
        tmp_shot.write_bytes(b"\x89PNG\r\n\x1a\nexport-shot")

        # 注意：shot_id/highlight_rect 是 body 顶层字段（非 payload 字段），
        # 须经 _submit 直传——_submit_simple 会把 kwargs 全喂给 payload
        c_dom = _submit(client, p, _dom_payload(), shot_id="shot-exp",
                        highlight_rect={"x": 1, "y": 2, "w": 3, "h": 4}).get_json()["data"]["comment_id"]
        c_doc = _submit_simple(client, p, target_type="doc_block", prototype_page="",
                               anchor_id="", nearest_anchor_id="", css_path="",
                               outer_html="", text_excerpt="导出文档评论段落",
                               doc_anchor_id="", doc_excerpt="导出文档评论段落",
                               doc_path="5.1 登录页", scope="doc")
        c_pend = _submit_simple(client, p, anchor_id="", nearest_anchor_id="")
        c_del = _submit_simple(client, p, anchor_id="", nearest_anchor_id="")
        client.post("/api/comments/batch-status", json={"cids": [c_dom], "action": "confirm"})
        client.delete(f"/api/comments/{c_del}")  # 软删（导出须排除）
        return c_dom, c_doc, c_pend, c_del

    def test_export_all(self, app, project):
        """scope=all：未删除评论全量；包结构与项目目录同构；manifest 字段齐全。"""
        client, p = project
        c_dom, c_doc, c_pend, c_del = self._seed(app, project)

        resp = client.get(f"/api/projects/{p.id}/comments/export?scope=all")
        zf, manifest, prefix = _open_export_zip(resp)

        # 包结构：顶层目录名 {project_id}-comments-{yyyymmdd}-{HHmm}
        assert re.fullmatch(rf"{re.escape(p.project_id)}-comments-\d{{8}}-\d{{4}}", prefix)
        # 附件下载头
        assert resp.headers["Content-Disposition"].startswith("attachment;")
        assert resp.headers["Content-Disposition"].endswith(".zip")
        # manifest 字段（技术方案 §2.8）
        assert manifest["scope"] == "all"
        assert manifest["total"] == 3  # 软删不在
        assert manifest["project"] == {"id": p.project_id, "name": p.name}
        assert manifest["exported_at"]
        by_cid = {m["comment_id"]: m for m in manifest["comments"]}
        assert set(by_cid) == {c_dom, c_doc, c_pend}
        assert by_cid[c_dom]["status"] == "已确认待修改"
        assert by_cid[c_dom]["has_shot"] is True
        assert by_cid[c_doc]["has_shot"] is False  # 文档评论无截图
        # comments/：全部 3 条 + 截图 1 张（仅被导出评论引用的）
        assert {n.split("/", 1)[1] for n in zf.namelist() if "/comments/" in n} == {
            f"comments/{c}.json" for c in (c_dom, c_doc, c_pend)
        }
        assert {n.split("/", 1)[1] for n in zf.namelist() if "/shots/" in n} == {f"shots/{c_dom}.png"}
        assert zf.read(f"{prefix}/shots/{c_dom}.png") == b"\x89PNG\r\n\x1a\nexport-shot"
        # 评论 JSON 与项目目录文件一致（事实源）
        _, projects_dir, _ = app
        disk = _read_cj(projects_dir, p, c_dom)
        assert json.loads(zf.read(f"{prefix}/comments/{c_dom}.json")) == disk

    def test_export_confirmed_only(self, app, project):
        """scope=confirmed：仅「已确认待修改」（交付修改的标准范围）。"""
        client, p = project
        c_dom, _, _, _ = self._seed(app, project)

        resp = client.get(f"/api/projects/{p.id}/comments/export?scope=confirmed")
        zf, manifest, prefix = _open_export_zip(resp)

        assert manifest["scope"] == "confirmed"
        assert manifest["total"] == 1
        assert [m["comment_id"] for m in manifest["comments"]] == [c_dom]
        assert f"{prefix}/comments/{c_dom}.json" in zf.namelist()
        assert len([n for n in zf.namelist() if "/comments/" in n]) == 1

    def test_export_empty(self, app, project):
        """无评论：空包（manifest total=0），不是 404/错误。"""
        client, p = project
        resp = client.get(f"/api/projects/{p.id}/comments/export?scope=all")
        zf, manifest, prefix = _open_export_zip(resp)
        assert manifest["total"] == 0
        assert manifest["comments"] == []
        assert zf.namelist() == [f"{prefix}/manifest.json"]

    def test_export_creator_only(self, app, project):
        """仅创建者：其他登录用户 403。"""
        client, p = project
        with client.session_transaction() as sess:
            sess["uid"] = 2
            sess["email"] = "other@corp.com"
            sess["name"] = "其他人"
        resp = client.get(f"/api/projects/{p.id}/comments/export?scope=all")
        assert resp.status_code == 403
        assert "仅项目创建者" in resp.get_json()["msg"]

    def test_export_bad_scope(self, app, project):
        """非法 scope：400。"""
        client, p = project
        resp = client.get(f"/api/projects/{p.id}/comments/export?scope=everything")
        assert resp.status_code == 400
        assert "scope" in resp.get_json()["msg"]
