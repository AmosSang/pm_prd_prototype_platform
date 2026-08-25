#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_orphan_anchors.py — PRD ↔ HTML 原型孤儿锚点检查

检查 PRD(markdown) 与 HTML 原型之间的锚点配对关系，找出孤儿与违规用法。

锚点约定（与平台 reconcile / prd-html-anchor 技能一致）：
  - PRD 侧：   行尾 HTML 注释 `<!-- pa: ID -->`（须贴在内容行尾，不单独成行）
  - 原型侧：   元素属性 `data-pa="ID"`（可空格分隔多值）；JS 动态创建用
               `el.setAttribute('data-pa', 'ID')`
  - ID 规则：  小写英文 + 数字 + 连字符，全局唯一

用法：
  python check_orphan_anchors.py <项目目录>          # 目录下应有 prd/ 与 prototype/
  python check_orphan_anchors.py --prd <md路径> --proto <原型路径>
                                                         # 路径可为单文件或目录
  可选参数：
  --json      以 JSON 输出结果（机器可读，供平台/CI 集成）
  --quiet     只输出汇总一行，不列明细

检查项：
  1. 孤儿锚点：PRD 有而原型缺（missing_in_proto）/
               原型有而 PRD 缺（undescribed_in_prd）
  2. 重复锚点：同一 ID 在 PRD 侧或原型侧出现多次
  3. 放置违规：PRD 中 `<!-- pa: -->` 单独成行（会锚到空行，跳转失准）
  4. 命名违规：ID 含 [a-z0-9-] 之外字符（空格/中文/大写/下划线等）

退出码：0 = 全部通过；1 = 发现问题；2 = 参数或路径错误
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------- 正则 ----------

# PRD 侧锚点：<!-- pa: xxx -->（捕获内容按空白拆分，兼容一注释多 ID 的宽容写法）
PRD_ANCHOR_RE = re.compile(r"<!--\s*pa:\s*(.*?)\s*-->")

# 原型侧静态属性：data-pa="a b"（单双引号均可）
PROTO_PA_RE = re.compile(r"""data-pa\s*=\s*(["'])(.*?)\1""", re.IGNORECASE)

# 原型侧 JS 动态注入：el.setAttribute('data-pa', 'xxx')
SETATTR_RE = re.compile(
    r"""setAttribute\s*\(\s*(["'])data-pa\1\s*,\s*(["'])(.*?)\2\s*\)""",
    re.IGNORECASE,
)

# 单独成行的 PRD 锚点（违反「贴内容行尾」铁律）
STANDALONE_RE = re.compile(r"^\s*<!--\s*pa:.*?-->\s*$")

