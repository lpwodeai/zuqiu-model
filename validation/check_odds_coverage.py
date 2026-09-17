import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds.db")

# 23/24 赛季 (match_type 包含 2023-2024)
minfo = conn.execute("""
    SELECT COUNT(*) FROM matches 
    WHERE match_type LIKE '%2023-2024%'
""").fetchone()[0]
print(f"match_type 含 2023-2024: {minfo} 场")

# 按 match_date 2023年
mdate = conn.execute("""
    SELECT COUNT(*) FROM matches WHERE match_date LIKE '2023%'
""").fetchone()[0]
print(f"match_date 2023年: {mdate} 场")

# WDL 赔率覆盖率
wdl = conn.execute("""
    SELECT COUNT(DISTINCT m.match_id) 
    FROM matches m
    INNER JOIN wdl_history w ON m.match_id = w.match_id
    WHERE m.match_type LIKE '%2023-2024%'
""").fetchone()[0]
print(f"\nWDL赔率覆盖 (match_type 2023-2024): {wdl}/{minfo} ({wdl*100/minfo:.1f}%)" if minfo > 0 else "N/A")

wdl2 = conn.execute("""
    SELECT COUNT(DISTINCT m.match_id) 
    FROM matches m
    INNER JOIN wdl_history w ON m.match_id = w.match_id
    WHERE m.match_date LIKE '2023%'
""").fetchone()[0]
print(f"WDL赔率覆盖 (match_date 2023): {wdl2}/{mdate} ({wdl2*100/mdate:.1f}%)" if mdate > 0 else "N/A")

# Handicap
hcp = conn.execute("""
    SELECT COUNT(DISTINCT m.match_id) 
    FROM matches m
    INNER JOIN handicap_history h ON m.match_id = h.match_id
    WHERE m.match_type LIKE '%2023-2024%'
""").fetchone()[0]
print(f"\n让球赔率覆盖 (match_type 2023-2024): {hcp}/{minfo} ({hcp*100/minfo:.1f}%)" if minfo > 0 else "N/A")

# Total goals
tg = conn.execute("""
    SELECT COUNT(DISTINCT m.match_id) 
    FROM matches m
    INNER JOIN total_goals_history t ON m.match_id = t.match_id
    WHERE m.match_type LIKE '%2023-2024%'
""").fetchone()[0]
print(f"总进球赔率覆盖 (match_type 2023-2024): {tg}/{minfo} ({tg*100/minfo:.1f}%)" if minfo > 0 else "N/A")

# Score
sc = conn.execute("""
    SELECT COUNT(DISTINCT m.match_id) 
    FROM matches m
    INNER JOIN score_history s ON m.match_id = s.match_id
    WHERE m.match_type LIKE '%2023-2024%'
""").fetchone()[0]
print(f"比分赔率覆盖 (match_type 2023-2024): {sc}/{minfo} ({sc*100/minfo:.1f}%)" if minfo > 0 else "N/A")

# 已有 sporttery 采集数据
import os
sporttery_dir = BASE_DIR / "data" / "sporttery_collected"
if os.path.exists(sporttery_dir):
    files = os.listdir(sporttery_dir)
    print(f"\nsporttery_collected 目录: {len(files)} 个文件")
    for f in sorted(files)[:10]:
        print(f"  {f}")
    if len(files) > 10:
        print(f"  ... 共 {len(files)} 个文件")

conn.close()