"""页面地图解析（T3.2）。

从 PRD markdown 中解析「页面地图」章节的表格（产品方案 §3.2 模板第 4 章）：

    ## 4 页面地图

    | 页面 | 原型文件 | 页面锚点 |
    |------|---------|---------|
    | 登录页 | prototype/pages/login.html | page-login |

输出 [{name, proto, anchor}]，供反向联动「文档 → 原型」查目标文件用。
解析规则（宽松，面向人写的文档）：
- 只认「页面地图」标题（2-4 级）下的第一个表格；章节结束（下一个同级或
  更高级标题）即停
- 表头列名含「页面」「原型」「锚点」即认（允许「页面名称」「原型文件路径」
  等变体）；顺序不限
- 原型文件列提取路径（允许含反引号/空格）；页面锚点列取第一个 kebab 词
- 坏行（缺列/空锚点）静默跳过——页面地图是对账数据源之一，解析失败会在
  对账（T3.3）暴露，这里不做强校验
"""
import re

# 标题：2-4 级，含「页面地图」
_PAGE_MAP_HEADING = re.compile(r"^#{2,4}\s*.*页面地图.*\s*$", re.MULTILINE)
# 表格行：| a | b | c |
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
# 分隔行：| --- | --- |
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
# 锚点 ID：kebab-case
_ANCHOR_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+|[a-z]+[a-z0-9]*")
# 路径（原型文件列）：允许字母数字 _-./ 与中文
_PROTO_PATH = re.compile(r"[\w\-./\u4e00-\u9fff]+\.html")


def parse_page_map(md_text: str) -> list[dict]:
    """解析单个 markdown 文本的页面地图章节，返回页面清单。"""
    m = _PAGE_MAP_HEADING.search(md_text)
    if not m:
        return []

    # 章节体：从标题行结束到下一个 2-4 级标题（同级或更高级）为止
    rest = md_text[m.end():]
    next_heading = re.search(r"^#{2,4}\s", rest, re.MULTILINE)
    section = rest[: next_heading.start()] if next_heading else rest

    # 逐行找表格：先找表头行（含 页面/原型/锚点 关键字），再吃后续数据行
    lines = section.split("\n")
    result: list[dict] = []
    col_page = col_proto = col_anchor = -1
    in_table = False

    for line in lines:
        row_m = _TABLE_ROW.match(line)
        if not in_table:
            if row_m and not _TABLE_SEP.match(line):
                cells = [c.strip() for c in row_m.group(1).split("|")]
                for i, c in enumerate(cells):
                    if col_page < 0 and "页面" in c and "锚" not in c:
                        col_page = i
                    elif col_proto < 0 and "原型" in c:
                        col_proto = i
                    elif col_anchor < 0 and "锚点" in c:
                        col_anchor = i
                if col_page >= 0 and col_proto >= 0 and col_anchor >= 0:
                    in_table = True
            continue

        # 表内：分隔行跳过；空行或非表格行 → 表格结束
        if not row_m:
            break
        if _TABLE_SEP.match(line):
            continue
        cells = [c.strip() for c in row_m.group(1).split("|")]
        name = cells[col_page].strip("` ") if col_page < len(cells) else ""
        proto_cell = cells[col_proto] if col_proto < len(cells) else ""
        anchor_cell = cells[col_anchor] if col_anchor < len(cells) else ""
        proto_m = _PROTO_PATH.search(proto_cell)
        anchor_m = _ANCHOR_ID.search(anchor_cell)
        if name and proto_m and anchor_m:
            result.append({"name": name, "proto": proto_m.group(0), "anchor": anchor_m.group(0)})

    return result


def parse_repo_page_map(doc_paths: list[str], read_file) -> list[dict]:
    """扫描仓库文档列表，合并所有页面地图条目（后发现的覆盖同名页面）。

    read_file(rel_path) -> str：读文件内容的回调（projects.py 注入，
    便于复用既有安全读取逻辑）。
    """
    merged: dict[str, dict] = {}
    for rel in doc_paths:
        try:
            text = read_file(rel)
        except Exception:
            continue
        for entry in parse_page_map(text):
            merged[entry["name"]] = entry
    return list(merged.values())


# 原型 HTML 中的 data-pa 属性（属性值为 kebab-case 锚点 ID）
_DATA_PA = re.compile(r'data-pa="([a-z0-9-]+)"')


def scan_proto_anchors(proto_files: list[str], read_file) -> dict[str, str]:
    """扫描原型 HTML 文件，返回 锚点 ID → 文件路径 索引。

    反向联动用：组件锚点（如 login-form）不在页面地图里（地图只登记
    页面锚点），通过本索引找到它所在的文件。同一锚点出现在多个文件时
    取第一个（按传入顺序，即 _list_proto_entries 的稳定排序）。
    """
    index: dict[str, str] = {}
    for rel in proto_files:
        try:
            text = read_file(rel)
        except Exception:
            continue
        for m in _DATA_PA.finditer(text):
            index.setdefault(m.group(1), rel)
    return index
