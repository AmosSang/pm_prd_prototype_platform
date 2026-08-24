"""pytest 配置：契约测试 fixture 索引 + 评论测试共用工具（T4.2 起）。"""
import os
import subprocess
import sys

# 让 tests/ 能导入 server 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def make_anchor_remote(tmp_path, name: str = "cm") -> str:
    """造带锚点（PRD 注释 + 原型 data-pa）的裸仓库远端（评论类测试共用）。"""
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


def dom_payload(**over) -> dict:
    """评论 DOM 定位 payload 基准（T4.2 起，多个测试模块共用）。"""
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


def _git(root, *args: str) -> str:
    """git 命令包装（返回 stdout.strip，失败抛 CalledProcessError）。"""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _wait_ok(timeout: float = 20.0) -> None:
    """等队列任务全部终态（断言失败即抛）。"""
    from server.git_tasks import wait_tasks

    assert wait_tasks(timeout=timeout), "队列任务未在时限内完成"


def submit_comment(client, p, payload=None, **over):
    """POST /comments 快捷提交（默认 dom payload），返回 response。"""
    body = {
        "payload": payload if payload is not None else dom_payload(),
        "content": "测试评论内容",
        "priority": "P2",
        "scope": "prototype",
    }
    body.update(over)
    return client.post(f"/api/projects/{p.id}/comments", json=body)
