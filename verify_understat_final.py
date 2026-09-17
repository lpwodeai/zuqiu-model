import sqlite3

c = sqlite3.connect(r'data/odds.db')
cur = c.cursor()

print("=== 西甲 25/26 全赛季 Understat 数据最终查验 ===\n")

# 比赛级
cur.execute("SELECT COUNT(*), COUNT(DISTINCT home_team) FROM understat_match_team_stats WHERE season='25/26'")
m, teams = cur.fetchone()
print(f"比赛级 understat_match_team_stats: {m} 场, {teams} 支球队")

# 球员级
cur.execute("SELECT COUNT(*), COUNT(DISTINCT match_id), COUNT(DISTINCT player_id) FROM understat_player_xg WHERE season='25/26'")
p, pm, pp = cur.fetchone()
print(f"球员级 understat_player_xg: {p} 行, {pm} 场, {pp} 名球员")

# 射门级
cur.execute("SELECT COUNT(*), COUNT(DISTINCT match_id) FROM understat_shots WHERE season='25/26'")
s, sm = cur.fetchone()
print(f"射门级 understat_shots: {s} 行, {sm} 场")

# 关键字段覆盖率
print("\n球员 xG 字段覆盖率:")
tot = p
for col in ['xg', 'xa', 'xg_chain', 'xg_buildup', 'key_passes', 'position']:
    cnt = cur.execute(f"SELECT COUNT(*) FROM understat_player_xg WHERE season='25/26' AND {col} IS NOT NULL AND {col} != ''").fetchone()[0]
    print(f"  {col:12s}: {cnt:>6} / {tot} ({cnt/tot*100:5.1f}%)")

# 射门结果分布
print("\n射门结果分布:")
cur.execute("SELECT result, COUNT(*) FROM understat_shots WHERE season='25/26' GROUP BY result ORDER BY COUNT(*) DESC")
for r in cur.fetchall():
    print(f"  {r[0]:20s}: {r[1]}")

# 射门方式分布
print("\n射门方式分布:")
cur.execute("SELECT shot_type, COUNT(*) FROM understat_shots WHERE season='25/26' GROUP BY shot_type ORDER BY COUNT(*) DESC")
for r in cur.fetchall():
    print(f"  {r[0]:20s}: {r[1]}")

# 抽查一场完整数据
print("\n抽查一场（进球最多）:")
cur.execute("""
    SELECT m.home_team, m.away_team, m.home_goals, m.away_goals, m.home_xg, m.away_xg
    FROM understat_match_team_stats m WHERE m.season='25/26'
    ORDER BY (m.home_goals + m.away_goals) DESC LIMIT 1
""")
r = cur.fetchone()
if r:
    print(f"  {r[0]} {r[2]}-{r[3]} {r[1]}  (xG {r[4]}-{r[5]})")

c.close()