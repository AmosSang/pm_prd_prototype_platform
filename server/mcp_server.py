"""MCP Server（T12.3 只读五工具 / T12.4 上传二工具，PRD §5.2 / AGENTS.md §7）。

架构：
- 独立进程，streamable-http transport（compose 内 :8082），经 Nginx ``/mcp``
  反代暴露；工具函数经 ``server.mcp_loopback`` 回环调 Flask API，透传
  Agent 请求头里的 ``Authorization: Bearer ppp_...``（权限 = 绑定用户）。
- 工具保持原子粒度（一个工具做一件事），自然语言到工具的组合交给 Agent
  对话层；使用引导集中在 instructions 与工具描述里。
- 上传类工具：文件内容以 base64 入参（Agent 本机读文件），工具内转 multipart
  回环调 Flask 既有上传接口——覆盖语义/安全校验全链路复用（T12.4 POC 结论
  见 docs/poc-report-mcp-upload.md）；操作写审计日志 data/logs/mcp-ops.jsonl。

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
- ``MCP_MAX_REQUEST_BODY_BYTES``：streamable-http 请求体上限（默认 160 MiB，
  覆盖 base64 大参数上传；SDK 默认 4 MiB 不够用，见 T12.4 POC 报告）
- ``DATA_DIR``：审计日志落盘根（默认与 Flask 共用，compose 内同为 /data）
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sys
import time

# 支持 `python server/mcp_server.py` 直接运行（platform/ 根加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.dependencies import get_http_headers, get_http_request  # noqa: E402

from server.config import DATA_DIR, PRD_MAX_BYTES, PROTO_ZIP_MAX_BYTES  # noqa: E402
from server.mcp_loopback import UPLOAD_TIMEOUT, ApiResult, call_api  # noqa: E402

STATUSES = ("待确认", "已确认待修改", "已修改", "忽略", "延后再改")

# ─── HTTP 请求体上限补丁（T12.4 POC 结论）────────────────────────────
# FastMCP 3.4.7 未透出 streamable-http 的请求体上限，底层 mcp SDK 默认
# 4 MiB（4194304）——base64 大参数（原型 zip）连 5MB 级文件都会被 413
# 秒拒（POC 实测，见 docs/poc-report-mcp-upload.md）。这里在构造 session
# manager 时注入可配置上限；默认 160 MiB 覆盖「100MB zip → ~134MB base64」
# 的 Flask 侧全量程。⚠️ fastmcp 升级需回归本补丁（依赖其 http 模块的内部
# 构造点与基类签名）。
import fastmcp.server.http as _fm_http  # noqa: E402
from mcp.server.streamable_http_manager import (  # noqa: E402
    StreamableHTTPSessionManager as _SdkSessionManager,
)

MCP_MAX_REQUEST_BODY_BYTES = int(
    os.environ.get("MCP_MAX_REQUEST_BODY_BYTES", str(160 * 1024 * 1024))
)


class _BigBodySessionManager(_fm_http.FastMCPStreamableHTTPSessionManager):
    """注入请求体上限的 session manager（替代 FastMCP 原构造点）。

    FastMCP 子类 __init__ 不接受 body 上限参数，无法直接 super() 透传——
    这里复刻其自身初始化（仅 `_shared_event_store` 一条状态，属性实现
    继承自原类），直接调 SDK 基类并带上上限。
    """

    def __init__(self, *args, **kwargs):
        self._shared_event_store = None
        kwargs.setdefault("max_request_body_size", MCP_MAX_REQUEST_BODY_BYTES)
        _SdkSessionManager.__init__(self, *args, **kwargs)


_fm_http.FastMCPStreamableHTTPSessionManager = _BigBodySessionManager

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
- 「把 {目录} 的最新原型传到 {项目}」→ ① 读本机 zip 文件并转 base64；
  ② upload_prototype（需要创建者/协作者权限）。**上传即覆盖旧版本**，
  调用前必须向用户复述确认。若本机没有现成 zip、需基于平台现有原型改，
  先走下面的「AI 改原型闭环」第 1 步。
- 「更新 XX 项目的 PRD 文档」→ upload_prd（同样为覆盖语义，先复述确认）

## AI 改原型闭环（读 → 改 → 回传 → 评审）
1. **取源码**：download_prototype → 返回 zip 下载地址与 curl 命令 → 在本机下载解压
   （包内为 `{slug}-prototype/prototype/…`）。原型动辄数十 MB，**不要试图让本工具
   直接返回文件内容**。
2. **改文件**：在本机修改 HTML/CSS。**必须保持 `data-pa` 锚点 ID 不变**——改名或删除
   会让 PRD↔原型双向联动与锚点对账断裂；新增元素如需联动，先与用户确认锚点命名。
3. **回传**：upload_prototype（zip 重新打包 → base64 → 传入）。覆盖旧版本，先复述确认。
4. **评审**：用户在平台评论 → get_all_comments（可带 status="已确认待修改"）→
   回到第 1 步，形成闭环。

## 上传覆盖语义（重要）
- upload_prototype / upload_prd 都是**上传即覆盖**：成功即替换旧版本，
  旧版本不保留、不可恢复。调用前先向用户复述「目标项目 + 将覆盖现有版本」并得到确认。
- 文件内容以 base64 传入（Agent 需先读取本机文件）。大小上限：原型 zip ≤100MB、
  PRD ≤5MB；**超限时请求会在传输层被拒（HTTP 413，非结构化错误）**，
  请在调用前预检文件大小，超限时告知用户先压缩。
- 安全校验失败（zip 结构异常/路径穿越/解压炸弹/软链等）时返回结构化错误，
  且**旧版本完好**——可放心提示用户修复后重试。

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


# ─── 上传辅助（T12.4）────────────────────────────────────────────────

def _audit(tool: str, bearer: str | None, project_slug: str, size: int | None, ok: bool, error_code: str | None = None) -> None:
    """上传操作审计（data/logs/mcp-ops.jsonl：时间/用户/项目/工具/文件大小）。

    best-effort：审计失败不阻断主流程（stderr 提示即可）。
    用户身份经 /api/me 回环获取（Bearer 双轨认证，与网页端同一账号）。
    路径按 DATA_DIR 调用时计算（测试可 monkeypatch 注入临时目录）。
    """
    try:
        me = call_api("GET", "/api/me", bearer)
        user = str(((me.data or {}) if me.ok else {}).get("email") or "")
        line = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user": user,
            "project": project_slug,
            "tool": tool,
            "file_size": size,
            "ok": ok,
        }
        if error_code:
            line["error_code"] = error_code
        log_path = os.path.join(DATA_DIR, "logs", "mcp-ops.jsonl")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 —— 审计不阻断主流程
        print(f"[mcp] audit log failed: {e}", file=sys.stderr)


def _platform_base() -> str:
    """平台对外基址（MCP 进程独立于 Flask，需自行推导下载直链的 origin）。

    优先级：环境变量 ``PLATFORM_PUBLIC_ORIGIN``（生产推荐显式配置）> 当前 MCP
    请求的 scheme + Host（Nginx 同域反代时即平台域名）。本地 dev 若 MCP 与
    Flask 不同端口，需显式配置该变量，否则回落到 MCP 自身地址。
    """
    env = (os.environ.get("PLATFORM_PUBLIC_ORIGIN") or "").strip().rstrip("/")
    if env:
        return env
    try:
        req = get_http_request()
        host = (req.headers.get("host") or "").strip()
        if host:
            return f"{req.url.scheme or 'http'}://{host}"
    except Exception:  # noqa: BLE001 —— 非 HTTP 上下文（如内存测试）取不到
        pass
    return ""


def _decode_base64(file_base64: str, limit: int) -> tuple[bytes | None, dict | None]:
    """base64 入参 → bytes（含大小预检与友好错误）。

    先按 base64 膨胀率预估大小、超限不开解码（省内存）；容忍换行/空白
    （macOS `base64` 输出默认折行）；非法编码给可操作提示。
    """
    s = re.sub(r"\s+", "", str(file_base64 or ""))
    if not s:
        return None, _err(
            "bad_request", "文件内容（base64）不能为空",
            "请先读取本机文件并做 base64 编码后传入（如 `base64 -i prototype.zip`）",
        )
    est = len(s) * 3 // 4
    if est > limit + 1024:
        return None, _err(
            "too_large",
            f"文件约 {est // (1024 * 1024)}MB，超过 {limit // (1024 * 1024)}MB 上限",
            "请先压缩文件再重试（平台原型 zip 上限 100MB、PRD 上限 5MB）",
        )
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None, _err(
            "bad_request", "file_base64 不是合法的 base64 内容",
            "请确认传入的是文件原文的 base64 编码（可用 `base64 -i 文件` 生成）",
        )
    if not raw:
        return None, _err("bad_request", "解码后文件为空", "请确认源文件有内容后重试")
    if len(raw) > limit:
        return None, _err(
            "too_large",
            f"文件 {len(raw) // (1024 * 1024)}MB 超过 {limit // (1024 * 1024)}MB 上限",
            "请先压缩文件再重试",
        )
    return raw, None


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


# ─── 上传二工具（T12.4）──────────────────────────────────────────────

@mcp.tool(name="upload_prototype")
def upload_prototype(project: str, file_base64: str, file_name: str = "prototype.zip") -> dict:
    """上传原型 zip 到项目——**上传即覆盖旧版本（不可恢复），调用前必须向用户复述确认**。

    project：项目 slug（project_id）或名称；模糊匹配多命中返回候选，需先确认。
    file_base64：原型 zip 文件内容的 base64 编码（文件在用户本机，Agent 先读取
      再传入；可含换行）。大小上限 100MB（base64 约 134MB）——超限会在传输层
      被拒（HTTP 413），请先预检文件大小。
    file_name：可选展示文件名。

    权限：需要创建者或协作者身份。zip 安全校验（路径穿越/解压总量/条目数/
    软链）失败时返回错误且**旧版本完好**；通过后原子替换，对账自动重算。
    """
    bearer = _bearer()
    # 解码/大小预检放最前（大文件快速失败，不做无谓回环）
    raw, err = _decode_base64(file_base64, PROTO_ZIP_MAX_BYTES)
    if err:
        _audit("upload_prototype", bearer, str(project or ""), None, ok=False, error_code=str(err.get("error_code")))
        return err
    p, err = _resolve_project(project, bearer)
    if err:
        return err
    files = {"zip": (file_name or "prototype.zip", raw, "application/zip")}
    r = call_api("POST", f"/api/projects/{p['id']}/prototype", bearer, files=files, timeout=UPLOAD_TIMEOUT)
    if not r.ok:
        mapped = _map_api_error(r, action="上传原型")
        _audit("upload_prototype", bearer, str(p.get("project_id")), len(raw), ok=False, error_code=str(mapped.get("error_code")))
        return mapped
    _audit("upload_prototype", bearer, str(p.get("project_id")), len(raw), ok=True)
    d = r.data or {}
    return {
        "project": _project_row(d if d.get("project_id") else p),
        "file_size": len(raw),
        "note": "已覆盖旧版本原型，查看器立即可见新版本",
    }


@mcp.tool(name="upload_prd")
def upload_prd(project: str, file_base64: str, file_name: str = "PRD.md") -> dict:
    """上传 PRD markdown 文档到项目——**上传即替换旧文档，调用前必须向用户复述确认**。

    project：项目 slug（project_id）或名称；模糊匹配多命中返回候选，需先确认。
    file_base64：markdown 文件内容的 base64 编码。大小上限 5MB。
    file_name：编译文件名，需以 .md 结尾（平台按该名保存）。

    权限：需要创建者或协作者身份；成功即替换 prd/ 下的旧文档（唯一一份约定）。
    """
    bearer = _bearer()
    name = (file_name or "").strip() or "PRD.md"
    if not name.lower().endswith(".md"):
        return _err("bad_request", "PRD 文件名需以 .md 结尾", "请提供 .md 文件名（如 需求文档.md）")
    raw, err = _decode_base64(file_base64, PRD_MAX_BYTES)
    if err:
        _audit("upload_prd", bearer, str(project or ""), None, ok=False, error_code=str(err.get("error_code")))
        return err
    p, err = _resolve_project(project, bearer)
    if err:
        return err
    files = {"file": (os.path.basename(name), raw, "text/markdown")}
    r = call_api("POST", f"/api/projects/{p['id']}/prd", bearer, files=files, timeout=UPLOAD_TIMEOUT)
    if not r.ok:
        mapped = _map_api_error(r, action="上传 PRD")
        _audit("upload_prd", bearer, str(p.get("project_id")), len(raw), ok=False, error_code=str(mapped.get("error_code")))
        return mapped
    _audit("upload_prd", bearer, str(p.get("project_id")), len(raw), ok=True)
    d = r.data or {}
    return {
        "project": _project_row(d if d.get("project_id") else p),
        "file_name": os.path.basename(name),
        "file_size": len(raw),
        "note": "已替换旧 PRD 文档",
    }


@mcp.tool(name="download_prototype")
def download_prototype(project: str, base_url: str | None = None, include_token: bool = False) -> dict:
    """获取项目原型源码（zip）的下载方式——「AI 改原型」闭环的第一步。

    典型流程：本工具取下载命令 → 本机下载解压 → 改 HTML/CSS（**保持 data-pa
    锚点 ID 不变**，否则 PRD↔原型联动会断）→ upload_prototype 回传（覆盖旧版）。

    为什么不直接返回文件内容：原型常有数十 MB，base64 传输膨胀 33% 且占满
    上下文；因此返回**下载地址 + 现成 curl 命令**，请在本机执行下载。

    project：项目 slug（project_id）或名称；模糊匹配多命中返回候选，需先确认。
    base_url：可选，平台基址（如 https://your.domain）；留空按当前连接自动推导。
    include_token：可选，默认 false——true 时额外返回已填好你当前 Token 的
      可直接执行命令（便于一步下载；该命令含凭证，请勿写入文件或外传）。

    返回：download_url / api_path / curl 命令 / zip 内目录结构与入口页数量。
    """
    bearer = _bearer()
    p, err = _resolve_project(project, bearer)
    if err:
        return err
    # 先确认有原型内容，避免让 Agent 下载到空包
    ov = call_api("GET", f"/api/projects/{p['id']}/overview", bearer)
    if not ov.ok:
        return _map_api_error(ov, action="读取项目概览")
    entries = (ov.data or {}).get("proto_entries") or []
    if not entries:
        _audit("download_prototype", bearer, str(p.get("project_id")), None, ok=False, error_code="prototype_empty")
        return _err("prototype_empty", "项目还没有原型内容", "请先上传原型 zip（网页端或 upload_prototype 工具）")

    base = (base_url or _platform_base()).rstrip("/")
    if not base:
        return _err(
            "base_url_unknown", "无法自动确定平台基址",
            "请显式传入 base_url（如 https://your.domain）；本地自建部署也可用 http://127.0.0.1:8081",
        )
    slug = str(p.get("project_id"))
    file_name = f"{slug}-prototype.zip"
    api_path = f"/api/projects/{p['id']}/prototype/export"
    url = f"{base}{api_path}"
    _audit("download_prototype", bearer, slug, None, ok=True)
    out = {
        "project": _project_row(p),
        "file_name": file_name,
        "download_url": url,
        "api_path": api_path,
        "curl": f"curl -sSL -H 'Authorization: Bearer <你的Token>' -o {file_name} '{url}'",
        "zip_structure": f"{slug}-prototype/prototype/…（顶层一层，解压不污染工作目录）",
        "entry_count": len(entries),
        "entries": entries[:20],
        "note": "本机执行 curl 下载并解压；改完用 upload_prototype 回传（覆盖旧版本，先向用户复述确认）。PRD 原文用 get_prd_content。",
    }
    if include_token and bearer:
        out["curl_ready"] = f"curl -sSL -H 'Authorization: Bearer {bearer}' -o {file_name} '{url}'"
    return out


def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8082"))
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
