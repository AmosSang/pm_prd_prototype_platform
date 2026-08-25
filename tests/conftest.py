"""pytest 配置：契约测试 fixture 索引 + 评论测试共用工具（T4.2 起；T8.1 去 Git 本地化修订）。"""
import os
import sys

# 让 tests/ 能导入 server 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def make_local_project(projects_dir, slug: str = "cm-proj") -> str:
    """造带锚点（PRD 注释 + 原型 data-pa）的本地项目目录（T8.1 评论类测试共用）。

    结构与 ensure_project_dirs 骨架一致；projects_dir 通常是 monkeypatch
    过的 PROJECTS_DIR 临时目录。
    """
    root = os.path.join(str(projects_dir), slug)
    os.makedirs(os.path.join(root, "prototype"), exist_ok=True)
    os.makedirs(os.path.join(root, "prd"), exist_ok=True)
    os.makedirs(os.path.join(root, "reviews", "comments"), exist_ok=True)
    os.makedirs(os.path.join(root, "reviews", "shots"), exist_ok=True)
    with open(os.path.join(root, "prototype", "index.html"), "w", encoding="utf-8") as f:
        f.write(
            '<html><body><main data-pa="page-login"><form data-pa="login-form">'
            '<input data-pa="login-account" placeholder="账号"></form></main></body></html>'
        )
    with open(os.path.join(root, "prd", "需求.md"), "w", encoding="utf-8") as f:
        f.write(
            "# PRD\n\n"
            "## 5.1 登录页 <!-- pa: page-login -->\n\n"
            "- 账号输入 <!-- pa: login-account -->：支持手机号\n\n"
            "补充段落。\n"
        )
    return root


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
