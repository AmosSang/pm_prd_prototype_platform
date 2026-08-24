"""锚点对账测试（T3.3）。

契约锚点：tests/fixtures/prd/sample.md + tests/fixtures/prototype/pages/。
任务卡验收：构造的失配样例（测试仓库）三态计数正确。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.reconcile import (
    compute_reconcile,
    extract_prd_anchors,
    extract_proto_anchors,
    reconcile_repo,
)

# ───────────────────────── extract_prd_anchors ─────────────────────────

PRD_TEXT = """# 系统名

## 4 页面地图

| 页面 | 原型文件 | 页面锚点 |
|------|---------|---------|
| 登录页 | prototype/pages/login.html | page-login |

## 5 功能需求

### 5.1 登录页 <!-- pa: page-login -->

#### 5.1.1 登录表单 <!-- pa: login-form -->

- 账号输入 <!-- pa: login-account -->：支持手机号。

## 6 全局规则
"""


def test_prd_extract_with_heading_chain():
    got = extract_prd_anchors(["prd/x.md"], lambda rel: PRD_TEXT)
    assert [(a["id"], a["doc_path"]) for a in got] == [
        ("page-login", "5 功能需求/5.1 登录页"),
        ("login-form", "5 功能需求/5.1 登录页/5.1.1 登录表单"),
        ("login-account", "5 功能需求/5.1 登录页/5.1.1 登录表单"),
    ]
    assert all(a["file"] == "prd/x.md" for a in got)
    # 行号有效（用于明细展示）
    assert all(isinstance(a["line"], int) and a["line"] > 0 for a in got)


def test_prd_extract_skips_h1():
    """h1 是文档题，不进标题链（文件名已体现）。"""
    got = extract_prd_anchors(["a.md"], lambda rel: "# T <!-- pa: in-title -->\n\n## S\n\ntext\n")
    # 标题行锚点仍提取（渲染时挂到标题），链为空
    assert got[0]["id"] == "in-title"
    assert got[0]["doc_path"] == ""


def test_prd_extract_missing_file():
    """读不到的文件跳过。"""
    got = extract_prd_anchors(["gone.md"], lambda rel: (_ for _ in ()).throw(FileNotFoundError(rel)))
    assert got == []


# ───────────────────────── extract_proto_anchors ─────────────────────────

PROTO_TEXT = """<!DOCTYPE html>
<html><body>
<main data-pa="page-login">
  <form id="login-form" data-pa="login-form">
    <input data-pa="login-account">
  </form>
</main>
</body></html>"""


def test_proto_extract_bs4():
    """BS4 提取（含 css_path）；正则会误伤注释/JS 字符串，BS4 不会。"""
    got = extract_proto_anchors(["prototype/pages/login.html"], lambda rel: PROTO_TEXT)
    assert [a["id"] for a in got] == ["page-login", "login-form", "login-account"]
    by_id = {a["id"]: a for a in got}
    assert by_id["page-login"]["css_path"] == "main"
    assert by_id["login-form"]["css_path"] == "main > form#login-form"
    assert by_id["login-account"]["css_path"] == "main > form#login-form > input"


def test_proto_extract_ignores_comment_and_js():
    """注释和 <script> 里的 data-pa= 字符串不算锚点。"""
    html = """<body>
<!-- data-pa="comment-fake" -->
<script>var s = '<div data-pa="js-fake"></div>'</script>
<div data-pa="real"></div>
</body>"""
    got = extract_proto_anchors(["a.html"], lambda rel: html)
    assert [a["id"] for a in got] == ["real"]


def test_proto_extract_nth_of_type():
    """无 id/class 的同名兄弟 >1 时补 :nth-of-type。"""
    html = '<body><ul><li data-pa="a"></li><li data-pa="b"></li></ul></body>'
    got = extract_proto_anchors(["a.html"], lambda rel: html)
    by_id = {a["id"]: a for a in got}
    assert by_id["a"]["css_path"] == "ul > li:nth-of-type(1)"
    assert by_id["b"]["css_path"] == "ul > li:nth-of-type(2)"


# ───────────────────────── compute_reconcile（三态 + 附加检查）─────────────

PRD_MIXED = """# T

## 5 功能需求

### 5.1 A <!-- pa: anchor-ok -->

A 内容。

### 5.2 B <!-- pa: anchor-missing -->

B 内容。

### 5.3 C <!-- pa: anchor-dup -->

C 内容。

### 5.4 D <!-- pa: anchor-dup -->

