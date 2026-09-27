"""MCP Server（T12.3）：FastMCP 壳 + 只读五工具（PRD §5.2 / AGENTS.md §7）。

架构：
- 独立进程，streamable-http transport（compose 内 :8082），经 Nginx ``/mcp``
  反代暴露；工具函数经 ``server.mcp_loopback`` 回环调 Flask API，透传
  Agent 请求头里的 ``Authorization: Bearer ppp_...``（权限 = 绑定用户）。
- 工具保持原子粒度（一个工具做一件事），自然语言到工具的组合交给 Agent
  对话层；使用引导集中在 instructions 与工具描述里。

约定：
- 返回一律结构化 JSON；错误统一 ``{error_code, message, hint}`` 三段式
  （作为正常返回值携带，不抛协议错——Agent 读结构化字段即可自适应）。
- ``project`` 参数：slug 精确 > 名称精确 > 名称包含；多命中返回候选列表
  （error_code=ambiguous_project），不猜测。
- 工具清单与字段口径以 AGENTS.md §7 / PRD §6.3 为准；新增工具走「新增可选、
  不破坏既有」演进。

本地开发运行（Flask 先在 :8081 起着）::

    server/.venv/bin/python server/mcp_server.py

环境变量：
- ``PPP_API_BASE``：Flask 回环地址（默认 http://127.0.0.1:8081；compose 用 http://server:8081）
- ``MCP_HOST`` / ``MCP_PORT``：MCP 监听（默认 0.0.0.0:8082）
"""
from __future__ import annotations

import os
import sys

# 支持 `python server/mcp_server.py` 直接运行（platform/ 根加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.dependencies import get_http_headers  # noqa: E402

from server.mcp_loopback import ApiResult, call_api  # noqa: E402

STATUSES = ("待确认", "已确认待修改", "已修改", "忽略", "延后再改")

MCP_INSTRUCTIONS = """本服务是「产品方案展示平台」的 MCP 接入：让 AI 以你的平台账号身份读取项目资料
（只读工具：项目列表 / 项目概览 / 全部评论 / PRD 原文 / 锚点对账）。

## 平台概念速览
- 项目：一个产品方案单元，由「原型」（可交互 HTML）与「PRD」（一份 markdown 文档）组成。
  每个项目有唯一 slug（project_id）与名称。工具里的 project 参数接受 slug 或名称。
- 锚点：原型元素上的 data-pa 与 PRD 里的 <!-- pa: xxx --> 注释按同名配对，
  建立「文档段落 ↔ 原型元素」的双向关联；锚点对账用于检查两侧是否一致。
- 评论：挂在原型元素（dom）/ 原型页面（page）/ PRD 段落（doc_block）上的修改意见。
  五种状态：待确认 / 已确认待修改 / 已修改 / 忽略 / 延后再改；
  「已确认待修改」是交付修改的标准范围。

## 常用意图 → 工具组合
- 「我有哪些项目 / 找一下 XX 项目」→ list_projects（然后按名称/ slug 继续操作）
- 「拉取 XX 项目的全部评论 / 整理修改计划」→ get_all_comments
  （默认全部；可带 status 参数按状态筛选。拿到评论后建议按 comment-revision-plan
  工作流逐条梳理：定位段落/元素、判断 PRD 与原型联动、产出修改计划）
- 「看看 XX 项目的 PRD / 整体情况 / 对账」→ get_prd_content / get_project_overview / get_reconcile

## 项目匹配与确认
- 所有带 project 参数的工具：按 slug 精确 → 名称精确 → 名称包含依次匹配。
- 多命中时不猜测：返回 error_code=ambiguous_project 与候选列表，
  请向用户复述候选并确认后，改用 project_id（slug）重试。
- 不确定项目标识时，先调 list_projects。

## 错误处理
- 所有错误返回 {error_code, message, hint} 三段式；请向用户清楚说明原因，
  并按 hint 给出的下一步行动处理（如 Token 失效 → 引导用户去「Agent 接入」页重新生成）。
"""

mcp = FastMCP(name="产品方案展示平台", instructions=MCP_INSTRUCTIONS)


# ─── 通用辅助 ────────────────────────────────────────────────────────

def _bearer() -> str | None:
    """从当前 MCP HTTP 请求头取 Bearer Token（fastmcp 默认剥离 authorization，须显式 include）。"""
    auth = get_http_headers(include={"authorization"}).get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[len("Bearer "):].strip()
        return token or None
    return None


def _err(error_code: str, message: str, hint: str, **extra) -> dict:
    """错误三段式（可附结构化附加字段，如 candidates）。"""
    out = {"error_code": error_code, "message": message, "hint": hint}
    out.update(extra)
    return out


