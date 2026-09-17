# -*- coding: utf-8 -*-
"""匹配预测报告(prematch_reports)与复盘报告(post_match)，建立「预测场次 ↔ 回测场次」对应关系。

匹配 key：联赛 + 归一化主队 + 归一化客队（队名用 team_name_mapping.normalize_team_name 归一），
并附带校验日期是否一致（prematch 文件名日期 vs post_match 目录日期）。

用法：
  python scripts/match_pre_post_reports.py                        # 打印到控制台
  python scripts/match_pre_post_reports.py --out docs/pre_post_match_matching.md  # 落盘 Markdown
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PREMATCH_DIR = PROJECT_DIR / "docs" / "prematch_reports"
POST_DIR = PROJECT_DIR / "docs" / "post_match"

sys.path.insert(0, str(PROJECT_DIR / "scripts"))
from team_name_mapping import normalize_team_name  # noqa: E402

PRE_RE = re.compile(r"^(?P<league>.+?)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<home>.+)_vs_(?P<away>.+)\.md$")
POST_RE = re.compile(r"^(?P<league>.+?)_(?P<home>.+)_vs_(?P<away>.+)_复盘\.md$")


def _norm(name: str) -> str:
    n = normalize_team_name(name)
    return n if n else name


def parse_prematch() -> dict:
    """返回 key -> list[记录]；key=(league, norm_home, norm_away)。"""
    result = defaultdict(list)
    for p in PREMATCH_DIR.rglob("*.md"):
        m = PRE_RE.match(p.name)
        if not m:
            continue
        key = (m["league"], _norm(m["home"]), _norm(m["away"]))
        result[key].append({
            "date": m["date"],
            "home": m["home"], "away": m["away"], "league": m["league"],
            "file": str(p.relative_to(PREMATCH_DIR)),
        })
    return dict(result)


def parse_post() -> dict:
    """返回 key -> list[记录]；key=(league, norm_home, norm_away)。"""
    result = defaultdict(list)
    for p in POST_DIR.rglob("*.md"):
        m = POST_RE.match(p.name)
        if not m:
            continue
        dir_date = p.parent.name
        date = f"{dir_date[:4]}-{dir_date[4:6]}-{dir_date[6:8]}" if len(dir_date) == 8 else ""
        key = (m["league"], _norm(m["home"]), _norm(m["away"]))
        result[key].append({
            "date": date, "home": m["home"], "away": m["away"], "league": m["league"],
            "file": str(p.relative_to(POST_DIR)),
        })
    return dict(result)


def build() -> dict:
    pre = parse_prematch()
    post = parse_post()
    matched = [(k, pre[k], post[k]) for k in sorted(pre) if k in post]
    pre_only = [(k, pre[k]) for k in sorted(pre) if k not in post]
    post_only = [(k, post[k]) for k in sorted(post.keys() - pre.keys())]
    return {"pre": pre, "post": post, "matched": matched, "pre_only": pre_only, "post_only": post_only}


def render_markdown(r: dict) -> str:
    matched, pre_only, post_only = r["matched"], r["pre_only"], r["post_only"]
    n_pre = len(r["pre"])
    n_post = len(r["post"])
    L = []
    L.append("# 预测场次 ↔ 回测场次 匹配对照表\n")
    L.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    L.append("> 口径：预测场次来自 `docs/prematch_reports/*/` 文件名；回测场次来自 `docs/post_match/{YYYYMMDD}/` 文件名。")
    L.append("> 队名经 `team_name_mapping.normalize_team_name` 归一，按「联赛 + 主队 + 客队」匹配。\n")
    L.append("## 汇总\n")
    L.append(f"- 预测场次（去重后）: **{n_pre}**")
    L.append(f"- 回测场次（去重后）: **{n_post}**")
    L.append(f"- 已匹配: **{len(matched)}**")
    L.append(f"- 仅有预测、无回测: **{len(pre_only)}**")
    L.append(f"- 仅有回测、无预测: **{len(post_only)}**\n")

    L.append("## 一、已匹配（{} 场）\n".format(len(matched)))
    L.append("| 联赛 | 主队 | 客队 | 日期 | 预测文件 | 回测文件 |")
    L.append("|------|------|------|------|----------|----------|")
    for k, pre_items, post_items in matched:
        _, home, away = k
        league = pre_items[0]["league"]
        dates = "/".join(sorted({i["date"] for i in pre_items}))
        pre_files = "; ".join(i["file"] for i in pre_items)
        post_files = "; ".join(i["file"] for i in post_items)
        L.append(f"| {league} | {home} | {away} | {dates} | {pre_files} | {post_files} |")

    L.append("\n## 二、仅有预测、无回测（{} 场）\n".format(len(pre_only)))
    L.append("| 联赛 | 主队 | 客队 | 日期 | 预测文件 |")
    L.append("|------|------|------|------|----------|")
    for k, pre_items in pre_only:
        _, home, away = k
        for i in pre_items:
            L.append(f"| {i['league']} | {i['home']} | {i['away']} | {i['date']} | {i['file']} |")

    L.append("\n## 三、仅有回测、无预测（{} 场）\n".format(len(post_only)))
    L.append("| 联赛 | 主队 | 客队 | 日期 | 回测文件 |")
    L.append("|------|------|------|------|----------|")
    for k, post_items in post_only:
        _, home, away = k
        for i in post_items:
            L.append(f"| {i['league']} | {i['home']} | {i['away']} | {i['date'] or '—'} | {i['file']} |")

    L.append("")
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="匹配预测场次与回测场次")
    parser.add_argument("--out", type=str, default=None, help="输出 Markdown 路径")
    args = parser.parse_args()

    r = build()
    md = render_markdown(r)

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = PROJECT_DIR / out_path
        out_path.write_text(md, encoding="utf-8")
        print(f"已写出: {out_path}")
    print(md)


if __name__ == "__main__":
    main()