# -*- coding: utf-8 -*-
"""诊断：今日(26/27未开赛)比赛在四张时序表的数据覆盖 + 让球盘口来源。"""
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
DB = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db"
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 今日/近期未开赛的 odds500_match
cur.execute("""SELECT fid, league, match_date, match_time, home_team_cn, away_team_cn, home_team_en, away_team_en, win, draw, lost, handicap, pan, round
  FROM odds500_match WHERE season='26/27' AND status=1 AND match_date<='2026-09-01' ORDER BY match_date, match_time""")
matches = cur.fetchall()
print(f"未开赛(<=09-01): {len(matches)} 场\n")
for m in matches:
    cn_h, cn_a = m['home_team_cn'], m['away_team_cn']
    mid_cn = f"{m['match_date']}_{cn_h}_{cn_a}"
    # Sporttery 用中文 match_id；日期可能差一天，用队名匹配
    def cnt(tbl):
        cur.execute(f"SELECT COUNT(*) c, MAX(timestamp) ts FROM {tbl} WHERE match_id LIKE ?", (f"%_{cn_h}_{cn_a}",))
        return cur.fetchone()
    w = cnt('wdl_history'); h = cnt('handicap_history'); t = cnt('total_goals_history'); s = cnt('score_history')
    # 也试 match_id_en
    cur.execute("SELECT COUNT(*) FROM match_id_mapping WHERE sh_match_id LIKE ?", (f"%{cn_h}%{cn_a}%",))
    mp = cur.fetchone()[0]
    print(f"{m['match_date']} {m['match_time']} [{m['league']}] {m['home_team_cn']} vs {m['away_team_cn']} (handicap={m['handicap']!r})")
    print(f"    wdl={w['c']}(ts={w['ts']}) hcp={h['c']}(ts={h['ts']}) tg={t['c']}(ts={t['ts']}) score={s['c']}(ts={s['ts']}) mapping={mp}")

conn.close()