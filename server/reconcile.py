"""锚点对账（T3.3，技术方案 §2.6）。

两侧均为服务端静态解析（不依赖浏览器在线状态，可随时重算）：
- PRD 侧：正则扫 prd/*.md 的 `<!-- pa: xxx -->`，附最近标题链（h2-h6，
  h1 是文档题跳过——明细里 file 列已体现）
- 原型侧：BeautifulSoup 解析 prototype/**/*.html 的 [data-pa]，附 CSS 路径
- 比对：两侧按 ID 匹配 → 三态（匹配 / 原型缺失 / 未描述）
- 附加检查：PRD 重复 ID（跨文件）、原型重复 ID、页面地图引用文件不存在

口径与前端 anchor-plugin、anchor-checker skill 一致：锚点 ID 为 kebab-case，
正则 `<!--\\s*pa:\\s*([a-z0-9-]+)\\s*-->`。

结果每次请求现算——仓库文件量小（几十个文件内），解析毫秒级；技术方案
「缓存于内存」为性能预留，量级到了再加。运行时锚点（ANCHOR_REPORT，
SPA 动态生成场景）暂不并入，留开放问题 2 一并处理。
"""
import re

from bs4 import BeautifulSoup

# 与前端 anchor-plugin.ts / 技术方案 §2.4 同口径
PRD_ANCHOR_RE = re.compile(r"<!--\s*pa:\s*([a-z0-9-]+)\s*-->")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def extract_prd_anchors(doc_paths: list[str], read_file) -> list[dict]:
    """提取 PRD 锚点：[{id, file, doc_path, line}]。

    doc_path 为最近标题链（"/" 连接，h2 起）。锚点在标题行上时链含该标题
    （如「5 功能需求/5.1 登录页」）。重复 ID 不去重——全部出现都返回，
    由 compute_reconcile 单独报告。
    """
    out: list[dict] = []
    for rel in doc_paths:
        try:
            text = read_file(rel)
        except Exception:
            continue
        stack: list[tuple[int, str]] = []  # (级别, 标题)
        for lineno, line in enumerate(text.split("\n"), start=1):
            m = _HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                # 标题文本里的锚点注释剔掉（如「### 5.1 登录页 <!-- pa: x -->」）
                title = PRD_ANCHOR_RE.sub("", m.group(2)).strip()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
            for am in PRD_ANCHOR_RE.finditer(line):
                chain = "/".join(t for lv, t in stack if lv >= 2)
                out.append({"id": am.group(1), "file": rel, "doc_path": chain, "line": lineno})
    return out


def _css_path(el) -> str:
    """元素向上的简短 CSS 路径（≤4 级，到 body 止）。

    每级 tag#id / tag.class / tag（同名兄弟 >1 时补 :nth-of-type(n)）。
    """
    parts: list[str] = []
    node = el
    while (
        node is not None
        and getattr(node, "name", None)
        and node.name not in ("body", "html", "[document]")
        and len(parts) < 4
    ):
        seg = node.name
        if node.get("id"):
            seg += "#" + node["id"]
        elif node.get("class"):
            seg += "." + ".".join(node["class"])
        else:
            # 同名兄弟 >1 时全都要带 nth-of-type 才唯一（含首个）
            total = 0
            idx = 0
            for sib in node.previous_siblings:
                if getattr(sib, "name", None) == node.name:
                    total += 1
            for sib in node.next_siblings:
                if getattr(sib, "name", None) == node.name:
                    total += 1
            if total > 0:
                for sib in node.previous_siblings:
                    if getattr(sib, "name", None) == node.name:
                        idx += 1
                seg += f":nth-of-type({idx + 1})"
        parts.append(seg)
        node = node.parent
    if not parts:
        return getattr(node, "name", "") or ""
    return " > ".join(reversed(parts))


def extract_proto_anchors(proto_files: list[str], read_file) -> list[dict]:
    """提取原型 DOM 锚点：[{id, file, css_path}]。重复 ID 不去重。

    BeautifulSoup 解析（跳过注释/脚本内字符串，比正则准）；
    每个带 data-pa 的元素一条记录。
    """
    out: list[dict] = []
    for rel in proto_files:
        try:
            html = read_file(rel)
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.find_all(attrs={"data-pa": True}):
            pa = (el.get("data-pa") or "").strip()
            if pa:
                out.append({"id": pa, "file": rel, "css_path": _css_path(el)})
    return out


def compute_reconcile(
    prd: list[dict],
    proto: list[dict],
    page_map: list[dict],
    proto_files: list[str],
) -> dict:
    """三态比对 + 附加检查。

    返回 {summary, matched, missing_in_proto, undescribed,
    duplicate_prd, duplicate_proto, map_broken}；明细条目结构：
    - matched: {id, prd: {...首次出现}, proto: {...首次出现}}
    - missing_in_proto: {id, prd: {...}}   PRD 有、原型无
    - undescribed: {id, proto: {...}}      原型有、PRD 无（超纲信号）
    - duplicate_prd / duplicate_proto: {id, occurrences: [...]}
    - map_broken: 页面地图里引用了不存在的原型文件的条目原样
    """
    prd_by_id: dict[str, list[dict]] = {}
    for a in prd:
        prd_by_id.setdefault(a["id"], []).append(a)
    proto_by_id: dict[str, list[dict]] = {}
    for a in proto:
        proto_by_id.setdefault(a["id"], []).append(a)

    matched: list[dict] = []
    missing: list[dict] = []
    for aid in sorted(prd_by_id):
        if aid in proto_by_id:
            matched.append({"id": aid, "prd": prd_by_id[aid][0], "proto": proto_by_id[aid][0]})
        else:
            missing.append({"id": aid, "prd": prd_by_id[aid][0]})
    undescribed = [{"id": aid, "proto": proto_by_id[aid][0]} for aid in sorted(proto_by_id) if aid not in prd_by_id]

    dup_prd = [{"id": aid, "occurrences": occ} for aid, occ in sorted(prd_by_id.items()) if len(occ) > 1]
    dup_proto = [{"id": aid, "occurrences": occ} for aid, occ in sorted(proto_by_id.items()) if len(occ) > 1]

    files = set(proto_files)
    map_broken = [e for e in page_map if e.get("proto") not in files]

    return {
        "summary": {
            "matched": len(matched),
            "missing_in_proto": len(missing),
            "undescribed": len(undescribed),
            "duplicate_prd": len(dup_prd),
            "duplicate_proto": len(dup_proto),
            "map_broken": len(map_broken),
        },
        "matched": matched,
        "missing_in_proto": missing,
        "undescribed": undescribed,
        "duplicate_prd": dup_prd,
        "duplicate_proto": dup_proto,
        "map_broken": map_broken,
    }


def reconcile_repo(docs: list[str], proto_files: list[str], page_map: list[dict], read_file) -> dict:
    """仓库级对账入口：解析两侧 + 页面地图坏引用检查。"""
    return compute_reconcile(
        extract_prd_anchors(docs, read_file),
        extract_proto_anchors(proto_files, read_file),
        page_map,
        proto_files,
    )