#: HTTP 状态码 → 三段式错误（message 优先用 Flask 的 msg）
_STATUS_ERRORS: dict[int, tuple[str, str, str]] = {
    401: ("unauthorized", "Token 无效或已撤销", "请在平台「Agent 接入」页确认 Token 状态；被撤销的 Token 无法恢复，请重新生成并更新 MCP 配置"),
    403: ("no_permission", "无权限", "当前账号没有该操作所需角色（上传类需要创建者或协作者）；可让项目创建者添加你为协作者"),
    404: ("not_found", "资源不存在", "请用 list_projects 确认项目标识；项目可能已被删除"),
    410: ("project_content_missing", "项目内容不存在", "项目可能尚未上传内容（原型/PRD），请先在平台上传后重试"),
    413: ("too_large", "文件超出大小限制", "原型 zip ≤100MB、PRD markdown ≤5MB、截图 ≤10MB"),
    429: ("rate_limited", "请求过于频繁", "单 Token 限流 60 次/分钟，请稍等约 1 分钟后重试"),
}


def _map_api_error(r: ApiResult, *, action: str) -> dict:
    """Flask 响应 → 三段式错误（网络失败/HTTP 状态码两路）。"""
    if r.network_error:
        return _err(
            "platform_unavailable",
            f"平台服务不可达：{r.raw_text}",
            "平台可能正在升级或重启，请稍后重试",
        )
    known = _STATUS_ERRORS.get(r.status_code)
    if known:
        code, default_msg, hint = known
        return _err(code, r.msg or default_msg, hint)
    return _err(
        "upstream_error",
        f"{action}失败（HTTP {r.status_code}）{('：' + r.msg) if r.msg else ''}",
        "请稍后重试；若持续失败请联系平台管理员",
    )


def _project_row(p: dict) -> dict:
    """项目条目（工具出参统一口径）。"""
    if p.get("is_creator"):
        role = "创建者"
    elif p.get("is_manager"):
        role = "协作者"
    else:
        role = "参与者"
    return {
        "project_id": p.get("project_id"),  # slug：其他工具的 project 参数优先用它
        "name": p.get("name"),
        "creator": (p.get("creator") or {}).get("name", ""),
        "my_role": role,
        "comment_count": p.get("comment_count", 0),
        "member_count": p.get("member_count", 0),
        "content_updated_at": p.get("content_updated_at"),
    }


def _fetch_projects(bearer: str | None) -> tuple[list[dict] | None, dict | None]:
    """拉取当前用户可见项目列表；返回 (rows, error)。"""
    r = call_api("GET", "/api/projects", bearer)
    if not r.ok:
        return None, _map_api_error(r, action="获取项目列表")
    return list(r.data or []), None


def _resolve_project(project: str, bearer: str | None) -> tuple[dict | None, dict | None]:
    """项目匹配（PRD §5.2.3）：slug 精确 > 名称精确 > 名称包含。

    返回 (命中项目行, None) 或 (None, 三段式错误)。
    多命中返回 ambiguous_project + candidates，不猜测。
    """
    q = str(project or "").strip()
    if not q:
        return None, _err(
            "bad_request", "project 参数不能为空",
            "请传项目 slug（project_id）或名称；可先用 list_projects 查询",
        )
    rows, err = _fetch_projects(bearer)
    if err:
        return None, err

    hit = [p for p in rows if p.get("project_id") == q]
    if not hit:
        hit = [p for p in rows if p.get("name") == q]
    if not hit:
        ql = q.lower()
        hit = [p for p in rows if ql in str(p.get("name") or "").lower()]

    if not hit:
        names = "、".join(str(p.get("name") or "") for p in rows[:20]) or "（无）"
        return None, _err(
            "project_not_found", f"未找到匹配「{q}」的项目",
            f"当前账号可访问的项目：{names}。也可用 list_projects 查看完整列表",
        )
    if len(hit) > 1:
        return None, _err(
            "ambiguous_project", f"「{q}」匹配到 {len(hit)} 个项目，本次未执行任何操作",
            "请向用户复述候选并确认后，改用 project_id（slug）精确指定",
            candidates=[_project_row(p) for p in hit],
        )
    return hit[0], None


def _comment_item(c: dict) -> dict:
    """评论条目 → 与 reviews/comments/*.json 同构（payload 全量 + 权威列覆盖）。

    payload_json 即创建时写入评论文件的完整 JSON（同一份契约）；状态/作者
    以 DB 列为准（创建后可能有流转/改名），保证与网页抽屉展示一致。
    """
    item = dict(c.get("payload") or {})
    item["comment_id"] = c.get("comment_id")
    item["author"] = c.get("author_name")
    item["status"] = c.get("status")
    if not item.get("created_at"):
        item["created_at"] = c.get("created_at")
    return item


# ─── 只读五工具（T12.3）──────────────────────────────────────────────

