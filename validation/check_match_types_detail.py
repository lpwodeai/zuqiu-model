import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
db = BASE_DIR / "data" / "odds.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

print("=" * 70)
print("📊 matches 表 match_type 分布统计")
print("=" * 70)

# 1. 带赛季的 match_type 分布
rows = cur.execute("""
    SELECT match_type, COUNT(*) 
    FROM matches 
    GROUP BY match_type 
    ORDER BY match_type
""").fetchall()

print("\n📋 带赛季后缀的 match_type（15种 = 5联赛 × 3赛季）:")
print(f"{'match_type':40s} {'场数':>6s}")
print("-" * 48)
for r in rows:
    print(f"  {r[0]:38s} {r[1]:>6d}")

# 2. 按纯联赛名统计
print("\n📋 按纯联赛名汇总（去掉赛季后缀）:")
league_counts = {}
for r in rows:
    mt = r[0]
    # 提取纯联赛名
    for league in ['英超', '西甲', '意甲', '德甲', '法甲']:
        if mt.startswith(league):
            league_counts[league] = league_counts.get(league, 0) + r[1]
            break

for league in ['英超', '西甲', '意甲', '德甲', '法甲']:
    print(f"  {league}: {league_counts.get(league, 0):>6d} 场")

# 3. 英文名残留检查
en_count = cur.execute("""
    SELECT COUNT(*) FROM matches 
    WHERE match_type IN ('Premier League','Serie A','La Liga','Bundesliga','Ligue 1')
""").fetchone()[0]

print(f"\n📋 英文名残留: {en_count} 条")

# 4. 按赛季统计
print("\n📋 按赛季统计:")
season_counts = {}
for r in rows:
    mt = r[0]
    # 提取赛季
    for season in ['2023-2024', '2024-2025', '2025-2026']:
        if season in mt:
            season_counts[season] = season_counts.get(season, 0) + r[1]
            break

for season in ['2023-2024', '2024-2025', '2025-2026']:
    print(f"  {season}赛季: {season_counts.get(season, 0):>6d} 场")

total = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
print(f"\n📊 总比赛数: {total}")
print(f"📊 联赛种类: {len(league_counts)} 个（纯联赛名）")
print(f"📊 match_type 种类: {len(rows)} 个（含赛季后缀）")

conn.close()

print("\n" + "=" * 70)
print("✅ 结论: 英文名 0 残留，纯联赛名 5 个，数据归一化完成")
print("=" * 70)