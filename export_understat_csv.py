"""
导出 Understat 三张表数据为 CSV，方便 Excel 分析。
默认导出西甲 25/26，可用参数指定其他赛季/联赛。
"""
import csv
import sqlite3
import sys
from pathlib import Path

DB = Path(r'data/odds.db')
OUT_DIR = Path(r'data/understat_export')
OUT_DIR.mkdir(parents=True, exist_ok=True)

TABLES = [
    ("understat_match_team_stats", "比赛级"),
    ("understat_player_xg", "球员级"),
    ("understat_shots", "射门级"),
]


def export(season: str, league: str = None) -> None:
    c = sqlite3.connect(DB)
    cur = c.cursor()
    where = "season=?"
    params = [season]
    if league:
        where += " AND league=?"
        params.append(league)
    for tbl, label in TABLES:
        # 取列名
        cur.execute(f"PRAGMA table_info({tbl})")
        cols = [r[1] for r in cur.fetchall()]
        # 取该赛季数据
        cur.execute(f"SELECT * FROM {tbl} WHERE {where}", params)
        rows = cur.fetchall()
        suffix = f"_{league}" if league else ""
        outfile = OUT_DIR / f"{tbl}_{season.replace('/', '-')}{suffix}.csv"
        with open(outfile, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        print(f"[{label}] {tbl}: {len(rows)} 行 -> {outfile}")
    c.close()
    print(f"\n导出完成，目录: {OUT_DIR}")


if __name__ == "__main__":
    season = sys.argv[1] if len(sys.argv) > 1 else "25/26"
    league = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"导出赛季: {season}" + (f" | 联赛: {league}" if league else " | 联赛: 全部") + "\n")
    export(season, league)