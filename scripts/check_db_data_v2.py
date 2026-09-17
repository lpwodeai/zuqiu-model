# -*- coding: utf-8 -*-
"""查验数据库中各联赛的赛前数据写入情况"""
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "odds.db"

conn = sqlite3.connect(str(DB))
c = conn.cursor()

# 1. matches 表中 2026-08-22/24
print("=" * 70)
print("1. matches 表 2026-08-22/24")
print("=" * 70)
c.execute("""
    SELECT match_id, home_team, away_team, match_date, match_type
    FROM matches
    WHERE match_date >= '2026-08-22' AND match_date <= '2026-08-24'
    ORDER BY match_date
""")
rows = c.fetchall()
print(f"共 {len(rows)} 场比赛:")
for r in rows:
    print(f"  {r[0]} | {r[1]} vs {r[2]} | {r[3]} | {r[4]}")

# 2. match_id_mapping 2026-08-22/24 (通过关联 matches)
print("\n" + "=" * 70)
print("2. match_id_mapping 关联 matches 2026-08-22/24")
print("=" * 70)
c.execute("""
    SELECT m.sh_match_id, m.matches_match_id, mt.home_team, mt.away_team, mt.match_date, mt.match_type
    FROM match_id_mapping m
    JOIN matches mt ON m.matches_match_id = mt.match_id
    WHERE mt.match_date >= '2026-08-22' AND mt.match_date <= '2026-08-24'
    ORDER BY mt.match_date
""")
rows = c.fetchall()
print(f"共 {len(rows)} 条映射:")
for r in rows:
    print(f"  event_id={r[0]} | {r[1]} | {r[2]} vs {r[3]} | {r[4]} | {r[5]}")

# 3. match_lineups 2026-08-22/24
print("\n" + "=" * 70)
print("3. match_lineups 2026-08-22/24")
print("=" * 70)
c.execute("""
    SELECT match_id, COUNT(*) as cnt
    FROM match_lineups
    WHERE match_id LIKE '2026-08-2%'
    GROUP BY match_id
    ORDER BY match_id
""")
rows = c.fetchall()
print(f"共 {len(rows)} 场比赛有 lineup 数据:")
for r in rows:
    print(f"  {r[0]}: {r[1]} 条")

# 4. match_player_stats 2026-08-22/24
print("\n" + "=" * 70)
print("4. match_player_stats 2026-08-22/24")
print("=" * 70)
c.execute("""
    SELECT match_id, COUNT(*) as cnt
    FROM match_player_stats
    WHERE match_id LIKE '2026-08-2%'
    GROUP BY match_id
    ORDER BY match_id
""")
rows = c.fetchall()
print(f"共 {len(rows)} 场比赛有 player_stats 数据:")
for r in rows:
    print(f"  {r[0]}: {r[1]} 条")

# 5. 从 progress 文件对比: 哪些 event_id 没有对应的 match_id
print("\n" + "=" * 70)
print("5. 对照 progress_26_27.json 检查缺失")
print("=" * 70)
import json
progress_file = BASE / "logs" / "sofascore_progress_26_27.json"
with open(progress_file) as f:
    progress = json.load(f)

# 英超 26/27
print("\n英超 26/27:")
for eid in progress.get("英超_26/27", []):
    c.execute("SELECT matches_match_id FROM match_id_mapping WHERE sh_match_id=?", (eid,))
    r = c.fetchone()
    if r:
        mid = r[0]
        c.execute("SELECT COUNT(*) FROM match_lineups WHERE match_id=?", (mid,))
        lu = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM match_player_stats WHERE match_id=?", (mid,))
        ps = c.fetchone()[0]
        print(f"  event_id={eid} -> {mid} | lineups={lu} stats={ps}")
    else:
        print(f"  event_id={eid} -> 未映射!")

# 西甲 26/27
print("\n西甲 26/27:")
for eid in progress.get("西甲_26/27", []):
    c.execute("SELECT matches_match_id FROM match_id_mapping WHERE sh_match_id=?", (eid,))
    r = c.fetchone()
    if r:
        mid = r[0]
        c.execute("SELECT COUNT(*) FROM match_lineups WHERE match_id=?", (mid,))
        lu = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM match_player_stats WHERE match_id=?", (mid,))
        ps = c.fetchone()[0]
        print(f"  event_id={eid} -> {mid} | lineups={lu} stats={ps}")
    else:
        print(f"  event_id={eid} -> 未映射!")

# 法甲 26/27
print("\n法甲 26/27:")
for eid in progress.get("法甲_26/27", []):
    c.execute("SELECT matches_match_id FROM match_id_mapping WHERE sh_match_id=?", (eid,))
    r = c.fetchone()
    if r:
        mid = r[0]
        c.execute("SELECT COUNT(*) FROM match_lineups WHERE match_id=?", (mid,))
        lu = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM match_player_stats WHERE match_id=?", (mid,))
        ps = c.fetchone()[0]
        print(f"  event_id={eid} -> {mid} | lineups={lu} stats={ps}")
    else:
        print(f"  event_id={eid} -> 未映射!")

# 意甲 26/27
print("\n意甲 26/27:")
for eid in progress.get("意甲_26/27", []):
    c.execute("SELECT matches_match_id FROM match_id_mapping WHERE sh_match_id=?", (eid,))
    r = c.fetchone()
    if r:
        mid = r[0]
        c.execute("SELECT COUNT(*) FROM match_lineups WHERE match_id=?", (mid,))
        lu = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM match_player_stats WHERE match_id=?", (mid,))
        ps = c.fetchone()[0]
        print(f"  event_id={eid} -> {mid} | lineups={lu} stats={ps}")
    else:
        print(f"  event_id={eid} -> 未映射!")

conn.close()
print("\n完成！")