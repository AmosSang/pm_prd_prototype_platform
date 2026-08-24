"""评论 payload schema 契约测试（T4.1）。

契约锚点：《产品方案-V1.md》§3.3 评论数据结构（DOM 定位字段组）+
《一期技术实现方案-V1.md》§2.3 payload 采集表。
任务卡验收：契约测试（payload 字段完整性）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.reviews import validate_comment_payload

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