# 合法 ID：小写字母/数字/连字符，不以连字符开头或结尾
VALID_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# 扫描原型时跳过的目录（垃圾/依赖）
SKIP_DIRS = {"__MACOSX", "node_modules", ".git", ".DS_Store"}
PROTO_SUFFIXES = {".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".vue"}
PRD_SUFFIX = ".md"


# ---------- 工具 ----------

def iter_files(root: Path, suffixes):
    """遍历 root（文件或目录），产出匹配后缀的文件。"""
    if root.is_file():
        yield root
        return
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in suffixes:
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            yield p


def split_ids(raw: str):
    """按空白拆分锚点值，容忍一个注释/属性里写多个 ID。"""
    return [t for t in raw.split() if t]


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


# ---------- 采集 ----------

def collect_prd(prd_paths, base: Path):
    """返回 (anchors: id -> [(file, line)], standalone: [(file, line, text)])"""
    anchors = {}
    standalone = []
    for f in prd_paths:
        text = f.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if STANDALONE_RE.match(line):
                standalone.append((rel(f, base), lineno, line.strip()))
            for m in PRD_ANCHOR_RE.finditer(line):
                for aid in split_ids(m.group(1)):
                    anchors.setdefault(aid, []).append((rel(f, base), lineno))
    return anchors, standalone


def collect_proto(proto_paths, base: Path):
    """返回 anchors: id -> [(file, line)]；同时覆盖静态属性与 setAttribute 注入。"""
    anchors = {}
    for f in proto_paths:
        text = f.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            hits = [m.group(2) for m in PROTO_PA_RE.finditer(line)]
            hits += [m.group(3) for m in SETATTR_RE.finditer(line)]
            for raw in hits:
                for aid in split_ids(raw):
                    anchors.setdefault(aid, []).append((rel(f, base), lineno))
    return anchors


# ---------- 汇总 ----------

def suggest_for_orphan(aid: str, side: str) -> str:
    """给孤儿锚点的修复建议：优先建议补另一侧，并提示核实是否该删。

    大幅修改后的常见情况是"锚点漂移"：功能还在但描述/位置变了。
    保守建议是补对侧；若功能已被删除，则应两侧一起删干净。
    """
    other = "原型" if side == "prd" else "PRD"
    return (f"建议：若该功能仍存在 → 在{other}侧补对应锚点（按 ID 定位元素/段落）；"
            f"若功能已删 → 两侧锚点一起删除，勿留单侧死链")

def analyze(prd_anchors, proto_anchors, standalone):
    """返回问题列表（每项含类别与明细）。"""
    problems = []

    prd_ids, proto_ids = set(prd_anchors), set(proto_anchors)

    for aid in sorted(prd_ids - proto_ids):
        locs = prd_anchors[aid]
        problems.append({
            "type": "missing_in_proto",
            "id": aid,
            "detail": "PRD 有锚点，但原型中找不到对应 data-pa",
            "locations": [f"{f}:{l}" for f, l in locs],
            "suggest": suggest_for_orphan(aid, "prd"),
        })

    for aid in sorted(proto_ids - prd_ids):
        locs = proto_anchors[aid]
        problems.append({
            "type": "undescribed_in_prd",
            "id": aid,
            "detail": "原型有 data-pa，但 PRD 中无对应锚点描述",
            "locations": [f"{f}:{l}" for f, l in locs],
            "suggest": suggest_for_orphan(aid, "proto"),
        })

    for side, anchors in (("prd", prd_anchors), ("proto", proto_anchors)):
        for aid in sorted(a for a, v in anchors.items() if len(v) > 1):
            problems.append({
                "type": "duplicate_" + ("prd" if side == "prd" else "proto"),
                "id": aid,
                "detail": ("PRD 中" if side == "prd" else "原型中")
                          + f"出现 {len(anchors[aid])} 次（锚点 ID 应全局唯一）",
                "locations": [f"{f}:{l}" for f, l in anchors[aid]],
                "suggest": "建议：逐处核对该 ID 锚定的具体内容，仅保留语义正确的一处，"
                           "其余删除或改用新 ID（改版后常出现两个段落撞同一个旧 ID）",
            })

    for aid in sorted(prd_ids | proto_ids):
        if not VALID_ID_RE.match(aid):
            problems.append({
                "type": "invalid_id",
                "id": aid,
                "detail": "ID 应为小写英文/数字/连字符（[a-z0-9-]），且不以连字符开头或结尾",
                "locations": [f"{f}:{l}" for f, l in prd_anchors.get(aid, [])]
                             + [f"{f}:{l}" for f, l in proto_anchors.get(aid, [])],
                "suggest": "建议：改为合法命名（小写英文/数字/连字符），两侧同步改，"
                           "旧 ID 若已被引用需一并更新",
            })

    for f, lineno, text in standalone:
        problems.append({
            "type": "standalone_line",
            "id": "(N/A)",
            "detail": "锚点注释单独成行，未贴在内容行尾——读取程序会锚到空行，跳转失准",
            "locations": [f"{f}:{lineno}  {text[:60]}"],
            "suggest": "建议：改版重排后锚点脱离了内容行，把注释移回目标标题/段落的行尾；"
                       "若锚定的内容已被删除，直接删掉该注释",
        })

    return problems


# ---------- 输出 ----------

TYPE_LABEL = {
    "missing_in_proto": "孤儿锚点（PRD 有 / 原型缺）",
    "undescribed_in_prd": "孤儿锚点（原型有 / PRD 缺）",
    "duplicate_prd": "重复锚点（PRD 侧）",
    "duplicate_proto": "重复锚点（原型侧）",
    "invalid_id": "命名违规",
    "standalone_line": "放置违规（单独成行）",
}


def print_report(problems, prd_count, proto_count, matched, quiet):
    ok = not problems
    summary = (f"PRD 锚点 {prd_count} 个 / 原型锚点 {proto_count} 个 / "
               f"配对成功 {matched} 个 / 问题 {len(problems)} 项")

    if quiet:
        print(("✔ " if ok else "✘ ") + summary)
        return

    if ok:
        print(f"✔ 全部通过：{summary}")
        return

    print(f"✘ 发现 {len(problems)} 个问题（{summary}）\n")
    cur = None
    for p in problems:
        label = TYPE_LABEL[p["type"]]
        if label != cur:
            print(f"—— {label} ——")
            cur = label
        locs = "，".join(p["locations"][:5])
        more = f" …等 {len(p['locations'])} 处" if len(p["locations"]) > 5 else ""
        print(f"  [{p['id']}] {p['detail']}")
        print(f"      位置：{locs}{more}")
        if p.get("suggest"):
            print(f"      {p['suggest']}")


# ---------- 主流程 ----------

def resolve_inputs(args):
    """把命令行参数解析为 (prd_paths, proto_paths, base) 或报错返回 None。"""
    if args.project:
        root = Path(args.project)
        if not root.is_dir():
            return None
        prd_root = root / "prd"
        proto_root = root / "prototype"
        if not prd_root.exists() or not proto_root.exists():
            print(f"错误：{root} 下未同时找到 prd/ 与 prototype/ 目录，"
                  f"请改用 --prd/--proto 显式指定路径", file=sys.stderr)
            return None
        return (list(iter_files(prd_root, {PRD_SUFFIX})),
                list(iter_files(proto_root, PROTO_SUFFIXES)), root)

    if not (args.prd and args.proto):
        return None
    prd_path, proto_path = Path(args.prd), Path(args.proto)
    if not prd_path.exists() or not proto_path.exists():
        print("错误：--prd 或 --proto 路径不存在", file=sys.stderr)
        return None
    base = prd_path.parent if prd_path.is_file() else prd_path
    return (list(iter_files(prd_path, {PRD_SUFFIX})),
            list(iter_files(proto_path, PROTO_SUFFIXES)), base)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="检查 PRD(markdown) 与 HTML 原型之间的孤儿锚点")
    parser.add_argument("project", nargs="?", help="项目目录（内含 prd/ 与 prototype/）")
    parser.add_argument("--prd", help="PRD markdown 文件或目录")
    parser.add_argument("--proto", help="原型目录或单个 html/js 文件")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--quiet", action="store_true", help="只输出汇总一行")
    args = parser.parse_args(argv)

    resolved = resolve_inputs(args)
    if resolved is None:
        print("用法：python check_orphan_anchors.py <项目目录>\n"
              "     python check_orphan_anchors.py --prd <md路径> --proto <原型路径>",
              file=sys.stderr)
        return 2
    prd_paths, proto_paths, base = resolved

    if not prd_paths:
        print("错误：PRD 侧没有找到任何 .md 文件", file=sys.stderr)
        return 2
    if not proto_paths:
        print("错误：原型侧没有找到任何 html/js 文件", file=sys.stderr)
        return 2

    prd_anchors, standalone = collect_prd(prd_paths, base)
    proto_anchors = collect_proto(proto_paths, base)
    problems = analyze(prd_anchors, proto_anchors, standalone)

    matched = len(set(prd_anchors) & set(proto_anchors))

    if args.json:
        print(json.dumps({
            "ok": not problems,
            "prd_anchor_count": len(prd_anchors),
            "proto_anchor_count": len(proto_anchors),
            "matched_count": matched,
            "problems": problems,
        }, ensure_ascii=False, indent=2))
    else:
        print_report(problems, len(prd_anchors), len(proto_anchors), matched, args.quiet)

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
