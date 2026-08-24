"""评论测试（T4.1 schema 契约 + T4.2 提交链路）。

契约锚点：《产品方案-V1.md》§3.3 评论数据结构（DOM 定位字段组）+
《一期技术实现方案-V1.md》§2.3 payload 采集表。
任务卡验收：T4.1 payload 字段完整性；T4.2 三类评论提交后
DB 与 reviews/ 文件均出现（API 级验证，界面级走 E2E）。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.reviews import validate_comment_payload  # noqa: E402

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


def make_anchor_remote(tmp_path, name: str = "cm") -> str:
    """造带锚点（PRD 注释 + 原型 data-pa）的裸仓库远端。"""
    work = tmp_path / f"{name}-work"
    bare = tmp_path / f"{name}.git"
    work.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(work)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.local"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    (work / "prototype").mkdir()
    (work / "prototype" / "index.html").write_text(
        '<html><body><main data-pa="page-login"><form data-pa="login-form">'
        '<input data-pa="login-account" placeholder="账号"></form></main></body></html>',
        encoding="utf-8",
    )
    (work / "prd").mkdir()
    (work / "prd" / "需求.md").write_text(
        "# PRD\n\n"
        "## 5.1 登录页 <!-- pa: page-login -->\n\n"
        "- 账号输入 <!-- pa: login-account -->：支持手机号\n\n"
        "补充段落。\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)], check=True, capture_output=True)
    return str(bare)


def _dom_payload(**over) -> dict:
    p = {
        "target_type": "dom",
        "prototype_page": "index.html",
        "anchor_id": "login-account",
        "nearest_anchor_id": "login-form",
        "css_path": '[data-pa="login-account"]',
        "outer_html": '<form data-pa="login-form"><input data-pa="login-account"></form>',
        "text_excerpt": "账号",
        "interaction_state": {
            "modal_open": False,
            "viewport": "1440x900",
            "scroll_y": 0,
            "route": "index.html",
        },
    }
    p.update(over)
    return p


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


def _submit(client, p, payload, **over):
    body = {"payload": payload, "content": "测试评论内容", "priority": "P2", "scope": "prototype"}
    body.update(over)
    return client.post(f"/api/projects/{p.id}/comments", json=body)


class TestCreateComment:
    def test_dom_comment_full_chain(self, app, project, tmp_path):
        """DOM 评论全链路：DB + reviews/ 文件 + git commit/push + doc 锚点匹配。"""
        client, p = project
        _, repos_dir = app

        resp = _submit(client, p, _dom_payload(), priority="P1", scope="both")
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        cid = data["comment_id"]
        assert cid.startswith("c-")
        assert data["status"] == "待确认"
        assert data["author"] == "产品桑"
        assert data["git_pushed"] is True

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
        """页面评论（target_type=page）：正常提交落仓。"""
        client, p = project
        resp = _submit(client, p, _dom_payload(target_type="page", anchor_id="",
                                               nearest_anchor_id="", css_path="body"))
        assert resp.status_code == 200
        cid = resp.get_json()["data"]["comment_id"]
        fj = json.loads(
            (Path(app[1]) / p.project_id / "reviews" / "comments" / f"{cid}.json")
            .read_text(encoding="utf-8")
        )
        assert fj["target_type"] == "page"
        assert fj["css_path"] == "body"

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

    def test_comment_id_daily_sequence(self, app, project):
        """同日两条：序号递增 001 → 002。"""
        client, p = project
        r1 = _submit(client, p, _dom_payload())
        r2 = _submit(client, p, _dom_payload(anchor_id="", nearest_anchor_id=""))
        c1 = r1.get_json()["data"]["comment_id"]
        c2 = r2.get_json()["data"]["comment_id"]
        assert c1.endswith("-001")
        assert c2.endswith("-002")

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
        """push 失败不阻塞评论：DB/文件已落，sync_error 记录，git_pushed=False。"""
        client, p = project
        _, repos_dir = app
        # 远端不可达：改本地 clone 的 origin URL（push 走 .git/config 而非 DB repo_url）
        root = Path(repos_dir) / p.project_id
        subprocess.run(
            ["git", "-C", str(root), "remote", "set-url", "origin", "/tmp/ppp-nonexistent-remote"],
            check=True, capture_output=True,
        )
        resp = _submit(client, p, _dom_payload())
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        assert data["git_pushed"] is False
        assert data["git_error"]
        # 评论本体已完整落库落文件
        assert Comment.get(Comment.comment_id == data["comment_id"])
        assert (root / "reviews" / "comments" / f"{data['comment_id']}.json").is_file()
        # sync_error 落库（首页红点提示用）
        p2 = Project.get_by_id(p.id)
        assert p2.sync_error
