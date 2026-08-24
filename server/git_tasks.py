"""评论落仓队列（T4.3，技术方案 §2.7）。

每项目一个 queue.Queue + 单 worker 守护线程：同一项目的 git 操作严格
串行，杜绝并发 commit 交错；不同项目并行（各自 clone 目录互不相干）。

任务类型：
- COMMIT_COMMENT：评论创建。文件（JSON/截图）已由 API 同步写入 reviews/
  （新文件、untracked，与 worker 的 git 操作无竞态），本任务做 git
  add/commit/push。
- COMMIT_STATUS：评论状态变更（T4.4 的确认/忽略/返工调用）。tracked 文件
  的修改必须在 worker 内串行做（API 侧直接改会与 rebase 竞态）。
- COMMIT_DELETE：删除评论文件与截图（T4.4 的删除调用）。

失败语义（不阻塞评论）：DB 与文件已落，git 失败只置任务 error +
项目 sync_error（首页卡片「同步异常」提示）；push 冲突（远端有新提交）
自动 pull --rebase 重试，最多 3 次 push（见 gitops.commit_and_push）。

重启语义：未完成的 pending 任务不自动恢复（一期不做）——评论以仓库为
事实源，T5.1 SYNC_PULL 的全量比对可补差异。
"""
import json
import os
import queue
import threading
import time

from server.gitops import commit_and_push, repo_path
from server.models import GitTask, Project, db, utcnow_str

# 每项目 (任务队列, worker 线程)；懒创建（首个任务入队时启动）
_workers: dict[int, tuple[queue.Queue, threading.Thread]] = {}
_lock = threading.Lock()


# ───────────────────────── worker ─────────────────────────

def _worker_loop(q: queue.Queue) -> None:
    while True:
        item = q.get()
        if item is None:
            return
        try:
            _run_task(item)
        except Exception as e:  # noqa: BLE001 — 最后防线：不让线程死掉
            try:
                t = GitTask.get_by_id(item["task_id"])
                t.status = "error"
                t.error = f"worker 兜底异常：{e}"
                t.updated_at = utcnow_str()
                t.save()
            except Exception:  # noqa: BLE001 — 兜底的兜底：只保线程活着
                pass


def _reset_db_conn() -> None:
    """重置本线程的 DB 连接（每任务开始时）。

    peewee 连接是线程局部的：测试间 db.init 换文件路径后，复用的 worker
    线程若持旧连接会写进过期 DB（任务状态永不更新）。close 后下一次查询
    自动按当前路径重连；生产路径不变，代价是一次本地重连，可忽略。
    """
    if not db.is_closed():
        db.close()


def _finish(task: GitTask, status: str, error: str | None, attempts: int) -> None:
    task.status = status
    task.error = error
    task.retry_count = attempts
    task.updated_at = utcnow_str()
    task.save()


def _run_task(item: dict) -> None:
    _reset_db_conn()
    task = GitTask.get_or_none(GitTask.id == item["task_id"])
    if not task:
        return
    p = Project.get_or_none(Project.id == task.project_id)
    if not p:
        _finish(task, "error", "项目不存在", 0)
        return

    try:
        error, attempts = _execute(task, p, item)
    except Exception as e:  # noqa: BLE001 — 任何执行异常都置错误态，不杀 worker
        _finish(task, "error", f"任务执行异常：{e}", 0)
        return

    if error:
        _finish(task, "error", error, attempts)
        p.sync_error = error
        p.save()
        return
    _finish(task, "done", None, attempts)
    # 成功后清 sync_error 的条件：本项目已无 error 态任务（有则红点保留，
    # 提示仍有评论未同步到仓库）
    still_error = GitTask.select().where(GitTask.project == p.id, GitTask.status == "error").count()
    if still_error == 0 and p.sync_error:
        p.sync_error = None
        p.save()


