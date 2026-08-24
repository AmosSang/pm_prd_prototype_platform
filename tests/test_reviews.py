"""评论测试（T4.1 schema 契约 + T4.2 提交链路）。

契约锚点：《产品方案-V1.md》§3.3 评论数据结构（DOM 定位字段组）+
《一期技术实现方案-V1.md》§2.3 payload 采集表。
任务卡验收：T4.1 payload 字段完整性；T4.2 三类评论提交后
DB 与 reviews/ 文件均出现（API 级验证，界面级走 E2E）。
"""
import datetime as dt
import json
import subprocess
import sys
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
from server.git_tasks import wait_tasks  # noqa: E402
from server.models import Comment, GitTask, Project, db  # noqa: E402
from tests.conftest import _git, _wait_ok, make_anchor_remote  # noqa: E402
from tests.conftest import dom_payload as _dom_payload  # noqa: E402
from tests.conftest import submit_comment as _submit  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db.close()
    db.init(str(tmp_path / "test.db"))
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    monkeypatch.setattr("server.gitops.REPOS_DIR", str(repos_dir))
    monkeypatch.setattr("server.reviews.SHOTS_DIR", str(tmp_path / "shots"))

    app = create_app()
    app.config["TESTING"] = True
    app.secret_key = "test-secret"
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["uid"] = 1
            sess["email"] = "pm@corp.com"
            sess["name"] = "产品桑"
        yield c, str(repos_dir)
    db.close()


@pytest.fixture()
def project(app, tmp_path):
    """绑定带锚点仓库，返回 (client, project_row)。"""
    client, _ = app
    remote = make_anchor_remote(tmp_path)
    resp = client.post("/api/projects", json={
        "name": "评论单测项目", "repo_url": remote, "token": "glpat-x", "branch": "main",
    })
    assert resp.status_code == 200, resp.get_json()
    p = Project.get(Project.name == "评论单测项目")
    return client, p