@mcp.tool(name="list_projects")
def list_projects() -> dict:
    """列出当前账号可访问的全部项目。

    返回每个项目的 project_id（slug，作为其他工具 project 参数的精确值）、名称、
    创建者、我的角色（创建者/协作者/参与者）、评论数、最近内容更新时间。
    不确定项目标识时先调用本工具。
    """
    rows, err = _fetch_projects(_bearer())
    if err:
        return err
    return {"count": len(rows), "projects": [_project_row(p) for p in rows]}


@mcp.tool(name="get_project_overview")
def get_project_overview(project: str) -> dict:
    """获取项目概览：文档列表、原型入口页、页面地图、锚点索引、对账摘要。

    project：项目 slug（project_id）或名称。名称模糊匹配多命中时返回候选
    （error_code=ambiguous_project），需先向用户确认再用 slug 重试。
    """
    bearer = _bearer()
    p, err = _resolve_project(project, bearer)
    if err:
        return err
    r = call_api("GET", f"/api/projects/{p['id']}/overview", bearer)
    if not r.ok:
        return _map_api_error(r, action="获取项目概览")
    d = r.data or {}
    return {
        "project": _project_row(d.get("project") or p),
        "docs": d.get("docs", []),
        "proto_entries": d.get("proto_entries", []),
        "page_map": d.get("page_map", []),
        "proto_anchor_index": d.get("proto_anchor_index", {}),
        "reconcile_summary": d.get("reconcile_summary"),
    }


@mcp.tool(name="get_all_comments")
def get_all_comments(project: str, status: str | None = None) -> dict:
    """拉取项目的全部评论（与项目目录 reviews/comments/*.json 同构，供整理修改计划使用）。

    project：项目 slug 或名称（模糊匹配多命中返回候选）。
    status：可选，按五态筛选——待确认 / 已确认待修改 / 已修改 / 忽略 / 延后再改；
    「已确认待修改」是交付修改的标准范围。
    注意：截图 PNG 不随本工具返回，评论中 screenshot 为项目内相对路径；
    需要视觉上下文时以 outer_html / text_excerpt / anchor_id 定位。
    """
    bearer = _bearer()
    if status and status not in STATUSES:
        return _err("bad_request", f"非法状态「{status}」", "可选状态：" + "、".join(STATUSES))
    p, err = _resolve_project(project, bearer)
    if err:
        return err
    params = {"status": status} if status else None
    r = call_api("GET", f"/api/projects/{p['id']}/comments", bearer, params=params)
    if not r.ok:
        return _map_api_error(r, action="拉取评论")
    items = [_comment_item(c) for c in (r.data or [])]
    return {"project": _project_row(p), "count": len(items), "comments": items}


@mcp.tool(name="get_prd_content")
def get_prd_content(project: str) -> dict:
    """读取项目的 PRD markdown 原文（返回 file 文件名与 content 全文）。

    project：项目 slug 或名称（模糊匹配多命中返回候选）。
    项目通常只有一份 PRD；如有多份，返回第一份并列出其余文件名（other_docs）。
    """
    bearer = _bearer()
    p, err = _resolve_project(project, bearer)
    if err:
        return err
    ov = call_api("GET", f"/api/projects/{p['id']}/overview", bearer)
    if not ov.ok:
        return _map_api_error(ov, action="读取项目概览")
    docs = (ov.data or {}).get("docs") or []
    if not docs:
        return _err("no_prd", "项目还没有 PRD 文档", "请先在平台网页端上传 PRD 文档后重试")
    file = docs[0]
    r = call_api("GET", f"/api/projects/{p['id']}/prd", bearer, params={"file": file})
    if not r.ok:
        return _map_api_error(r, action="读取 PRD")
    d = r.data or {}
    out: dict = {
        "project": _project_row(p),
        "file": d.get("file", file),
        "content": d.get("content", ""),
    }
    if len(docs) > 1:
        out["other_docs"] = docs[1:]
        out["note"] = f"项目共有 {len(docs)} 份文档，已返回第一份（{file}）"
    return out


@mcp.tool(name="get_reconcile")
def get_reconcile(project: str) -> dict:
    """锚点对账明细：匹配 / 原型缺失（PRD 有锚点、原型没有）/ 未描述（原型有锚点、PRD 没有）
    三态清单 + 重复 ID + 页面地图坏引用。用于检查 PRD 与原型的一致性。

    project：项目 slug 或名称（模糊匹配多命中返回候选）。
    """
    bearer = _bearer()
    p, err = _resolve_project(project, bearer)
    if err:
        return err
    r = call_api("GET", f"/api/projects/{p['id']}/reconcile", bearer)
    if not r.ok:
        return _map_api_error(r, action="获取对账明细")
    return {"project": _project_row(p), **(r.data or {})}


def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8082"))
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