def _execute(task: GitTask, p: Project, item: dict) -> tuple[str | None, int]:
    """按任务类型分发执行。"""
    root = repo_path(p.project_id)
    if task.task_type == "COMMIT_COMMENT":
        paths = item["paths"]
        return commit_and_push(
            p.project_id, p.encrypted_token, p.branch,
            f"comment: {task.ref_id} 创建",
            item["author_name"], item["author_email"], paths=paths,
        )
    if task.task_type == "COMMIT_STATUS":
        # tracked 文件的修改在 worker 内做（与 git 操作同队列串行，无竞态）
        fpath = os.path.join(root, "reviews", "comments", f"{task.ref_id}.json")
        if not os.path.isfile(fpath):
            return f"评论文件不存在：{task.ref_id}", 0
        with open(fpath, encoding="utf-8") as f:
            cj = json.load(f)
        cj["status"] = item["new_status"]
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(cj, f, ensure_ascii=False, indent=2)
        return commit_and_push(
            p.project_id, p.encrypted_token, p.branch,
            f"comment: {task.ref_id} → {item['new_status']}",
            item["author_name"], item["author_email"],
            paths=[f"reviews/comments/{task.ref_id}.json"],
        )
    if task.task_type == "COMMIT_DELETE":
        return commit_and_push(
            p.project_id, p.encrypted_token, p.branch,
            f"comment: {task.ref_id} 删除",
            item["author_name"], item["author_email"],
            paths=[f"reviews/comments/{task.ref_id}.json", f"reviews/shots/{task.ref_id}.png"],
            remove=True,
        )
    return f"未知任务类型：{task.task_type}", 0


# ───────────────────────── 入队 API ─────────────────────────

def _ensure_worker(project_pk: int) -> tuple[queue.Queue, threading.Thread]:
    with _lock:
        w = _workers.get(project_pk)
        if w:
            return w
        q: queue.Queue = queue.Queue()
        t = threading.Thread(
            target=_worker_loop, args=(q,),
            daemon=True, name=f"git-worker-{project_pk}",
        )
        t.start()
        _workers[project_pk] = (q, t)
        return _workers[project_pk]


def _enqueue(task_type: str, project: Project, ref_id: str, **ctx) -> GitTask:
    task = GitTask.create(
        project=project.id,
        task_type=task_type,
        ref_id=ref_id,
        status="pending",
        created_at=utcnow_str(),
        updated_at=utcnow_str(),
    )
    q, _t = _ensure_worker(project.id)
    q.put({"task_id": task.id, **ctx})
    return task


def enqueue_comment(
    project: Project, comment_id: str, has_shot: bool,
    author_name: str, author_email: str,
) -> GitTask:
    """COMMIT_COMMENT：评论创建（文件已由调用方写入 reviews/）。"""
    paths = [f"reviews/comments/{comment_id}.json"]
    if has_shot:
        paths.append(f"reviews/shots/{comment_id}.png")
    return _enqueue(
        "COMMIT_COMMENT", project, comment_id,
        paths=paths, author_name=author_name, author_email=author_email,
    )


def enqueue_status(
    project: Project, comment_id: str, new_status: str,
    author_name: str, author_email: str,
) -> GitTask:
    """COMMIT_STATUS：状态变更（T4.4 确认/忽略/返工调用；先落 DB 再入队）。"""
    return _enqueue(
        "COMMIT_STATUS", project, comment_id,
        new_status=new_status, author_name=author_name, author_email=author_email,
    )


def enqueue_delete(
    project: Project, comment_id: str,
    author_name: str, author_email: str,
) -> GitTask:
    """COMMIT_DELETE：删除评论文件与截图（T4.4 删除调用；先软删 DB 再入队）。"""
    return _enqueue(
        "COMMIT_DELETE", project, comment_id,
        author_name=author_name, author_email=author_email,
    )


# ───────────────────────── 测试/调试辅助 ─────────────────────────

def wait_tasks(timeout: float = 15.0) -> bool:
    """等全部任务离开 pending（测试与调试用；生产不调用）。

    pending 行在任务入队时创建、终态时更新——队列中与执行中的任务都算
    pending，因此「无 pending 行」即全部完成。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if GitTask.select().where(GitTask.status == "pending").count() == 0:
            return True
        time.sleep(0.05)
    return GitTask.select().where(GitTask.status == "pending").count() == 0