D 内容。
"""

PROTO_MIXED = """<body>
<main data-pa="anchor-ok"></main>
<div data-pa="undescribed-1"></div>
<section data-pa="undescribed-2"></section>
<span data-pa="proto-dup"></span>
<span data-pa="proto-dup"></span>
<i data-pa="anchor-dup"></i>
</body>"""

def _mixed_recon():
    prd = extract_prd_anchors(["prd/x.md"], lambda rel: PRD_MIXED)
    proto = extract_proto_anchors(["prototype/index.html"], lambda rel: PROTO_MIXED)
    return compute_reconcile(prd, proto, page_map=[], proto_files=["prototype/index.html"])


def test_three_state_counts():
    """三态计数：2 匹配 / 1 原型缺失 / 3 未描述。

    - 匹配：anchor-ok、anchor-dup（PRD 重复但原型存在——重复是附加检查，
      不影响三态归类的正确性）
    - 缺失：anchor-missing（PRD 有原型无）
    - 未描述：undescribed-1/2（原型有 PRD 无）+ proto-dup（原型内重复，
      且 PRD 无——重复与未描述是正交的附加检查）
    """
    r = _mixed_recon()
    assert r["summary"]["matched"] == 2
    assert r["summary"]["missing_in_proto"] == 1
    assert r["summary"]["undescribed"] == 3


def test_three_state_details():
    r = _mixed_recon()
    assert {m["id"] for m in r["matched"]} == {"anchor-ok", "anchor-dup"}
    by_id = {m["id"]: m for m in r["matched"]}
    assert by_id["anchor-ok"]["prd"]["doc_path"] == "5 功能需求/5.1 A"
    assert r["missing_in_proto"][0]["id"] == "anchor-missing"
    assert r["missing_in_proto"][0]["prd"]["file"] == "prd/x.md"
    assert {u["id"] for u in r["undescribed"]} == {"undescribed-1", "undescribed-2", "proto-dup"}
    assert r["undescribed"][0]["proto"]["css_path"].startswith(("div", "section", "span"))


def test_duplicate_detection():
    """重复 ID：PRD 侧 anchor-dup ×2、原型侧 proto-dup ×2 都报。"""
    r = _mixed_recon()
    assert r["summary"]["duplicate_prd"] == 1
    assert r["summary"]["duplicate_proto"] == 1
    dup = r["duplicate_prd"][0]
    assert dup["id"] == "anchor-dup"
    assert len(dup["occurrences"]) == 2
    assert r["duplicate_proto"][0]["id"] == "proto-dup"


def test_map_broken_reference():
    """页面地图引用不存在的原型文件 → map_broken。"""
    prd = extract_prd_anchors(["p.md"], lambda rel: "# T\n\n## 4 页面地图\n\n| 页面 | 原型文件 | 页面锚点 |\n|---|---|---|\n| A | prototype/ghost.html | page-a |\n")
    proto = extract_proto_anchors(["prototype/index.html"], lambda rel: "<body><main data-pa='page-a'></main></body>")
    page_map = [{"name": "A", "proto": "prototype/ghost.html", "anchor": "page-a"}]
    r = compute_reconcile(prd, proto, page_map=page_map, proto_files=["prototype/index.html"])
    assert r["summary"]["map_broken"] == 1
    assert r["map_broken"] == [{"name": "A", "proto": "prototype/ghost.html", "anchor": "page-a"}]


def test_empty_inputs():
    """空仓库（无 PRD 或无原型）不炸，全部归零。"""
    r = compute_reconcile([], [], page_map=[], proto_files=[])
    assert r["summary"] == {
        "matched": 0,
        "missing_in_proto": 0,
        "undescribed": 0,
        "duplicate_prd": 0,
        "duplicate_proto": 0,
        "map_broken": 0,
    }


# ───────────────────────── 契约 fixture（真实样例）────────────────────────

FIXTURES = Path(__file__).parent / "fixtures"


def test_contract_fixture_reconcile():
    """契约样例：sample.md 的 5 锚点 vs login.html 的 4 锚点。

    PRD：page-login / login-form / login-account / login-captcha / login-submit
    原型 login.html：page-login / login-form / login-account / login-captcha / login-submit
    （modal.html / scroll.html 的锚点都是「未描述」——sample.md 没写它们。）
    """
    docs = ["prd/sample.md"]
    proto_files = [
        "prototype/pages/login.html",
        "prototype/pages/modal.html",
        "prototype/pages/scroll.html",
    ]

    def read(rel):
        return (FIXTURES / rel).read_text(encoding="utf-8")

    r = reconcile_repo(docs, proto_files, page_map=[], read_file=read)
    assert r["summary"]["matched"] == 5
    assert r["summary"]["missing_in_proto"] == 0
    # modal.html 8 个 + scroll.html 17 个锚点全部未描述
    assert r["summary"]["undescribed"] == 8 + 17
    # 页面地图登记了 prototype/pages/index.html 但 fixture 没有 → 坏引用
    # （此测试 page_map 传空，坏引用在 test_contract_fixture_map_broken 验）


def test_contract_fixture_map_broken():
    """sample.md 页面地图登记了 index.html，fixture 里不存在 → map_broken=1。"""
    from server.page_map import parse_repo_page_map

    docs = ["prd/sample.md"]
    proto_files = ["prototype/pages/login.html"]

    def read(rel):
        return (FIXTURES / rel).read_text(encoding="utf-8")

    page_map = parse_repo_page_map(docs, read)
    r = reconcile_repo(docs, proto_files, page_map, read)
    assert r["summary"]["map_broken"] == 1
    assert r["map_broken"][0]["proto"] == "prototype/pages/index.html"
