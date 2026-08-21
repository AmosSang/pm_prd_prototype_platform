"""proto_proxy 单元测试：路径安全 + 注入逻辑。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.proto_proxy import PROJECT_ID, _resolve, inject_bridge


def test_project_id_pattern():
    assert PROJECT_ID.fullmatch("demo")
    assert PROJECT_ID.fullmatch("demo-project-01")
    assert not PROJECT_ID.fullmatch("Demo")  # 大写拒绝
    assert not PROJECT_ID.fullmatch("../evil")
    assert not PROJECT_ID.fullmatch("a" * 33)  # 超长拒绝


def test_resolve_blocks_traversal(tmp_path, monkeypatch):
    from server import proto_proxy

    monkeypatch.setattr(proto_proxy, "REPOS_DIR", str(tmp_path))
    (tmp_path / "demo" / "prototype" / "pages").mkdir(parents=True)
    (tmp_path / "demo" / "prototype" / "pages" / "login.html").write_text("<html></html>")
    # 非法路径全部 404
    with pytest.raises(Exception):
        _resolve("demo", "prd/secret.md")
    with pytest.raises(Exception):
        _resolve("demo", "prototype/../prd/secret.md")
    with pytest.raises(Exception):
        _resolve("demo", "")
    # 合法路径解析成功
    full = _resolve("demo", "prototype/pages/login.html")
    assert full.endswith("login.html")


def test_inject_bridge():
    html = "<html><body><h1>hi</h1></body></html>"
    out = inject_bridge(html)
    assert '<script src="/bridge.js"></script></body>' in out
    # 幂等：重复注入不再叠加
    assert inject_bridge(out) == out
    # 无 </body> 兜底：追加末尾
    assert inject_bridge("<html><h1>hi</h1></html>").endswith(
        '<script src="/bridge.js"></script>'
    )
