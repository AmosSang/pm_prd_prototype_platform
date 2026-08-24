"""落仓队列测试（T4.3）。

任务卡验收点：
- 队列时序：同项目多任务串行按序执行（git log 顺序 = 提交顺序）
- push 冲突重试：远端有新提交 → push 被拒 → pull --rebase → push 成功
- 失败不阻塞：push 不可达 → 任务 error + sync_error，评论 DB/文件完好
- COMMIT_STATUS / COMMIT_DELETE 执行器（T4.4 的 API 将直接复用入队函数）
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.app import create_app  # noqa: E402
from server.git_tasks import enqueue_delete, enqueue_status  # noqa: E402
from server.models import Comment, GitTask, Project, db  # noqa: E402
from tests.conftest import (  # noqa: E402
    _git,
    _wait_ok,
    make_anchor_remote,  # noqa: E402
)
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
    client, _ = app
    remote = make_anchor_remote(tmp_path, f"qt{abs(hash(tmp_path)) % 10000}")
    resp = client.post("/api/projects", json={
        "name": "队列单测项目", "repo_url": remote, "token": "glpat-x", "branch": "main",
    })
    assert resp.status_code == 200, resp.get_json()
    return client, Project.get(Project.name == "队列单测项目")


def _clone_root(app, p: Project) -> Path:
    return Path(app[1]) / p.project_id


class TestQueueOrdering:
    def test_serial_order_three_comments(self, app, project):
        """同项目连发 3 条评论：任务串行按序执行，git log 顺序一致。"""
        client, p = project
        cids = []
        for _ in range(3):
            resp = _submit(client, p, _dom_payload())
            assert resp.status_code == 200, resp.get_json()
            cids.append(resp.get_json()["data"]["comment_id"])
        _wait_ok()

        root = _clone_root(app, p)
        messages = _git(root, "log", "--format=%s").split("\n")
        # log 倒序（新在前）：3 条创建 commit 按提交顺序排列（旧在后）
        creates = [m for m in messages if "创建" in m]
        assert creates == [f"comment: {c} 创建" for c in reversed(cids)]
        # 全部任务 done
        for t in GitTask.select().where(GitTask.project == p.id):
            assert t.status == "done", t.error
            assert t.retry_count == 1  # 首次 push 即成功
        # 无 sync_error
        assert Project.get_by_id(p.id).sync_error is None

    def test_task_rows_lifecycle(self, app, project):
        """GitTask 行：入队 pending → 执行后 done；ref_id 关联 comment_id。"""
        client, p = project
        resp = _submit(client, p, _dom_payload())
        cid = resp.get_json()["data"]["comment_id"]
        # 响应携带任务信息（入队即返回，不等待 git）
        assert resp.get_json()["data"]["git_task"]["status"] == "pending"
        _wait_ok()
        t = GitTask.get(GitTask.ref_id == cid)
        assert t.task_type == "COMMIT_COMMENT"
        assert t.status == "done"
        assert t.error is None


class TestPushConflictRetry:
    def test_remote_advanced_rebase_retry(self, app, project, tmp_path):
        """push 冲突：远端被外部推进 → push 被拒 → pull --rebase → push 成功。"""
        client, p = project
        bare = Path(p.repo_url)

        # 绑定后，外部 clone 推进远端（新文件，无冲突内容）
        ext = tmp_path / "ext-work"
        subprocess.run(["git", "clone", "-q", str(bare), str(ext)], check=True, capture_output=True)
        (ext / "prd").mkdir(exist_ok=True)
        (ext / "prd" / "ext.md").write_text("# 外部新文档\n", encoding="utf-8")
        _git(ext, "add", "-A")
        _git(ext, "-c", "user.email=ext@t.local", "-c", "user.name=ext", "commit", "-qm", "ext: 外部提交")
        _git(ext, "push", "-q")

        # 提交评论 → worker push 必被拒（non-FF）→ rebase → 重试成功
        resp = _submit(client, p, _dom_payload())
        cid = resp.get_json()["data"]["comment_id"]
        _wait_ok()

        t = GitTask.get(GitTask.ref_id == cid)
        assert t.status == "done", t.error
        assert t.retry_count == 2  # 首次被拒 + rebase 后成功

        # 远端：外部提交之上是评论提交（rebase 生效）
        remote_msgs = _git(bare, "log", "--format=%s").split("\n")
        assert remote_msgs[0] == f"comment: {cid} 创建"
        assert remote_msgs[1] == "ext: 外部提交"
        # 本地工作区同步到 rebase 后状态（外部文件可见）
        assert (_clone_root(app, p) / "prd" / "ext.md").is_file()


class TestPushFailureNotBlocking:
    def test_unreachable_remote_error_state(self, app, project):
        """远端不可达：任务 error + sync_error，评论 DB/文件完好（不阻塞）。"""
        client, p = project
        root = _clone_root(app, p)
        subprocess.run(
            ["git", "-C", str(root), "remote", "set-url", "origin", "/tmp/ppp-nonexistent-remote"],
            check=True, capture_output=True,
        )

        resp = _submit(client, p, _dom_payload())
        assert resp.status_code == 200, resp.get_json()  # 提交不被 git 阻塞
        cid = resp.get_json()["data"]["comment_id"]
        _wait_ok()

        t = GitTask.get(GitTask.ref_id == cid)
        assert t.status == "error"
        assert t.retry_count >= 1
        assert t.error
        # sync_error 落库（首页卡片红点提示）
        assert Project.get_by_id(p.id).sync_error
        # 评论本体完好：DB + 本地文件 + 本地 commit（push 失败但 commit 已做）
        assert Comment.get(Comment.comment_id == cid)
        assert (root / "reviews" / "comments" / f"{cid}.json").is_file()
        assert _git(root, "log", "-1", "--format=%s") == f"comment: {cid} 创建"

    def test_error_task_keeps_sync_error_until_all_done(self, app, project):
        """失败任务后 sync_error 置位；后续任务成功但 error 未清时红点保留。"""
        client, p = project
        root = _clone_root(app, p)
        subprocess.run(
            ["git", "-C", str(root), "remote", "set-url", "origin", "/tmp/ppp-nonexistent-remote"],
            check=True, capture_output=True,
        )
        r1 = _submit(client, p, _dom_payload())
        _wait_ok()
        assert GitTask.get(GitTask.ref_id == r1.get_json()["data"]["comment_id"]).status == "error"
        assert Project.get_by_id(p.id).sync_error

        # 远端恢复 → 新任务成功，但旧 error 任务仍在 → sync_error 保留
        _git(root, "remote", "set-url", "origin", p.repo_url)
        r2 = _submit(client, p, _dom_payload())
        _wait_ok()
        assert GitTask.get(GitTask.ref_id == r2.get_json()["data"]["comment_id"]).status == "done"
        assert Project.get_by_id(p.id).sync_error  # 仍有 error 任务，红点保留


class TestStatusAndDeleteExecutors:
    def test_status_executor(self, app, project):
        """COMMIT_STATUS：worker 改 JSON status 字段并提交（T4.4 复用）。"""
        client, p = project
        resp = _submit(client, p, _dom_payload())
        cid = resp.get_json()["data"]["comment_id"]
        _wait_ok()

        enqueue_status(p, cid, "已确认待修改", "产品桑", "pm@corp.com")
        _wait_ok()

        root = _clone_root(app, p)
        fj = json.loads((root / "reviews" / "comments" / f"{cid}.json").read_text(encoding="utf-8"))
        assert fj["status"] == "已确认待修改"
        assert _git(root, "log", "-1", "--format=%s") == f"comment: {cid} → 已确认待修改"
        assert _git(Path(p.repo_url), "log", "-1", "--format=%s") == f"comment: {cid} → 已确认待修改"

    def test_delete_executor(self, app, project):
        """COMMIT_DELETE：git rm 评论 JSON + 截图并提交（T4.4 复用）。"""
        client, p = project
        resp = _submit(client, p, _dom_payload())
        cid = resp.get_json()["data"]["comment_id"]
        _wait_ok()
        root = _clone_root(app, p)
        assert (root / "reviews" / "comments" / f"{cid}.json").is_file()

        enqueue_delete(p, cid, "产品桑", "pm@corp.com")
        _wait_ok()

        assert not (root / "reviews" / "comments" / f"{cid}.json").exists()
        assert _git(root, "log", "-1", "--format=%s") == f"comment: {cid} 删除"
        assert _git(Path(p.repo_url), "log", "-1", "--format=%s") == f"comment: {cid} 删除"

    def test_status_executor_missing_file(self, app, project):
        """COMMIT_STATUS 目标文件不存在（异常场景）：任务 error 不抛出。"""
        _, p = project
        enqueue_status(p, "c-20990101-999", "已确认待修改", "产品桑", "pm@corp.com")
        _wait_ok()
        t = GitTask.get(GitTask.ref_id == "c-20990101-999")
        assert t.status == "error"
        assert "不存在" in t.error
