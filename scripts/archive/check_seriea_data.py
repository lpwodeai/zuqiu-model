"""检查odds_timing.db中的意甲数据"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds_timing.db")
cursor = conn.cursor()

# 检查意甲比赛
cursor.execute("SELECT COUNT(*) FROM matches WHERE league LIKE '%意甲%'")
total = cursor.fetchone()[0]
print(f'意甲比赛总数: {total}')

# 检查赔率数据
cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
wdl = cursor.fetchone()[0]
print(f'胜平负赔率: {wdl} 条')

cursor.execute("SELECT COUNT(*) FROM handicap_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
hcp = cursor.fetchone()[0]
print(f'让球赔率: {hcp} 条')

cursor.execute("SELECT COUNT(*) FROM score_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
score = cursor.fetchone()[0]
print(f'比分赔率: {score} 条')

cursor.execute("SELECT COUNT(*) FROM total_goals_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
tg = cursor.fetchone()[0]
print(f'总进球赔率: {tg} 条')

# 查看一场比赛的详细数据
cursor.execute('''
    SELECT m.match_id, m.home_team, m.away_team, m.match_date,
           (SELECT COUNT(*) FROM wdl_timing w WHERE w.match_id = m.match_id) as wdl_count,
           (SELECT COUNT(*) FROM handicap_timing h WHERE h.match_id = m.match_id) as hcp_count,
           (SELECT COUNT(*) FROM score_timing s WHERE s.match_id = m.match_id) as score_count,
           (SELECT COUNT(*) FROM total_goals_timing t WHERE t.match_id = m.match_id) as tg_count
    FROM matches m
    WHERE m.league LIKE '%意甲%'
    LIMIT 5
''')
print('\n前5场比赛数据:')
for row in cursor.fetchall():
    print(f'  {row[0]}: WDL={row[4]}, HCP={row[5]}, Score={row[6]}, TG={row[7]}')

# 检查比赛结果
cursor.execute("SELECT COUNT(*) FROM match_results WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
results = cursor.fetchone()[0]
print(f'\n比赛结果记录: {results} 条')

conn.close()
