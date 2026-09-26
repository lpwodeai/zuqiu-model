# -*- coding: utf-8 -*-
"""
存量盘口回填工具（C-20260922-046）
=================================
背景：
  matches.handicap 只能由竞彩源写入（DB 触发器门禁）。历史竞彩赛程 JSON
  （data/sporttery_collected/*.json，getUniformMatchResultV1 返回）含 goalLine，
  但 23/24 起 matches 行多由 SofaScore 以英文名写入，match_id 与竞彩中文键不同源，
  导致 5000+ 行盘口为 NULL。

两种模式：
  1) merge（默认）：读全部 sporttery_collected/*.json，按
     “日期±1 天 + 双向队名归一化（normalize_team_name）” 桥接，
     仅对 handicap IS NULL 且已完赛的 matches 行 UPSERT：
       handicap = goalLine, handicap_source='sporttery'
       actual_handicap 为空时用 compute_actual_handicap(goalLine, 比分) 补算
     默认 dry-run，加 --apply 才写库。
  2) fetch-schedule：只调赛程接口（跳过逐场 getFixedBonus，时序赔率库里已有），
     按月分页拉取整季赛程并落盘成与历史采集器同名的 raw JSON，随后再跑 merge。

用法：
  python scripts/backfill_handicap_from_schedule.py --dry-run
  python scripts/backfill_handicap_from_schedule.py --apply
  python scripts/backfill_handicap_from_schedule.py --fetch-schedule 2024-2025
  python scripts/backfill_handicap_from_schedule.py --fetch-schedule 2024-2025,2025-2026 --leagues all
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from team_name_mapping import normalize_team_name  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "odds.db"
RAW_DIR = DATA_DIR / "sporttery_collected"
AUDIT_DIR = PROJECT_ROOT / "backup"

REQUEST_DELAY = 1.0


# ============================================================
# merge 模式
# ============================================================

def load_raw_index() -> tuple[dict, dict]:
    """读全部 raw JSON。

    返回:
      index: (date, canon_home, canon_away) -> {'handicap': v, 'actual_score': s}
             冲突时优先保留“已完赛且有 goalLine”的记录
      stats: 调试统计
    """
    index: dict[tuple, dict] = {}
    stats = {"files": 0, "matches": 0, "with_hcp": 0, "conflicts_diff": 0}
    for f in sorted(RAW_DIR.glob("*.json")):
        if not f.name.endswith(".json"):
            continue
        stats["files"] += 1
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            print(f"  [WARN] 读取失败 {f.name}: {e}")
            continue
        for m in data if isinstance(data, list) else []:
            stats["matches"] += 1
            hcp = m.get("handicap")
            if hcp is not None:
                stats["with_hcp"] += 1
            key = (
                str(m.get("match_date", ""))[:10],
                normalize_team_name(m.get("home_team", "")),
                normalize_team_name(m.get("away_team", "")),
            )
            if key[1] is None or key[2] is None or not key[0]:
                continue
            rec = {"handicap": hcp, "actual_score": m.get("actual_score") or ""}
            old = index.get(key)
            if old is None:
                index[key] = rec
            else:
                # 跨月文件可能重复；不一致则记一次冲突，优先已完赛/非空盘口
                if old["handicap"] != hcp:
                    stats["conflicts_diff"] += 1
                old_finished = bool(old["actual_score"]) and old["handicap"] is not None
                new_finished = bool(rec["actual_score"]) and hcp is not None
                if new_finished and not old_finished:
                    index[key] = rec
    return index, stats


def _date_variants(d: str) -> list[str]:
    out = [d]
    try:
        b = dt.date.fromisoformat(d)
        out.append((b - dt.timedelta(days=1)).isoformat())
        out.append((b + dt.timedelta(days=1)).isoformat())
    except ValueError:
        pass
    return out


def merge_backfill(apply: bool) -> None:
    print("=" * 70)
    print(f"盘口回填 merge 模式（{'APPLY 写库' if apply else 'DRY-RUN'}）")
    print("=" * 70)

    raw_index, stats = load_raw_index()
    print(f"raw JSON: {stats['files']} 文件, {stats['matches']} 场, "
          f"{stats['with_hcp']} 场含盘口, 索引 {len(raw_index)} 键, "
          f"跨文件冲突 {stats['conflicts_diff']}")

    # 延迟导入：仅在补算 actual_handicap 时需要
    from sporttery_collector import compute_actual_handicap

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT rowid, home_team, away_team, match_date, match_type,
               handicap, actual_handicap, actual_score
        FROM matches
        WHERE handicap IS NULL AND actual_score IS NOT NULL
    """).fetchall()
    print(f"待评估 matches 行（handicap NULL + 已完赛）: {len(rows)}")

    per_season: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    audit_rows = []
    ambiguous = []
    unresolved_samples = []

    def season_of(mt):
        import re
        mm = re.search(r"(20\d{2})-(20\d{2})", mt or "")
        return f"{mm.group(1)[2:]}/{mm.group(2)[2:]}" if mm else "?"

    for rowid, home, away, mdate, mtype, hcp_old, ahcp_old, score in rows:
        season = season_of(mtype)
        d = str(mdate)[:10]
        ch = normalize_team_name(home)
        ca = normalize_team_name(away)
        if ch is None or ca is None:
            per_season[season]["unmappable"] += 1
            if len(unresolved_samples) < 20:
                unresolved_samples.append((season, d, home, away, "name_unmappable"))
            continue

        candidates = {}
        for dd in _date_variants(d):
            rec = raw_index.get((dd, ch, ca))
            if rec is not None:
                candidates[dd] = rec
        if not candidates:
            per_season[season]["no_raw"] += 1
            if len(unresolved_samples) < 20:
                unresolved_samples.append((season, d, home, away, "not_in_raw"))
            continue

        hcps = {c["handicap"] for c in candidates.values()}
        if len(hcps) > 1:
            # 不同日期候选给出不同 goalLine（理论上不应发生），保守跳过
            per_season[season]["ambiguous"] += 1
            if len(ambiguous) < 50:
                ambiguous.append((season, d, home, away, sorted(hcps)))
            continue

        rec = next(iter(candidates.values()))
        goal_line = rec["handicap"]
        if goal_line is None:
            per_season[season]["raw_no_goalline"] += 1
            continue

        new_ahcp = ahcp_old
        if not ahcp_old:
            try:
                new_ahcp = compute_actual_handicap(float(goal_line), score) or None
            except Exception:
                new_ahcp = None

        per_season[season]["will_update"] += 1
        audit_rows.append({
            "rowid": rowid, "season": season, "date": d,
            "home": home, "away": away, "canon_home": ch, "canon_away": ca,
            "handicap": goal_line, "actual_handicap": new_ahcp,
            "actual_score": score,
        })

    print("\n%-9s %8s %8s %8s %10s %11s %10s" % (
        "season", "null", "will_up", "no_raw", "unmappable", "ambiguous", "no_line"))
    total = defaultdict(int)
    for s in sorted(per_season):
        d2 = per_season[s]
        n = sum(d2.values())
        for k in d2:
            total[k] += d2[k]
        print("%-9s %8d %8d %8d %10d %11d %10d" % (
            s, n, d2["will_update"], d2["no_raw"], d2["unmappable"],
            d2["ambiguous"], d2["raw_no_goalline"]))
    print("-" * 72)
    print("TOTAL     %8d %8d %8d %10d %11d %10d" % (
        sum(total.values()), total["will_update"], total["no_raw"],
        total["unmappable"], total["ambiguous"], total["raw_no_goalline"]))

    if unresolved_samples:
        print("\n未解析样本（前 20）:")
        for x in unresolved_samples:
            print(" ", x)
    if ambiguous:
        print("\n歧义跳过（前 50）:")
        for x in ambiguous:
            print(" ", x)

    if not apply:
        print("\n[DRY-RUN] 未写库。确认无误后加 --apply 执行。")
        conn.close()
        return

    updated = 0
    ahcp_filled = 0
    for r in audit_rows:
        cur.execute(
            "UPDATE matches SET handicap=?, handicap_source='sporttery', "
            "updated_at=datetime('now','localtime') WHERE rowid=? AND handicap IS NULL",
            (r["handicap"], r["rowid"]),
        )
        if cur.rowcount:
            updated += 1
        if r["actual_handicap"]:
            cur.execute(
                "UPDATE matches SET actual_handicap=? WHERE rowid=? "
                "AND (actual_handicap IS NULL OR actual_handicap='')",
                (r["actual_handicap"], r["rowid"]),
            )
            ahcp_filled += cur.rowcount
    conn.commit()
    conn.close()

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_path = AUDIT_DIR / f"handicap_backfill_{ts}.json"
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "w", encoding="utf-8") as fh:
        json.dump({"updated": updated, "actual_handicap_filled": ahcp_filled,
                   "rows": audit_rows}, fh, ensure_ascii=False, indent=2)
    print(f"\n[APPLY] handicap 更新 {updated} 行；actual_handicap 补算 {ahcp_filled} 行")
    print(f"[AUDIT] 明细: {audit_path}")


