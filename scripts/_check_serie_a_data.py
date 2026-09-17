"""临时检查脚本：意甲4场比赛在odds.db中的数据"""
import sqlite3
import os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 1. matches 表中有没有意甲26/27的比赛 (match_type 字段)
print("=== matches 表中意甲26/27 ===")
cnt = conn.execute("SELECT COUNT(*) FROM matches WHERE match_type='意甲' AND match_date LIKE '2026%'").fetchone()[0]
print(f"  意甲+2026年: {cnt} 场")
if cnt > 0:
    for row in conn.execute("SELECT * FROM matches WHERE match_type='意甲' AND match_date LIKE '2026%' LIMIT 5"):
        print(dict(row))

# 2. match_type 有哪些值
print("\n=== match_type 分布 ===")
for row in conn.execute("SELECT match_type, COUNT(*) FROM matches GROUP BY match_type"):
    print(f"  {row[0]}: {row[1]} 场")

# 3. match_lineups 中4场比赛
print("\n=== match_lineups 中4场比赛 ===")
for mid in ['2026-08-23_Inter_Monza', '2026-08-23_Udinese_Como', '2026-08-23_Genoa_SSC Napoli', '2026-08-23_Parma_Cagliari']:
    cnt = conn.execute("SELECT COUNT(*) FROM match_lineups WHERE match_id=?", (mid,)).fetchone()[0]
    print(f"  {mid}: {cnt} 条")

# 4. match_player_stats
print("\n=== match_player_stats 中4场比赛 ===")
for mid in ['2026-08-23_Inter_Monza', '2026-08-23_Udinese_Como', '2026-08-23_Genoa_SSC Napoli', '2026-08-23_Parma_Cagliari']:
    cnt = conn.execute("SELECT COUNT(*) FROM match_player_stats WHERE match_id=?", (mid,)).fetchone()[0]
    print(f"  {mid}: {cnt} 条")

# 5. sofascore_team_features
cnt = conn.execute("SELECT COUNT(*) FROM sofascore_team_features").fetchone()[0]
print(f"\n=== sofascore_team_features 总数: {cnt} ===")
print("最新3条:")
for row in conn.execute("SELECT event_id, match_date, league, home_team_cn, away_team_cn FROM sofascore_team_features ORDER BY match_date DESC LIMIT 3"):
    print(dict(row))

# 6. sofascore_team_features 中有没有这4场
cnt2 = conn.execute("SELECT COUNT(*) FROM sofascore_team_features WHERE event_id IN ('16283050','16283044','16283040','16283043')").fetchone()[0]
print(f"\n  sofascore_team_features 中4场比赛: {cnt2} 条")

# 7. sofascore_team_features 列名
print("\n=== sofascore_team_features 列名 ===")
cols = [r[1] for r in conn.execute("PRAGMA table_info(sofascore_team_features)")]
print(f"  共 {len(cols)} 列: {cols}")

conn.close()