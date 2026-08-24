"""页面地图解析测试（T3.2）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.page_map import parse_page_map, parse_repo_page_map, scan_proto_anchors

# ───────────────────────── parse_page_map ─────────────────────────

STD = """# 标题

## 4 页面地图

| 页面 | 原型文件 | 页面锚点 |
|------|---------|---------|
| 登录页 | prototype/pages/login.html | page-login |
| 工作台首页 | prototype/pages/index.html | page-home |

## 5 功能需求

正文。
"""


def test_std_table():
    got = parse_page_map(STD)
    assert got == [
        {"name": "登录页", "proto": "prototype/pages/login.html", "anchor": "page-login"},
        {"name": "工作台首页", "proto": "prototype/pages/index.html", "anchor": "page-home"},
    ]


def test_no_section():
    assert parse_page_map("# 无地图\n\n| 页面 | 原型 | 锚点 |\n|---|---|---|\n| a | b.html | c-d |\n") == []


def test_section_boundary():
    """表格在下一章节标题处截止，不吞后续章节的表格。"""
    md = """## 4 页面地图

| 页面 | 原型文件 | 页面锚点 |
|------|---------|---------|
| 登录页 | prototype/pages/login.html | page-login |

## 5 其他

| 页面 | 原型文件 | 页面锚点 |
|------|---------|---------|
| 骗人的页 | no.html | nope |
"""
    got = parse_page_map(md)
    assert len(got) == 1
    assert got[0]["name"] == "登录页"


def test_header_variants():
    """表头列名变体（页面名称/原型文件路径/页面锚点）与列顺序无关。"""
    md = """### 4.2 页面地图

| 原型文件路径 | 页面名称 | 页面锚点 |
|---|---|---|
| prototype/pages/a.html | A 页 | page-a |
"""
    got = parse_page_map(md)
    assert got == [{"name": "A 页", "proto": "prototype/pages/a.html", "anchor": "page-a"}]


def test_backtick_and_noise():
    """单元格含反引号/空格/说明文字时提取路径与锚点。"""
    md = """## 4 页面地图

| 页面 | 原型文件 | 页面锚点 |
|------|---------|---------|
| 登录页 | `prototype/pages/login.html`（旧版） | page-login 见 5.1 |
"""
    got = parse_page_map(md)
    assert got == [{"name": "登录页", "proto": "prototype/pages/login.html", "anchor": "page-login"}]


def test_bad_rows_skipped():
    """缺列/空锚点/无 .html 的行静默跳过。"""
    md = """## 4 页面地图

| 页面 | 原型文件 | 页面锚点 |
|------|---------|---------|
| 好页 | prototype/pages/ok.html | page-ok |
| 坏页 | （待定） | |
| 坏页2 | not-a-html | page-x |
"""
    got = parse_page_map(md)
    assert [g["name"] for g in got] == ["好页"]


def test_level4_heading():
    """4 级标题下的页面地图也认。"""
    md = """#### 4.1 页面地图

| 页面 | 原型文件 | 页面锚点 |
|---|---|---|
| X | prototype/x.html | page-x |
"""
    assert parse_page_map(md)[0]["anchor"] == "page-x"


def test_level1_not_matched():
    """1 级标题（# 页面地图）不认——按模板约定是 2-4 级章节。"""
    md = """# 页面地图

| 页面 | 原型文件 | 页面锚点 |
|---|---|---|
| X | prototype/x.html | page-x |
"""
    assert parse_page_map(md) == []


# ───────────────────────── parse_repo_page_map ─────────────────────────

def test_repo_merge_and_missing_file():
    """多文档合并（后覆盖同名）+ 读不到的文件跳过。"""
    files = {
        "prd/a.md": "## 4 页面地图\n\n| 页面 | 原型文件 | 页面锚点 |\n|---|---|---|\n| A | prototype/a.html | page-a |\n",
        "prd/b.md": "## 4 页面地图\n\n| 页面 | 原型文件 | 页面锚点 |\n|---|---|---|\n| B | prototype/b.html | page-b |\n| A | prototype/a2.html | page-a2 |\n",
    }

    def read(rel):
        if rel not in files:
            raise FileNotFoundError(rel)
        return files[rel]

    got = parse_repo_page_map(["prd/a.md", "prd/b.md", "prd/gone.md"], read)
    by_name = {g["name"]: g for g in got}
    assert by_name["A"]["proto"] == "prototype/a2.html"  # b.md 覆盖
    assert by_name["B"]["anchor"] == "page-b"


def test_real_fixture():
    """契约 fixture（tests/fixtures/prd/sample.md）真实解析。"""
    fixture = Path(__file__).parent / "fixtures" / "prd" / "sample.md"
    got = parse_repo_page_map(["prd/sample.md"], lambda rel: fixture.read_text(encoding="utf-8"))
    assert {"name": "登录页", "proto": "prototype/pages/login.html", "anchor": "page-login"} in got
    assert {"name": "工作台首页", "proto": "prototype/pages/index.html", "anchor": "page-home"} in got


def test_real_tomato_prd_no_map():
    """无页面地图章节的 PRD（如番茄钟）返回空——单页原型不强制有地图。"""
    assert parse_page_map("# PRD：网页版番茄时钟\n\n## 1 产品概述\n\n内容\n") == []


# ───────────────────────── scan_proto_anchors ─────────────────────────

def test_scan_proto_anchors():
    """data-pa → 文件索引：重复锚点取第一个文件。"""
    files = {
        "prototype/pages/login.html": '<main data-pa="page-login"><form data-pa="login-form"></form></main>',
        "prototype/pages/home.html": '<main data-pa="page-home"></main>',
        "prototype/pages/other.html": '<div data-pa="page-login"></div>',  # 重复锚点
    }
    got = scan_proto_anchors(list(files), lambda rel: files[rel])
    assert got["page-login"] == "prototype/pages/login.html"  # 先到先得
    assert got["login-form"] == "prototype/pages/login.html"
    assert got["page-home"] == "prototype/pages/home.html"


def test_scan_proto_anchors_real_fixture():
    """契约 fixture 的 login.html 真实扫描。"""
    fixture = Path(__file__).parent / "fixtures" / "prototype" / "pages" / "login.html"
    got = scan_proto_anchors(["prototype/pages/login.html"], lambda rel: fixture.read_text(encoding="utf-8"))
    assert got["page-login"] == "prototype/pages/login.html"
    assert got["login-form"] == "prototype/pages/login.html"
    assert got["login-account"] == "prototype/pages/login.html"


def test_scan_proto_anchors_missing_file():
    """读不到的文件跳过不炸。"""
    got = scan_proto_anchors(["gone.html"], lambda rel: (_ for _ in ()).throw(FileNotFoundError(rel)))
    assert got == {}