# ============================================================
# fetch-schedule 模式（只拉赛程，不拉逐场赔率）
# ============================================================

async def fetch_schedule(seasons: list[str], leagues: list[str], headless: bool) -> None:
    from sporttery_collector import (
        SportteryCollector, parse_match_list_response,
        SEASON_RANGES, generate_monthly_chunks,
        COLLECTED_DATA_DIR, REQUEST_DELAY,
    )

    print("=" * 70)
    print(f"赛程-only 轻量采集: seasons={seasons} leagues={leagues}")
    print("=" * 70)

    collector = SportteryCollector(headless=headless)
    await collector.start()
    try:
        await collector._get_cookies()
        for season in seasons:
            if season not in SEASON_RANGES:
                print(f"[WARN] 未知赛季 {season}，支持: {sorted(SEASON_RANGES)}")
                continue
            rng = SEASON_RANGES[season]
            for league_name in leagues:
                chunks = generate_monthly_chunks(rng["start"], rng["end"])
                for ci, (begin, end) in enumerate(chunks, 1):
                    raw_file = COLLECTED_DATA_DIR / (
                        f"{league_name}_{season.replace('-', '_')}_{begin}_{end}.json")

                    # 已存在则读旧数据，按 sporttery_match_id 去重合并
                    existing = []
                    if raw_file.exists():
                        with open(raw_file, encoding="utf-8") as fh:
                            existing = json.load(fh)

                    all_matches: list[dict] = []
                    page_no, total_pages = 1, 1
                    while page_no <= total_pages:
                        data = await collector.fetch_match_list(begin, end, page_no)
                        if not data or data.get("errorCode") != "0":
                            print(f"  [WARN] {league_name} {begin} 第{page_no}页失败: "
                                  f"{data.get('errorMessage') if data else '无响应'}")
                            break
                        value = data.get("value", {})
                        total_pages = value.get("pages", 1)
                        ms = parse_match_list_response(data)
                        ms = [m for m in ms if m["league_name_abbr"] == league_name]
                        all_matches.extend(ms)
                        page_no += 1
                        await asyncio.sleep(REQUEST_DELAY)

                    if not all_matches and not existing:
                        print(f"  {league_name} {season} {begin}~{end}: 0 场，跳过")
                        continue

                    merged = {str(m["sporttery_match_id"]): m for m in existing}
                    new_n = 0
                    for m in all_matches:
                        sid = str(m["sporttery_match_id"])
                        if sid not in merged:
                            new_n += 1
                        # 新数据可能补全了赛果/盘口，同 id 直接覆盖（赛程字段只增不减）
                        merged[sid] = m
                    out = sorted(merged.values(),
                                 key=lambda m: (m.get("match_date", ""), str(m.get("sporttery_match_id"))))
                    with open(raw_file, "w", encoding="utf-8") as fh:
                        json.dump(out, fh, ensure_ascii=False, indent=2)
                    print(f"  {league_name} {begin}~{end} ({ci}/{len(chunks)}): "
                          f"本次 {len(all_matches)}，新增 {new_n}，存量合计 {len(out)}")
    finally:
        await collector.stop()
    print("\n赛程采集完成。请运行: python scripts/backfill_handicap_from_schedule.py --apply")


