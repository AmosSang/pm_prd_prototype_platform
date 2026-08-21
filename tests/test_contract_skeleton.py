"""契约测试骨架：验证 fixture 机制可用。

T0.2 只验证样例文件存在且锚点注释格式符合 3.1 契约；
真正的解析器用例随 T3.3 实现补充。
"""
import os
import re

from tests.conftest import FIXTURES_DIR

ANCHOR_PATTERN = re.compile(r"<!--\s*pa:\s*([a-z0-9-]+)\s*-->")


def test_fixture_prd_exists():
    path = os.path.join(FIXTURES_DIR, "prd", "sample.md")
    assert os.path.exists(path), "契约 fixture sample.md 缺失"


def test_fixture_prd_anchor_syntax():
    """样例 PRD 中所有锚点注释必须匹配 3.1 语法（kebab-case）。"""
    path = os.path.join(FIXTURES_DIR, "prd", "sample.md")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    anchors = ANCHOR_PATTERN.findall(content)
    assert len(anchors) >= 5, f"样例锚点过少: {anchors}"
    # 全局唯一
    assert len(anchors) == len(set(anchors)), f"锚点 ID 重复: {anchors}"


def test_health_endpoint_exists():
    """server 工厂应注册 /api/health（保证 T0.1 行为不被后续改动破坏）。"""
    from server.app import create_app

    app = create_app()
    rules = [r.rule for r in app.url_map.iter_rules()]
    assert "/api/health" in rules
