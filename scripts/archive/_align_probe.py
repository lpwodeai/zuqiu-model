# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r'data/odds.db')
c.row_factory = sqlite3.Row

print('=== odds500_match 今日+明日未开赛(status=1) ===')
for r in c.execute("SELECT fid, match_id, league, season, round, match_date, match_time, home_team_cn, away_team_cn, home_team_en, away_team_en, win, draw, lost, handicap, pan FROM odds500_match WHERE status=1 AND match_date >= '2026-08-29' ORDER BY match_date, match_time"):
    print(' ', dict(r))

print('\n=== assets team_name_map.json 样例 ===')
import json
from pathlib import Path
p = Path('assets/team_name_map.json')
if p.exists():
    d = json.loads(p.read_text(encoding='utf-8'))
    items = list(d.items())[:8] if isinstance(d, dict) else d[:8]
    for k, v in items:
        print(f'  {k!r} -> {v!r}')

print('\n=== 500表 今日未开赛 英文名 vs sporttery wdl_history 对齐测试 ===')
rows = c.execute("SELECT match_id, home_team_en, away_team_en, match_date FROM odds500_match WHERE status=1 AND match_date >= '2026-08-29' LIMIT 5").fetchall()
for r in rows:
    # 用英文名找 sporttery 的中文 match_id（不好直接找，改用 fbref_match_mapping 反查）
    fb = c.execute("SELECT odds_match_id, home_team_cn, away_team_cn FROM fbref_match_mapping WHERE home_team_fbref=? AND away_team_fbref=? AND match_date=? LIMIT 1",
                   (r['home_team_en'], r['away_team_en'], r['match_date'])).fetchone()
    print(f"  500 match_id={r['match_id']} | en={r['home_team_en']} vs {r['away_team_en']}")
    if fb:
        wdl = c.execute("SELECT COUNT(*) n, MIN(timestamp), MAX(timestamp) FROM wdl_history WHERE match_id=?", (fb['odds_match_id'],)).fetchone()
        print(f"    fbref odds_match_id={fb['odds_match_id']} | cn={fb['home_team_cn']} vs {fb['away_team_cn']} | wdl_history {wdl['n']} 条 {wdl[1]}~{wdl[2]}")
    else:
        print('    无 fbref 映射')