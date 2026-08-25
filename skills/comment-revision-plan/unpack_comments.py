#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unpack_comments.py — 解压平台评论导出包并输出摘要

平台（产品方案展示平台）评论导出 zip 契约（server/reviews.py::export_comments）：
  {project_id}-comments-{yyyymmdd}-{HHmm}/
  ├── manifest.json     # {exported_at, project{id,name}, scope, total, comments[]}
  ├── comments/         # 每条评论一个 JSON（字段组见《产品方案-V1.md》§3.3）
  └── shots/            # 评论整页截图 PNG（仅被引用且存在的）

本脚本做的事：
  1. 把导出 zip 安全解压（拒绝路径穿越/软链/炸弹，限额可调）
  2. 解析 manifest，逐条读取 comments/*.json
  3. 输出结构化摘要：每条评论的 ID / 状态 / 宿主 / 定位锚点 / 内容
  4. 汇总统计：按状态、按宿主（PRD 文档 / 原型）分组计数

用法：
  python unpack_comments.py <导出包.zip>                 # 解压到同目录下同名文件夹并输出摘要
  python unpack_comments.py <导出包.zip> -o <输出目录>
  python unpack_comments.py <导出包.zip> --json          # 摘要以 JSON 输出（供下游程序消费）
  python unpack_comments.py <导出包.zip> --keep           # 已解压时复用，不重复解压

  也可以直接对「已解压的导出目录」运行（目录内含 manifest.json）：
  python unpack_comments.py <导出目录>

退出码：0 = 成功；1 = 包内无评论或无已确认待修改；2 = 解压/结构错误
"""

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

# ---------- 常量 ----------

MAX_TOTAL_SIZE = 300 * 1024 * 1024   # 解压总量上限 300MB（与平台原型 zip 同口径）
MAX_ENTRIES = 5000                   # 条目数上限
STATUS_CONFIRMED = "已确认待修改"     # 交付修改的标准范围（产品方案 §3.4）

# PRD 文档评论（宿主为文档段落）
TT_DOC = "doc_block"
# 原型评论（DOM 元素或页面）
TT_PROTO = ("dom", "page")


# ---------- 解压 ----------

def safe_extract(zip_path: Path, out_dir: Path) -> Path:
    """安全解压，返回导出包根目录（manifest.json 所在层）。

    安全校验与平台一致：拒绝路径穿越与软链；限额（总量/条目数）；
    先校验全部 entry 再落盘。
    """
    with zipfile.ZipFile(zip_path) as zf:
        entries = zf.infolist()
        if len(entries) > MAX_ENTRIES:
            raise ValueError(f"条目数超限（{len(entries)} > {MAX_ENTRIES}）")
        total = sum(e.file_size for e in entries)
        if total > MAX_TOTAL_SIZE:
            raise ValueError(f"解压总量超限（{total} > {MAX_TOTAL_SIZE} 字节）")

        out_dir.mkdir(parents=True, exist_ok=True)
        for e in entries:
            name = e.filename
            # 拒绝绝对路径与路径穿越
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"非法条目路径：{name}")
            # 拒绝软链与目录型 entry
            if e.is_dir():
                continue
            if (e.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"拒绝软链条目：{name}")
            target = out_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(e) as src, open(target, "wb") as dst:
                dst.write(src.read())

    # manifest 可能在 zip 根（理论）或唯一顶层目录下（平台实际：{prefix}/manifest.json）
    if (out_dir / "manifest.json").is_file():
        return out_dir
    tops = [p for p in out_dir.iterdir() if p.is_dir()]
    if len(tops) == 1 and (tops[0] / "manifest.json").is_file():
        return tops[0]
    raise ValueError("解压后未找到 manifest.json（不是平台评论导出包？）")


def _manifest_dir_under(base: Path) -> Path | None:
    """在 base（或其唯一一级子目录）下找 manifest.json，找到返回其目录。"""
    if (base / "manifest.json").is_file():
        return base
    if base.is_dir():
        tops = [p for p in base.iterdir() if p.is_dir()]
        if len(tops) == 1 and (tops[0] / "manifest.json").is_file():
            return tops[0]
    return None


def find_manifest_root(path: Path, out_dir: Path | None = None) -> tuple[Path, bool]:
    """返回 (manifest 所在目录, 是否本次新解压)。

    path 可能是 zip 文件，也可能已是解压后的目录。
    zip 默认解压到「zip 同目录 / zip 文件名」；out_dir 可覆盖。
    已解压过则直接复用，不重复解压。
    """
    if path.is_dir():
        found = _manifest_dir_under(path)
        if found:
            return found, False
        raise ValueError(f"{path} 下未找到 manifest.json（不是已解压的评论导出包？）")
    if path.suffix.lower() == ".zip":
        out = out_dir if out_dir else path.parent / path.stem
        found = _manifest_dir_under(out)
        if found:
            return found, False
        root = safe_extract(path, out)
        return root, True
    raise ValueError(f"不支持的输入：{path}（应为 .zip 文件或含 manifest.json 的目录）")


# ---------- 评论解析 ----------

def host_of(cj: dict) -> str:
    """判断评论宿主：PRD 文档 / 原型。

    - target_type=doc_block → PRD 文档
    - target_type=dom/page  → 原型（anchor_id/nearest_anchor_id 或 css_path 定位）
    """
    return "prd" if cj.get("target_type") == TT_DOC else "proto"


def locate_hint(cj: dict) -> str:
    """评论的定位提示串（宿主侧锚点 / 路径 / 摘录）。"""
    if host_of(cj) == "prd":
        parts = []
        if cj.get("doc_anchor_id"):
            parts.append(f"锚点 {cj['doc_anchor_id']}")
        if cj.get("doc_file"):
            parts.append(f"文件 {cj['doc_file']}")
        if cj.get("doc_excerpt"):
            parts.append(f"段落摘录「{str(cj['doc_excerpt'])[:40]}…」")
        return "；".join(parts) or "无定位信息"
    parts = []
    if cj.get("anchor_id"):
        parts.append(f"锚点 {cj['anchor_id']}")
    elif cj.get("nearest_anchor_id"):
        parts.append(f"最近锚点 {cj['nearest_anchor_id']}")
    if cj.get("prototype_page"):
        parts.append(f"页面 {cj['prototype_page']}")
    if cj.get("css_path"):
        parts.append(cj["css_path"])
    if cj.get("text_excerpt"):
        parts.append(f"元素「{str(cj['text_excerpt'])[:30]}」")
    return "；".join(parts) or "无定位信息"


def read_comments(root: Path) -> tuple[dict, list[dict]]:
    """读 manifest + 全部评论 JSON，返回 (manifest, comments)。缺失/坏 JSON 的评论跳过并计入 warnings。"""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    comments = []
    for f in sorted((root / "comments").glob("*.json")):
        try:
            comments.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            print(f"警告：跳过损坏的评论文件 {f.name}（{e}）", file=sys.stderr)
    return manifest, comments


def summarize(manifest: dict, comments: list[dict]) -> dict:
    """结构化摘要（--json 直接输出；文本模式据此渲染）。"""
    by_status: dict[str, int] = {}
    by_host: dict[str, int] = {}
    for c in comments:
        by_status[c.get("status", "未知")] = by_status.get(c.get("status", "未知"), 0) + 1
        h = host_of(c)
        by_host[h] = by_host.get(h, 0) + 1
    confirmed = [c for c in comments if c.get("status") == STATUS_CONFIRMED]
    return {
        "export_root": None,  # 由调用方填
        "exported_at": manifest.get("exported_at"),
        "project": manifest.get("project", {}),
        "scope": manifest.get("scope"),
        "total": manifest.get("total"),
        "loaded": len(comments),
        "by_status": by_status,
        "by_host": by_host,
        "confirmed_count": len(confirmed),
        "confirmed": [
            {
                "comment_id": c.get("comment_id"),
                "host": host_of(c),
                "locate": locate_hint(c),
                "content": c.get("content", ""),
                "screenshot": bool(c.get("screenshot")),
                "has_prd_link": bool(c.get("doc_anchor_id") or c.get("doc_excerpt")),
            }
            for c in confirmed
        ],
        "others": [
            {
                "comment_id": c.get("comment_id"),
                "status": c.get("status"),
                "host": host_of(c),
                "content": c.get("content", ""),
            }
            for c in comments if c.get("status") != STATUS_CONFIRMED
        ],
    }


# ---------- 输出 ----------

HOST_LABEL = {"prd": "PRD 文档", "proto": "原型"}


def print_summary(s: dict, root: Path):
    proj = s["project"]
    print(f"项目：{proj.get('name', '?')}（{proj.get('id', '?')}）"
          f"  scope={s['scope']}  导出时间={s.get('exported_at', '')}")
    print(f"导出目录：{root}")
    print(f"评论总数：manifest {s['total']} / 实际加载 {s['loaded']} 条")
    print("按状态：" + "，".join(f"{k} {v}" for k, v in sorted(s["by_status"].items())))
    print("按宿主：" + "，".join(f"{HOST_LABEL.get(k, k)} {v}" for k, v in sorted(s["by_host"].items())))
    print(f"已确认待修改：{s['confirmed_count']} 条（修改计划的工作范围）")
    if not s["confirmed"]:
        print("\n（无「已确认待修改」评论——修改计划将无内容，建议向产品经理确认导出范围）")
        return
    print()
    for c in s["confirmed"]:
        shot = "有截图" if c["screenshot"] else "无截图"
        link = "已关联 PRD 锚点" if c["has_prd_link"] else "无 PRD 关联"
        print(f"  [{c['comment_id']}] {HOST_LABEL[c['host']]} ｜ {c['locate']}")
        print(f"      {shot}；{link}")
        print(f"      意见：{c['content']}")
    if s["others"]:
        print(f"\n（另有 {len(s['others'])} 条非待修改状态评论，不进入本轮修改计划）")


# ---------- 主流程 ----------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="解压平台评论导出包并输出结构化摘要")
    parser.add_argument("input", help="评论导出 zip 或已解压的导出目录")
    parser.add_argument("-o", "--out", help="解压输出目录（默认 zip 同目录下同名文件夹）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出摘要")
    parser.add_argument("--quiet", action="store_true", help="仅输出汇总一行")
    args = parser.parse_args(argv)

    src = Path(args.input)
    if not src.exists():
        print(f"错误：{src} 不存在", file=sys.stderr)
        return 2

    try:
        root, _ = find_manifest_root(src, Path(args.out) if args.out else None)
    except (ValueError, zipfile.BadZipFile) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2

    manifest, comments = read_comments(root)
    s = summarize(manifest, comments)
    s["export_root"] = str(root)

    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    elif args.quiet:
        print(f"{'✔' if s['confirmed_count'] else '✘'} "
              f"已确认待修改 {s['confirmed_count']}/{s['loaded']} 条"
              f"（导出目录 {root}）")
    else:
        print_summary(s, root)

    return 0 if s["confirmed_count"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
