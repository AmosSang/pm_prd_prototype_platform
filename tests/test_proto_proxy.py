"""proto_proxy 单元测试：路径安全 + 注入逻辑。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.proto_proxy import BRIDGE_GUARD_TAG, PROJECT_ID, _resolve, inject_bridge


def test_project_id_pattern():
    assert PROJECT_ID.fullmatch("demo")
    assert PROJECT_ID.fullmatch("demo-project-01")
    assert not PROJECT_ID.fullmatch("Demo")  # 大写拒绝
    assert not PROJECT_ID.fullmatch("../evil")
    assert not PROJECT_ID.fullmatch("a" * 33)  # 超长拒绝


def test_resolve_blocks_traversal(tmp_path, monkeypatch):
    """T8.1：数据源为 PROJECTS_DIR；非法路径/越出 prototype/ 全 404。"""
    monkeypatch.setattr("server.storage.PROJECTS_DIR", str(tmp_path))
    (tmp_path / "proj-x" / "prototype" / "pages").mkdir(parents=True)
    (tmp_path / "proj-x" / "prototype" / "pages" / "login.html").write_text("<html></html>")
    # 非法路径全部 404
    with pytest.raises(Exception):
        _resolve("proj-x", "prd/secret.md")
    with pytest.raises(Exception):
        _resolve("proj-x", "prototype/../prd/secret.md")
    with pytest.raises(Exception):
        _resolve("proj-x", "")
    # 合法路径解析成功
    full = _resolve("proj-x", "prototype/pages/login.html")
    assert full.endswith("login.html")
    # storage 侧 slug 白名单更严（PROJECT_ID 允许前导 -，SLUG_RE 拒绝）→ 404
    with pytest.raises(Exception):
        _resolve("-lead", "prototype/index.html")


def test_inject_bridge():
    html = "<html><head><meta charset='utf-8'></head><body><h1>hi</h1></body></html>"
    out = inject_bridge(html)
    assert '<script src="/bridge.js"></script></body>' in out
    # 早期护栏注入在 <head> 后（原型脚本崩溃/改写文档时 bridge 可自愈补挂）
    assert BRIDGE_GUARD_TAG in out
    assert out.index(BRIDGE_GUARD_TAG) < out.index("<body>")
    # 幂等：重复注入不再叠加（护栏与 bridge 各只一次）
    assert inject_bridge(out) == out
    # 无 </body> 兜底：追加末尾
    assert inject_bridge("<html><h1>hi</h1></html>").endswith(
        '<script src="/bridge.js"></script>'
    )


def test_injected_guard_captures_nonce():
    """护栏脚本含 nonce 捕获与 __PP_BRIDGE__ 幂等补挂逻辑（回归防护）。"""
    assert "pp-nonce=([A-Za-z0-9_-]+)" in BRIDGE_GUARD_TAG
    assert "__PP_NONCE__" in BRIDGE_GUARD_TAG
    assert "__PP_BRIDGE__" in BRIDGE_GUARD_TAG
    assert "data-pp-bridge" in BRIDGE_GUARD_TAG
