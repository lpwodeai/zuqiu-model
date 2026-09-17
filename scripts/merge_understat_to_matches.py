# -*- coding: utf-8 -*-
"""
matches(英文队名) ←→ understat(英文短名) 对齐与覆盖率统计
=========================================================

背景:
  understat_match_team_stats 用 understat 自己的数字 match_id，与 matches.match_id 无关；
  但两者都有英文队名 + 比赛日期，可据此对齐。

做法:
  1. 用 feature_utils.normalize_team_name() 把两侧队名都归一化到同一套「中文标准名」，
     消除 Liverpool->Liverpool FC、Bayern Munich->FC Bayern München 等英英变体差异。
  2. 额外补一小撮 understat 特有的拼写别名（重音/空格/俱乐部全称差异）。
  3. 按 (日期, 主队标准名, 客队标准名) 精确对齐，并允许主客互换。

运行: python scripts/merge_understat_to_matches.py
"""
import sys
import os
import sqlite3
from pathlib import Path
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feature_utils import normalize_team_name

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "odds.db"

# understat 特有拼写 → 能与 TEAM_NAME_MAP 命中的写法
UNDERSTAT_ALIASES = {
    "West Ham": "West Ham United",
    "Tottenham": "Tottenham Hotspur",
    "Paris Saint Germain": "Paris Saint-Germain",
    "RasenBallsport Leipzig": "RB Leipzig",
    "Atletico Madrid": "Atlético Madrid",
    "Borussia M.Gladbach": "Borussia M'gladbach",
    "Leicester": "Leicester City",
    "FC Cologne": "1. FC Köln",
    "Saint-Etienne": "Saint-Étienne",
    "FC Heidenheim": "1. FC Heidenheim",
    "Bochum": "VfL Bochum 1848",
    "Leganes": "Leganés",
    "Leeds": "Leeds United",
    "Parma Calcio 1913": "Parma",
}


def norm_understat(name: str) -> str:
    """understat 队名 → 中文标准名（先用别名表纠正，再走 normalize_team_name）。"""
    if not name:
        return ""
    name = " ".join(name.split())
    if name in UNDERSTAT_ALIASES:
        name = UNDERSTAT_ALIASES[name]
    return normalize_team_name(name)


def main():
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row

    # 1. matches 索引: (date, home_norm, away_norm) -> [match_id ...]
    matches_idx = {}
    for r in c.execute("SELECT match_id, match_date, home_team, away_team FROM matches"):
        key = (r["match_date"],
               normalize_team_name(r["home_team"]),
               normalize_team_name(r["away_team"]))
        matches_idx.setdefault(key, []).append(r["match_id"])

    print(f"matches 表索引键 {len(matches_idx)} 个")

    # 2. understat 行
    us = c.execute(
        "SELECT match_id, datetime, home_team, away_team, league, season "
        "FROM understat_match_team_stats"
    ).fetchall()

    matched = 0          # 唯一 matches.match_id 命中数
    matched_rows = 0     # understat 行命中数(含 ±1 天容差)
    matched_matches_ids = set()
    unmatched_teams = Counter()
    season_rows = Counter()
    season_matched = Counter()

    for r in us:
        season_rows[r["season"]] += 1
        date = r["datetime"].split(" ")[0]
        hn = norm_understat(r["home_team"])
        an = norm_understat(r["away_team"])

        # 日期容差：欧盟时区(UTC+1/2)晚间场次在 UTC 下会推移到次日
        from datetime import datetime as _dt, timedelta as _td
        dates = [date]
        try:
            d0 = _dt.strptime(date, "%Y-%m-%d")
            dates.extend([(d0 + _td(days=1)).strftime("%Y-%m-%d"),
                          (d0 - _td(days=1)).strftime("%Y-%m-%d")])
        except ValueError:
            pass

        hit = False
        for d in dates:
            if hit:
                break
            k1 = (d, hn, an)
            k2 = (d, an, hn)
            if k1 in matches_idx:
                matched_rows += 1
                matched_matches_ids.update(matches_idx[k1])
                season_matched[r["season"]] += 1
                hit = True
            elif k2 in matches_idx:
                matched_rows += 1
                matched_matches_ids.update(matches_idx[k2])
                season_matched[r["season"]] += 1
                hit = True
        if not hit:
            if hn and any('\u4e00' <= ch <= '\u9fff' for ch in hn) is False:
                unmatched_teams[r["home_team"]] += 1
            if an and any('\u4e00' <= ch <= '\u9fff' for ch in an) is False:
                unmatched_teams[r["away_team"]] += 1

    matched = len(matched_matches_ids)

    print(f"\nunderstat 行: {len(us)}")
    print(f"能对齐到 matches 的 understat 行(含±1天容差): {matched_rows} ({matched_rows/len(us)*100:.1f}%)")
    print(f"对齐后唯一 matches 场次: {matched}")

    print(f"\n按 season 覆盖率 (matches 主要覆盖 23/24 起):")
    for s in sorted(season_rows):
        m = season_matched.get(s, 0)
        print(f"  {s}: {m}/{season_rows[s]} ({m/season_rows[s]*100:.1f}%)")

    # 3. 按球队名残留
    if unmatched_teams:
        print(f"\n归一化后仍无法对齐的 understat 队名 (前 30，多为不在 matches 的历史球队):")
        for t, n in unmatched_teams.most_common(30):
            print(f"  {t}: {n} 次")

    c.close()


if __name__ == "__main__":
    main()