def main() -> None:
    p = argparse.ArgumentParser(description="存量盘口回填（raw 赛程 JSON → matches.handicap）")
    p.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    p.add_argument("--fetch-schedule", type=str, default=None,
                   help="只采集赛程不写库，逗号分隔赛季，如 2024-2025,2025-2026")
    p.add_argument("--leagues", type=str, default="all",
                   help="all 或 逗号分隔：英超/西甲/意甲/德甲/法甲")
    p.add_argument("--no-headless", action="store_true", help="采集时显示浏览器窗口")
    args = p.parse_args()

    if args.fetch_schedule:
        seasons = [s.strip() for s in args.fetch_schedule.split(",") if s.strip()]
        leagues = (list(LEAGUE_IDS_KEYS) if args.leagues.strip() == "all"
                   else [x.strip() for x in args.leagues.split(",") if x.strip()])
        bad = [x for x in leagues if x not in LEAGUE_IDS_KEYS]
        if bad:
            print(f"[ERROR] 未知联赛: {bad}")
            sys.exit(2)
        asyncio.run(fetch_schedule(seasons, leagues, headless=not args.no_headless))
    else:
        merge_backfill(apply=args.apply)


# 延迟别名（main 中用到，定义放底部避免循环导入观感）
from sporttery_collector import LEAGUE_IDS as LEAGUE_IDS_KEYS  # noqa: E402

if __name__ == "__main__":
    main()
