# -*- coding: utf-8 -*-
"""
reconcile_match_reports.py — 赛前预测 vs 赛后复盘 逐日对账工具

对齐 generate_post_match_report.py 生成的 post_match 复盘文件与
generate_unified_report.py 生成的 prematch_reports 预测文件，产出
match_date 级别的对账表，并可选择性地整改 post_match 文件命名。

核心口径：
  - 预测场次来源 = docs/prematch_reports/*/ 下文件名中的 {联赛}_{YYYY-MM-DD}_{主}_vs_{客}.md
  - 复盘记录来源 = odds.db 的 post_match_review 表（match_date + match_id + 队名）
  - 复盘文件来源 = docs/post_match/{YYYYMMDD}/*_复盘.md
  - 比赛日统一用 YYYY-MM-DD

用法：
  python scripts/reconcile_match_reports.py                        # 只生成对账表
  python scripts/reconcile_match_reports.py --fix                  # 同时整改 post_match 文件名（改名+去重）
  python scripts/reconcile_match_reports.py --out docs/对账表.md    # 自定义输出路径
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from feature_utils import normalize_team_name, canonical_team_name  # noqa: E402

ODDS_DB = PROJECT_DIR / "data" / "odds.db"
DOCS = PROJECT_DIR / "docs"
PREMATCH_DIR = DOCS / "prematch_reports"
POSTMATCH_DIR = DOCS / "post_match"

PREMATCH_RE = re.compile(r"^(?P<league>.+?)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<home>.+?)_vs_(?P<away>.+)\.md$")
POSTMATCH_RE = re.compile(r"^(?P<league>.+?)_(?P<home>.+?)_vs_(?P<away>.+?)_复盘\.md$")


def norm(s: Optional[str]) -> str:
    return normalize_team_name(s) if s else ""


def to_iso(d: str) -> str:
    """统一为 YYYY-MM-DD。"""
    d = (d or "").strip()
    if re.fullmatch(r"\d{8}", d):
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.row_factory = sqlite3.Row
    return conn


def load_review_rows(conn: sqlite3.Connection) -> List[dict]:
    out = []
    for r in conn.execute("SELECT * FROM post_match_review ORDER BY match_date, match_id"):
        d = dict(r)
        league = d["league"] or "未知"
        can_home = canonical_team_name(d["match_id"], d["home_team"], is_home=True)
        can_away = canonical_team_name(d["match_id"], d["away_team"], is_home=False)
        d["_date_iso"] = to_iso(d["match_date"])
        d["_league"] = league
        d["_can_home"] = can_home
        d["_can_away"] = can_away
        d["_canon_key"] = f"{norm(can_home)}|{norm(can_away)}"
        d["_raw_key"] = f"{norm(d['home_team'])}|{norm(d['away_team'])}"
        d["_cfname"] = f"{league}_{norm(can_home)}_vs_{norm(can_away)}_复盘.md"
        d["_has_file"] = False
        out.append(d)
    return out


def scan_prematch() -> Dict[str, List[dict]]:
    """返回 {YYYY-MM-DD: [ {league, home, away, key, file} ]}，跨文件夹去重。"""
    out: Dict[str, List[dict]] = defaultdict(list)
    seen: set = set()
    if not PREMATCH_DIR.exists():
        return out
    for f in PREMATCH_DIR.rglob("*.md"):
        if f.name == "_summary.md":
            continue
        m = PREMATCH_RE.match(f.name)
        if not m:
            continue
        date = m.group("date")
        home, away = m.group("home"), m.group("away")
        league = m.group("league")
        key = f"{norm(home)}|{norm(away)}"
        dedup_key = (date, league, key)
        if dedup_key in seen:
            continue  # 同一场次在多个批次文件夹重复生成，只计一次
        seen.add(dedup_key)
        out[date].append({"league": league, "home": home, "away": away,
                          "key": key, "file": str(f.relative_to(DOCS))})
    return out


def scan_postmatch() -> Dict[str, List[dict]]:
    """返回 {YYYY-MM-DD: [ {name, home, away, key, path} ]}。"""
    out: Dict[str, List[dict]] = defaultdict(list)
    if not POSTMATCH_DIR.exists():
        return out
    for d in POSTMATCH_DIR.iterdir():
        if not d.is_dir():
            continue
        iso = to_iso(d.name)
        for f in d.glob("*.md"):
            if f.name == "_summary.md":
                continue
            m = POSTMATCH_RE.match(f.name)
            if not m:
                continue
            home, away = m.group("home"), m.group("away")
            key = f"{norm(home)}|{norm(away)}"
            out[iso].append({"name": f.name, "home": home, "away": away, "key": key, "path": f})
    return out


def _build_actions(rows: List[dict], postmatch: Dict[str, List[dict]]):
    """将现有 post_match 文件映射到 DB 行，返回 (rename, delete, matched_canonical)。"""
    # 按日期建 canonical/raw 索引
    canon_of_date: Dict[str, Dict[str, str]] = defaultdict(dict)
    raw_of_date: Dict[str, Dict[str, str]] = defaultdict(dict)
    for rd in rows:
        canon_of_date[rd["_date_iso"]][rd["_canon_key"]] = rd["_cfname"]
        raw_of_date[rd["_date_iso"]][rd["_raw_key"]] = rd["_cfname"]

    # 现有文件 → (date, 目标 canonical 名) 分组
    groups: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    deletes: List[Path] = []
    for iso, files in postmatch.items():
        for f in files:
            target = None
            if f["key"] in canon_of_date[iso]:
                target = canon_of_date[iso][f["key"]]
            elif f["key"] in raw_of_date.get(iso, {}):
                target = raw_of_date[iso][f["key"]]
            if target is None:
                deletes.append(f["path"])  # 孤儿文件
            else:
                groups[(iso, target)].append({**f, "_target": target})

    renames: List[Tuple[Path, Path]] = []
    matched: set = set()
    for (iso, target), fs in groups.items():
        canon_hit = next((i for i, f in enumerate(fs) if f["name"] == target), None)
        if canon_hit is not None:
            matched.add((iso, target))
            for i, f in enumerate(fs):
                if i != canon_hit:
                    deletes.append(f["path"])  # 同 canonical 的重复变体
        else:
            first = fs[0]
            renames.append((first["path"], first["path"].with_name(target)))
            matched.add((iso, target))
            for f in fs[1:]:
                deletes.append(f["path"])
    return renames, deletes, matched


def build_report_and_actions() -> Tuple[str, List[Tuple[Path, Path]], List[Path]]:
    conn = _get_conn()
    try:
        rows = load_review_rows(conn)
    finally:
        conn.close()

    prematch = scan_prematch()
    postmatch = scan_postmatch()
    renames, deletes, matched = _build_actions(rows, postmatch)

    for rd in rows:
        rd["_has_file"] = (rd["_date_iso"], rd["_cfname"]) in matched

    # ---- 渲染 ----
    lines: List[str] = []
    lines.append("# 赛前预测 vs 赛后复盘 · match_date 级别对账表\n")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("> 口径：预测场次来自 `prematch_reports` 文件名；复盘记录来自 `odds.db::post_match_review`；"
                 "复盘文件来自 `post_match`。\n")

    all_dates = sorted({rd["_date_iso"] for rd in rows} | set(prematch))

    lines.append("## 一、逐日汇总\n")
    lines.append("| 比赛日 | 预测场次 | 复盘记录 | 复盘文件 | 缺复盘文件 | 重复/孤儿文件 | 数据告警(<0.7) |")
    lines.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    for d in all_dates:
        pre_n = len(prematch.get(d, []))
        rows_d = [rd for rd in rows if rd["_date_iso"] == d]
        rev_n = len(rows_d)
        file_n = sum(1 for rd in rows_d if rd["_has_file"])
        missing_n = rev_n - file_n
        dup_n = len([p for p in deletes if to_iso(p.parent.name) == d])
        alert_n = sum(1 for rd in rows_d
                      if rd.get("data_quality_score") is not None and rd["data_quality_score"] < 0.7)
        lines.append(f"| {d} | {pre_n} | {rev_n} | {file_n} | {missing_n} | {dup_n} | {alert_n} |")
    lines.append("")

    lines.append("## 二、逐日对阵对账明细\n")
    for d in all_dates:
        rows_d = [rd for rd in rows if rd["_date_iso"] == d]
        pre_list = prematch.get(d, [])
        reviewed_keys = {rd["_canon_key"] for rd in rows_d}
        pre_keys = {p["key"] for p in pre_list}

        lines.append(f"### {d}\n")
        lines.append(f"- 预测 {len(pre_list)} 场；复盘记录 {len(rows_d)} 场。\n")
        if pre_list:
            lines.append("  - 预测对阵：" + "、".join(
                f"{p['league']} {p['home']}vs{p['away']}" for p in pre_list) + "\n")
        if rows_d:
            lines.append("  | 对阵 | 比分 | 结果 | 复盘文件 | 状态 |")
            lines.append("  |------|------|------|:---:|------|")
            for rd in rows_d:
                score = rd.get("actual_score") or "—"
                has_attr = bool(rd.get("attribution_json"))
                dq = rd.get("data_quality_score")
                alert = "🚨" if (dq is not None and dq < 0.7) else ""
                if rd["_has_file"]:
                    status = "✅ 已复盘" + alert
                elif not has_attr:
                    status = "⚠ 归因未完成" + alert
                else:
                    status = "⚠ 缺复盘文件" + alert
                lines.append(
                    f"  | {rd['_league']} {rd['_can_home']}vs{rd['_can_away']} | {score} | "
                    f"{rd.get('actual_wdl') or '—'} | {'有' if rd['_has_file'] else '无'} | {status} |")
            lines.append("")
        unmatched_pre = [p for p in pre_list if p["key"] not in reviewed_keys]
        if unmatched_pre:
            lines.append(f"  - ⚠ 预测了但无复盘记录（{len(unmatched_pre)}）：" +
                         "、".join(f"{p['league']} {p['home']}vs{p['away']}" for p in unmatched_pre))
            lines.append("")

    lines.append("## 三、整改清单（队名归一 + 去重）\n")
    lines.append("### 需重命名\n")
    if renames:
        for src, dst in renames:
            lines.append(f"- `{src.relative_to(DOCS)}` → `{dst.relative_to(DOCS)}`")
    else:
        lines.append("- 无。")
    lines.append("")
    lines.append("### 需删除（重复/孤儿）\n")
    if deletes:
        for p in deletes:
            lines.append(f"- `{p.relative_to(DOCS)}`")
    else:
        lines.append("- 无。")
    lines.append("")

    return "\n".join(lines), renames, deletes


def main() -> None:
    parser = argparse.ArgumentParser(description="赛前/赛后 match_date 级别对账")
    parser.add_argument("--out", type=str, default=None, help="输出 md 路径（默认 docs/match_date_reconciliation.md）")
    parser.add_argument("--fix", action="store_true", help="实际执行改名 + 删除整改")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else DOCS / "match_date_reconciliation.md"
    md, renames, deletes = build_report_and_actions()

    if args.fix:
        for p in deletes:
            if p.exists():
                p.unlink()
                print(f"[删除] {p.relative_to(DOCS)}")
        for src, dst in renames:
            if src.exists():
                if dst.exists():
                    print(f"[跳过改名] {src.relative_to(DOCS)} → 已存在 {dst.relative_to(DOCS)}")
                    src.unlink()
                else:
                    src.rename(dst)
                    print(f"[改名] {src.relative_to(DOCS)} → {dst.relative_to(DOCS)}")
        md, renames, deletes = build_report_and_actions()

    out_path.write_text(md, encoding="utf-8")
    print(f"对账表已写出: {out_path}")
    print(f"待改名: {len(renames)} | 待删除: {len(deletes)}")


if __name__ == "__main__":
    main()