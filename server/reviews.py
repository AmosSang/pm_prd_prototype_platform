"""评论系统（T4.1 起）。

T4.1：评论 payload（DOM 定位字段组）schema 校验——契约见《产品方案-V1.md》
§3.3 与《一期技术实现方案-V1.md》§2.3。T4.2/T4.3 将加入评论 CRUD、
落仓队列与状态流转（reviews 蓝图届时注册进 app.py）。

字段口径（bridge.js collectPayload 同源约定）：
  target_type        dom（原型元素）/ page（页面根）/ doc_block（PRD 块级元素）
  prototype_page     相对 prototype/ 的路径（如 pages/login.html）；doc_block 可空
  anchor_id          目标自身 data-pa，未命中为空串（合法）
  nearest_anchor_id  最近 [data-pa] 祖先，无则空串（合法）
  css_path           锚点祖先用属性选择器短路的结构链；doc_block 可空
  outer_html         目标 outerHTML + ≤2 层祖先开/闭标签，>4KB 截断
  text_excerpt       文本 200 字截断（表单控件取 value/placeholder）
  interaction_state  {modal_open, viewport, scroll_y, route}
"""
import re

# target_type 三类评论宿主（产品方案 §3.3）
TARGET_TYPES = ("dom", "page", "doc_block")

# bridge 侧 outer_html 截断 4096 + 尾部省略标记；schema 侧留少量余量
OUTER_HTML_MAX = 4100
# bridge 侧 text_excerpt 截断 200 + 省略号
TEXT_EXCERPT_MAX = 300

# interaction_state 必填子字段与类型（bool 是 int 子类，须显式排除）
_INTERACTION_FIELDS = {"modal_open": bool, "viewport": str, "scroll_y": int, "route": str}

_TYPE_NAMES = {bool: "布尔值", str: "字符串", int: "整数"}

_VIEWPORT_RE = re.compile(r"^\d+x\d+$")

_STR_FIELDS = (
    "prototype_page",
    "anchor_id",
    "nearest_anchor_id",
    "css_path",
    "outer_html",
    "text_excerpt",
)


def validate_comment_payload(payload: object) -> list[str]:
    """校验评论 DOM 定位 payload，返回错误列表（空列表 = 合法）。

    T4.2 的 POST /api/projects/{id}/comments 将在提交前调用本函数；
    契约漂移由 tests/test_reviews.py 固定用例兜底（make check 必红）。
    """
    if not isinstance(payload, dict):
        return ["payload 必须是 JSON 对象"]

    errors: list[str] = []

    target_type = payload.get("target_type")
    if target_type not in TARGET_TYPES:
        errors.append(f"target_type 必须是 {TARGET_TYPES} 之一，当前：{target_type!r}")

    for field in _STR_FIELDS:
        if field not in payload:
            errors.append(f"缺少必填字段 {field}")
        elif not isinstance(payload[field], str):
            errors.append(f"{field} 必须是字符串")

    # dom/page 类评论必须落在原型侧；doc_block（文档评论）无原型定位，允许为空
    if target_type in ("dom", "page"):
        if isinstance(payload.get("prototype_page"), str) and not payload["prototype_page"]:
            errors.append("target_type 为 dom/page 时 prototype_page 不能为空")
        if isinstance(payload.get("css_path"), str) and not payload["css_path"]:
            errors.append("target_type 为 dom/page 时 css_path 不能为空")

    outer_html = payload.get("outer_html")
    if isinstance(outer_html, str) and len(outer_html) > OUTER_HTML_MAX:
        errors.append(f"outer_html 超长（>{OUTER_HTML_MAX} 字符，bridge 侧应截断）")
    text_excerpt = payload.get("text_excerpt")
    if isinstance(text_excerpt, str) and len(text_excerpt) > TEXT_EXCERPT_MAX:
        errors.append(f"text_excerpt 超长（>{TEXT_EXCERPT_MAX} 字符）")

    ist = payload.get("interaction_state")
    if not isinstance(ist, dict):
        errors.append("interaction_state 必须是对象")
    else:
        for field, typ in _INTERACTION_FIELDS.items():
            if field not in ist:
                errors.append(f"interaction_state 缺少 {field}")
                continue
            value = ist[field]
            if typ is int and isinstance(value, bool):
                errors.append(f"interaction_state.{field} 必须是整数（bool 不算）")
            elif not isinstance(value, typ):
                errors.append(
                    f"interaction_state.{field} 必须是{_TYPE_NAMES[typ]}，"
                    f"当前：{type(value).__name__}"
                )
        viewport = ist.get("viewport")
        if isinstance(viewport, str) and not _VIEWPORT_RE.match(viewport):
            errors.append("interaction_state.viewport 格式应为 宽x高（如 1440x900）")

    return errors