class TestCreateComment:
    def test_dom_comment_full_chain(self, app, project, tmp_path):
        """DOM 评论全链路：DB + reviews/ 文件 + git commit/push（队列）+ doc 锚点匹配。"""
        client, p = project
        _, repos_dir = app

        resp = _submit(client, p, _dom_payload(), priority="P1", scope="both")
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        cid = data["comment_id"]
        assert cid.startswith("c-")
        assert data["status"] == "待确认"
        assert data["author"] == "产品桑"
        # T4.3：git 走异步队列——请求返回时 pending，等队列跑完再断言 git 结果
        assert data["git_task"]["status"] == "pending"
        assert wait_tasks(timeout=20)

        # DB 落库（展示缓存）
        row = Comment.get(Comment.comment_id == cid)
        assert row.project.id == p.id
        assert row.author_email == "pm@corp.com"
        cj = json.loads(row.payload_json)
        assert cj["content"] == "测试评论内容"
        assert cj["priority"] == "P1"

        # reviews/ 文件落仓（事实源）
        root = Path(repos_dir) / p.project_id
        fpath = root / "reviews" / "comments" / f"{cid}.json"
        assert fpath.is_file()
        fj = json.loads(fpath.read_text(encoding="utf-8"))
        assert fj["comment_id"] == cid
        assert fj["status"] == "待确认"
        assert fj["anchor_id"] == "login-account"
        # doc 匹配：候选锚点 login-account 命中 PRD 锚点
        assert fj["doc_anchor_id"] == "login-account"
        assert "账号输入" in fj["doc_excerpt"]
        assert fj["doc_file"] == "prd/需求.md"

        # git：clone 与裸仓库（push 生效）最新 commit，作者=评论人
        msg = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%s"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert msg == f"comment: {cid} 创建"
        author = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%an"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert author == "产品桑"
        bare = Path(p.repo_url)
        remote_msg = subprocess.run(
            ["git", "-C", str(bare), "log", "-1", "--format=%s"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert remote_msg == f"comment: {cid} 创建"
        # 任务终态 done
        task = GitTask.get(GitTask.ref_id == cid)
        assert task.status == "done" and task.error is None

    def test_dom_comment_without_anchor_no_doc_link(self, app, project):
        """非锚点区域（候选锚点查 PRD 未命中）→ 标记「无 PRD 锚点关联」。"""
        client, p = project
        resp = _submit(client, p, _dom_payload(anchor_id="", nearest_anchor_id=""))
        assert resp.status_code == 200
        cid = resp.get_json()["data"]["comment_id"]
        fj = json.loads(
            (Path(app[1]) / p.project_id / "reviews" / "comments" / f"{cid}.json")
            .read_text(encoding="utf-8")
        )
        assert fj["doc_anchor_id"] == ""
        assert fj["doc_note"] == "无 PRD 锚点关联"

    def test_page_comment(self, app, project):
        """页面评论（target_type=page）：正常提交落仓；outer_html 为空
        （T4.2 修订：页面根的整页 HTML 无定位意义，bridge 不采集）。"""
        client, p = project
        resp = _submit(client, p, _dom_payload(target_type="page", anchor_id="",
                                               nearest_anchor_id="", css_path="body",
                                               outer_html=""))
        assert resp.status_code == 200
        cid = resp.get_json()["data"]["comment_id"]
        fj = json.loads(
            (Path(app[1]) / p.project_id / "reviews" / "comments" / f"{cid}.json")
            .read_text(encoding="utf-8")
        )
        assert fj["target_type"] == "page"
        assert fj["css_path"] == "body"
        assert fj["outer_html"] == ""

    def test_doc_comment_with_fingerprint(self, app, project):
        """文档评论（doc_block）：fingerprint = sha1(doc_path|excerpt)[:16]。"""
        client, p = project
        import hashlib

        resp = _submit(client, p, _dom_payload(
            target_type="doc_block", prototype_page="", anchor_id="",
            nearest_anchor_id="", css_path="", outer_html="",
            text_excerpt="账号输入：支持手机号",
            doc_anchor_id="login-account", doc_excerpt="账号输入：支持手机号",
        ), scope="doc")
        assert resp.status_code == 200
        cid = resp.get_json()["data"]["comment_id"]
        fj = json.loads(
            (Path(app[1]) / p.project_id / "reviews" / "comments" / f"{cid}.json")
            .read_text(encoding="utf-8")
        )
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
        fj = json.loads(
            (Path(app[1]) / p.project_id / "reviews" / "comments" / f"{cid}.json")
            .read_text(encoding="utf-8")
        )
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

    def test_push_failure_not_blocking(self, app, project):
        """push 失败不阻塞评论：DB/文件已落，任务 error + sync_error（队列语义）。"""
        client, p = project
        _, repos_dir = app
        # 远端不可达：改本地 clone 的 origin URL（push 走 .git/config 而非 DB repo_url）
        root = Path(repos_dir) / p.project_id
        subprocess.run(
            ["git", "-C", str(root), "remote", "set-url", "origin", "/tmp/ppp-nonexistent-remote"],
            check=True, capture_output=True,
        )
        resp = _submit(client, p, _dom_payload())
        assert resp.status_code == 200, resp.get_json()  # 提交不被 git 阻塞
        cid = resp.get_json()["data"]["comment_id"]
        assert wait_tasks(timeout=20)

        task = GitTask.get(GitTask.ref_id == cid)
        assert task.status == "error"
        assert task.retry_count >= 1
        # 评论本体已完整落库落文件（本地 commit 也已做，仅 push 失败）
        assert Comment.get(Comment.comment_id == cid)
        assert (root / "reviews" / "comments" / f"{cid}.json").is_file()
        # sync_error 落库（首页红点提示用）
        p2 = Project.get_by_id(p.id)
        assert p2.sync_error


# ═══════════════════════ T4.4 列表 / 编辑 / 删除 / 批量状态 ═══════════════════════


def _submit_simple(client, p, **over) -> str:
    """快捷提交一条 dom 评论，返回 comment_id（队列同步跑完）。"""
    resp = _submit(client, p, _dom_payload(**over))
    assert resp.status_code == 200, resp.get_json()
    cid = resp.get_json()["data"]["comment_id"]
    return cid


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
        """批量确认（任务卡验收）：DB 流转 + 落仓 JSON status 变更 + git log。"""
        client, p = project
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
        _wait_ok()

        # DB 流转
        for cid in (c1, c2):
            assert Comment.get(Comment.comment_id == cid).status == "已确认待修改"
        # 落仓 JSON + git log（每条一个 commit）
        root = Path(app[1]) / p.project_id
        for cid in (c1, c2):
            fj = json.loads((root / "reviews" / "comments" / f"{cid}.json").read_text(encoding="utf-8"))
            assert fj["status"] == "已确认待修改"
        msgs = _git(root, "log", "--format=%s").split("\n")
        assert f"comment: {c2} → 已确认待修改" in msgs
        assert f"comment: {c1} → 已确认待修改" in msgs
        assert msgs[0] == f"comment: {c2} → 已确认待修改"  # 后确认的在顶

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
        """作者编辑：DB + payload 更新，落仓 JSON 同步（COMMIT_EDIT）。"""
        client, p = project
        c1 = _submit_simple(client, p)

        resp = client.patch(f"/api/comments/{c1}", json={
            "content": "改后的评论内容", "priority": "P1", "scope": "both",
        })
        assert resp.status_code == 200, resp.get_json()
        row = Comment.get(Comment.comment_id == c1)
        assert row.priority == "P1"
        assert row.scope == "both"
        assert json.loads(row.payload_json)["content"] == "改后的评论内容"
        _wait_ok()

        root = Path(app[1]) / p.project_id
        fj = json.loads((root / "reviews" / "comments" / f"{c1}.json").read_text(encoding="utf-8"))
        assert fj["content"] == "改后的评论内容"
        assert fj["priority"] == "P1"
        assert _git(root, "log", "-1", "--format=%s") == f"comment: {c1} 编辑"

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
        """作者删除：软删 DB + git rm 文件（COMMIT_DELETE）。"""
        client, p = project
        c1 = _submit_simple(client, p)

        resp = client.delete(f"/api/comments/{c1}")
        assert resp.status_code == 200
        # 软删：行还在但 deleted=1，列表不返回
        row = Comment.get(Comment.comment_id == c1)
        assert row.deleted is True
        assert client.get(f"/api/projects/{p.id}/comments").get_json()["data"] == []

        _wait_ok()
        root = Path(app[1]) / p.project_id
        assert not (root / "reviews" / "comments" / f"{c1}.json").exists()
        assert _git(root, "log", "-1", "--format=%s") == f"comment: {c1} 删除"

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
    双写窗口，这些操作都会产生 commit）。查看不受影响。"""

    def test_all_write_ops_blocked_when_off(self, app, project):
        client, p = project
        c1 = _submit_simple(client, p)
        _wait_ok()

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
